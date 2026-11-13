"""每天的业界简报：抓各来源 → 去重 → 大模型筛选、分类、写标题和摘要（中文或英文） → 存进数据库。"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..cloud import repo
from ..cloud.store import Store
from ..llm.rerank import LLMClient, parse_json_array, parse_json_object
from ..llm.style import style_for
from ..presets import get_preset
from .fetch import fetch_all
from .sources import custom_sources, sources_for

log = logging.getLogger("robot_times.news")

CATEGORIES = get_preset()["news_categories"]  # 默认预设的分类（兼容旧代码）
BATCH = 12
MAX_NEWS, MAX_DEEP = 15, 6
SEEN_DAYS = 30

PROMPT = """他的研究兴趣：{interests}
他关心{focus}的动向：谁发布了什么、谁融了多少钱、哪些结果是真的进展、行业往哪走。

下面是 {n} 条{kind}（序号、来源、原标题、摘录）。逐条判断：
- keep：值不值得放进他今天的简报。只留有实质内容的：产品或模型发布、真机演示和部署、融资并购、
  重要研究结果、政策监管、有见地的分析。招聘、活动预告、软文、教程、榜单凑数、重复报道一律 false。
- category：从 {categories} 里选一个。
- score：0–10，对他有多重要。9–10 是这个领域本周级别的大事；5 以下就不用放了。要拉开差距。
  和他的研究兴趣直接相关的，比分量差不多的泛行业新闻（融资、上市、人事变动）高一两分。
- title_zh：中文标题，不超过 26 字，像报纸标题一样点出主体和关键数字（例如“Figure 完成 10 亿美元融资，估值 390 亿”）。
- summary：{summary_rule}只用摘录里有的事实，没有的数字不要编。
- why：一句话（不超过 40 字），对他说为什么值得看；没有特别理由就写空字符串。

只输出 JSON：{{"items": [{{"i": 序号, "keep": true, "category": "...", "score": 7, "title_zh": "...", "summary": "...", "why": "..."}}]}}

{listing}
"""
EVENT_PROMPT = """下面是今天简报里的 {n} 条新闻（序号、标题）。找出报道同一件事的：同一次发布、同一笔融资、
同一次上市的不同侧面（比如“启动 IPO”“拟募资多少”“创始人套现多少”）都算同一件事。

只输出 JSON：{{"groups": [[序号, 序号], ...]}}，每组是同一件事的两条或更多；没有重复就输出 {{"groups": []}}。

{listing}
"""
RULES = {
    "news": ("条新闻", "1–2 句，不超过 90 字，说清谁、做了什么、关键数字。"),
    "deep": ("篇深度文章", "2 句，不超过 110 字，说清作者的核心论点和依据。"),
}

# 英文版：title_zh 字段名不变（存进数据库叫 titleZh），里面装英文标题
PROMPT_EN = """Their research interests: {interests}
They follow what's happening in {focus}: who launched what, who raised how much, which results are real progress,
where the industry is heading.

Below are {n} {kind} (index, source, original title, excerpt). Judge each one:
- keep: whether it belongs in today's brief. Keep only substantive items: product or model launches, real-robot
  demos and deployments, funding and M&A, important research results, policy and regulation, insightful analysis.
  Hiring, event announcements, advertorials, tutorials, filler listicles and duplicate coverage are all false.
- category: pick one of {categories}.
- score: 0–10, how much it matters to them. 9–10 is a big-news-of-the-week item for this field; below 5 doesn't
  make the cut. Spread the scores out. Items directly related to their research interests score a point or two
  higher than general industry news of similar weight (funding, IPOs, people moves).
- title_zh: an English headline, at most 14 words, like a newspaper headline naming the subject and the key
  number (e.g. "Figure raises $1B at a $39B valuation"). Write a fresh headline even if the original is English.
- summary: {summary_rule} Use only facts in the excerpt; never invent numbers.
- why: one sentence (at most 20 words) telling them why it's worth reading; empty string if there's no particular reason.

Output only JSON: {{"items": [{{"i": index, "keep": true, "category": "...", "score": 7, "title_zh": "...", "summary": "...", "why": "..."}}]}}

{listing}
"""
EVENT_PROMPT_EN = """Below are {n} items from today's brief (index, headline). Find the ones covering the same event:
different angles on the same launch, the same funding round or the same IPO ("files for IPO", "plans to raise",
"founder cashes out") all count as one event.

Output only JSON: {{"groups": [[index, index], ...]}}, each group being two or more items about the same event;
if there are no duplicates, output {{"groups": []}}.

