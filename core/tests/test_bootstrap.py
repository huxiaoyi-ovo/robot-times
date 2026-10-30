"""安装包的启动器（packaging/bootstrap.py）：复制代码、判断要不要重装依赖、“正在准备”页面。不联网。"""

import importlib.util
import socket
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def boot(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "packaging" / "bootstrap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    payload = tmp_path / "opt"
    (payload / "app" / "core").mkdir(parents=True)
    (payload / "app" / "core" / "pyproject.toml").write_text('version = "0.2.0"\n')
    (payload / "app" / "uv.lock").write_text("lock-a\n")
    home = tmp_path / "home"
    monkeypatch.setattr(mod, "PAYLOAD", payload)
    monkeypatch.setattr(mod, "HOME", home)
    monkeypatch.setattr(mod, "APP", home / "app")
    return mod


def test_first_run_copies_code_then_leaves_it_alone(boot):
    assert boot.ensure_app(log=lambda m: None) is True
    assert boot.version(boot.APP) == (0, 2, 0)
    assert boot.ensure_app(log=lambda m: None) is False


def test_newer_package_replaces_old_copy_but_not_an_auto_updated_one(boot):
    boot.ensure_app(log=lambda m: None)
    (boot.APP / ".venv").mkdir()
    (boot.APP / "core" / "pyproject.toml").write_text('version = "0.1.0"\n')
    (boot.APP / "core" / "stale.py").write_text("")
    assert boot.ensure_app(log=lambda m: None) is True  # 装了新的安装包
    assert boot.version(boot.APP) == (0, 2, 0) and not (boot.APP / "core" / "stale.py").exists()
    assert (boot.APP / ".venv").exists()  # 依赖不删
    (boot.APP / "core" / "pyproject.toml").write_text('version = "0.3.0"\n')
    assert boot.ensure_app(log=lambda m: None) is False  # 看板自己更新到了更新的版本


def test_dependencies_reinstall_when_uv_lock_changes(boot):
    boot.ensure_app(log=lambda m: None)
    py = boot.venv_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    assert not boot.env_ready()
    boot.marker().write_text(boot.lock_id())
    assert boot.env_ready()
    (boot.APP / "uv.lock").write_text("lock-b\n")  # 自动更新换了依赖
    assert not boot.env_ready()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_preparing_page_shows_progress_and_is_not_the_dashboard(boot, monkeypatch):
    monkeypatch.setattr(boot, "PORT", free_port())
    prep = boot.Preparing()
    try:
        prep.say("安装 Python 和依赖")
        get = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        base = f"http://127.0.0.1:{boot.PORT}"
        assert "安装 Python 和依赖" in get(f"{base}/").read().decode()
        assert get(f"{base}/status").read().decode() == "安装 Python 和依赖"
        with pytest.raises(urllib.error.HTTPError):
            get(f"{base}/api/job")  # 页面靠这个判断看板起来没有
        assert boot.running() is False
    finally:
        prep.close()


def test_update_uses_the_bundled_uv(monkeypatch, tmp_path):
    from robot_times.local import update

    uv = tmp_path / "uv"
    uv.write_text("")
    monkeypatch.setenv("ROBOT_TIMES_UV", str(uv))
    assert update.find_uv() == str(uv)


def test_prepare_takes_over_a_lock_left_by_a_killed_run(boot, monkeypatch):
    import os
    import time

    monkeypatch.setattr(boot, "ensure_env", lambda log: None)
    lock = boot.HOME / ".preparing"
    lock.mkdir(parents=True)
    old = time.time() - 3600
    os.utime(lock, (old, old))
    boot.prepare(log=lambda m: None)
    assert boot.version(boot.APP) == (0, 2, 0) and not lock.exists()
