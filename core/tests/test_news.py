"""业界简报：各类来源的解析、去重、筛选和保存。全部离线。"""

import json
import re
from datetime import UTC, date, datetime

import httpx

from robot_times.cloud.store import SqliteStore
from robot_times.news.digest import dedupe, run_news
from robot_times.news.fetch import canonical, fetch_all
from robot_times.news.sources import SOURCES, custom_sources

NOW = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Figure raises $1B at $39B valuation</title><link>https://ex.com/figure?utm_source=rss</link>
<pubDate>Sun, 25 Oct 2026 15:00:00 GMT</pubDate><description>&lt;p&gt;&lt;img src="/img/figure.jpg"&gt;Figure AI said...&lt;/p&gt;</description></item>
<item><title>Old story</title><link>https://ex.com/old</link><pubDate>Mon, 01 Jun 2026 15:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Deep</title>
<entry><title>Why robots won't have a ChatGPT moment</title><link href="https://deep.ex/essay"/>
<updated>2026-10-20T10:00:00Z</updated><summary>Reliability compounds slowly.</summary></entry></feed>"""

LISTING = """<html><body>
<a href="/news/robostrategy-articles">RoboStrategy Articles</a>
<a class="card" href="./the-chatgpt-moment-for-robotics"><h3>The ChatGPT Moment for Robotics</h3></a>
<a href="/news/the-chatgpt-moment-for-robotics">The ChatGPT Moment for Robotics</a>
<a href="/news/media">Media</a><a href="/about">About us page</a></body></html>"""


def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "rss.ex":
        return httpx.Response(200, text=RSS)
    if host == "atom.ex":
        return httpx.Response(200, text=ATOM)
    if host == "list.ex" and request.url.path.endswith("robostrategy-articles"):
        return httpx.Response(200, text=LISTING)
    if host == "list.ex":
        return httpx.Response(
            200,
            text='<meta property="og:description" content="Adoption will be gradual.">'
            '<meta property="og:image" content="https://list.ex/cover.png">',
        )
    if host == "aihot.news":
        assert request.headers["User-Agent"].startswith("aihot-api/2.0.0")
        return httpx.Response(200, json={"items": [
            {"id": "x", "title": "智元发布新一代人形机器人", "originalTitle": "AgiBot launches", "summary": "售价 9.9 万元",
             "links": {"story": "https://aihot.news/s/x", "original": "https://aihot.ex/a1"},
             "publishedAt": "2026-10-26T01:00:00Z", "source": {"name": "机器之心"}}]})  # fmt: skip
    if host == "hn.algolia.com":
        return httpx.Response(200, json={"hits": [
            {"title": "Show HN: open-source humanoid", "url": "https://gh.ex/h", "objectID": "1",
             "points": 120, "created_at_i": int(NOW.timestamp()) - 3600}]})  # fmt: skip
    if host == "blocked.ex":
        return httpx.Response(403)
    return httpx.Response(503)


SRC = [
    {"id": "rss", "name": "RSS 源", "kind": "rss", "type": "news", "url": "https://rss.ex/feed"},
    {"id": "atom", "name": "深度源", "kind": "rss", "type": "deep", "url": "https://atom.ex/feed"},
    {"id": "list", "name": "列表页", "kind": "html", "type": "deep", "url": "https://list.ex/news/robostrategy-articles",
     "pattern": SOURCES[[s["id"] for s in SOURCES].index("robostrategy")]["pattern"].replace(
         "https://robostrategy\\.co", "https://list\\.ex")},
    {"id": "aihot", "name": "AIHOT", "kind": "aihot", "type": "news", "url": "https://aihot.news/api/v1/items"},
    {"id": "hn", "name": "Hacker News", "kind": "hn", "type": "news", "url": "https://hn.algolia.com/api/v1/search_by_date",
     "queries": ["humanoid", "robot"], "min_points": 40},
    {"id": "down", "name": "坏掉的源", "kind": "rss", "type": "news", "url": "https://down.ex/feed"},
    {"id": "blocked", "name": "拦截的源", "kind": "rss", "type": "deep", "url": "https://blocked.ex/feed"},
]  # fmt: skip


def fetched():
    return fetch_all(SRC, now=NOW, transport=httpx.MockTransport(handler))


def test_fetch_all_parses_every_kind_and_isolates_failures():
    items, health = fetched()
    by = {h["id"]: h for h in health}
    assert by["rss"]["count"] == 1  # 旧文章被时间窗口过滤
    assert by["atom"]["count"] == 1 and by["list"]["count"] == 1
    assert by["aihot"]["count"] == 1 and by["hn"]["count"] == 1  # 两个查询的同一条只算一次
    assert not by["down"]["ok"] and "503" in by["down"]["error"]
    titles = {it["title"] for it in items}
    assert "Figure raises $1B at $39B valuation" in titles
    assert "The ChatGPT Moment for Robotics" in titles
    assert (
        next(it for it in items if it["sourceId"] == "list")["excerpt"]
        == "Adoption will be gradual."
    )
    assert (
        next(it for it in items if it["sourceId"] == "list")["image"] == "https://list.ex/cover.png"
    )
    rss = next(it for it in items if it["sourceId"] == "rss")
    assert rss["image"] == "https://ex.com/img/figure.jpg"  # 正文里的第一张图，相对地址补全
    assert rss["excerpt"] == "Figure AI said..."  # 去掉了 HTML
    aihot = next(it for it in items if it["sourceId"] == "aihot")
    assert aihot["source"] == "AIHOT（机器之心）" and aihot["lang"] == "zh"
    assert aihot["url"] == "https://aihot.ex/a1" and "AgiBot launches" in aihot["excerpt"]
    assert by["blocked"]["blocked"] and not by["blocked"]["ok"]


def test_blocked_feed_read_from_worker_cache():
    from robot_times.cloud import repo
    from robot_times.news.digest import feed_cache

    store = SqliteStore()
    repo.set_json(store, "proxy_feeds", ["https://removed.ex/feed"])  # 删掉的来源不再代抓
    run_news(
        store,
        None,
        {"newsFeeds": ["https://blocked.ex/feed"]},
        date(2026, 10, 26),
        fetch=lambda s, cache: fetch_all(
            SRC, now=NOW, transport=httpx.MockTransport(handler), cache=cache
        ),
    )
    assert repo.get_json(store, "proxy_feeds") == ["https://blocked.ex/feed"]  # 交给看板服务代抓
    repo.set_json(store, "feed_cache:https://blocked.ex/feed",
                  {"at": datetime.now(UTC).isoformat(), "status": 200, "body": ATOM.replace("deep.ex/essay", "deep.ex/cached")})  # fmt: skip
    assert feed_cache(store, "https://blocked.ex/feed")
    items, health = fetch_all(
        SRC, now=NOW, transport=httpx.MockTransport(handler), cache=lambda u: feed_cache(store, u)
    )
    h = next(x for x in health if x["id"] == "blocked")
    assert h["ok"] and h["cached"] and any(it["url"] == "https://deep.ex/cached" for it in items)


def test_canonical_and_dedupe():
    assert canonical("https://Ex.com/a/?utm_source=x&id=3") == "https://ex.com/a?id=3"
    items = [
        {"id": "1", "title": "Figure raises $1B at $39B valuation"},
        {"id": "2", "title": "Figure raises $1B at a $39B valuation"},
        {"id": "1", "title": "dup id"},
        {"id": "3", "title": "Unitree files for IPO"},
    ]
    assert [it["id"] for it in dedupe(items)] == ["1", "3"]


def test_custom_sources():
    out = custom_sources(["https://foo.substack.com/feed", "not a url", " "])
    assert len(out) == 1 and out[0]["name"] == "foo.substack.com" and out[0]["type"] == "deep"


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        assert kw.get("json_mode")
        rows = []
        for i, title in re.findall(r"^\[(\d+)\] 来源：.*?\n标题：(.*)$", prompt, re.M):
            keep = "Old" not in title
            rows.append({"i": int(i), "keep": keep, "category": "机器人产业", "score": 8 if "Figure" in title else 6,
                         "title_zh": f"中文：{title[:10]}", "summary": "摘要", "why": "理由"})  # fmt: skip
        return json.dumps({"items": rows}, ensure_ascii=False)


def test_run_news_end_to_end_and_no_repeats_next_day():
    store = SqliteStore()
    llm = FakeLLM()
    doc = run_news(
        store, llm, {"interests": "人形"}, date(2026, 10, 26), fetch=lambda s, cache=None: fetched()
    )
    assert doc["news"][0]["titleZh"].startswith("中文：Figure")  # 分数最高的排第一
    assert {it["category"] for it in doc["news"]} == {"机器人产业"}
    assert len(doc["deep"]) == 2
    assert all("excerpt" not in it for it in doc["news"] + doc["deep"])  # 不存原文摘录
    assert any("篇深度文章" in p for p in llm.prompts) and any("条新闻" in p for p in llm.prompts)
    saved = json.loads(store.rows("select data from news where date = '2026-10-26'")[0]["data"])
    assert saved == doc
    again = run_news(store, llm, {}, date(2026, 10, 26), fetch=lambda s, cache=None: fetched())
    assert len(again["news"]) == len(doc["news"])  # 当天重跑结果不变
    nxt = run_news(store, llm, {}, date(2026, 10, 27), fetch=lambda s, cache=None: fetched())
    assert nxt["news"] == [] and nxt["deep"] == []  # 第二天不重复推


def test_run_news_without_llm_keeps_robotics_items():
    doc = run_news(
        SqliteStore(), None, {}, date(2026, 10, 26), fetch=lambda s, cache=None: fetched()
    )
    assert any("humanoid" in it["title"].lower() for it in doc["news"])
    assert doc["stats"]["llm"] is False


def test_preset_picks_news_sources_and_categories():
    from robot_times.news.digest import judge
    from robot_times.news.sources import sources_for
    from robot_times.presets import get_preset

    robo, general = get_preset(), get_preset({"preset": "general"})
    assert {s["id"] for s in sources_for(robo["news_groups"])} >= {"robotreport", "openai"}
    assert all(s["group"] == "ai" for s in sources_for(general["news_groups"]))
    items = [{"id": "1", "title": "OpenAI ships a new model", "excerpt": "", "source": "x"}]
    out = judge(None, items, "news", "", [], general)
    assert out[0]["category"] in general["news_categories"] and out[0]["score"] == 6.0
