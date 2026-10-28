"""配置：从环境变量和项目根目录的 .env 读取。所有项都有默认值，最少只需要开着 Zotero。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

CONFIG_DIR = Path(os.environ.get("ROBOT_TIMES_HOME", Path.home() / ".robot-times"))


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.replace("，", ",").split(",") if v.strip()]


@dataclass
class Settings:
    arxiv_categories: list[str] = field(default_factory=lambda: ["cs.RO"])
    interests: str = ""
    zotero_url: str = "http://localhost:23119/api"
    zotero_tag: str = "Robot Times"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    embed_model: str = "BAAI/bge-small-en-v1.5"
    weights: dict[str, float] = field(
        default_factory=lambda: {"relevance": 0.5, "impact": 0.3, "buzz": 0.2}
    )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @classmethod
    def load(cls, env_file: str | Path | None = ".env") -> Settings:
        if env_file and Path(env_file).exists():
            load_dotenv(env_file, override=False)
        e = os.environ.get
        s = cls()
        if e("ROBOT_TIMES_ARXIV_CATEGORIES"):
            s.arxiv_categories = _split(e("ROBOT_TIMES_ARXIV_CATEGORIES", ""))
        s.interests = e("ROBOT_TIMES_INTERESTS", s.interests)
        s.zotero_url = e("ROBOT_TIMES_ZOTERO_URL", s.zotero_url).rstrip("/")
        s.zotero_tag = e("ROBOT_TIMES_ZOTERO_TAG", s.zotero_tag)
        s.llm_base_url = e("ROBOT_TIMES_LLM_BASE_URL", s.llm_base_url).rstrip("/")
        s.llm_api_key = e("ROBOT_TIMES_LLM_API_KEY", s.llm_api_key)
        s.llm_model = e("ROBOT_TIMES_LLM_MODEL", s.llm_model)
        s.embed_model = e("ROBOT_TIMES_EMBED_MODEL", s.embed_model)
        return s
