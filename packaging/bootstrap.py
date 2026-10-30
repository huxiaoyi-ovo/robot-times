"""安装包的启动器：只用标准库，由安装包里自带的 uv 运行（uv 会自动准备好 Python）。

    launch open    点应用图标：看板没开就准备好、启动，再在浏览器里打开
    launch serve   开机自动启动：在后台运行看板（已经在运行就什么也不做）

安装包装在系统目录里（只读），第一次启动时把代码复制到 ~/.robot-times/app，
依赖装在 ~/.robot-times/app/.venv。以后的自动更新只改这份用户自己的副本，不需要管理员密码；
装了更新版本的安装包，下次启动时会换上安装包里更新的代码。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent
WIN = os.name == "nt"
HOME = Path(os.environ.get("ROBOT_TIMES_HOME") or Path.home() / ".robot-times")
APP = HOME / "app"
LOG = HOME / "serve.log"
PORT = int(os.environ.get("ROBOT_TIMES_PORT") or 8765)
URL = f"http://localhost:{PORT}/"
CODE = ("core", "web", ".claude", "pyproject.toml", "uv.lock", "README.md", "README.en.md",
        "CHANGELOG.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "docs")  # fmt: skip


def uv() -> str:
    for name in ("uv.exe", "uv") if WIN else ("uv",):
        p = PAYLOAD / name
        if p.exists():
            return str(p)
    found = shutil.which("uv")
    if not found:
        raise SystemExit("找不到 uv，安装包可能不完整，请重新安装。")
    return found


def version(app: Path) -> tuple[int, ...]:
    try:
        text = (app / "core" / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return (0,)
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return tuple(int(n) for n in re.findall(r"\d+", m.group(1))[:3]) if m else (0,)


def ensure_app(log=print) -> bool:
    """把安装包里的代码复制到用户目录（没有，或者安装包里的更新时）。返回是否复制了。"""
    src = PAYLOAD / "app"
    if (APP / "core").exists() and version(APP) >= version(src):
        return False
    log(f"准备代码：{APP}")
    APP.mkdir(parents=True, exist_ok=True)
    for name in CODE:
        s, d = src / name, APP / name
        if not s.exists():
            continue
        if d.is_dir():
            shutil.rmtree(d)
        elif d.exists():
            d.unlink()
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    return True


def venv_python() -> Path:
    return APP / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")


def lock_id() -> str:
    """依赖清单的指纹：uv.lock 变了（新版本、自动更新过）就重新装一次依赖。"""
    import hashlib

    try:
        return hashlib.sha256((APP / "uv.lock").read_bytes()).hexdigest()[:16]
    except OSError:
        return "none"


def marker() -> Path:
    return APP / ".venv" / ".robot-times-lock"


def env_ready() -> bool:
    m = marker()
    return venv_python().exists() and m.exists() and m.read_text().strip() == lock_id()


def ensure_env(log=print) -> None:
    """用 uv 装 Python 和依赖（第一次大约一分钟，要联网）。"""
    if env_ready():
        return
    log("安装 Python 和依赖……第一次大约一分钟")
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "setup.log", "a", encoding="utf-8") as out:
        r = subprocess.run(
            [uv(), "sync", "--frozen", "--project", str(APP), "--python", "3.12"],
            stdout=out, stderr=subprocess.STDOUT, env=_env(), **_hidden(),
        )  # fmt: skip
    if r.returncode != 0:
        raise SystemExit(f"安装依赖失败，详情见 {HOME / 'setup.log'}。请检查网络后重新打开。")
    marker().write_text(lock_id())


def needs_prepare() -> bool:
    return not (
        (APP / "core").exists() and version(APP) >= version(PAYLOAD / "app") and env_ready()
    )


def prepare(log=print) -> None:
    """复制代码、装依赖。同时点了两次图标、或者开机启动和点图标撞在一起时，只让一个进程做，另一个等着。"""
    HOME.mkdir(parents=True, exist_ok=True)
    lock = HOME / ".preparing"
    end = time.time() + 900
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > 900  # 上次准备到一半被关掉了
            except OSError:
                continue
            if stale or time.time() > end:
                shutil.rmtree(lock, ignore_errors=True)
                continue
            log("另一个窗口正在准备，稍等……")
            time.sleep(2)
    try:
        ensure_app(log)
        ensure_env(log)
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "ROBOT_TIMES_UV": uv(), "ROBOT_TIMES_PACKAGED": "1"})
    return env


def _hidden() -> dict:
    return {"creationflags": 0x08000000} if WIN else {}  # CREATE_NO_WINDOW：不弹黑窗口


def serve_cmd() -> list[str]:
    return [str(venv_python()), "-m", "robot_times.cli", "local", "serve", "--no-open",
            "--port", str(PORT), "--log-file", str(LOG)]  # fmt: skip


def running() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{PORT}/api/job", timeout=2) as r:
            return r.headers.get("Server", "").startswith("robot-times-local")
    except OSError:
        return False


def spawn(cmd: list[str]) -> None:
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "env": _env(), "cwd": str(APP)}  # fmt: skip
    if WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)  # noqa: S603


def wait_running(seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if running():
            return True
        time.sleep(0.5)
    return False


# ---------- 第一次启动时的“正在准备”页面 ----------

PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>The Robot Times</title>
<style>body{font-family:system-ui,-apple-system,"PingFang SC","Noto Sans SC",sans-serif;max-width:560px;margin:18vh auto;padding:0 20px;color:#121212;background:#fff}
h1{font-family:Georgia,serif;font-size:30px;margin:0 0 12px}p{color:#555;line-height:1.7}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#121212;margin-right:8px;animation:p 1.2s infinite}
@keyframes p{50%{opacity:.2}}@media (prefers-color-scheme:dark){body{background:#121212;color:#eee}.dot{background:#eee}p{color:#aaa}}</style>
<h1>The Robot Times</h1><p><span class="dot"></span><span id="s">{status}</span></p>
<p>第一次打开要下载 Python 和依赖，大约一分钟。好了这个页面会自动变成设置引导，不用做别的。</p>
<script>
// 准备页面和看板用同一个端口：准备页面没有 /api/job，看板有。看板起来了就刷新；中间连不上也继续等。
setInterval(async () => {
  try {
    const r = await fetch("/api/job", {cache: "no-store"});
    if (r.ok) return location.reload();
    const s = await fetch("/status", {cache: "no-store"});
    if (s.ok) document.getElementById("s").textContent = await s.text();
  } catch (e) {}
}, 1500);
</script></html>"""


