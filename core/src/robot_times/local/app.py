"""把本地版的各部分接起来：数据库、Zotero、大模型、后台任务、看板接口。

配置都在 ~/.robot-times（可以用环境变量 ROBOT_TIMES_HOME 换地方）：
  robot-times.db     数据库：推荐、笔记、设置、业界简报
  zotero_key    Zotero 写入权限（robot-times local init 时在 Zotero 弹窗里点 Always Allow 得到）
  .env          可选：大模型密钥，例如 DEEPSEEK_API_KEY=...；还可以填 ROBOT_TIMES_LLM_BASE_URL、
                ROBOT_TIMES_LLM_API_KEY、ROBOT_TIMES_LLM_MODEL 换成其他 OpenAI 兼容的模型
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from ..cloud import repo
from ..cloud.env import DEEPSEEK_BASE, DEEPSEEK_MODEL, llm_from_env, settings_fallback_from_env
from ..cloud.store import SqliteStore
from ..config import CONFIG_DIR
from ..zotero.local_api import ZoteroLocalClient
from .api import LocalApi, stream_chat
from .jobs import Jobs, weekly_target

log = logging.getLogger("robot_times.local")

DB_FILE = CONFIG_DIR / "robot-times.db"
KEY_FILE = CONFIG_DIR / "zotero_key"
ENV_FILE = CONFIG_DIR / ".env"
ZOTERO_URL = "http://localhost:23119/api"


def load_env() -> None:
    """先读 ~/.robot-times/.env，再读当前目录的 .env；已经设过的环境变量不覆盖。"""
    for f in (ENV_FILE, Path(".env")):
        if f.exists():
            load_dotenv(f, override=False)


def zotero_client() -> ZoteroLocalClient:
    key = KEY_FILE.read_text(encoding="utf-8").strip() if KEY_FILE.exists() else None
    url = os.environ.get("ROBOT_TIMES_ZOTERO_URL", ZOTERO_URL)
    return ZoteroLocalClient(url, app_name="The Robot Times", api_key=key or None)


def save_zotero_key(key: str) -> None:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key, encoding="utf-8")
    try:
        KEY_FILE.chmod(0o600)
    except OSError:  # Windows 上没有这种权限位
        pass


def open_store(path: Path | None = None) -> SqliteStore:
    return SqliteStore(path or Path(os.environ.get("ROBOT_TIMES_DB") or DB_FILE))


def _settings(store) -> dict[str, Any]:
    return repo.load_settings(store, settings_fallback_from_env())


def make_runners(store, zotero: ZoteroLocalClient, max_results: int = 800) -> dict[str, Any]:
    """三个任务：每日推荐、业界简报、周报。每个返回一句结果。"""

    def daily(force: bool = False) -> str:
        from ..cloud.daily import DailyDeps, run_daily
        from ..ranking.embedder import default_embedder
        from ..signals import hf_upvotes
        from ..sources.arxiv_html import ArxivHtml
        from ..sources.arxiv_source import fetch_recent

        if not zotero.ping():
            raise RuntimeError("连不上 Zotero：请先打开 Zotero，再在看板上点“现在生成”。")
        deps = DailyDeps(
            store=store,
            zotero=zotero,
            embedder=default_embedder(
                os.environ.get("ROBOT_TIMES_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
            ),
            fetch_papers=lambda cats, days: fetch_recent(cats, days=days, max_results=max_results),
            hf_upvotes=hf_upvotes,
            llm=llm_from_env(),
            html=ArxivHtml(),
        )
        doc = run_daily(deps, _settings(store))
        if doc.get("skipped"):
            return doc["headline"]
        return f"{len(doc['picks'])} 篇推荐：{doc['headline']}"

    def news(force: bool = False) -> str:
        from ..cloud.daily import today_local
        from ..news.digest import run_news
        from ..news.weather import save_weather

        settings = _settings(store)
        day = today_local()
        save_weather(store, day, str(settings.get("weatherCity") or ""))
        doc = run_news(store, llm_from_env(), settings, day)
        return f"快讯 {len(doc['news'])} 条，深度 {len(doc['deep'])} 篇"

    def weekly(force: bool = False) -> str:
        from ..cloud.daily import TZ, today_local
        from ..cloud.weekly import run_weekly

        day: date = today_local() if force else weekly_target(datetime.now(TZ))
        week = run_weekly(store, llm_from_env(), _settings(store), day)
        return week.get("headline", "")

    return {"daily": daily, "news": news, "weekly": weekly}


def build(store: SqliteStore | None = None, *, background: bool = True) -> LocalApi:
    load_env()
    store = store or open_store()
    zotero = zotero_client()
    key = os.environ.get("ROBOT_TIMES_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    llm_stream = None
    if key:
        llm_stream = stream_chat(
            os.environ.get("ROBOT_TIMES_LLM_BASE_URL") or DEEPSEEK_BASE,
            key,
            os.environ.get("ROBOT_TIMES_LLM_MODEL") or DEEPSEEK_MODEL,
        )
    jobs = Jobs(store, make_runners(store, zotero), background=background)
    pdf = os.environ.get("ROBOT_TIMES_ZOTERO_PDF", "1").lower() not in ("0", "false", "no")
    return LocalApi(
        store,
        zotero=zotero,
        llm=llm_from_env(),
        llm_stream=llm_stream,
        jobs=jobs,
        pdf=pdf,
        http=httpx.Client(timeout=60.0, follow_redirects=True),
    )
