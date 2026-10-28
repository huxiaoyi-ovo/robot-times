"""云端单人版：Zotero Web API、D1、热度/影响力、阅读计划、每日和每周任务。全部离线。"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from robot_times.cloud import repo
from robot_times.cloud.daily import FETCH_DAYS, DailyDeps, run_daily
from robot_times.cloud.store import SCHEMA, D1Error, D1Store, SqliteStore
from robot_times.cloud.weekly import iso_week, run_weekly
from robot_times.models import Paper
from robot_times.planning import assign_tiers, day_plan, week_plan
from robot_times.ranking.embedder import HashingEmbedder
from robot_times.signals import buzz_score, hf_upvotes, venue_impact
from robot_times.zotero.local_api import ZoteroError
from robot_times.zotero.web_api import ZoteroWebClient

ROOT = Path(__file__).resolve().parents[2]


def test_schema_matches_worker_migration():
    migrations = sorted((ROOT / "web" / "app" / "migrations").glob("*.sql"))
    assert "\n".join(m.read_text(encoding="utf-8") for m in migrations) == SCHEMA


# ---------------- Zotero Web API ----------------


class FakeZoteroWeb:
    """行为对照 https://www.zotero.org/support/dev/web_api/v3/basics 。"""

    def __init__(self, n_items: int = 0, busy_once: bool = False, key: str = "good"):
        self.items = [
            {"key": f"I{i}", "data": {"itemType": "journalArticle", "title": f"Paper {i}"}}
            for i in range(n_items)
        ]
        self.busy_once = busy_once
        self.key = key
        self.can_write = True
        self.requests: list[httpx.Request] = []
        self.created: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Zotero-API-Version"] == "3"
        if request.headers.get("Zotero-API-Key") != self.key:
            return httpx.Response(403, text="Forbidden")
        if self.busy_once:
            self.busy_once = False
            return httpx.Response(429, headers={"Retry-After": "2"})
        path = request.url.path
        if path == "/keys/current":
            return httpx.Response(
                200,
                json={
                    "userID": 42,
                    "access": {"user": {"library": True, "notes": True, "write": self.can_write}},
                },
            )
        if path == "/users/42/items/top" and request.method == "GET":
            start = int(request.url.params.get("start", 0))
            limit = int(request.url.params.get("limit", 25))
            assert limit <= 100
            page = self.items[start : start + limit]
            return httpx.Response(200, json=page, headers={"Total-Results": str(len(self.items))})
        if path == "/users/42/items" and request.method == "POST":
            assert request.headers.get("Zotero-Write-Token")
            body = json.loads(request.content)
            assert len(body) <= 50
            base = len(self.created)
            self.created.extend(body)
            success = {str(i): f"N{base + i}" for i in range(len(body))}
            return httpx.Response(
                200, json={"successful": {}, "success": success, "unchanged": {}, "failed": {}}
            )
        return httpx.Response(404)


def web_client(fake: FakeZoteroWeb, key: str = "good", sleeps: list | None = None):
    return ZoteroWebClient(
        "42",
        key,
        transport=httpx.MockTransport(fake),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


def test_web_top_items_paginates():
    fake = FakeZoteroWeb(n_items=230)
    items = web_client(fake).top_items()
    assert len(items) == 230
    assert [r.url.params["start"] for r in fake.requests] == ["0", "100", "200"]


def test_web_retries_after_429():
    sleeps: list = []
    fake = FakeZoteroWeb(n_items=3, busy_once=True)
    assert len(web_client(fake, sleeps=sleeps).top_items()) == 3
    assert sleeps == [2.0]


def test_web_bad_key_is_explained():
    with pytest.raises(ZoteroError, match="403"):
        web_client(FakeZoteroWeb(), key="bad").top_items()


def test_web_add_papers_with_notes():
    fake = FakeZoteroWeb()
    p = Paper(
        id="2610.00001", title="T", abstract="A", authors=["Ann Lee"], published=date(2026, 10, 24)
    )
    keys = web_client(fake).add_papers([p], ["<p>笔记</p>"], tags=["Robot Times"])
    assert keys == ["N0"]
    assert fake.created[0]["itemType"] == "preprint"
    assert {"tag": "Robot Times"} in fake.created[0]["tags"]
    assert fake.created[1]["itemType"] == "note" and fake.created[1]["parentItem"] == "N0"


def test_web_requires_credentials():
    with pytest.raises(ZoteroError):
        ZoteroWebClient("", "")


# ---------------- D1 ----------------


def test_d1_store_sends_sql_and_reads_rows():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"success": True, "errors": [], "result": [{"results": [{"x": 1}]}]}
        )

    s = D1Store("acc", "db", "tok", transport=httpx.MockTransport(handler))
    assert s.rows("select ? as x", [1]) == [{"x": 1}]
    req = seen[0]
    assert req.url.path == "/client/v4/accounts/acc/d1/database/db/query"
    assert req.headers["Authorization"] == "Bearer tok"
    assert json.loads(req.content) == {"sql": "select ? as x", "params": [1]}


