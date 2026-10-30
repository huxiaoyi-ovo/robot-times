"""第一次打开看板时的设置引导：连上 Zotero、授权、填研究方向、配大模型。

全部在网页上完成，不用开终端。每一步都能单独重做，设置页里也能改。
"""

from __future__ import annotations

import configparser
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from ..cloud import repo
from ..cloud.env import DEEPSEEK_BASE, DEEPSEEK_MODEL
from ..cloud.store import Store
from ..config import CONFIG_DIR
from ..llm.rerank import LLMClient
from ..zotero.local_api import ZoteroError, ZoteroLocalClient

ZOTERO = "http://localhost:23119"
MIN_ZOTERO = 10  # 本地接口能写入从 Zotero 10 开始
PREF = (
    "extensions.zotero.httpServer.localAPI.enabled"  # “允许此计算机上的其他应用程序与 Zotero 通信”
)
LLM_KEYS = (
    "DEEPSEEK_API_KEY",
    "ROBOT_TIMES_LLM_API_KEY",
    "ROBOT_TIMES_LLM_BASE_URL",
    "ROBOT_TIMES_LLM_MODEL",
)


class SetupError(RuntimeError):
    """给用户看的原因。"""


# ---------- Zotero 的配置文件夹 ----------


def profile_roots(home: Path | None = None, platform: str = sys.platform) -> list[Path]:
    home = home or Path.home()
    if platform == "darwin":
        return [home / "Library" / "Application Support" / "Zotero"]
    if platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return [appdata / "Zotero" / "Zotero"]
    return [
        home / ".zotero" / "zotero",
        home / ".var" / "app" / "org.zotero.Zotero" / ".zotero" / "zotero",
    ]


def find_prefs(roots: list[Path]) -> Path | None:
    """profiles.ini 里默认配置的 prefs.js。"""
    for root in roots:
        ini = root / "profiles.ini"
        if not ini.exists():
            continue
        cp = configparser.ConfigParser()
        cp.read(ini, encoding="utf-8")
        sections = [
            s for s in cp.sections() if s.lower().startswith("profile") and cp[s].get("Path")
        ]
        sections.sort(key=lambda s: cp[s].get("Default") != "1")
        for s in sections:
            p = cp[s]["Path"]
            d = root / p if cp[s].get("IsRelative", "1") == "1" else Path(p)
            if (d / "prefs.js").exists() or d.is_dir():
                return d / "prefs.js"
    return None


def enable_pref(prefs: Path) -> None:
    """在 prefs.js 里打开本地接口。Zotero 退出时会重写这个文件，所以只能在 Zotero 关着时改。"""
    text = prefs.read_text(encoding="utf-8") if prefs.exists() else ""
    line = f'user_pref("{PREF}", true);'
    pattern = re.compile(rf'^user_pref\("{re.escape(PREF)}",\s*(true|false)\);\s*$', re.M)
    text = (
        pattern.sub(line, text)
        if pattern.search(text)
        else text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    )
    prefs.write_text(text, encoding="utf-8")


# ---------- 引导 ----------


