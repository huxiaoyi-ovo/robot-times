"""每日推荐：抓新论文 → 结合文献库和你的反馈打分 → LLM 精排写中文摘要 → 排阅读计划 → 存进数据库。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..llm.rerank import LLMClient, parse_json_object
from ..llm.style import style_for
from ..models import Paper, ScoredPaper
from ..pipeline import arxiv_ids_in_library, recommend, total_score
from ..planning import assign_tiers, day_plan
from ..presets import get_preset
from ..ranking.embedder import Embedder
from ..signals import buzz_score, venue_impact
from . import repo
from .analyze import analyze_papers, carry_over
from .store import Store

log = logging.getLogger("robot_times.daily")
TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class DailyDeps:
    store: Store
    zotero: Any  # 需要 top_items()
    embedder: Embedder
    fetch_papers: Callable[[list[str], int], list[Paper]]  # (分类, 天数) -> 论文
    hf_upvotes: Callable[[list[date]], dict[str, int]] | None = None
    llm: LLMClient | None = None
    html: Any = None  # arXiv HTML 版全文，需要 get(arxiv_id)；给了才写详细解读


DIVERSITY = 0.4  # 默认的多样性：同一方向的论文太多时，让位给其他方向（设置里可调）
RELEASE_HOUR = 8  # arXiv 北京时间 8 点（夏令时）或 9 点发布；这之后生成的推荐才包含当天的新论文


def generated_after_release(store: Store, day: date) -> bool:
    """这一天的推荐是否已经在 arXiv 当天发布之后生成过。"""
    rows = store.rows("select data from days where date = ?", [day.isoformat()])
    if not rows:
        return False
    at = json.loads(rows[0]["data"]).get("generatedAt")
    if not at:
        return False
    local = datetime.fromisoformat(at).astimezone(TZ)
    return local.date() == day and local.hour >= RELEASE_HOUR + 1


def today_local(now: datetime | None = None) -> date:
    return (now or datetime.now(TZ)).astimezone(TZ).date()


# arXiv 每天北京时间 8 点（美东夏令时）或 9 点（冬令时）发布前一天的提交，周末不发布，
# 所以每次看最近 5 天的提交，再去掉以前已经看过的候选，既不漏也不重复。
FETCH_DAYS = 5
SEEN_KEEP_DAYS = 14


def fresh_candidates(store: Store, papers: list[Paper], day: date) -> list[Paper]:
    """去掉以前几天已经当过候选的论文（当天重跑时，当天的候选照样保留）。

    同时把这次的候选记下来，只保留最近两周。
    """
    today = day.isoformat()
    seen: dict[str, str] = repo.get_json(store, "seen") or {}
    fresh = [p for p in papers if seen.get(p.id, today) >= today]
    for p in papers:
        seen.setdefault(p.id, today)
    oldest = (day - timedelta(days=SEEN_KEEP_DAYS)).isoformat()
    repo.set_json(store, "seen", {k: v for k, v in seen.items() if v >= oldest})
    return fresh


def deep_first(scored: list[ScoredPaper], window: int = 5) -> list[ScoredPaper]:
    """精读那篇看“值不值得你读”，不看热度：前几篇里大模型分最高的排到第一（同分看综合分顺序）。"""
    head = scored[:window]
    if not any(sp.llm_score is not None for sp in head):
        return scored
    best = max(range(len(head)), key=lambda i: (head[i].llm_score or 0, -i))
    return [scored[best]] + scored[:best] + scored[best + 1 :]


def paper_record(sp: ScoredPaper, tier: str, weights: dict, added_on: str, signals: str) -> dict:
    p = sp.paper
    impact, venue = venue_impact(p)
    return {
        "id": p.id,
        "title": p.title,
        "authors": p.authors,
        "abstract": p.abstract,
        "date": p.published.isoformat(),
        "categories": p.categories,
        "url": p.url,
        "pdf": p.pdf_url,
        "venue": venue or p.venue or (f"arXiv · {p.categories[0]}" if p.categories else "arXiv"),
        "comment": p.comment,
        "source": p.source,
        "tier": tier,
        "scores": {
            "rel": round(sp.relevance, 1),
            "llm": sp.llm_score,
            "impact": sp.impact if sp.impact is not None else impact,
            "buzz": sp.buzz,
            "total": total_score(sp, weights),
        },
        "summary": sp.summary_zh,
        "why": sp.why_zh,
        "tip": sp.tip_zh,
        "related": sp.related,
        "topic": sp.topic,
        "signals": signals,
        "addedOn": added_on,
    }


def _complete_json(llm: LLMClient, prompt: str, preset: dict | None = None) -> str:
    return llm.complete(prompt, system=style_for(preset), json_mode=True)


def _headline(
    llm: LLMClient | None, top: list[dict], preset: dict | None = None
) -> tuple[str, str]:
    if not top:
        return "今天没有新推荐", ""
    fallback = (top[0]["title"], "")
    if llm is None:
        return fallback
    tiers = {"deep": "精读", "abstract": "看摘要", "scan": "扫读"}
    listing = "\n".join(
        f"{i}. [{tiers.get(d['tier'], d['tier'])}] {d['title']}"
        f"\n   要点：{d.get('summary') or d['abstract'][:300]}"
        f"\n   和他的关系：{d.get('why') or '（无）'}"
        for i, d in enumerate(top[:5], 1)
    )
    prompt = (
        "下面是今天排给他的前几篇论文（按推荐顺序，第 1 篇排给他精读），以及每篇的要点和与他的关系。\n"
        "写两段，像早上发给他的一条消息：\n"
        "headline：一句话，不超过 25 字，点出今天最值得看的具体东西（谁用什么办法把什么做到了什么程度），"
        "不要“今日推荐”“值得关注”这种空话。\n"
        "intro：2–3 句完整的话，总共 80 到 140 字，不要电报体。先说为什么精读第 1 篇、重点看什么；"
        "再用一句话带过另外几篇的看点。"
        "如果今天整体没有特别贴他方向的论文，就直说，建议他今天把时间花在补以前的在读论文上。\n"
        '只输出 JSON：{"headline": "...", "intro": "..."}\n\n' + listing
    )
    try:
        data = parse_json_object(_complete_json(llm, prompt, preset))
        return str(data.get("headline") or fallback[0]), str(data.get("intro") or "")
    except Exception as e:  # LLM 失败不影响当天推荐
        log.warning("生成标题失败：%s", e)
        return fallback


def check_zotero_key(zotero: Any, problems: list[str]) -> dict[str, bool] | None:
    """只读检查 Zotero key 的权限，缺写入或笔记权限时记进 problems（看板上会显示）。"""
    if not hasattr(zotero, "key_access"):
        return None
    try:
        access = zotero.key_access()
    except Exception as e:
        log.warning("查不到 Zotero key 的权限：%s", e)
        return None
    log.info("Zotero key 权限：%s", access)
    missing = [name for k, name in (("write", "写入"), ("notes", "笔记")) if not access.get(k)]
    if missing:
        problems.append(
            f"Zotero key 没有{'和'.join(missing)}权限，“加入 Zotero”会失败。"
            "到 zotero.org/settings/keys 编辑这个 key，勾选 Allow write access 和 Allow notes access。"
        )
    return access


def run_daily(deps: DailyDeps, settings: dict[str, Any], day: date | None = None) -> dict[str, Any]:
    day = day or today_local()
    store = deps.store
    weights = settings["weights"]
    preset = get_preset(settings)

    problems: list[str] = []
    zotero_access = check_zotero_key(deps.zotero, problems)
    library = deps.zotero.top_items()
    log.info("文献库 %d 条", len(library))
    fetched = deps.fetch_papers(settings.get("categories") or preset["categories"], FETCH_DAYS)
    papers = fresh_candidates(store, fetched, day)
    log.info("arXiv 最近 %d 天 %d 篇，其中新候选 %d 篇", FETCH_DAYS, len(fetched), len(papers))
    if not papers:
        # 节假日 arXiv 不发布：不生成空的一天，看板继续显示上一次的推荐
        log.info("没有新论文，今天不生成推荐")
        return {
            "date": day.isoformat(),
            "skipped": True,
            "picks": [],
            "headline": "没有新论文，今天不生成推荐",
        }

    upvotes: dict[str, int] | None = None
    if deps.hf_upvotes is not None:
        try:
            upvotes = deps.hf_upvotes([day - timedelta(days=i) for i in range(4)])
        except Exception as e:
            log.warning("拿不到 Hugging Face 热度，本次不计热度：%s", e)
            problems.append(f"Hugging Face 热度没拿到：{e}")

    def annotate(sp: ScoredPaper) -> None:
        sp.impact, _ = venue_impact(sp.paper)
        if upvotes is not None:
            sp.buzz = buzz_score(upvotes.get(sp.paper.id, 0))

    liked, skipped = repo.feedback_texts(store)
    llm_errors: list[str] = []
    known = arxiv_ids_in_library(library) | repo.known_paper_ids(store, before=day.isoformat())
    scored = recommend(
        papers,
        library,
        deps.embedder,
        weights=weights,
        llm=deps.llm,
        interests=settings.get("interests", ""),
        known_ids=known,
        liked_texts=liked,
        skipped_texts=skipped,
        annotate=annotate,
        llm_errors=llm_errors,
        limit=int(settings.get("top", 12)),
        diversity=float(settings.get("diversity", DIVERSITY)),
        preset=preset,
    )
    if llm_errors:
        log.warning("大模型精排失败 %d 批：%s", len(llm_errors), llm_errors)
        problems.append(f"大模型精排有 {len(llm_errors)} 批失败：{llm_errors[0]}")

    scored = deep_first(scored)
    tiers = assign_tiers(len(scored))
    added_on = day.isoformat()
    records = []
    for sp, tier in zip(scored, tiers, strict=True):
        n = (upvotes or {}).get(sp.paper.id, 0)
        records.append(
            paper_record(sp, tier, weights, added_on, f"Hugging Face {n} 赞" if n else "")
        )
    carry_over(store, records)
    for rec in records:
        repo.upsert_paper(store, rec)

    if deps.llm is not None and deps.html is not None:
        analyze_papers(
            store,
            deps.llm,
            records,
            settings.get("interests", ""),
            deps.html,
            problems,
            preset=preset,
        )

    by_tier = {
        t: [r["id"] for r in records if r["tier"] == t] for t in ("deep", "abstract", "scan")
    }
    plan = day_plan(
        int(settings.get("dailyMinutes", 60)),
        by_tier["deep"],
        by_tier["abstract"],
        by_tier["scan"],
        reading=repo.notes_by_status(store, "reading"),
    )
    headline, intro = _headline(deps.llm, records, preset)
    day_doc = {
        "date": added_on,
        "headline": headline,
        "intro": intro,
        "picks": [r["id"] for r in records],
        "plan": plan,
        "stats": {
            "library": len(library),
            "fetched": len(fetched),
            "candidates": len(papers),
            "llm": deps.llm is not None,
            "problems": problems,
            "zotero": zotero_access,
        },
        "generatedAt": repo.now_iso(),
    }
    repo.save_day(store, day_doc)
    log.info("已保存 %s 的推荐：%d 篇", added_on, len(records))
    return day_doc