def test_d1_store_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"success": False, "errors": [{"message": "no such table"}]}
        )

    s = D1Store("acc", "db", "tok", transport=httpx.MockTransport(handler))
    with pytest.raises(D1Error, match="no such table"):
        s.run("select * from nope")
    with pytest.raises(D1Error):
        D1Store("", "db", "tok")


def test_chunked_lookup_beyond_param_limit():
    store = SqliteStore()
    for i in range(205):
        repo.upsert_paper(store, {"id": f"p{i}", "addedOn": "2026-10-20", "scores": {"total": i}})
    assert len(repo.papers_by_ids(store, [f"p{i}" for i in range(205)])) == 205


# ---------------- 热度、影响力、计划 ----------------


def test_hf_upvotes_and_buzz():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["date"] in ("2026-10-24", "2026-10-23")
        return httpx.Response(
            200,
            json=[{"paper": {"id": "2610.00001", "upvotes": 30}}, {"paper": {"id": "2610.00002"}}],
        )

    ups = hf_upvotes(
        [date(2026, 10, 24), date(2026, 10, 23)], transport=httpx.MockTransport(handler)
    )
    assert ups == {"2610.00001": 30, "2610.00002": 0}
    assert buzz_score(0) == 5.0


def test_hf_upvotes_skips_bad_days_but_fails_if_all_bad():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-10-26":
            return httpx.Response(400, json={"error": "no papers for this date"})
        return httpx.Response(200, json=[{"paper": {"id": "2610.00001", "upvotes": 5}}])

    t = httpx.MockTransport(handler)
    assert hf_upvotes([date(2026, 10, 26), date(2026, 10, 25)], transport=t) == {"2610.00001": 5}
    with pytest.raises(RuntimeError, match="2026-10-26"):
        hf_upvotes([date(2026, 10, 26)], transport=t)


def test_daily_records_problems_when_hf_down():
    def down(days):
        raise RuntimeError("HTTP 503")

    day = run_daily(deps(SqliteStore(), hf=down)[0], SETTINGS, MONDAY)
    assert day["stats"]["problems"] == ["Hugging Face 热度没拿到：HTTP 503"]
    assert 7 < buzz_score(10) < 7.5
    assert buzz_score(10_000) == 10.0


def _p(comment=None, venue=None):
    return Paper(id="x", title="t", abstract="a", authors=[], published=date(2026, 10, 1),
                 comment=comment, venue=venue)  # fmt: skip


def test_venue_impact():
    assert venue_impact(_p()) == (5.0, None)
    assert venue_impact(_p("Accepted to ICRA 2027")) == (8.0, "ICRA")
    assert venue_impact(_p("Submitted to CoRL")) == (6.0, "CoRL")
    assert venue_impact(_p(venue="IEEE T-RO")) == (8.0, "T-RO")
    assert venue_impact(_p("8 pages, 5 figures")) == (5.0, None)
    assert venue_impact(_p("PRICRAT workshop"))[1] is None  # 不能把单词里的 ICRA 当成会议


def test_day_plan_fits_minutes():
    tiers = assign_tiers(8)
    assert tiers.count("deep") == 1 and tiers.count("abstract") == 4 and tiers.count("scan") == 3
    plan = day_plan(60, ["d"], ["a1", "a2"], ["s1"], reading=["r"])
    assert sum(i["minutes"] for i in plan) == 60
    assert [i["mode"] for i in plan] == ["scan", "abstract", "review", "deep", "note"]