class Preparing:
    """看板准备好之前，先在同一个端口上显示“正在准备”。"""

    def __init__(self) -> None:
        self.status = "正在准备……"
        prep = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/status"):
                    self._send(200, "text/plain", prep.status)
                elif self.path.startswith("/api/"):
                    self._send(404, "text/plain", "preparing")
                else:
                    self._send(200, "text/html", PAGE.replace("{status}", prep.status))

            def _send(self, code: int, kind: str, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", f"{kind}; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", PORT), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def say(self, text: str) -> None:
        self.status = text

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------- 两个命令 ----------


def cmd_serve() -> int:
    if running():
        return 0
    register_autostart()
    if needs_prepare():
        prepare()
    if running():  # 等的时候别的进程已经把看板启动了
        return 0
    if WIN:  # 不占着安装目录里的 uv.exe，升级安装包时才能替换
        spawn(serve_cmd())
        return 0
    os.chdir(APP)
    os.execve(serve_cmd()[0], serve_cmd(), _env())
    return 0


def cmd_open() -> int:
    if running():
        webbrowser.open(URL)
        return 0
    register_autostart()
    if not needs_prepare():
        spawn(serve_cmd())  # 已经准备好了：直接启动
        wait_running(30)
        webbrowser.open(URL)
        return 0
    try:
        prep = Preparing()
    except OSError:
        prep = None  # 端口被占用：不显示准备页面，准备好再打开
    if prep:
        webbrowser.open(URL)
    say = prep.say if prep else print
    try:
        prepare(log=say)
    except SystemExit as e:
        say(str(e))
        if prep:
            time.sleep(120)  # 让页面把原因显示一会儿
        raise
    if prep:
        say("马上就好……")
        time.sleep(2)
        prep.close()
    spawn(serve_cmd())
    ok = wait_running(60)
    if not prep and ok:
        webbrowser.open(URL)
    return 0 if ok else 1


def register_autostart() -> None:
    """macOS 的开机启动由启动器自己登记（Linux 的安装包放在 /etc/xdg/autostart，Windows 的安装包放进“启动”文件夹）。"""
    if sys.platform != "darwin":
        return
    launch = PAYLOAD / "launch"
    agent = Path.home() / "Library" / "LaunchAgents" / "com.therobottimes.dashboard.plist"
    want = f"<string>{launch}</string>"
    if agent.exists() and want in agent.read_text(encoding="utf-8", errors="ignore"):
        return
    import plistlib

    agent.parent.mkdir(parents=True, exist_ok=True)
    HOME.mkdir(parents=True, exist_ok=True)
    agent.write_bytes(plistlib.dumps({
        "Label": "com.therobottimes.dashboard", "ProgramArguments": [str(launch), "serve"],
        "RunAtLoad": True, "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG),
    }))  # fmt: skip


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # 英文 Windows 的控制台编码打不出中文，别因为这个崩掉
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    cmd = argv[1] if len(argv) > 1 else "open"
    if cmd == "serve":
        return cmd_serve()
    if cmd == "open":
        return cmd_open()
    print("用法：launch [open|serve]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
