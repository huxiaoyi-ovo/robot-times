"""本地版：看板接口、Zotero 本地接口、后台任务、HTTP 服务。外部服务全用假的。"""

import json
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest

from robot_times.cloud import repo
from robot_times.cloud.daily import TZ
from robot_times.cloud.store import SqliteStore
from robot_times.local.api import LocalApi, stream_chat
from robot_times.local.jobs import Jobs, due, weekly_target
from robot_times.local.server import serve
from robot_times.zotero.local_api import ZoteroLocalClient

ROOT = Path(__file__).resolve().parents[2]
PDF = b"%PDF-1.7 fake"


class FakeZotero:
    """模拟 Zotero 桌面端的本地接口（localhost:23119/api）。"""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.calls: list[str] = []
        self.uploaded: dict[str, bytes] = {}
        self.n = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        self.calls.append(f"{m} {path}")
        head = {"Zotero-Server-ID": "S1"}
        if m != "GET" and not path.startswith("/upload"):
            assert req.headers["Zotero-API-Key"] == "K" and req.headers["Zotero-Server-ID"] == "S1"
        if path == "/api/":
            return httpx.Response(200, headers=head, text="Nothing to see here.")
        if path == "/api/users/0/collections":
            return httpx.Response(
                200, headers=head, json=[{"key": "INBOX", "data": {"name": "Inbox"}}]
            )
        if path == "/api/users/0/items/top":
            return httpx.Response(200, headers=head, json=[])
        if path == "/api/users/0/items" and m == "POST":
            out = {}
            for i, it in enumerate(json.loads(req.content)):
                self.n += 1
                key = f"K{self.n}"
                self.items[key] = {"key": key, "version": 1, "data": {**it, "key": key}}
                out[str(i)] = key
            return httpx.Response(200, headers=head, json={"success": out, "failed": {}})
        g = re.fullmatch(r"/api/users/0/items/(\w+)(/children|/file)?", path)
        if g:
            key, sub = g.group(1), g.group(2)
            it = self.items.get(key)
            if it is None:
                return httpx.Response(404)
            if sub == "/children" and m == "GET":
                kids = [x for x in self.items.values() if x["data"].get("parentItem") == key]
                return httpx.Response(200, headers=head, json=kids)
            if sub == "/file" and m == "POST":
                form = dict(parse_qsl(req.content.decode()))
                if "md5" in form:
                    assert req.headers["If-None-Match"] == "*"
                    return httpx.Response(200, json={"url": "http://localhost:23119/upload/x",
                        "contentType": "application/pdf", "prefix": "", "suffix": "", "uploadKey": "U1"})  # fmt: skip
                assert form == {"upload": "U1"}
                return httpx.Response(204)
            if m == "GET":
                return httpx.Response(200, headers=head, json=it)
            if m == "PATCH":
                assert req.headers["If-Unmodified-Since-Version"] == str(it["version"])
                it["data"].update(json.loads(req.content))
                it["version"] += 1
                return httpx.Response(204)
        if path == "/upload/x":
            self.uploaded["last"] = req.content
            return httpx.Response(201)
        return httpx.Response(500, text=f"unexpected {m} {path}")


def paper(store, pid="2610.00001", **extra):
    data = {"id": pid, "title": "Legged robots", "authors": ["Ada Lovelace"], "abstract": "abs",
            "scores": {"total": 7.5}, "addedOn": "2026-10-26", "pdf": f"https://arxiv.org/pdf/{pid}v1",
            "summary": "要点", **extra}  # fmt: skip
    repo.upsert_paper(store, data)
    return data


def make_api(store=None, *, folder="Inbox", pdf=True):
    store = store or SqliteStore()
    s = repo.load_settings(store)
    repo.save_settings(store, {**s, "zoteroCollection": folder})
    fake = FakeZotero()
    z = ZoteroLocalClient(api_key="K", transport=httpx.MockTransport(fake))
    z.key_is_persistent = True
    arxiv = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})))  # fmt: skip
    return LocalApi(store, zotero=z, pdf=pdf, http=arxiv), fake