def test_week_plan_shape():
    plan = week_plan(date(2026, 10, 26), 60, ["d1", "d2"], [f"a{i}" for i in range(4)])
    assert len(plan) == 7 and plan[0]["day"] == "周一 10/26"
    deep_days = [d["day"][:2] for d in plan for i in d["items"] if i["mode"] == "deep"]
    assert deep_days == ["周一", "周三", "周四", "周五"]
    assert plan[1]["items"][1]["paperIds"] == ["a0", "a1", "a2"]


# ---------------- 每日、每周任务 ----------------


class FakeLibrary:
    def __init__(self):
        self.items = [
            {"data": {"itemType": "journalArticle", "title": "Humanoid whole-body control",
                      "abstractNote": "Reinforcement learning for humanoid robot locomotion and motion imitation.",
                      "dateAdded": "2026-10-01T00:00:00Z"}},
            {"data": {"itemType": "preprint", "title": "Already have this",
                      "url": "https://arxiv.org/abs/2610.00009", "dateAdded": "2026-10-02T00:00:00Z"}},
        ]  # fmt: skip

    def top_items(self):
        return self.items


def candidates() -> list[Paper]:
    def mk(i, title, abstract, comment=None):
        return Paper(id=f"2610.0000{i}", title=title, abstract=abstract, authors=["A B"],
                     published=date(2026, 10, 23), categories=["cs.RO"],
                     url=f"https://arxiv.org/abs/2610.0000{i}", comment=comment)  # fmt: skip

    return [
        mk(1, "Humanoid locomotion with reinforcement learning", "humanoid robot locomotion reinforcement learning motion imitation", "Accepted to CoRL 2026"),
        mk(2, "Whole-body humanoid control from video", "humanoid whole-body control motion imitation from human video"),
        mk(3, "Protein folding benchmark", "protein structure prediction benchmark dataset"),
        mk(4, "Robot grasping with tactile sensing", "robot grasping tactile sensors manipulation"),
        mk(5, "Quantum error correction", "surface codes for quantum computers"),
        mk(6, "Legged robot reinforcement learning sim2real", "reinforcement learning legged robot locomotion sim-to-real"),
        mk(9, "Already have this", "dup"),
    ]  # fmt: skip


class FakeLLM:
    """按提示词类型返回固定 JSON，模拟 DeepSeek。"""

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, prompt: str, **kw) -> str:
        self.prompts.append(prompt)
        if "trends" in prompt:
            return json.dumps({"headline": "人形机器人周", "summary": "三句话。",
                               "trends": [{"title": "全身控制", "body": "两句话。",
                                           "paperIds": ["2610.00001", "9999.99999"]}]},
                              ensure_ascii=False)  # fmt: skip
        if "headline" in prompt:
            return '```json\n{"headline": "今天看人形", "intro": "精读第一篇。"}\n```'
        ids = [line[1:].split("]")[0] for line in prompt.splitlines() if line.startswith("[")]
        return json.dumps(
            [{"id": i, "score": 9 if "umanoid" in prompt.split(i)[1][:80] else 2,
              "summary_zh": f"摘要{i}", "why_zh": f"理由{i}"} for i in ids],
            ensure_ascii=False,
        )  # fmt: skip


SETTINGS = {**repo.DEFAULT_SETTINGS, "top": 5, "interests": "人形机器人全身控制"}
MONDAY = date(2026, 10, 19)


def deps(store, llm=None, hf=None, papers=None):
    calls = []

    def fetch(cats, days):
        calls.append((tuple(cats), days))
        return papers if papers is not None else candidates()

    d = DailyDeps(store=store, zotero=FakeLibrary(), embedder=HashingEmbedder(),
                  fetch_papers=fetch, hf_upvotes=hf, llm=llm)  # fmt: skip
    return d, calls


