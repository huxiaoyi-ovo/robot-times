"""第一次打开时的设置引导：检测 Zotero、打开本地接口、授权、选文件夹、配大模型、完成。不联网。"""

import json
import os

import httpx
import pytest

from robot_times.cloud import repo
from robot_times.cloud.store import SqliteStore
from robot_times.local.api import LocalApi
from robot_times.local.setup import PREF, Setup, enable_pref, find_prefs, write_env


def zotero(running=True, version="10.0.2", local_api=True, authorize=None):
    """模拟 Zotero：/connector/ping 判断有没有开，/api/ 判断本地接口开没开。"""

    def handler(req: httpx.Request) -> httpx.Response:
        if not running:
            raise httpx.ConnectError("refused")
        head = {"X-Zotero-Version": version, "Zotero-Server-ID": "S1"}
        p = req.url.path
        if p == "/connector/ping":
            return httpx.Response(200, headers=head, text="Zotero is running")
        if not local_api:
            return httpx.Response(403, headers=head, text="Local API is not enabled")
        if p == "/api/":
            return httpx.Response(200, headers=head, text="Nothing to see here.")
        if p == "/api/local/authorize":
            return (
                authorize(req)
                if authorize
                else httpx.Response(200, json={"key": "K", "remember": True})
            )
        if p == "/api/users/0/collections":
            return httpx.Response(200, headers=head, json=[{"key": "A", "data": {"name": "Inbox"}},
                                                           {"key": "B", "data": {"name": "读过的"}}])  # fmt: skip
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in (
        "DEEPSEEK_API_KEY",
        "ROBOT_TIMES_LLM_API_KEY",
        "ROBOT_TIMES_LLM_BASE_URL",
        "ROBOT_TIMES_LLM_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def make(env, transport, **kw):
    store = SqliteStore()
    api = LocalApi(store)
    st = Setup(store, api, http=httpx.Client(transport=transport), env_file=env / ".env",
               key_file=env / "zotero_key", roots=[env / "zotero"], **kw)  # fmt: skip
    api.setup = st
    return api, st


def call(api, method, path, body=None):
    r = api.handle(method, path, {}, json.dumps(body).encode() if body is not None else b"")
    return r.status, r.body


def test_detects_each_zotero_problem(env):
    _, st = make(env, zotero(running=False))
    assert st.zotero() == {"running": False, "version": None, "versionOk": None, "localApi": False}
    _, st = make(env, zotero(version="7.0.11"))
    z = st.zotero()
    assert z["running"] and z["version"] == "7.0.11" and z["versionOk"] is False
    _, st = make(env, zotero(local_api=False))
    z = st.zotero()
    assert z["running"] and z["versionOk"] and not z["localApi"]
    _, st = make(env, zotero())
    assert st.zotero()["localApi"] is True


def test_enable_local_api_edits_prefs_only_when_zotero_is_closed(env):
    root = env / "zotero"
    (root / "abc.default").mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[General]\nStartWithLastProfile=1\n\n[Profile0]\nName=default\nIsRelative=1\nPath=abc.default\nDefault=1\n"
    )
    prefs = root / "abc.default" / "prefs.js"
    prefs.write_text(
        f'user_pref("extensions.zotero.sync.server.username", "me");\nuser_pref("{PREF}", false);\n'
    )
    assert find_prefs([root]) == prefs

    api, _ = make(env, zotero(running=True))
    status, err = call(api, "POST", "/api/setup/enable-api")
    assert status == 400 and "退出 Zotero" in err["error"]

    api, _ = make(env, zotero(running=False))
    status, _ = call(api, "POST", "/api/setup/enable-api")
    text = prefs.read_text()
    assert status == 200 and f'user_pref("{PREF}", true);' in text and "false" not in text
    assert 'sync.server.username", "me"' in text


def test_enable_pref_adds_the_line_when_missing(tmp_path):
    p = tmp_path / "prefs.js"
    p.write_text('user_pref("a", 1);')
    enable_pref(p)
    assert p.read_text() == f'user_pref("a", 1);\nuser_pref("{PREF}", true);\n'


def test_authorize_saves_key_and_rejects_one_time_allow(env):
    api, st = make(env, zotero())
    st.base = "http://zotero.test"

    import robot_times.local.setup as mod

    real = mod.ZoteroLocalClient
    mod.ZoteroLocalClient = lambda *a, **k: real(*a, **{**k, "transport": zotero()})
    try:
        status, r = call(api, "POST", "/api/setup/authorize")
        assert status == 200 and (env / "zotero_key").read_text() == "K"
        assert api.zotero.api_key == "K"
        once = zotero(
            authorize=lambda req: httpx.Response(200, json={"key": "X", "remember": False})
        )
        mod.ZoteroLocalClient = lambda *a, **k: real(*a, **{**k, "transport": once})
        status, r = call(api, "POST", "/api/setup/authorize")
        assert status == 400 and "Always Allow" in r["error"]
    finally:
        mod.ZoteroLocalClient = real