def call(api, method, path, body=None, query=None):
    raw = json.dumps(body).encode() if body is not None else b""
    r = api.handle(method, path, query or {}, raw)
    return r.status, r.body


def test_add_to_zotero_with_folder_pdf_and_note_then_check_and_undo():
    api, z = make_api()
    paper(api.store)
    call(api, "PUT", "/api/notes/2610.00001", {"status": "reading", "note": "我的看法\n\n第二段"})
    status, r = call(api, "POST", "/api/zotero/2610.00001")
    assert status == 200 and r["folder"] == "Inbox" and r["pdfError"] is None and r["pdf"] is True
    item = z.items[r["key"]]["data"]
    assert item["itemType"] == "preprint" and item["collections"] == ["INBOX"]
    assert item["archiveID"] == "arXiv:2610.00001" and item["tags"] == [{"tag": "Robot Times"}]
    kids = [x["data"] for x in z.items.values() if x["data"].get("parentItem") == r["key"]]
    assert {k["itemType"] for k in kids} == {"attachment", "note"}
    note = next(k for k in kids if k["itemType"] == "note")["note"]
    assert "要点" in note and "<h2>我的笔记</h2><p>我的看法</p><p>第二段</p>" in note
    assert z.uploaded["last"] == PDF

    _, boot = call(api, "GET", "/api/bootstrap")
    assert boot["zotero"] == {"2610.00001": r["key"]} and boot["mode"] == "local"

    _, chk = call(api, "GET", "/api/zotero/2610.00001")
    assert chk["pdf"] is True and chk["inFolder"] is True
    assert chk["web"] == f"zotero://select/library/items/{r['key']}"

    _, gone = call(api, "DELETE", "/api/zotero/2610.00001")
    assert gone["key"] is None and z.items[r["key"]]["data"]["deleted"] == 1
    _, chk = call(api, "GET", "/api/zotero/2610.00001")
    assert chk == {"paperId": "2610.00001", "key": None}


def test_add_again_fills_missing_folder_and_pdf():
    api, z = make_api(pdf=False)
    paper(api.store)
    _, r = call(api, "POST", "/api/zotero/2610.00001")
    assert r["pdf"] is False and not z.uploaded
    z.items[r["key"]]["data"]["collections"] = []
    api.pdf = True
    _, again = call(api, "POST", "/api/zotero/2610.00001")
    assert again["existing"] is True and again["pdfError"] is None
    assert z.items[r["key"]]["data"]["collections"] == ["INBOX"] and z.uploaded["last"] == PDF


def test_zotero_errors_are_readable():
    api, _ = make_api(folder="不存在")
    paper(api.store)
    status, r = call(api, "POST", "/api/zotero/2610.00001")
    assert status == 400 and "找不到名为“不存在”的文件夹" in r["error"]
    api.zotero = None
    status, r = call(api, "POST", "/api/zotero/2610.00001")
    assert status == 400 and "Always Allow" in r["error"]


