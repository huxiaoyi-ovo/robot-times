"""更新：检查新版本、git 和压缩包两种更新方式、自动更新的条件、数据库结构迁移。不联网。"""

import io
import json
import sqlite3
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from robot_times import __version__
from robot_times.cloud import repo
from robot_times.cloud.store import SqliteStore, migrate
from robot_times.local import update
from robot_times.local.update import UpdateError, Updater, newer

ROOT = Path(__file__).resolve().parents[2]


def test_version_comes_from_pyproject():
    text = (ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text


def test_compare_versions():
    assert newer("0.2.0", "0.1.0") and newer("v1.0.0", "0.9.9") and newer("0.10.0", "0.9.0")
    assert not newer("0.1.0", "0.1.0") and not newer("0.1.0", "0.2.0")


def github(latest_tag="v9.0.0", calls=None, zip_bytes=b""):
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        if req.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": latest_tag,
                    "body": "- 修好了 Zotero 文件夹",
                    "html_url": "https://x/r",
                },
            )
        if req.url.path.endswith(".zip"):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def clock(start="2026-10-30T09:00:00+00:00"):
    t = {"now": datetime.fromisoformat(start)}
    return t, lambda: t["now"]


def test_check_is_cached_for_12_hours_and_status_shows_new_version(tmp_path):
    calls = []
    t, now = clock()
    up = Updater(SqliteStore(), root=tmp_path, http=github(calls=calls), now=now)
    assert up.check()["version"] == "9.0.0"
    up.check()
    assert len(calls) == 1
    t["now"] += timedelta(hours=13)
    up.check()
    assert len(calls) == 2
    st = up.status()
    assert st["available"] and st["latest"]["notes"] == "- 修好了 Zotero 文件夹"
    assert st["method"] == "none" and st["auto"] is True


def test_failed_check_keeps_last_result(tmp_path):
    t, now = clock()
    store = SqliteStore()
    Updater(store, root=tmp_path, http=github(), now=now).check()
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    t["now"] += timedelta(days=1)
    assert Updater(store, root=tmp_path, http=down, now=now).check()["version"] == "9.0.0"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_upstream(tmp_path: Path) -> Path:
    up = tmp_path / "upstream"
    up.mkdir()
    git("init", "-q", "-b", "main", cwd=up)
    for v in ("0.1.0", "0.2.0"):
        (up / "core").mkdir(exist_ok=True)
        (up / "core" / "pyproject.toml").write_text(f'version = "{v}"\n')
        git("add", "-A", cwd=up)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", v, cwd=up)
        git("tag", f"v{v}", cwd=up)
    return up


def test_git_update_moves_code_to_the_new_tag(tmp_path):
    up = make_upstream(tmp_path)
    work = tmp_path / "work"
    git("clone", "-q", "--depth", "1", "--branch", "v0.1.0", up.as_uri(), str(work), cwd=tmp_path)
    git("checkout", "-q", "-B", "main", cwd=work)
    u = Updater(SqliteStore(), root=work)
    u._sync = lambda: None  # 不真的装依赖
    assert update.method(work) == "git"
    u.apply({"version": "0.2.0", "tag": "v0.2.0"})
    assert 'version = "0.2.0"' in (work / "core" / "pyproject.toml").read_text()


def test_git_update_ignores_rewritten_uv_lock(tmp_path):
    up = make_upstream(tmp_path)
    (up / "uv.lock").write_text("lock\n")
    git("add", "-A", cwd=up)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "lock", cwd=up)
    git("tag", "-f", "v0.2.0", cwd=up)
    work = tmp_path / "work"
    git("clone", "-q", up.as_uri(), str(work), cwd=tmp_path)
    git("reset", "-q", "--hard", "v0.1.0", cwd=work)
    (work / "uv.lock").write_text("uv 改写过\n")  # 0.1.0 里没有 uv.lock，这里模拟 uv 自己生成的
    git("add", "uv.lock", cwd=work)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x", cwd=work)
    (work / "uv.lock").write_text("又被 uv 改写了\n")
    u = Updater(SqliteStore(), root=work)
    u._sync = lambda: None
    u.apply({"version": "0.2.0", "tag": "v0.2.0"})
    assert (work / "uv.lock").read_text() == "lock\n"


def test_git_update_refuses_when_user_changed_code(tmp_path):
    up = make_upstream(tmp_path)
    work = tmp_path / "work"
    git("clone", "-q", up.as_uri(), str(work), cwd=tmp_path)
    (work / "core" / "pyproject.toml").write_text("改过了\n")
    u = Updater(SqliteStore(), root=work)
    u._sync = lambda: None
    with pytest.raises(UpdateError, match="改过代码"):
        u.apply({"version": "0.2.0", "tag": "v0.2.0"})
    assert (work / "core" / "pyproject.toml").read_text() == "改过了\n"


