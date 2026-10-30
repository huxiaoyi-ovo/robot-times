"""本地版的更新：检查 GitHub 上的最新发布版本，下载新代码、装依赖、重启看板。

- 只看正式发布的版本（GitHub Release），不跟着 main 上的每次提交走。
- 用 git 装的（一行命令安装）：取新版本的标签，把代码切过去；用户自己改过代码就不动，只提示。
- 下载压缩包装的（Windows）：下载新版本的压缩包，替换 core/、web/ 等代码，不碰 .venv。
- 数据在 ~/.robot-times，不在代码文件夹里，更新不会碰。数据库结构有变化时，下次启动自动迁移（cloud/store.py）。
- 正在生成推荐时不更新；自动更新只在看板 10 分钟没人用时进行。
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .. import __version__
from ..cloud import repo
from ..cloud.store import Store

log = logging.getLogger("robot_times.update")

UPSTREAM = os.environ.get(
    "ROBOT_TIMES_UPSTREAM", "huxiaoyi-ovo/robot-times"
)  # 项目主页，不是用户的仓库
ROOT = Path(__file__).resolve().parents[4]  # 代码文件夹（里面有 core/、web/）
CHECK_EVERY = timedelta(hours=12)
IDLE = timedelta(minutes=10)  # 看板多久没人用才自动更新
CODE = ("core", "web", ".claude", "install.sh", "install.ps1", "pyproject.toml", "uv.lock",
        "README.md", "README.en.md", "CHANGELOG.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "docs")  # fmt: skip


def parse(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("-")[0])
    return tuple(int(n) for n in nums[:3]) or (0,)


def newer(latest: str, current: str) -> bool:
    return parse(latest) > parse(current)


def method(root: Path = ROOT) -> str:
    """git：用 git 装的；zip：下载压缩包装的；none：开发环境之类，不自动更新。"""
    if (root / ".git").exists():
        return "git"
    if (root / "core" / "pyproject.toml").exists():
        return "zip"
    return "none"


def fetch_latest(http: httpx.Client | None = None) -> dict[str, Any] | None:
    """GitHub 上最新的正式发布版本：{version, notes, url, published}；没有发布过返回 None。"""
    client = http or httpx.Client(timeout=15.0, follow_redirects=True)
    r = client.get(
        f"https://api.github.com/repos/{UPSTREAM}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "robot-times"},
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    return {
        "version": str(d.get("tag_name", "")).lstrip("v"),
        "tag": d.get("tag_name", ""),
        "notes": str(d.get("body") or "")[:4000],
        "url": d.get("html_url", ""),
        "published": d.get("published_at", ""),
    }


class Updater:
    """检查和执行更新。状态给看板显示：{current, latest, available, method, state, error}。"""

    def __init__(
        self,
        store: Store,
        *,
        root: Path = ROOT,
        http: httpx.Client | None = None,
        busy: Callable[[], bool] = lambda: False,
        restart: Callable[[], None] | None = None,
        run: Callable[[list[str], Path], None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.root = root
        self.http = http
        self.busy = busy  # 有没有任务在跑（生成推荐时不更新）
        self.restart = restart or restart_dashboard
        self.run = run or _run
        self.now = now
        self.state = "idle"  # idle / updating / restarting / failed
        self.error = ""
        self.last_seen = now()  # 看板最后一次被打开的时间
        self._lock = threading.Lock()

    # ---------- 检查 ----------

    def cached(self) -> dict[str, Any] | None:
        return repo.get_json(self.store, "update_check")

    def check(self, force: bool = False) -> dict[str, Any] | None:
        """最多 12 小时查一次 GitHub；查不到就用上次的结果。"""
        c = self.cached()
        if not force and c and self.now() - datetime.fromisoformat(c["at"]) < CHECK_EVERY:
            return c.get("latest")
        try:
            latest = fetch_latest(self.http)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("检查更新失败：%s", e)
            return c.get("latest") if c else None
        repo.set_json(self.store, "update_check", {"at": self.now().isoformat(), "latest": latest})
        return latest

    def status(self) -> dict[str, Any]:
        latest = (self.cached() or {}).get("latest")
        settings = repo.load_settings(self.store)
        return {
            "current": __version__,
            "latest": latest,
            "available": bool(latest and newer(latest["version"], __version__)),
            "method": method(self.root),
            "auto": bool(settings.get("autoUpdate", True)),
            "state": self.state,
            "error": self.error,
        }

    # ---------- 更新 ----------

    def start(self) -> dict[str, Any]:
        """看板上点“更新”：在后台更新，完成后重启看板。"""
        st = self.status()
        if not st["available"]:
            raise UpdateError("已经是最新版本。")
        if st["method"] == "none":
            raise UpdateError("这份代码不是用安装脚本装的，没法自动更新：请自己 git pull。")
        if self.busy():
            raise UpdateError("正在生成推荐，等生成完再更新。")
        with self._lock:
            if self.state in ("updating", "restarting"):
                return self.status()
            self.state, self.error = "updating", ""
        threading.Thread(target=self._apply, args=(st["latest"],), daemon=True).start()
        return self.status()

    def tick(self) -> None:
        """后台每小时调一次：查新版本；开了自动更新、没人在用、没任务在跑，就更新。"""
        self.check()
        st = self.status()
        idle = self.now() - self.last_seen > IDLE
        if st["available"] and st["auto"] and st["method"] != "none" and idle and not self.busy():
            if self.state == "idle":
                log.info("自动更新到 %s", st["latest"]["version"])
                try:
                    self.start()
                except UpdateError as e:
                    log.info("这次不自动更新：%s", e)

    def _apply(self, latest: dict[str, Any]) -> None:
        try:
            self.apply(latest)
            self.state = "restarting"
            log.info("已更新到 %s，重启看板", latest["version"])
            self.restart()
        except Exception as e:  # 更新失败不影响继续用旧版本
            log.exception("更新失败")
            self.state, self.error = "failed", str(e)[:400]

    def apply(self, latest: dict[str, Any]) -> None:
        """下载新代码并装好依赖（同步执行）。"""
        tag = latest["tag"] or f"v{latest['version']}"
        if method(self.root) == "git":
            self._git(tag)
        else:
            self._zip(tag)
        self._sync()

    def _git(self, tag: str) -> None:
        # uv.lock 是自动生成的，uv 有时会改写它，不算用户改过代码
        self.run(["git", "checkout", "--", "uv.lock"], self.root) if (
            self.root / "uv.lock"
        ).exists() else None
        changed = _out(["git", "status", "--porcelain", "--untracked-files=no"], self.root)
        if changed.strip():
            raise UpdateError(
                "你改过代码文件夹里的文件，没法自动更新。在代码文件夹里运行 git stash 后再试，或者自己 git pull。"
            )
        self.run(
            [
                "git",
                "fetch",
                "--depth",
                "1",
                "origin",
                f"refs/tags/{tag}:refs/tags/{tag}",
                "--force",
            ],
            self.root,
        )
        self.run(["git", "reset", "--keep", tag], self.root)

    def _zip(self, tag: str) -> None:
        client = self.http or httpx.Client(timeout=120.0, follow_redirects=True)
        r = client.get(f"https://github.com/{UPSTREAM}/archive/refs/tags/{tag}.zip")
        r.raise_for_status()
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for name in z.namelist():  # 防止压缩包里有 ../ 之类跳出目录的路径
                if name.startswith("/") or ".." in Path(name).parts:
                    raise UpdateError("下载的压缩包不对。")
            z.extractall(tmp)
            top = next(p for p in Path(tmp).iterdir() if p.is_dir())
            if not (top / "core" / "pyproject.toml").exists():
                raise UpdateError("下载的压缩包里没有代码。")
            for name in CODE:
                src, dst = top / name, self.root / name
                if not src.exists():
                    continue
                if dst.is_dir():
                    shutil.rmtree(dst)
                elif dst.exists():
                    dst.unlink()
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

    def _sync(self) -> None:
        uv = find_uv()
        if uv is None:
            raise UpdateError("找不到 uv，代码已经更新，请在代码文件夹里运行 uv sync 后重启看板。")
        cmd = [uv, "sync", "--frozen"]  # 按仓库里的 uv.lock 装，不改写它
        try:
            import sentence_transformers  # noqa: F401

            cmd += ["--extra", "embed"]
        except ImportError:
            pass
        self.run(cmd, self.root)


class UpdateError(RuntimeError):
    """给用户看的更新失败原因。"""


def find_uv() -> str | None:
    home = Path.home()
    # 安装包版：启动器把自带的 uv 的位置放在 ROBOT_TIMES_UV 里
    for p in (os.environ.get("ROBOT_TIMES_UV"), shutil.which("uv"), home / ".local/bin/uv", home / ".cargo/bin/uv",
              home / ".local/bin/uv.exe", home / ".cargo/bin/uv.exe"):  # fmt: skip
        if p and Path(p).exists():
            return str(p)
    return None


def restart_dashboard() -> None:
    """另起一个进程执行 robot-times local restart：它会让现在这个看板退出，再启动新版本。"""
    from . import install

    install._spawn([sys.executable, "-m", "robot_times.cli", "local", "restart"])


def _run(cmd: list[str], cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)  # noqa: S603
    if r.returncode != 0:
        raise UpdateError(f"{' '.join(cmd[:3])} 失败：{(r.stderr or r.stdout).strip()[-300:]}")


def _out(cmd: list[str], cwd: Path) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)  # noqa: S603
    return r.stdout if r.returncode == 0 else ""


def ticker(updater: Updater, every: float = 3600.0, first: float = 60.0) -> threading.Thread:
    """后台线程：启动 1 分钟后检查一次，之后每小时一次。"""

    def loop() -> None:
        time.sleep(first)
        while True:
            try:
                updater.tick()
            except Exception:
                log.exception("检查更新出错")
            time.sleep(every)

    t = threading.Thread(target=loop, name="robot-times-update", daemon=True)
    t.start()
    return t
