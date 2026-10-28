"""数据模型：论文和打过分的论文。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class Paper:
    id: str  # arXiv 编号，不带版本号，例如 "2610.31608"
    title: str
    abstract: str
    authors: list[str]
    published: date
    categories: list[str] = field(default_factory=list)
    url: str = ""
    pdf_url: str | None = None
    source: str = "arxiv"
    venue: str | None = None
    comment: str | None = None  # arXiv 的 comments 字段，常写着“Accepted to ICRA 2027”

    def text_for_embedding(self) -> str:
        return f"{self.title}. {self.abstract}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Paper:
        d = dict(d)
        d["published"] = date.fromisoformat(d["published"])
        return cls(**d)


@dataclass
class ScoredPaper:
    paper: Paper
    relevance: float  # 0-10，和用户 Zotero 库的相似度
    impact: float | None = None  # 0-10，作者组 / venue / 引用（之后接入）
    buzz: float | None = None  # 0-10，社区热度（之后接入）
    llm_score: float | None = None  # 0-10，LLM 精排给的分
    summary_zh: str = ""
    why_zh: str = ""
    tip_zh: str = ""  # 怎么读：先看哪张图、留意什么
    related: list[str] = field(default_factory=list)  # 文献库里最接近的几篇的标题
    topic: str = ""  # 大模型标的方向（人形、足式、操作……），用来限制同一方向一天最多几篇

    def to_dict(self) -> dict:
        d = asdict(self)
        d["paper"] = self.paper.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ScoredPaper:
        d = dict(d)
        d["paper"] = Paper.from_dict(d["paper"])
        return cls(**d)
