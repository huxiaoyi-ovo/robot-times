from datetime import UTC, date, datetime, timedelta

import numpy as np

from robot_times.models import Paper
from robot_times.pipeline import arxiv_ids_in_library, recommend
from robot_times.ranking.embedder import HashingEmbedder
from robot_times.ranking.relevance import combine, recency_weights, to_ten_point

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def lib_item(title: str, abstract: str = "", days_ago: int = 10, **extra) -> dict:
    added = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "data": {
            "itemType": "journalArticle",
            "title": title,
            "abstractNote": abstract,
            "dateAdded": added,
            **extra,
        }
    }


def paper(pid: str, title: str, abstract: str) -> Paper:
    return Paper(
        id=pid, title=title, abstract=abstract, authors=["A B"], published=date(2026, 10, 24)
    )


LIBRARY = [
    lib_item(
        "Learning humanoid whole-body control via motion imitation",
        "We train a humanoid robot to track human motion capture with reinforcement learning.",
    ),
    lib_item(
        "Hexapod locomotion on uneven terrain",
        "Gait adaptation for a six-legged robot on rough terrain.",
    ),
    lib_item(
        "World models for robot learning",
        "A latent world model predicts future states for control.",
    ),
]


def test_relevant_paper_ranks_first():
    cands = [
        paper(
            "2610.00001",
            "Protein folding with diffusion",
            "We fold proteins with a diffusion model.",
        ),
        paper(
            "2610.00002",
            "Humanoid motion imitation for whole-body control",
            "Reinforcement learning lets a humanoid robot track human motion.",
        ),
        paper(
            "2610.00003", "Large language model reasoning", "Chain of thought prompting for math."
        ),
    ]
    out = recommend(cands, LIBRARY, HashingEmbedder(), now=NOW)
    assert out[0].paper.id == "2610.00002"
    assert out[0].relevance == 10.0


def test_known_ids_are_skipped():
    cands = [paper("2610.00002", "Humanoid", "humanoid robot")]
    assert recommend(cands, LIBRARY, HashingEmbedder(), known_ids={"2610.00002"}, now=NOW) == []


def test_recency_weights_halve_each_half_life():
    w = recency_weights([NOW, NOW - timedelta(days=180), None], NOW, half_life_days=180)
    assert np.allclose(w[:2], [1.0, 0.5])
    assert w[2] < 0.2


def test_ten_point_scale_is_rank_based():
    assert list(to_ten_point(np.array([0.1, 0.9, 0.5]))) == [0.0, 10.0, 5.0]


def test_combine_skips_missing_parts():
    w = {"relevance": 0.5, "impact": 0.3, "buzz": 0.2}
    assert combine(8.0, None, None, w) == 8.0
    assert combine(8.0, 6.0, None, w) == round((8 * 0.5 + 6 * 0.3) / 0.8, 2)


def test_arxiv_ids_found_in_library():
    items = [
        lib_item("a", archiveID="arXiv:2610.31608"),
        lib_item("b", url="https://arxiv.org/abs/2602.16511v2"),
        lib_item("c", url="https://example.com/2610.99999"),
    ]
    assert arxiv_ids_in_library(items) == {"2610.31608", "2602.16511"}