def test_bootstrap_notes_feedback_search_and_settings():
    api, _ = make_api()
    paper(api.store, analysis={"review": "长解读", "cover": {"src": "x"}}, abstractZh="译文")
    paper(api.store, "2610.00002", title="Humanoid whole-body control")
    repo.save_day(api.store, {"date": "2026-10-26", "picks": ["2610.00001"], "plan": []})
    call(api, "POST", "/api/feedback/2610.00002", {"action": "skip"})
    _, boot = call(api, "GET", "/api/bootstrap")
    p = boot["papers"]["2610.00001"]
    assert "analysis" not in p and p["hasAnalysis"] is True and p["cover"] == {"src": "x"}
    assert boot["feedback"] == {"2610.00002": "skip"} and boot["site"]["name"] == "The Robot Times"
    assert boot["features"] == {"zotero": True, "ask": False, "run": False}
    _, full = call(api, "GET", "/api/paper/2610.00001")
    assert full["analysis"]["review"] == "长解读"

    _, found = call(api, "GET", "/api/papers", query={"q": "whole-body"})
    assert found["total"] == 1 and found["papers"][0]["id"] == "2610.00002"
    _, found = call(api, "GET", "/api/papers", query={"q": "100%_"})
    assert found["total"] == 0

    status, s = call(api, "PUT", "/api/config", {"preset": "general", "siteName": "The Vision Times",
                                                  "categories": ["cs.CV"], "diversity": 0.3})  # fmt: skip
    assert status == 200 and s["categories"] == ["cs.CV"] and s["diversity"] == 0.3
    _, boot = call(api, "GET", "/api/bootstrap")
    assert boot["site"]["name"] == "The Vision Times"
    status, err = call(api, "PUT", "/api/config", {"categories": ["机器人"]})
    assert status == 400 and "arXiv 分类" in err["error"]
    status, err = call(api, "PUT", "/api/config", {"preset": "nope"})
    assert status == 400
    assert call(api, "GET", "/api/nothing")[0] == 404


def test_default_settings_match_worker():
    js = (ROOT / "web" / "app" / "src" / "index.js").read_text(encoding="utf-8")
    block = re.search(r"const DEFAULT_SETTINGS = \{(.*?)\n\};", js, re.S).group(1)
    keys = set(re.findall(r"^\s+(\w+):", block, re.M))
    assert keys == set(repo.DEFAULT_SETTINGS)


def test_ask_streams_from_llm():
    def llm(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["stream"] is True and "机器人学习" in body["messages"][0]["content"]
        assert "这篇的局限？" in body["messages"][1]["content"]
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')

    api, _ = make_api()
    paper(api.store)
    api.llm_stream = stream_chat(
        "https://llm.test", "k", "m", httpx.Client(transport=httpx.MockTransport(llm))
    )
    r = api.handle(
        "POST",
        "/api/ask",
        {},
        json.dumps({"paperId": "2610.00001", "question": "这篇的局限？"}).encode(),
    )
    assert b"".join(r.stream) == b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'

    api.llm_stream = stream_chat("https://llm.test", "k", "m",
                                 httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(402))))  # fmt: skip
    status, err = call(api, "POST", "/api/ask", {"paperId": "2610.00001"})
    assert status == 502 and "余额不足" in err["error"]


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def test_due_jobs():
    store = SqliteStore()
    assert due(store, at("2026-10-26 07:00")) == ["daily", "news"]  # 第一次用：马上生成
    repo.save_day(
        store, {"date": "2026-10-23", "picks": [], "generatedAt": "2026-10-23T02:00:00+00:00"}
    )
    assert due(store, at("2026-10-26 07:00")) == [
        "news",
        "weekly",
    ]  # arXiv 还没发布；上周的周报没写
    repo.set_json(store, "local_runs", {"news": "2026-10-26T06:30:00+08:00"})
    repo.save_week(store, {"week": "2026-W43"})
    assert due(store, at("2026-10-26 08:00")) == []
    assert due(store, at("2026-10-26 10:00")) == ["daily", "news"]
    repo.save_day(
        store, {"date": "2026-10-26", "picks": [], "generatedAt": "2026-10-26T02:30:00+00:00"}
    )
    store.run("insert into news (date, data) values ('2026-10-26', '{}')")
    assert due(store, at("2026-10-26 12:00")) == []
    assert weekly_target(at("2026-11-01 19:00")).isoformat() == "2026-11-01"
    assert weekly_target(at("2026-11-01 10:00")).isoformat() == "2026-10-25"
    assert weekly_target(at("2026-10-28 10:00")).isoformat() == "2026-10-25"