class Setup:
    def __init__(
        self,
        store: Store,
        api: Any,  # LocalApi：授权后换上新的 Zotero 客户端，配好大模型后换上新的客户端
        *,
        base: str = ZOTERO,
        http: httpx.Client | None = None,
        env_file: Path | None = None,
        key_file: Path | None = None,
        roots: list[Path] | None = None,
        llm_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.store = store
        self.api = api
        self.base = base.rstrip("/")
        self.http = http or httpx.Client(timeout=5.0)
        self.env_file = env_file or CONFIG_DIR / ".env"
        self.key_file = key_file or CONFIG_DIR / "zotero_key"
        self.roots = roots if roots is not None else profile_roots()
        self.llm_transport = llm_transport

    # ---------- 状态 ----------

    def done(self) -> bool:
        if (repo.get_json(self.store, "setup") or {}).get("done"):
            return True
        # 以前用命令行装好的：有写入权限、填了研究兴趣，就算设置过了
        return self.key_file.exists() and bool(repo.load_settings(self.store).get("interests"))

    def zotero(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "running": False,
            "version": None,
            "versionOk": None,
            "localApi": False,
        }
        try:
            r = self.http.get(f"{self.base}/connector/ping")
        except httpx.HTTPError:
            return out
        out["running"] = r.status_code == 200
        version = r.headers.get("X-Zotero-Version")
        if version:
            out["version"] = version
            out["versionOk"] = (
                int(re.match(r"\d+", version).group(0)) >= MIN_ZOTERO
                if re.match(r"\d+", version)
                else None
            )
        try:
            out["localApi"] = self.http.get(f"{self.base}/api/").status_code == 200
        except httpx.HTTPError:
            pass
        return out

    def status(self) -> dict[str, Any]:
        s = repo.load_settings(self.store)
        z = self.zotero()
        key = os.environ.get("ROBOT_TIMES_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        return {
            "done": self.done(),
            "zotero": {
                **z,
                "authorized": bool(getattr(self.api.zotero, "api_key", None)),
                "canEnable": find_prefs(self.roots) is not None,
            },  # fmt: skip
            "settings": {
                k: s.get(k) for k in ("interests", "categories", "preset", "zoteroCollection")
            },
            "llm": {
                "configured": bool(key),
                "base": os.environ.get("ROBOT_TIMES_LLM_BASE_URL") or DEEPSEEK_BASE,
                "model": os.environ.get("ROBOT_TIMES_LLM_MODEL") or DEEPSEEK_MODEL,
            },
        }

    # ---------- 第 1 步：打开本地接口 ----------

    def enable_local_api(self) -> dict[str, Any]:
        if self.zotero()["running"]:
            raise SetupError("请先完全退出 Zotero（不是最小化），再点这个按钮。")
        prefs = find_prefs(self.roots)
        if prefs is None:
            raise SetupError(
                "没找到 Zotero 的配置文件夹：请先打开一次 Zotero，或者按页面上的说明手动勾选。"
            )
        enable_pref(prefs)
        return {"ok": True, "prefs": str(prefs)}

    # ---------- 第 2 步：授权 ----------

    def authorize(self) -> dict[str, Any]:
        z = ZoteroLocalClient(
            f"{self.base}/api",
            app_name="The Robot Times",
            timeout=180.0,  # 等用户在 Zotero 里点
        )
        try:
            key = z.authorize()
        except ZoteroError as e:
            raise SetupError(str(e)) from None
        except httpx.HTTPError:
            raise SetupError(
                "连不上 Zotero，或者等太久没点：请切到 Zotero，在弹窗里点 Always Allow。"
            ) from None
        if not z.key_is_persistent:
            raise SetupError(
                "你点的是 Allow（只允许一次）。请再点一次“连接 Zotero”，这次选 Always Allow。"
            )
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text(key, encoding="utf-8")
        try:
            self.key_file.chmod(0o600)
        except OSError:
            pass
        current = self.api.zotero
        if current is not None:
            current.api_key, current.key_is_persistent = key, True
        else:
            self.api.zotero = ZoteroLocalClient(f"{self.base}/api", api_key=key)
        return {"ok": True}

    def collections(self) -> list[str]:
        z = self.api.zotero or ZoteroLocalClient(f"{self.base}/api")
        try:
            names = {str(c.get("data", {}).get("name", "")).strip() for c in z.collections()}
        except (ZoteroError, httpx.HTTPError):
            raise SetupError(
                "读不到 Zotero 的文件夹：请确认 Zotero 开着、已经允许本机程序访问。"
            ) from None
        return sorted(n for n in names if n)

    # ---------- 第 4 步：大模型 ----------

    def save_llm(self, b: dict[str, Any]) -> dict[str, Any]:
        key = str(b.get("key") or "").strip()
        if not key:
            raise SetupError("请粘贴密钥。")
        base = str(b.get("base") or "").strip().rstrip("/") or DEEPSEEK_BASE
        model = str(b.get("model") or "").strip() or DEEPSEEK_MODEL
        if not base.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise SetupError("接口地址要以 https:// 开头（本机的模型可以用 http://localhost）。")
        client = LLMClient(base, key, model, transport=self.llm_transport, timeout=60.0)
        try:
            client.complete("只回复两个字：可以", system="", max_tokens=200)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            raise SetupError({401: "密钥不对，请检查有没有复制完整。", 402: "账户余额不足，先充一点再试。",
                              404: "接口地址或模型名不对。", 429: "请求太频繁，等一会儿再试。"}.get(
                code, f"大模型返回了错误：HTTP {code}")) from None  # fmt: skip
        except httpx.HTTPError as e:
            raise SetupError(f"连不上大模型接口：{type(e).__name__}") from None
        custom = base != DEEPSEEK_BASE or model != DEEPSEEK_MODEL
        values = (
            {
                "ROBOT_TIMES_LLM_API_KEY": key,
                "ROBOT_TIMES_LLM_BASE_URL": base,
                "ROBOT_TIMES_LLM_MODEL": model,
            }
            if custom
            else {"DEEPSEEK_API_KEY": key}
        )
        write_env(self.env_file, values)
        for k in LLM_KEYS:  # 换掉进程里的旧设置，马上生效
            os.environ.pop(k, None)
        os.environ.update(values)
        if hasattr(self.api, "reload_llm"):
            self.api.reload_llm()
        return {"ok": True}

    # ---------- 完成 ----------

    def finish(self, b: dict[str, Any]) -> dict[str, Any]:
        if not str(b.get("interests") or "").strip():
            raise SetupError("写一两句研究兴趣吧，推荐主要靠它和你的 Zotero 文献库。")
        changes = {
            k: b[k] for k in ("interests", "categories", "preset", "zoteroCollection") if k in b
        }
        s = repo.update_settings(repo.load_settings(self.store), changes)
        repo.save_settings(self.store, s)
        repo.set_json(self.store, "setup", {"done": True, "at": repo.now_iso()})
        if getattr(self.api, "jobs", None) is not None:
            self.api.jobs.auto()  # 马上开始生成今天的推荐
        return {"ok": True}


def write_env(path: Path, values: dict[str, str]) -> None:
    """改 .env 里的大模型设置，其他行保留；只有自己能读。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keep = [ln for ln in lines if ln.split("=", 1)[0].strip() not in LLM_KEYS]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([*keep, *(f"{k}={v}" for k, v in values.items())]) + "\n", encoding="utf-8"
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
