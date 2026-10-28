"""从环境变量（GitHub Actions 的 Secrets）组装云端任务需要的东西。

必填：
  ZOTERO_USER_ID, ZOTERO_API_KEY            zotero.org/settings/keys 生成，key 要能写入
  CF_ACCOUNT_ID, CF_API_TOKEN, D1_DATABASE_ID   Cloudflare D1（token 需要 D1 编辑权限）
可选：
  DEEPSEEK_API_KEY                           有了就用 DeepSeek 精排、写中文摘要和周报
  ROBOT_TIMES_LLM_BASE_URL / ROBOT_TIMES_LLM_API_KEY / ROBOT_TIMES_LLM_MODEL   换成其他 OpenAI 兼容模型
  ROBOT_TIMES_ARXIV_CATEGORIES, ROBOT_TIMES_INTERESTS  第一次运行时的默认设置（之后在看板里改）
  ROBOT_TIMES_STORE=sqlite:路径                    本地调试时不用 D1
"""

from __future__ import annotations

import os

from ..config import _split
from ..llm.rerank import LLMClient
from .store import D1Store, SqliteStore, Store

DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"


def store_from_env() -> Store:
    spec = os.environ.get("ROBOT_TIMES_STORE", "")
    if spec.startswith("sqlite:"):
        return SqliteStore(spec.removeprefix("sqlite:"))
    return D1Store(
        os.environ.get("CF_ACCOUNT_ID", ""),
        os.environ.get("D1_DATABASE_ID", ""),
        os.environ.get("CF_API_TOKEN", ""),
    )


def llm_from_env() -> LLMClient | None:
    key = os.environ.get("ROBOT_TIMES_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    base = os.environ.get("ROBOT_TIMES_LLM_BASE_URL") or DEEPSEEK_BASE
    model = os.environ.get("ROBOT_TIMES_LLM_MODEL") or DEEPSEEK_MODEL
    return LLMClient(base, key, model)


def settings_fallback_from_env() -> dict:
    out: dict = {}
    if os.environ.get("ROBOT_TIMES_ARXIV_CATEGORIES"):
        out["categories"] = _split(os.environ["ROBOT_TIMES_ARXIV_CATEGORIES"])
    if os.environ.get("ROBOT_TIMES_INTERESTS"):
        out["interests"] = os.environ["ROBOT_TIMES_INTERESTS"]
    return out
