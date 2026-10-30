"""出错时看得懂、能追溯：报错翻译成办法、任务历史、体检、日志打码、接口报错带按钮。不联网。"""

from robot_times.cloud import repo
from robot_times.cloud.store import SqliteStore
from robot_times.local import diagnose
from robot_times.local.api import LocalApi
from robot_times.local.jobs import Jobs


def test_explain_turns_errors_into_actions():
    z = diagnose.explain("RuntimeError: 连不上 Zotero：请先打开 Zotero", "daily")
    assert z["title"] == "连不上 Zotero" and z["actions"][:2] == ["retry:daily", "setup"]
    assert "logs" in z["actions"]
    assert (
        diagnose.explain("HTTPStatusError: Client error '402 Payment Required'")["title"]
        == "大模型账户余额不足"
    )
    assert diagnose.explain("HTTPStatusError: '401 Unauthorized'")["actions"][0] == "setup"
    assert diagnose.explain("ConnectTimeout: timed out", "news")["title"] == "网络连不上"
    other = diagnose.explain("KeyError: 'x'", "weekly")
    assert other["title"] == "出了点问题" and other["actions"] == ["retry:weekly", "logs"]
    assert diagnose.explain("共 4010 篇")["title"] == "出了点问题"  # 数字里带 401 不算


def test_failed_job_explains_itself_and_is_remembered_after_restart():
    store = SqliteStore()

    def boom(force=False):
        raise RuntimeError("连不上 Zotero：请先打开 Zotero")

    jobs = Jobs(store, {"daily": boom, "news": lambda force=False: "快讯 3 条"}, background=False)
    jobs.start("daily")
    last = jobs.status()["last"]
    assert not last["ok"] and last["title"] == "连不上 Zotero" and "retry:daily" in last["actions"]
    jobs.start("news")
    again = Jobs(store, {}, background=False)  # 看板重启后
    assert again.status()["last"]["kind"] == "news"
    assert [h["kind"] for h in diagnose.history(store)] == ["news", "daily"]


def test_logs_are_tailed_and_secrets_hidden(tmp_path):
    (tmp_path / "serve.log").write_text(
        "\n".join(f"line {i}" for i in range(500)) + "\nDEEPSEEK_API_KEY=sk-abcdef1234567890\n"
    )
    r = diagnose.tail("serve", 10, tmp_path)
    lines = r["text"].splitlines()
    assert len(lines) == 10 and "1234567890" not in r["text"]
    assert "1234567890" not in diagnose.redact("token: sk-abcdef1234567890")
    assert diagnose.tail("setup", 10, tmp_path)["exists"] is False


class Setup:
    def __init__(self, running=True):
        self.running = running

    def zotero(self):
        return {"running": self.running, "version": "10.0.3", "versionOk": True, "localApi": True}

    def done(self):
        return True


def test_doctor_lists_problems_with_fixes(tmp_path):
    store = SqliteStore()
    api = LocalApi(store)
    api.setup = Setup(running=False)
    d = diagnose.Doctor(api, home=tmp_path, net=lambda url: "arxiv" in url).run()
    by = {c["id"]: c for c in d["checks"]}
    assert by["zotero"]["ok"] is False and by["zotero"]["actions"]
    assert by["zotero_key"]["actions"] == ["setup"]
    assert by["llm"]["ok"] is None  # 可选项
    assert by["arxiv"]["ok"] and by["github"]["ok"] is False
    assert d["ok"] is False
    repo.save_settings(store, {**repo.load_settings(store), "interests": "x"})
    text = diagnose.Doctor(api, home=tmp_path, net=lambda url: True).report()
    assert "✗ Zotero 打开了" in text and "体检" in text


def test_api_errors_carry_hints_and_restart_waits_for_jobs(tmp_path):
    api = LocalApi(SqliteStore())
    r = api.handle("POST", "/api/zotero/x", {}, b"")
    assert r.status == 400 and r.body["actions"][0] == "setup" and r.body["hint"]
    r = api.handle("POST", "/api/restart", {}, b"")
    assert r.status == 400  # 没有重启的办法

    class Busy:
        def status(self):
            return {"running": "daily", "queue": [], "last": None}

    api.jobs, api.restart = Busy(), lambda: None
    assert "等生成完" in api.handle("POST", "/api/restart", {}, b"").body["error"]
    assert api.handle("GET", "/api/logs", {"name": "nope"}, b"").status == 400


def test_export_backup_from_dashboard():
    api = LocalApi(SqliteStore())
    r = api.handle("GET", "/api/export", {}, b"")
    assert r.status == 200 and r.body["filename"].endswith(".sql") and "导出" in r.body["sql"]