{listing}
"""
RULES_EN = {
    "news": ("news items", "1–2 sentences, at most 45 words: who, what they did, the key numbers."),
    "deep": (
        "long-form articles",
        "2 sentences, at most 55 words: the author's core argument and evidence.",
    ),
}


def _norm_title(t: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[一-鿿]", t.lower()))


def dedupe(items: list[dict], key: str = "title") -> list[dict]:
    """同一链接只留一条；标题几乎一样（不同媒体报同一件事）也只留第一条。"""
    out: list[dict] = []
    seen_ids: set[str] = set()
    sigs: list[set[str]] = []
    for it in items:
        if it["id"] in seen_ids:
            continue
        sig = _norm_title(it.get(key) or it["title"])
        if any(len(sig & s) / max(1, len(sig | s)) >= 0.7 for s in sigs):
            continue
        seen_ids.add(it["id"])
        sigs.append(sig)
        out.append(it)
    return out


def fresh_only(store: Store, items: list[dict], day: date) -> list[dict]:
    """去掉以前几天已经出现过的（当天重跑时，当天的照样保留），并记下这次的。"""
    today = day.isoformat()
    seen: dict[str, str] = repo.get_json(store, "news_seen") or {}
    fresh = [it for it in items if seen.get(it["id"], today) >= today]
    for it in items:
        seen.setdefault(it["id"], today)
    oldest = (day - timedelta(days=SEEN_DAYS)).isoformat()
    repo.set_json(store, "news_seen", {k: v for k, v in seen.items() if v >= oldest})
    return fresh


def _llm_batch(
    llm: LLMClient, batch: list[dict], kind: str, interests: str, preset: dict[str, Any]
) -> list[dict]:
    en = preset.get("lang") == "en"
    label, rule = (RULES_EN if en else RULES)[kind]
    categories = preset["news_categories"]
    if en:
        listing = "\n\n".join(
            f"[{i}] Source: {it['source']}\nTitle: {it['title']}\nExcerpt: {it['excerpt'][:500] or '(none)'}"
            for i, it in enumerate(batch)
        )
    else:
        listing = "\n\n".join(
            f"[{i}] 来源：{it['source']}\n标题：{it['title']}\n摘录：{it['excerpt'][:500] or '（无）'}"
            for i, it in enumerate(batch)
        )
    prompt = (PROMPT_EN if en else PROMPT).format(
        interests=interests or ("(not given)" if en else "（未填写）"),
        focus=preset["news_focus"],
        n=len(batch),
        kind=label,
        categories=(", " if en else "、").join(categories),
        summary_rule=rule,
        listing=listing,
    )
    kw = {"system": style_for(preset)} if en else {}  # 中文沿用默认的写作风格
    results = parse_json_array(llm.complete(prompt, json_mode=True, **kw))
    out = []
    for r in results:
        try:
            it = batch[int(r.get("i"))]
        except (TypeError, ValueError, IndexError):
            continue
        if not r.get("keep"):
            continue
        try:
            score = max(0.0, min(10.0, float(r.get("score", 0))))
        except (TypeError, ValueError):
            score = 0.0
        cat = r.get("category")
        if cat not in categories:
            cat = preset["news_fallback"][1]
        out.append({**it, "category": cat, "score": score,
                    "titleZh": str(r.get("title_zh") or it["title"])[:120 if en else 60],
                    "summary": str(r.get("summary") or ""), "why": str(r.get("why") or "")})  # fmt: skip
    return out


def merge_events(
    llm: LLMClient | None, items: list[dict], errors: list[str], lang: str = "zh"
) -> list[dict]:
    """同一件事的不同报道只留第一条（items 已按重要性排好）。标题去重挡不住“启动 IPO / 拟募资 / 创始人套现”这种。"""
    if llm is None or len(items) < 2:
        return items
    listing = "\n".join(f"[{i}] {it['titleZh']}" for i, it in enumerate(items))
    template = EVENT_PROMPT_EN if lang == "en" else EVENT_PROMPT
    try:
        data = parse_json_object(
            llm.complete(
                template.format(n=len(items), listing=listing),
                json_mode=True,
                **({"system": style_for(get_preset({"lang": "en"}))} if lang == "en" else {}),
            )
        )
    except Exception as e:  # 合并失败不影响简报，照原样放
        head = "Merging duplicate events failed" if lang == "en" else "合并同一事件失败"
        errors.append(f"{head}{': ' if lang == 'en' else '：'}{type(e).__name__} {str(e)[:120]}")
        return items
    drop: set[int] = set()
    for group in data.get("groups") or []:
        idx = sorted({int(i) for i in group if str(i).isdigit() and int(i) < len(items)})
        drop.update(idx[1:])
    return [it for i, it in enumerate(items) if i not in drop]


def judge(
    llm: LLMClient | None,
    items: list[dict],
    kind: str,
    interests: str,
    errors: list[str],
    preset: dict[str, Any] | None = None,
) -> list[dict]:
    preset = preset or get_preset()
    if llm is None:  # 没有大模型：只按关键词挑和方向有关的，保留原标题
        words = re.compile(preset["news_keywords"], re.I)
        near, far = preset["news_fallback"]
        return [
            {**it, "category": near if words.search(it["title"]) else far,
             "score": 6.0 if words.search(it["title"] + it["excerpt"]) else 4.0,
             "titleZh": it["title"], "summary": it["excerpt"][:120], "why": ""}
            for it in items
        ]  # fmt: skip
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
    kept: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_llm_batch, llm, b, kind, interests, preset) for b in batches]
        for f in futures:
            try:
                kept.extend(f.result())
            except Exception as e:
                errors.append(f"{type(e).__name__} {str(e)[:120]}")
    return kept


CACHE_HOURS = 30


def feed_cache(store: Store, url: str) -> bytes | None:
    """看板服务代抓的订阅内容；超过 30 小时的不用。"""
    data = repo.get_json(store, f"feed_cache:{url}")
    if not data or not data.get("body") or int(data.get("status", 0)) != 200:
        return None
    at = datetime.fromisoformat(str(data["at"]).replace("Z", "+00:00"))
    if datetime.now(UTC) - at > timedelta(hours=CACHE_HOURS):
        return None
    return str(data["body"]).encode()


def _sort_key(it: dict) -> tuple:
    return (
        -it["score"],
        -(datetime.fromisoformat(it["published"]).timestamp() if it.get("published") else 0),
    )


def run_news(
    store: Store,
    llm: LLMClient | None,
    settings: dict[str, Any],
    day: date,
    fetch: Callable[..., tuple[list[dict], list[dict]]] = fetch_all,
) -> dict[str, Any]:
    preset = get_preset(settings)
    sources = sources_for(preset["news_groups"]) + custom_sources(settings.get("newsFeeds") or [])
    items, health = fetch(sources, cache=lambda url: feed_cache(store, url))
    # 拒绝 GitHub 服务器的订阅交给看板服务（Cloudflare Worker）定时代抓，内容存在 config 的 feed_cache:<地址>
    proxied = set(repo.get_json(store, "proxy_feeds") or []) | {
        h["url"] for h in health if h.get("blocked")
    }
    direct_ok = {h["url"] for h in health if h["ok"] and not h.get("cached")}
    current = {s["url"] for s in sources}  # 已经删掉的来源不再代抓
    repo.set_json(store, "proxy_feeds", sorted((proxied & current) - direct_ok))
    items = dedupe(sorted(items, key=lambda it: it.get("published") or "", reverse=True))
    items = fresh_only(store, items, day)
    news = [it for it in items if it["type"] == "news"][:180]
    deep = [it for it in items if it["type"] == "deep"][:48]
    log.info("业界：快讯候选 %d 条，深度候选 %d 篇", len(news), len(deep))

    errors: list[str] = []
    interests = settings.get("interests", "")
    picked_news = [
        it for it in judge(llm, news, "news", interests, errors, preset) if it["score"] >= 5
    ]
    picked_deep = [
        it for it in judge(llm, deep, "deep", interests, errors, preset) if it["score"] >= 5
    ]
    picked_news = dedupe(sorted(picked_news, key=_sort_key), key="titleZh")
    picked_news = merge_events(llm, picked_news[: MAX_NEWS * 2], errors, preset["lang"])[:MAX_NEWS]
    picked_deep = sorted(picked_deep, key=_sort_key)[:MAX_DEEP]

    def public(it: dict) -> dict:  # 不存原文摘录，只存链接和我们自己写的摘要
        keep = ("id", "title", "titleZh", "url", "source", "sourceId", "published", "category",
                "score", "summary", "why", "discussion", "points", "lang", "image")  # fmt: skip
        return {k: it[k] for k in keep if k in it}

    doc = {
        "date": day.isoformat(),
        "news": [public(it) for it in picked_news],
        "deep": [public(it) for it in picked_deep],
        "sources": health,
        "stats": {
            "candidates": len(news) + len(deep),
            "llm": llm is not None,
            "problems": errors[:3],
        },
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    store.run(
        "insert into news (date, data) values (?, ?) on conflict(date) do update set data = excluded.data",
        [doc["date"], json.dumps(doc, ensure_ascii=False)],
    )
    log.info("已保存业界简报：快讯 %d 条，深度 %d 篇", len(doc["news"]), len(doc["deep"]))
    return doc
