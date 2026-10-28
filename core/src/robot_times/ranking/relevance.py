"""相关度：候选论文和用户 Zotero 库有多像。

做法：每篇候选只和库里最像的 k 篇比较（而不是和整库平均），这样小众方向也能被识别；
越近加入库的文献权重越高（半衰期默认 180 天），反映用户“现在”的兴趣。
最后按候选之间的百分位换算成 0–10 分，保证每天的分数可比。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np


def recency_weights(
    added: Sequence[datetime | None], now: datetime, half_life_days: float = 180.0
) -> np.ndarray:
    ages = np.array(
        [((now - a).total_seconds() / 86400.0) if a else 3 * half_life_days for a in added],
        dtype=np.float64,
    )
    return np.power(0.5, np.clip(ages, 0, None) / half_life_days)


def raw_relevance(
    candidates: np.ndarray, library: np.ndarray, weights: np.ndarray, k: int = 5
) -> np.ndarray:
    """返回每篇候选的加权 top-k 相似度，范围大致在 [-1, 1]。"""
    if len(candidates) == 0:
        return np.zeros(0)
    if len(library) == 0:
        return np.zeros(len(candidates))
    sims = candidates @ library.T  # (C, L)
    k = min(k, library.shape[0])
    idx = np.argpartition(-sims, k - 1, axis=1)[:, :k]
    top_sims = np.take_along_axis(sims, idx, axis=1)
    top_w = weights[idx]
    return (top_sims * top_w).sum(axis=1) / np.clip(top_w.sum(axis=1), 1e-9, None)


def to_ten_point(raw: np.ndarray) -> np.ndarray:
    """按百分位换算成 0–10 分。"""
    n = len(raw)
    if n == 0:
        return raw
    if n == 1:
        return np.array([10.0])
    ranks = raw.argsort().argsort()
    return np.round(ranks / (n - 1) * 10.0, 2)


def combine(
    relevance: float,
    impact: float | None,
    buzz: float | None,
    weights: dict[str, float],
) -> float:
    """按权重合成综合分；缺失的分项不参与，剩下的权重重新归一化。"""
    parts = {"relevance": relevance, "impact": impact, "buzz": buzz}
    used = {k: v for k, v in parts.items() if v is not None and weights.get(k, 0) > 0}
    total_w = sum(weights[k] for k in used)
    if total_w == 0:
        return relevance
    return round(sum(v * weights[k] for k, v in used.items()) / total_w, 2)
