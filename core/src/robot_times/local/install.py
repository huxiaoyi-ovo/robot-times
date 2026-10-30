"""开机自动启动看板，并在桌面放一个打开看板的快捷方式。

- macOS：~/Library/LaunchAgents 里的登录项（launchd），登录后在后台启动
- Windows：“启动”文件夹里的一个小脚本，登录后在后台启动（不弹黑色窗口）
- Linux：~/.config/autostart 里的自启动项（桌面环境登录后启动）

浏览器不允许别的程序直接往收藏夹里写东西，所以“收藏”这一步用桌面快捷方式代替：
双击就在默认浏览器里打开看板。看板页面上也会提示按 ⌘D / Ctrl+D 收藏、或者安装成应用。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

LABEL = "com.therobottimes.dashboard"
NAME = "The Robot Times"
PORT = 8765
ICON = Path(__file__).resolve().parents[4] / "web" / "app" / "public" / "icon-maskable-512.png"


@dataclass
class Plan:
    """要写哪些文件、要不要马上启动。测试里只看 Plan，不真的执行。"""

    files: dict[Path, bytes] = field(default_factory=dict)
    start: list[str] | None = None  # 马上在后台启动看板的命令
    stop: list[str] | None = None  # 关掉自动启动时先停掉的命令


def serve_command(python: str | None = None) -> list[str]:
    """后台启动看板的命令：直接用当前环境的 Python，不依赖登录时的 PATH 里有没有 uv。"""
    return [python or sys.executable, "-m", "robot_times.cli", "local", "serve", "--no-open"]


def desktop_dir(home: Path) -> Path | None:
    for d in (home / "Desktop", home / "OneDrive" / "Desktop", home / "桌面"):
        if d.is_dir():
            return d
    return None


def plan_autostart(
    platform: str, home: Path, workdir: Path, env: dict[str, str], name: str = NAME
) -> Plan:
    """按系统生成自动启动项和桌面快捷方式。"""
    url = f"http://localhost:{PORT}/"
    cmd = serve_command()
    log = Path(env.get("ROBOT_TIMES_HOME") or home / ".robot-times") / "serve.log"
    extra_env = {k: v for k, v in env.items() if k == "ROBOT_TIMES_HOME"}
    plan = Plan()
    desk = desktop_dir(home)
    if platform == "darwin":
        agent = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        plan.files[agent] = plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": cmd,
                "WorkingDirectory": str(workdir),
                "RunAtLoad": True,
                "StandardOutPath": str(log),
                "StandardErrorPath": str(log),
                "EnvironmentVariables": {"PYTHONUTF8": "1", **extra_env},
            }
        )
        plan.start = ["launchctl", "load", "-w", str(agent)]
        plan.stop = ["launchctl", "unload", "-w", str(agent)]
        if desk:
            plan.files[desk / f"{name}.webloc"] = plistlib.dumps({"URL": url})
    elif platform == "win32":
        startup = (
            Path(env.get("APPDATA") or home / "AppData" / "Roaming")
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        )  # fmt: skip
        script = startup / f"{name}.vbs"
        sets = "".join(f'env("{k}") = "{v}"\n' for k, v in {"PYTHONUTF8": "1", **extra_env}.items())
        line = " ".join(f'""{c}""' for c in [*cmd, "--log-file", str(log)])
        plan.files[script] = (
            "' The Robot Times：登录后在后台启动看板（窗口隐藏）。删掉这个文件就不再自动启动。\n"
            'Set sh = CreateObject("WScript.Shell")\n'
            'Set env = sh.Environment("Process")\n'
            f"{sets}"
            f'sh.CurrentDirectory = "{workdir}"\n'
            f'sh.Run "{line}", 0, False\n'
        ).encode("utf-16")  # WScript 要 UTF-16 才认中文路径
        plan.start = ["wscript", str(script)]
        if desk:
            plan.files[desk / f"{name}.url"] = f"[InternetShortcut]\r\nURL={url}\r\n".encode()
    else:
        auto = home / ".config" / "autostart" / "the-robot-times.desktop"
        envs = " ".join(f"{k}={v}" for k, v in {"PYTHONUTF8": "1", **extra_env}.items())
        plan.files[auto] = (
            "[Desktop Entry]\nType=Application\n"
            f"Name={name}\nComment=后台启动论文早报看板\n"
            f'Exec=sh -c \'cd "{workdir}" && env {envs} {" ".join(cmd)} >> "{log}" 2>&1\'\n'
            "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n"
        ).encode()
        plan.start = cmd
        # 应用菜单里的图标（GNOME 等默认不显示桌面图标，从应用列表里搜“Robot Times”打开）
        menu = home / ".local" / "share" / "applications" / "the-robot-times.desktop"
        plan.files[menu] = (
            f"[Desktop Entry]\nType=Application\nName={name}\nComment=打开论文早报看板\n"
            f"Exec=xdg-open {url}\nIcon={ICON}\nCategories=Education;Office;\nTerminal=false\n"
        ).encode()
        if desk:
            plan.files[desk / "the-robot-times.desktop"] = (
                f"[Desktop Entry]\nType=Link\nName={name}\nURL={url}\nIcon=text-html\n"
            ).encode()
    return plan


def installed(platform: str, home: Path, env: dict[str, str]) -> list[Path]:
    """已经装上的自动启动项和快捷方式。"""
    return [p for p in plan_autostart(platform, home, Path("."), env).files if p.exists()]


def apply(
    plan: Plan,
    *,
    start: bool,
    running: Callable[[], bool],
    spawn: Callable[[list[str]], None] | None = None,
) -> list[Path]:
    """写文件，按需在后台启动看板（已经在运行就不再启动）。返回写了哪些文件。"""
    for path, data in plan.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if start and plan.start and not running():
        (spawn or _spawn)(plan.start)
    return list(plan.files)


def remove(plan: Plan, *, run: Callable[[list[str]], None] | None = None) -> list[Path]:
    """关掉自动启动：删掉自动启动项和桌面快捷方式（正在运行的看板这次不停，下次登录不再启动）。"""
    gone = []
    if plan.stop:
        try:
            (run or _run)(plan.stop)
        except (OSError, subprocess.SubprocessError):
            pass
    for path in plan.files:
        if path.exists():
            path.unlink()
            gone.append(path)
    return gone


def _spawn(cmd: list[str]) -> None:
    kw: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)  # noqa: S603  命令是我们自己拼的


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, capture_output=True, timeout=20)  # noqa: S603


# ---------- 看板设置页里的“开机自动启动”开关 ----------
# 安装包装的：Linux 的系统级自启动项在 /etc/xdg/autostart，用户目录里放一个 Hidden=true 的同名文件就能关掉；
# macOS 是启动器登记的 LaunchAgent（关掉时留一个标记，启动器就不再登记）；Windows 是“启动”文件夹里的快捷方式。
# 命令行装的：用上面的 plan_autostart。


def packaged(env: dict[str, str]) -> bool:
    return env.get("ROBOT_TIMES_PACKAGED") == "1"


def _startup_dir(home: Path, env: dict[str, str]) -> Path:
    return (Path(env.get("APPDATA") or home / "AppData" / "Roaming")
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")  # fmt: skip


def autostart_on(platform: str, home: Path, env: dict[str, str]) -> bool:
    data = Path(env.get("ROBOT_TIMES_HOME") or home / ".robot-times")
    if not packaged(env):
        return any(p.parent.name in ("autostart", "LaunchAgents", "Startup")
                   for p in installed(platform, home, env))  # fmt: skip
    if platform == "darwin":
        return not (data / "autostart-off").exists()
    if platform == "win32":
        d = _startup_dir(home, env)
        return (d / f"{NAME}.lnk").exists() or (d / f"{NAME}.vbs").exists()
    override = home / ".config" / "autostart" / "the-robot-times.desktop"
    return not (
        override.exists() and "Hidden=true" in override.read_text(encoding="utf-8", errors="ignore")
    )


def set_autostart(on: bool, platform: str, home: Path, env: dict[str, str], workdir: Path) -> None:
    data = Path(env.get("ROBOT_TIMES_HOME") or home / ".robot-times")
    if not packaged(env):
        plan = plan_autostart(platform, home, workdir, env)
        if on:
            apply(plan, start=False, running=lambda: True)
        else:
            remove(plan)
        return
    launcher = env.get("ROBOT_TIMES_LAUNCHER", "")
    if platform == "darwin":
        flag, agent = data / "autostart-off", home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        if on:
            flag.unlink(missing_ok=True)
            agent.parent.mkdir(parents=True, exist_ok=True)
            agent.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": [launcher, "serve"],
                                              "RunAtLoad": True}))  # fmt: skip
        else:
            data.mkdir(parents=True, exist_ok=True)
            flag.write_text("用户在看板设置页里关掉了开机自动启动\n", encoding="utf-8")
            agent.unlink(missing_ok=True)
    elif platform == "win32":
        d = _startup_dir(home, env)
        if on:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{NAME}.vbs").write_bytes((
                "' The Robot Times：登录后在后台启动看板。删掉这个文件就不再自动启动。\n"
                f'CreateObject("WScript.Shell").Run "wscript ""{launcher}"" serve", 0, False\n'
            ).encode("utf-16"))  # fmt: skip
        else:
            for f in (d / f"{NAME}.lnk", d / f"{NAME}.vbs"):
                f.unlink(missing_ok=True)
    else:
        override = home / ".config" / "autostart" / "the-robot-times.desktop"
        if on:
            if override.exists() and "Hidden=true" in override.read_text(
                encoding="utf-8", errors="ignore"
            ):
                override.unlink()
        else:
            override.parent.mkdir(parents=True, exist_ok=True)
            override.write_text("[Desktop Entry]\nType=Application\nName=The Robot Times\nHidden=true\n",
                                encoding="utf-8")  # fmt: skip