def test_daily_end_to_end():
    store = SqliteStore()
    llm = FakeLLM()
    d, calls = deps(store, llm=llm, hf=lambda days: {"2610.00002": 50})
    day = run_daily(d, SETTINGS, MONDAY)

    assert calls == [(("cs.RO",), FETCH_DAYS)]
    assert len(day["picks"]) == 5
    assert "2610.00009" not in day["picks"]  # 文献库里已有
    assert day["picks"][0] in ("2610.00001", "2610.00002")
    assert day["headline"] == "今天看人形"
    assert sum(i["minutes"] for i in day["plan"]) == 60

    papers = repo.papers_by_ids(store, day["picks"])
    first = papers["2610.00001"]
    assert first["scores"]["impact"] == 8.0 and first["venue"] == "CoRL"
    assert first["summary"] == "摘要2610.00001"
    assert papers["2610.00002"]["signals"] == "Hugging Face 50 赞"
    assert papers[day["picks"][0]]["tier"] == "deep"
    stored = json.loads(
        store.rows("select data from days where date = ?", ["2026-10-19"])[0]["data"]
    )
    assert stored == day


def test_daily_rerun_same_day_keeps_picks_next_day_skips_them():
    store = SqliteStore()
    first = run_daily(deps(store)[0], SETTINGS, MONDAY)
    again = run_daily(deps(store)[0], SETTINGS, MONDAY)
    assert again["picks"] == first["picks"]
    nxt = run_daily(deps(store)[0], SETTINGS, date(2026, 10, 20))
    assert nxt["picks"] == [] and nxt["skipped"]  # 昨天的候选（包括落选的）不会再推一遍
    assert [d["date"] for d in repo.days_between(store, "2026-10-01", "2026-10-31")] == [
        "2026-10-19"
    ]


def test_daily_survives_hf_and_llm_failures():
    class BrokenLLM:
        def complete(self, prompt, **kw):
            raise httpx.ConnectError("down")

    def broken_hf(days):
        raise httpx.ConnectError("down")

    store = SqliteStore()
    day = run_daily(deps(store, llm=BrokenLLM(), hf=broken_hf)[0], SETTINGS, MONDAY)
    assert len(day["picks"]) == 5
    assert day["headline"]  # 退回到第一篇的标题


def test_feedback_changes_ranking():
    store = SqliteStore()
    base = run_daily(deps(store)[0], {**SETTINGS, "top": 6}, MONDAY)
    grasp = "2610.00004"
    pos_before = base["picks"].index(grasp)
    store.run("insert into feedback (paper_id, action, at) values (?, 'like', 'x')", [grasp])
    for pid in ("2610.00001", "2610.00002"):
        store.run("insert into feedback (paper_id, action, at) values (?, 'skip', 'x')", [pid])
    store.run("delete from papers where added_on = ?", ["2026-10-19"])  # 只删推荐，不删反馈依赖的
    # 反馈需要论文文本：重新放回论文记录但标为更早的日子
    for pid in ("2610.00001", "2610.00002", grasp):
        rec = {"id": pid, "title": next(p.title for p in candidates() if p.id == pid),
               "abstract": next(p.abstract for p in candidates() if p.id == pid),
               "addedOn": "2026-10-01", "scores": {"total": 0}}  # fmt: skip
        repo.upsert_paper(store, rec)
    liked, skipped = repo.feedback_texts(store)
    assert len(liked) == 1 and len(skipped) == 2
    fresh = [p for p in candidates() if p.id not in ("2610.00001", "2610.00002", grasp)]
    fresh.append(Paper(id="2610.00007", title="Tactile robot grasping", abstract="robot grasping tactile manipulation",
                       authors=[], published=date(2026, 10, 23)))  # fmt: skip
    fresh.append(Paper(id="2610.00008", title="Humanoid motion imitation", abstract="humanoid whole-body control motion imitation",
                       authors=[], published=date(2026, 10, 23)))  # fmt: skip
    after = run_daily(deps(store, papers=fresh)[0], {**SETTINGS, "top": 6}, date(2026, 10, 20))
    assert after["picks"].index("2610.00007") < after["picks"].index("2610.00008")
    assert pos_before >= 0


