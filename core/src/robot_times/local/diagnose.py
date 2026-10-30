"""出了问题能看懂、能追溯、知道怎么办：

- explain()：把一条报错翻译成“发生了什么 + 怎么办 + 可以点的按钮”，任务失败、接口报错都用它
- Doctor：设置页的“体检”，一项项检查 Zotero、大模型、网络、磁盘、版本，每项不对都带修复按钮
- 日志：看板上直接看 serve.log / setup.log 的最后几百行（密钥会打码）
- 诊断信息：一键复制，贴到 GitHub issue 里就能定位问题

按钮（actions）是给页面用的标识：
  retry:<任务>   重新生成            setup   打开设置引导（连 Zotero、配大模型）
  logs          查看日志            doctor  去体检          restart  重启看板
  update        更新到最新版本      url:<地址>  打开一个网页
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx

from .. import __version__
from ..cloud import repo
from ..config import CONFIG_DIR

LOGS = {"serve": "serve.log", "setup": "setup.log"}
ISSUES = "https://github.com/huxiaoyi-ovo/robot-times/issues/new"
HISTORY = 20  # 记住最近多少次任务结果

# ---------- 报错 → 原因和办法 ----------

# (匹配报错的正则, 标题, 怎么办, 按钮)；从上往下，第一条匹配的生效
RULES: list[tuple[str, str, str, list[str]]] = [
    (
        r"连不上 Zotero|localhost:23119|Zotero 已经打开|ConnectError.*23119",
        "连不上 Zotero",
        "打开 Zotero 后点“重试”。Zotero 开着还不行，就去设置引导里检查“允许本机程序访问”。",
        ["retry", "setup"],
    ),
    (
        r"Zotero.*(\b403\b|拒绝|denied|没有权限)|Always Allow|写入权限",
        "Zotero 没有给写入权限",
        "去设置引导里点“重新连接”，在 Zotero 弹窗里选 Always Allow。",
        ["setup"],
    ),
    (
        r"找不到名为“.*”的文件夹",
        "Zotero 里没有这个文件夹",
        "在 Zotero 里建好这个文件夹，或者到设置页把文件夹改成已有的（留空就放在“我的文库”）。",
        ["setup"],
    ),
    (
        r"\b401\b|密钥不对|Unauthorized|invalid api key|拒绝了请求",
        "大模型密钥不对",
        "去设置引导里换一个密钥（会马上测试能不能用）。",
        ["setup"],
    ),
    (
        r"\b402\b|余额不足|Insufficient Balance",
        "大模型账户余额不足",
        "到大模型的网站充值，充好后点“重试”。",
        ["url:https://platform.deepseek.com/top_up", "retry"],
    ),
    (
        r"\b429\b|Too Many Requests|rate limit|太频繁",
        "请求太频繁，被限流了",
        "arXiv 或大模型暂时限流，过半小时再点“重试”，一般就好了。",
        ["retry"],
    ),
    (
        r"还没配置大模型|未配置.*大模型",
        "还没配大模型",
        "去设置引导里填密钥；不填也能用，只是没有中文解读。",
        ["setup"],
    ),
    (
        r"ConnectError|ConnectTimeout|ReadTimeout|NameResolution|getaddrinfo|Network is unreachable|"
        r"连不上|timed out|RemoteProtocolError|SSL",
        "网络连不上",
        "检查一下网络（arXiv、大模型接口要能访问；用代理的话确认代理开着），然后点“重试”。",
        ["retry", "doctor"],
    ),
    (r"No space left|磁盘", "磁盘满了", "清理一下磁盘空间再试。", ["doctor"]),
    (r"database is locked|sqlite", "数据库被占用", "可能开了两个看板。点“重启看板”。", ["restart"]),
]


def explain(message: str, job: str | None = None) -> dict[str, Any]:
    """一条报错 → {title, hint, actions}。认不出来的给通用办法：看日志、体检、报告问题。"""
    text = str(message or "")
    hit = next((r for r in RULES if re.search(r[0], text, re.I)), None)
    if hit:
        _, title, hint, actions = hit
    else:
        title, hint = (
            "出了点问题",
            "先点“重试”；还不行就看看日志，或者复制诊断信息发到 GitHub 上问。",
        )
        actions = ["retry", "logs"]
    acts = []
    for a in actions:
        if a == "retry":
            if job:
                acts.append(f"retry:{job}")
        else:
            acts.append(a)
    if "logs" not in acts:
        acts.append("logs")
    return {"title": title, "hint": hint, "actions": acts}


# ---------- 任务历史 ----------


def record(store, result: dict[str, Any]) -> None:
    """每次任务的结果都记下来（最近 20 次），体检页里能翻看。"""
    hist = repo.get_json(store, "job_history") or []
    same = hist and all(hist[0].get(k) == result.get(k) for k in ("kind", "ok", "message"))
    if same:  # 同样的失败一再重复（比如 Zotero 一直没开），只留最新的一条
        hist = hist[1:]
    repo.set_json(store, "job_history", [result, *hist][:HISTORY])


def history(store) -> list[dict[str, Any]]:
    return repo.get_json(store, "job_history") or []


# ---------- 日志 ----------

SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+|((?:api[_-]?key|key|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',]{6,}",
                    re.I)  # fmt: skip


def redact(text: str) -> str:
    return SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "…", text)


def tail(name: str, lines: int = 300, home: Path = CONFIG_DIR) -> dict[str, Any]:
    if name not in LOGS:
        raise ValueError(name)
    path = home / LOGS[name]
    if not path.exists():
        return {"name": name, "path": str(path), "text": "", "exists": False}
    with open(path, "rb") as f:  # 只读最后一段，日志很大也不慢
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        data = f.read().decode("utf-8", errors="replace")
    text = "\n".join(data.splitlines()[-max(1, min(lines, 2000)) :])
    return {"name": name, "path": str(path), "text": redact(text), "exists": True}


def log_to_file(log_file: str = "", home: Path = CONFIG_DIR) -> None:
    """看板的日志：一直写进 ~/.robot-times/serve.log（最多 3 个 2 MB 的文件轮换），前台运行时也打到终端。"""
    path = Path(log_file) if log_file else home / LOGS["serve"]
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if not log_file and sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 每个请求一行，太多了


# ---------- 体检 ----------


def _check(id_: str, label: str, ok: bool | None, detail: str = "", hint: str = "",
           actions: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    # ok：True 正常、False 有问题、None 可选项没配（不算错）
    return {"id": id_, "label": label, "ok": ok, "detail": detail, "hint": hint,
            "actions": actions or []}  # fmt: skip


class Doctor:
    def __init__(self, api: Any, *, http: httpx.Client | None = None, home: Path = CONFIG_DIR,
                 net: Callable[[str], bool] | None = None) -> None:  # fmt: skip
        self.api = api
        self.http = http or httpx.Client(timeout=6.0, follow_redirects=True)
        self.home = home
        self.net = net or self._reachable

    def _reachable(self, url: str) -> bool:
        try:
            return self.http.head(url).status_code < 500
        except httpx.HTTPError:
            return False

    def run(self) -> dict[str, Any]:
        api, out = self.api, []
        setup = getattr(api, "setup", None)
        if setup is not None:
            z = setup.zotero()
            if not z["running"]:
                out.append(_check("zotero", "Zotero 打开了", False, "连不上 localhost:23119",
                                  "打开 Zotero（没装的话先装 Zotero 10）。", ["url:https://www.zotero.org/download/"]))  # fmt: skip
            elif z["versionOk"] is False:
                out.append(_check("zotero", "Zotero 版本", False, f"现在是 {z['version']}",
                                  "需要 Zotero 10 或更新版本。", ["url:https://www.zotero.org/download/"]))  # fmt: skip
            elif not z["localApi"]:
                out.append(_check("zotero", "Zotero 允许本机程序访问", False, "",
                                  "去设置引导里打开这个开关。", ["setup"]))  # fmt: skip
            else:
                out.append(_check("zotero", "Zotero 已连接", True, f"版本 {z['version']}"))
        authorized = bool(getattr(api.zotero, "api_key", None))
        out.append(_check("zotero_key", "Zotero 写入权限", authorized, "",
                          "" if authorized else "去设置引导里点“连接 Zotero”，在弹窗里选 Always Allow。",
                          [] if authorized else ["setup"]))  # fmt: skip
        llm = api.llm is not None or api.llm_stream is not None
        out.append(_check("llm", "大模型", True if llm else None, "已配置" if llm else "没配（可选）",
                          "" if llm else "配上才有中文解读和“问 AI”。", [] if llm else ["setup"]))  # fmt: skip
        settings = repo.load_settings(api.store)
        interests = bool(str(settings.get("interests") or "").strip())
        out.append(_check("interests", "研究兴趣", interests, "" if interests else "没填",
                          "" if interests else "填了推荐才准。", [] if interests else ["setup"]))  # fmt: skip
        for id_, label, url in (("arxiv", "能访问 arXiv", "https://export.arxiv.org/"),
                                ("github", "能访问 GitHub（检查更新用）", "https://api.github.com/")):  # fmt: skip
            ok = self.net(url)
            out.append(_check(id_, label, ok, "" if ok else "连不上",
                              "" if ok else "检查网络或代理设置。", []))  # fmt: skip
        free = shutil.disk_usage(self.home if self.home.exists() else Path.home()).free
        out.append(_check("disk", "磁盘空间", free > 500_000_000, f"剩 {free / 1e9:.1f} GB",
                          "" if free > 500_000_000 else "空间不够了，清理一下。"))  # fmt: skip
        u = api.updater.status() if getattr(api, "updater", None) else None
        if u:
            ok = not u["available"]
            out.append(_check("version", "版本", ok, f"v{u['current']}" + ("" if ok else f"，可以更新到 v{u['latest']['version']}"),
                              "", [] if ok else ["update"]))  # fmt: skip
        hist = history(api.store)
        last_fail = next((h for h in hist if not h.get("ok")), None)
        recent_ok = bool(hist) and hist[0].get("ok")
        if hist:
            out.append(_check("jobs", "最近一次生成", bool(recent_ok),
                              (hist[0].get("message") or "成功") if recent_ok else hist[0].get("message", ""),
                              "" if recent_ok else (last_fail or {}).get("hint", ""),
                              [] if recent_ok else (last_fail or {}).get("actions", [])))  # fmt: skip
        return {"checks": out, "ok": all(c["ok"] is not False for c in out), "history": hist}

    def report(self) -> str:
        """复制给别人看的诊断信息（不含密钥）。"""
        d = self.run()
        lines = [
            f"The Robot Times v{__version__}",
            f"系统：{platform.system()} {platform.release()} {platform.machine()}，Python {platform.python_version()}",
            "安装方式：" + ("安装包" if os.environ.get("ROBOT_TIMES_PACKAGED") else "命令行"),
            "",
            "体检：",
            *(f"  {'✓' if c['ok'] else ('·' if c['ok'] is None else '✗')} {c['label']} {c['detail']}" for c in d["checks"]),
            "",
            "最近的任务：",
            *(f"  {h.get('at', '')} {h.get('kind', '')} {'成功' if h.get('ok') else '失败'} {h.get('message', '')}"
              for h in d["history"][:8]),
            "",
            "日志（最后 80 行）：",
            tail("serve", 80, self.home)["text"],
        ]  # fmt: skip
        return redact("\n".join(lines))
