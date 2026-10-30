"""开机自动启动和桌面快捷方式：只检查写出的文件和要执行的命令，不真的启动。"""

import plistlib
from pathlib import Path

from robot_times.local import install


def home_with_desktop(tmp_path: Path) -> Path:
    (tmp_path / "Desktop").mkdir()
    return tmp_path


def test_macos_login_item_and_webloc(tmp_path):
    home = home_with_desktop(tmp_path)
    plan = install.plan_autostart(
        "darwin", home, Path("/repo"), {"ROBOT_TIMES_HOME": "/data/robot-times"}
    )
    agent = home / "Library/LaunchAgents/com.therobottimes.dashboard.plist"
    item = plistlib.loads(plan.files[agent])
    assert item["ProgramArguments"][1:] == ["-m", "robot_times.cli", "local", "serve", "--no-open"]
    assert item["RunAtLoad"] is True and item["WorkingDirectory"] == str(Path("/repo"))
    assert item["EnvironmentVariables"]["ROBOT_TIMES_HOME"] == "/data/robot-times"
    assert item["StandardOutPath"] == str(Path("/data/robot-times") / "serve.log")
    assert plistlib.loads(plan.files[home / "Desktop/The Robot Times.webloc"]) == {
        "URL": "http://localhost:8765/"
    }
    assert plan.start == ["launchctl", "load", "-w", str(agent)]


def test_windows_startup_script_and_url_shortcut(tmp_path):
    home = home_with_desktop(tmp_path)
    appdata = tmp_path / "AppData" / "Roaming"
    plan = install.plan_autostart("win32", home, Path("C:/repo"), {"APPDATA": str(appdata)})
    script = appdata / "Microsoft/Windows/Start Menu/Programs/Startup/The Robot Times.vbs"
    text = plan.files[script].decode("utf-16")
    assert 'sh.Run ""' in text and '""--log-file""' in text and ", 0, False" in text  # 隐藏窗口
    assert 'env("PYTHONUTF8") = "1"' in text
    assert plan.files[home / "Desktop/The Robot Times.url"].decode() == (
        "[InternetShortcut]\r\nURL=http://localhost:8765/\r\n"
    )
    assert plan.start == ["wscript", str(script)]


def test_linux_autostart_without_desktop(tmp_path):
    plan = install.plan_autostart("linux", tmp_path, Path("/repo"), {})
    auto = tmp_path / ".config/autostart/the-robot-times.desktop"
    menu = tmp_path / ".local/share/applications/the-robot-times.desktop"
    assert list(plan.files) == [auto, menu]  # 没有桌面文件夹就不放桌面快捷方式
    assert "Exec=xdg-open http://localhost:8765/" in plan.files[menu].decode()
    assert "local serve --no-open" in plan.files[auto].decode()


def test_apply_starts_only_when_not_running_and_remove_cleans_up(tmp_path):
    home = home_with_desktop(tmp_path)
    plan = install.plan_autostart("linux", home, Path("/repo"), {})
    started = []
    written = install.apply(plan, start=True, running=lambda: False, spawn=started.append)
    assert all(p.exists() for p in written) and started == [plan.start]
    install.apply(plan, start=True, running=lambda: True, spawn=started.append)
    assert len(started) == 1  # 已经在运行就不再启动
    assert install.installed("linux", home, {}) == written
    gone = install.remove(plan, run=lambda cmd: None)
    assert gone == written and not any(p.exists() for p in written)


def test_packaged_autostart_toggle_each_platform(tmp_path):
    from robot_times.local.install import autostart_on, set_autostart

    env = {"ROBOT_TIMES_PACKAGED": "1", "ROBOT_TIMES_LAUNCHER": "/opt/robot-times/launch",
           "ROBOT_TIMES_HOME": str(tmp_path / ".robot-times"), "APPDATA": str(tmp_path / "AppData")}  # fmt: skip
    for platform in ("linux", "darwin", "win32"):
        home = tmp_path / platform
        home.mkdir()
        if platform == "win32":  # 安装程序放的快捷方式
            env["APPDATA"] = str(home / "AppData")
            startup = home / "AppData/Microsoft/Windows/Start Menu/Programs/Startup"
            startup.mkdir(parents=True)
            (startup / "The Robot Times.lnk").write_bytes(b"lnk")
        env["ROBOT_TIMES_HOME"] = str(home / ".robot-times")
        assert autostart_on(platform, home, env), platform
        set_autostart(False, platform, home, env, tmp_path)
        assert not autostart_on(platform, home, env), platform
        set_autostart(True, platform, home, env, tmp_path)
        assert autostart_on(platform, home, env), platform
    linux = tmp_path / "linux" / ".config/autostart/the-robot-times.desktop"
    assert not linux.exists()  # 打开时删掉覆盖文件，用回系统的那个
    assert (
        "/opt/robot-times/launch"
        in (tmp_path / "darwin/Library/LaunchAgents/com.therobottimes.dashboard.plist").read_text()
    )