def test_weekly_review_and_next_week_plan():
    store = SqliteStore()
    llm = FakeLLM()
    mon = run_daily(deps(store, llm=llm)[0], SETTINGS, MONDAY)
    store.run("insert into notes (paper_id, status, note, updated_at) values (?, 'done', '好', 'x')",
              [mon["picks"][0]])  # fmt: skip
    store.run("insert into notes (paper_id, status, note, updated_at) values (?, 'reading', '', 'x')",
              [mon["picks"][3]])  # fmt: skip

    week = run_weekly(store, llm, SETTINGS, date(2026, 10, 25))
    assert week["week"] == iso_week(MONDAY) == "2026-W43"
    assert week["range"] == "10月19日–10月25日"
    assert week["headline"] == "人形机器人周"
    assert week["trends"][0]["paperIds"] == ["2610.00001"]  # 编造的编号被去掉
    assert week["stats"] == {"recommended": 5, "done": 1, "days": 1}
    plan = week["nextWeek"]["plan"]
    assert plan[0]["day"] == "周一 10/26"
    first_deep = next(i for i in plan[0]["items"] if i["mode"] == "deep")
    assert first_deep["paperIds"] == [mon["picks"][3]]  # 在读的优先
    assert mon["picks"][0] not in json.dumps(plan)  # 读完的不再排
    assert json.loads(store.rows("select data from weeks")[0]["data"]) == week


def test_weekly_without_llm_or_data():
    week = run_weekly(SqliteStore(), None, SETTINGS, date(2026, 10, 25))
    assert week["stats"]["recommended"] == 0
    assert week["headline"].startswith("本周推荐 0 篇")


def test_settings_roundtrip():
    store = SqliteStore()
    assert repo.load_settings(store, {"categories": ["cs.LG"]})["categories"] == ["cs.LG"]
    repo.save_settings(
        store, {**repo.DEFAULT_SETTINGS, "categories": ["cs.RO"], "dailyMinutes": 45}
    )
    s = repo.load_settings(store, {"categories": ["cs.LG"]})
    assert s["dailyMinutes"] == 45 and s["categories"] == ["cs.RO"]  # 数据库优先
    # 没填分类时用方向预设的
    assert repo.load_settings(SqliteStore())["categories"] == ["cs.RO"]
    assert repo.load_settings(SqliteStore(), {"preset": "general"})["categories"] == ["cs.LG"]


def test_day_plan_reassigns_unused_time():
    assert sum(i["minutes"] for i in day_plan(60, ["d"], ["a"], [])) == 60
    assert sum(i["minutes"] for i in day_plan(45, [], ["a"], ["s"])) == 45


def test_deep_read_is_the_best_fit_not_the_most_popular():
    from robot_times.cloud.daily import deep_first
    from robot_times.models import ScoredPaper

    def sp(i, llm):
        p = Paper(id=str(i), title="t", abstract="a", authors=[], published=date(2026, 10, 1))
        return ScoredPaper(p, 5.0, llm_score=llm)

    order = [x.paper.id for x in deep_first([sp(0, 7), sp(1, 9), sp(2, 9), sp(3, 4)])]
    assert order == ["1", "0", "2", "3"]
    assert [x.paper.id for x in deep_first([sp(0, None), sp(1, None)])] == ["0", "1"]


def test_key_access_and_daily_warns_without_write():
    from robot_times.cloud.daily import check_zotero_key

    fake = FakeZoteroWeb()
    client = web_client(fake)
    assert client.key_access() == {"library": True, "notes": True, "write": True}
    problems: list[str] = []
    assert check_zotero_key(client, problems)["write"] and problems == []
    fake.can_write = False
    check_zotero_key(client, problems)
    assert len(problems) == 1 and "Allow write access" in problems[0]
    assert check_zotero_key(object(), []) is None  # 本地 API 客户端没有这个检查


def test_generated_after_release():
    from robot_times.cloud.daily import generated_after_release

    store = SqliteStore()
    d = date(2026, 10, 26)
    assert not generated_after_release(store, d)
    repo.save_day(
        store, {"date": "2026-10-26", "generatedAt": "2026-10-25T17:27:54+00:00"}
    )  # 北京 01:27
    assert not generated_after_release(store, d)
    repo.save_day(
        store, {"date": "2026-10-26", "generatedAt": "2026-10-26T01:45:00+00:00"}
    )  # 北京 09:45
    assert generated_after_release(store, d)