def test_jobs_run_in_order_and_record_failures():
    store = SqliteStore()
    seen = []

    def ok(force=False):
        seen.append(("daily", force))
        return "12 篇推荐"

    def boom(force=False):
        raise RuntimeError("连不上 Zotero")

    jobs = Jobs(store, {"daily": ok, "news": boom, "weekly": ok}, background=False,
                clock=lambda: at("2026-10-26 10:00"))  # fmt: skip
    st = jobs.auto()
    assert seen == [("daily", False)] and st["running"] is None
    assert {k: st["last"][k] for k in ("kind", "ok", "message", "at")} == {
        "kind": "news", "ok": False, "message": "RuntimeError: 连不上 Zotero",
        "at": "2026-10-26T10:00:00+08:00"}  # fmt: skip
    assert st["last"]["title"] == "连不上 Zotero" and "retry:news" in st["last"]["actions"]
    assert set(repo.get_json(store, "local_runs")) == {"daily", "news"}
    assert jobs.auto()["queue"] == []  # 刚试过，3 小时内不再自动重试
    jobs.start("daily")
    assert seen[-1] == ("daily", True)
    with pytest.raises(ValueError):
        jobs.start("nope")


@pytest.fixture
def server(tmp_path):
    (tmp_path / "index.html").write_text("<title>The Robot Times</title>", encoding="utf-8")
    api, _ = make_api()
    paper(api.store)
    httpd = serve(api, 0, tmp_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def fetch(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_server_serves_page_and_api_and_blocks_other_sites(server):
    assert fetch(server + "/")[1] == "<title>The Robot Times</title>"
    assert fetch(server + "/today")[0] == 200  # 其他路径回到首页
    assert fetch(server + "/../../etc/passwd")[1].startswith("<title>")
    status, text = fetch(server + "/api/paper/2610.00001")
    assert status == 200 and json.loads(text)["title"] == "Legged robots"
    note = json.dumps({"status": "done"}).encode()
    assert (
        fetch(server + "/api/notes/2610.00001", "PUT", note, {"Content-Type": "text/plain"})[0]
        == 403
    )
    assert fetch(server + "/api/notes/2610.00001", "PUT", note,
                 {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"})[0] == 403  # fmt: skip
    assert (
        fetch(server + "/api/notes/2610.00001", "PUT", note, {"Content-Type": "application/json"})[
            0
        ]
        == 200
    )
    assert fetch(server + "/api/bootstrap", headers={"Host": "evil.example"})[0] == 403


def test_is_running_detects_our_dashboard(server):
    from robot_times.local.server import is_running

    port = int(server.rsplit(":", 1)[1])
    assert is_running(port)
    assert not is_running(1)  # 没有服务的端口


def test_export_sql_round_trips_into_a_fresh_database():
    from robot_times.local.export import export_sql

    api, _ = make_api()
    paper(api.store, title="It's a robot")
    call(api, "PUT", "/api/notes/2610.00001", {"status": "done", "note": "读完了"})
    call(api, "POST", "/api/feedback/2610.00001", {"action": "like"})
    repo.save_day(api.store, {"date": "2026-10-26", "picks": ["2610.00001"]})
    repo.set_json(api.store, "local_runs", {"daily": "x"})
    sql = export_sql(api.store)
    assert "local_runs" not in sql and "It''s a robot" in sql
    fresh = SqliteStore()
    fresh.db.executescript(sql)
    fresh.db.executescript(sql)  # 重复导入不会出错，笔记和推荐不重复
    assert fresh.rows("select note from notes") == [{"note": "读完了"}]
    assert len(fresh.rows("select * from days")) == 1
    assert repo.load_settings(fresh)["zoteroCollection"] == "Inbox"


def test_stop_shuts_down_the_dashboard(server):
    from robot_times.local.server import is_running, stop

    port = int(server.rsplit(":", 1)[1])
    assert is_running(port) and stop(port) and not is_running(port)