def test_collections_for_the_folder_dropdown(env):
    from robot_times.zotero.local_api import ZoteroLocalClient

    api, _ = make(env, zotero())
    api.zotero = ZoteroLocalClient(transport=zotero())
    assert call(api, "GET", "/api/setup/collections") == (200, ["Inbox", "读过的"])


def llm(status=200):
    def handler(req):
        body = json.loads(req.content)
        assert (
            req.headers["Authorization"] == "Bearer sk-test"
            and body["messages"][-1]["role"] == "user"
        )
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"choices": [{"message": {"content": "可以"}}]})

    return httpx.MockTransport(handler)


def test_llm_key_is_tested_then_saved_and_takes_effect(env):
    (env / ".env").write_text("OTHER=1\nDEEPSEEK_API_KEY=old\n")
    api, _ = make(env, zotero(), llm_transport=llm(401))
    status, r = call(api, "PUT", "/api/setup/llm", {"key": "sk-test"})
    assert status == 400 and "密钥不对" in r["error"]
    assert "old" in (env / ".env").read_text()  # 没通过测试就不保存

    api, _ = make(env, zotero(), llm_transport=llm())
    status, _ = call(api, "PUT", "/api/setup/llm", {"key": "sk-test"})
    assert status == 200
    assert (env / ".env").read_text() == "OTHER=1\nDEEPSEEK_API_KEY=sk-test\n"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test"
    assert api.llm is not None and api.llm_stream is not None  # 不用重启
    _, s = call(api, "GET", "/api/setup")
    assert s["llm"]["configured"] is True


def test_write_env_switches_between_deepseek_and_custom(tmp_path):
    p = tmp_path / ".env"
    write_env(p, {"ROBOT_TIMES_LLM_API_KEY": "k", "ROBOT_TIMES_LLM_BASE_URL": "http://localhost:11434/v1",
                  "ROBOT_TIMES_LLM_MODEL": "qwen"})  # fmt: skip
    write_env(p, {"DEEPSEEK_API_KEY": "d"})
    assert p.read_text() == "DEEPSEEK_API_KEY=d\n"


def test_finish_saves_settings_and_starts_generating(env):
    class Jobs:
        started = 0

        def auto(self):
            Jobs.started += 1

        def status(self):
            return {"running": None, "queue": [], "last": None}

    api, st = make(env, zotero())
    api.jobs = Jobs()
    _, boot = call(api, "GET", "/api/bootstrap")
    assert boot["setup"] == {"done": False} and Jobs.started == 0  # 没设置好之前不自动生成
    status, r = call(api, "POST", "/api/setup/finish", {"interests": ""})
    assert status == 400
    status, _ = call(api, "POST", "/api/setup/finish",
                     {"interests": "足式机器人", "zoteroCollection": "Inbox", "categories": ["cs.RO"]})  # fmt: skip
    assert status == 200 and Jobs.started == 1 and st.done()
    s = repo.load_settings(api.store)
    assert s["interests"] == "足式机器人" and s["zoteroCollection"] == "Inbox"
    _, boot = call(api, "GET", "/api/bootstrap")
    assert boot["setup"] == {"done": True} and Jobs.started == 2


def test_old_command_line_installs_count_as_set_up(env):
    _, st = make(env, zotero())
    (env / "zotero_key").write_text("K")
    repo.save_settings(st.store, {**repo.load_settings(st.store), "interests": "x"})
    assert st.done()


def test_finish_picks_preset_from_categories(env):
    api, _ = make(env, zotero())
    call(
        api,
        "POST",
        "/api/setup/finish",
        {"interests": "扩散模型", "categories": ["cs.LG", "cs.CV"]},
    )
    s = repo.load_settings(api.store)
    assert s["preset"] == "general" and s["categories"] == ["cs.LG", "cs.CV"]
    call(api, "POST", "/api/setup/finish", {"interests": "四足", "categories": ["cs.RO"]})
    assert repo.load_settings(api.store)["preset"] == "robotics"
    call(api, "POST", "/api/setup/finish",
         {"interests": "x", "categories": ["cs.LG"], "preset": "robotics"})  # fmt: skip
    assert repo.load_settings(api.store)["preset"] == "robotics"  # 明确给了就听它的