def zipped(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"robot-times-0.2.0/{name}", text)
    return buf.getvalue()


def test_zip_update_replaces_code_but_keeps_venv(tmp_path):
    root = tmp_path / "app"
    (root / "core" / "old").mkdir(parents=True)
    (root / "core" / "pyproject.toml").write_text('version = "0.1.0"\n')
    (root / ".venv").mkdir()
    (root / ".venv" / "keep").write_text("x")
    data = zipped(
        {"core/pyproject.toml": 'version = "0.2.0"\n', "web/app/public/index.html": "new"}
    )
    u = Updater(SqliteStore(), root=root, http=github(zip_bytes=data))
    u._sync = lambda: None
    u.apply({"version": "0.2.0", "tag": "v0.2.0"})
    assert 'version = "0.2.0"' in (root / "core" / "pyproject.toml").read_text()
    assert not (root / "core" / "old").exists()  # 旧版本多出来的文件也清掉
    assert (root / "web/app/public/index.html").read_text() == "new"
    assert (root / ".venv" / "keep").exists()


def test_zip_with_paths_outside_folder_is_rejected(tmp_path):
    root = tmp_path / "app"
    (root / "core").mkdir(parents=True)
    (root / "core" / "pyproject.toml").write_text("")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.txt", "x")
    u = Updater(SqliteStore(), root=root, http=github(zip_bytes=buf.getvalue()))
    with pytest.raises(UpdateError):
        u.apply({"version": "0.2.0", "tag": "v0.2.0"})
    assert not (tmp_path / "evil.txt").exists()


def test_auto_update_only_when_idle_and_no_job_running(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "pyproject.toml").write_text("")
    t, now = clock()
    busy = {"v": True}
    applied, restarted = [], []
    u = Updater(SqliteStore(), root=tmp_path, http=github(), now=now, busy=lambda: busy["v"],
                restart=lambda: restarted.append(1))  # fmt: skip
    u.apply = lambda latest: applied.append(latest["version"])

    def tick_and_wait():
        u.tick()
        for th in __import__("threading").enumerate():
            if th is not __import__("threading").current_thread() and th.daemon:
                th.join(timeout=2)

    tick_and_wait()
    assert applied == []  # 刚打开过看板
    t["now"] += timedelta(minutes=30)
    tick_and_wait()
    assert applied == []  # 正在生成推荐
    busy["v"] = False
    repo.save_settings(u.store, {**repo.load_settings(u.store), "autoUpdate": False})
    tick_and_wait()
    assert applied == []  # 关了自动更新
    repo.save_settings(u.store, {**repo.load_settings(u.store), "autoUpdate": True})
    tick_and_wait()
    assert applied == ["9.0.0"] and restarted == [1] and u.state == "restarting"


def test_start_explains_why_it_cannot_update(tmp_path):
    u = Updater(SqliteStore(), root=tmp_path, http=github(latest_tag=f"v{__version__}"))
    u.check()
    with pytest.raises(UpdateError, match="最新"):
        u.start()
    u2 = Updater(SqliteStore(), root=tmp_path, http=github())
    u2.check()
    with pytest.raises(UpdateError, match="git pull"):
        u2.start()


def test_failed_update_keeps_old_version_running(tmp_path):
    u = Updater(SqliteStore(), root=tmp_path, http=github(), restart=lambda: None)

    def boom(latest):
        raise UpdateError("网络断了")

    u.apply = boom
    u._apply({"version": "9.0.0", "tag": "v9.0.0"})
    assert u.state == "failed" and u.status()["error"] == "网络断了"


def test_dashboard_api_shows_and_starts_update(tmp_path):
    from robot_times.local.api import LocalApi

    store = SqliteStore()
    u = Updater(store, root=tmp_path, http=github())
    api = LocalApi(store, updater=u)
    r = api.handle("GET", "/api/update", {"check": "1"}, b"")
    assert r.body["available"] is True and r.body["latest"]["version"] == "9.0.0"
    r = api.handle("POST", "/api/update", {}, b"")
    assert r.status == 400 and "git pull" in r.body["error"]
    boot = api.handle("GET", "/api/bootstrap", {}, b"").body
    assert boot["update"]["current"] == __version__


def test_migrations_run_once_in_order():
    db = sqlite3.connect(":memory:")
    steps = ["create table t (a int);", "alter table t add column b int;"]
    assert migrate(db, steps[:1]) == 1
    assert migrate(db, steps) == 1 and migrate(db, steps) == 0
    assert [r[1] for r in db.execute("pragma table_info(t)")] == ["a", "b"]
    assert json.dumps(db.execute("pragma user_version").fetchone()) == "[2]"
