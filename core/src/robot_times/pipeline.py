"""推荐流水线：候选论文 + 用户文献库 → 排好序的推荐。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .llm.rerank import LLMClient, rerank
from .models import Paper, ScoredPaper
from .presets import get_preset
from .ranking.embedder import Embedder
from .ranking.relevance import combine, raw_relevance, recency_weights, to_ten_point
from .zotero.local_api import item_text

LLM_CANDIDATES = 60  # 只把向量粗筛后的前 60 篇交给 LLM（要给多样性留出别的方向的候选），控制成本


def _parse_added(item: dict[str, Any]) -> datetime | None:
    value = item.get("data", {}).get("dateAdded")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def recommend(
    candidates: list[Paper],
    library_items: list[dict[str, Any]],
    embedder: Embedder,
    *,
    weights: dict[str, float] | None = None,
    llm: LLMClient | None = None,
    interests: str = "",
    known_ids: set[str] | None = None,
    now: datetime | None = None,
    liked_texts: Sequence[str] = (),
    skipped_texts: Sequence[str] = (),
    annotate: Callable[[ScoredPaper], None] | None = None,
    llm_errors: list[str] | None = None,
    limit: int | None = None,
    diversity: float = 0.0,
    preset: dict[str, Any] | None = None,
) -> list[ScoredPaper]:
    """给候选论文打分排序。

    liked_texts：你点过“喜欢”或“加入 Zotero”的论文，当作刚加入文献库的条目参与相似度；
    skipped_texts：你点过“跳过”的论文，和它们越像扣分越多；
    annotate：在 LLM 精排前给每篇补上影响力、热度等分项。
    limit、diversity：只要前 limit 篇时，按 diversity（0–1）压低和已选论文太像的，免得一天全是同一个方向。
    preset：方向预设（写作人设和方向标签），默认机器人。
    """
    now = now or datetime.now(UTC)
    preset = preset or get_preset()
    weights = weights or {"relevance": 0.5, "impact": 0.3, "buzz": 0.2}
    known_ids = known_ids or set()
    candidates = [p for p in candidates if p.id not in known_ids]
    if not candidates:
        return []

    library_items = [it for it in library_items if item_text(it).strip(". ")]
    lib_texts = [item_text(it) for it in library_items] + list(liked_texts)
    lib_dates = [_parse_added(it) for it in library_items] + [now] * len(liked_texts)
    lib_vecs = embedder.encode(lib_texts)
    lib_w = recency_weights(lib_dates, now)
    cand_vecs = embedder.encode([p.text_for_embedding() for p in candidates])
    raw = raw_relevance(cand_vecs, lib_vecs, lib_w)
    if skipped_texts:
        skip_vecs = embedder.encode(list(skipped_texts))
        raw = raw - 0.5 * np.clip((cand_vecs @ skip_vecs.T).max(axis=1), 0, None)
    rel = to_ten_point(raw)

    scored = [
        ScoredPaper(paper=p, relevance=float(r)) for p, r in zip(candidates, rel, strict=True)
    ]
    if annotate is not None:
        for sp in scored:
            annotate(sp)
    scored.sort(key=lambda sp: total_score(sp, weights), reverse=True)

    if llm is not None:
        top = scored[:LLM_CANDIDATES]
        lib_titles = [it.get("data", {}).get("title", "") for it in library_items] + [
            t.split(". ")[0] for t in liked_texts
        ]
        index = {id(p): i for i, p in enumerate(candidates)}
        sims = cand_vecs[[index[id(sp.paper)] for sp in top]] @ lib_vecs.T
        for sp, row in zip(top, sims, strict=True):
            sp.related = [lib_titles[j] for j in np.argsort(-row)[:2] if lib_titles[j]]
        rerank(llm, top, interests, errors=llm_errors, preset=preset)

    scored.sort(key=lambda sp: total_score(sp, weights), reverse=True)
    if limit is not None:
        index = {id(p): i for i, p in enumerate(candidates)}
        vecs = cand_vecs[[index[id(sp.paper)] for sp in scored]]
        scored = diversify(scored, vecs, limit, diversity, weights, preset)
    return scored


# 大模型没看过的论文（排在精排范围外）按标题和摘要的关键词补一个方向，免得绕过“每个方向最多几篇”
def guess_topic(paper: Paper, preset: dict[str, Any] | None = None) -> str:
    text = f"{paper.title} {paper.abstract[:600]}".lower()
    for topic, pattern in (preset or get_preset())["topic_words"]:
        if re.search(pattern, text):
            return topic
    return "其他"


def diversify(
    scored: list[ScoredPaper],
    vecs: np.ndarray,
    k: int,
    diversity: float,
    weights: dict[str, float],
    preset: dict[str, Any] | None = None,
) -> list[ScoredPaper]:
    """MMR 选前 k 篇：每次选“综合分 − diversity × 10 × 和已选论文的最大相似度”最高的。

    diversity=0 就是按综合分排；0.4 左右时，同一方向的第三、四篇会让位给分数稍低的其他方向。
    返回的顺序仍按综合分排，方便后面分精读、看摘要、扫读。
    """
    if diversity <= 0 or len(scored) <= k:
        return scored[:k]
    for sp in scored:
        if not sp.topic:
            sp.topic = guess_topic(sp.paper, preset)
    # 同一方向（大模型标的 topic）一天最多几篇：多样性 0.4 时 12 篇里最多 4 篇
    cap = max(2, round(k * (0.5 - 0.4 * diversity)))
    per_topic: dict[str, int] = {}
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    unit = vecs / np.where(norms == 0, 1, norms)
    base = np.array([total_score(sp, weights) for sp in scored])
    chosen: list[int] = []
    max_sim = np.zeros(len(scored))
    remaining = set(range(len(scored)))
    while len(chosen) < k and remaining:
        idx = [
            i
            for i in sorted(remaining)
            if per_topic.get(scored[i].topic, 0) < cap or not scored[i].topic
        ]
        if not idx:  # 剩下的方向都满了：上限加一，继续均匀地补
            cap += 1
            continue
        gain = base[idx] - diversity * 10 * np.clip(max_sim[idx], 0, None)
        best = idx[int(np.argmax(gain))]
        chosen.append(best)
        remaining.discard(best)
        if scored[best].topic:
            per_topic[scored[best].topic] = per_topic.get(scored[best].topic, 0) + 1
        max_sim = np.maximum(max_sim, unit @ unit[best])
    return sorted(
        (scored[i] for i in chosen), key=lambda sp: total_score(sp, weights), reverse=True
    )


def total_score(sp: ScoredPaper, weights: dict[str, float]) -> float:
    # 有大模型判断时更信它：向量相似度只看“像不像你的库”，大模型能看出“值不值得你读”
    rel_score = sp.relevance if sp.llm_score is None else 0.3 * sp.relevance + 0.7 * sp.llm_score
    return combine(rel_score, sp.impact, sp.buzz, weights)


def arxiv_ids_in_library(library_items: list[dict[str, Any]]) -> set[str]:
    """从文献库里找出已有的 arXiv 编号，避免重复推荐。"""
    ids: set[str] = set()
    for it in library_items:
        d = it.get("data", {})
        for field in ("archiveID", "url", "DOI", "extra"):
            v = str(d.get(field, ""))
            if "arxiv" in v.lower():
                ids.update(_ARXIV_ID.findall(v))
    return ids


_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
