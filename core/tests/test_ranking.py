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


def test_llm_judged_papers_are_not_pushed_out_by_unjudged(monkeypatch):
    # 大模型只审前 LLM_CANDIDATES 篇；它老实打了低分时，没审过的论文不能靠向量百分位分挤进来
    import json
    import re

    import httpx

    from robot_times import pipeline
    from robot_times.llm.rerank import LLMClient

    def handler(request):
        prompt = json.loads(request.content)["messages"][-1]["content"]
        ids = re.findall(r"^\[(\d{4}\.\d{5})\]", prompt, re.M)
        reply = json.dumps({"papers": [{"id": i, "score": 3, "summary_zh": "s"} for i in ids]})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    monkeypatch.setattr(pipeline, "LLM_CANDIDATES", 2)
    llm = LLMClient("https://llm.example/v1", "k", "m", transport=httpx.MockTransport(handler))
    cands = [
        paper("2610.00001", "Humanoid whole-body control", "humanoid robot motion imitation"),
        paper("2610.00002", "Hexapod gait on rough terrain", "six-legged robot gait"),
        paper("2610.00003", "World model for control", "latent world model robot"),
        paper("2610.00004", "Protein folding", "diffusion for proteins"),
        paper("2610.00005", "Math reasoning", "chain of thought"),
    ]
    out = recommend(cands, LIBRARY, HashingEmbedder(), llm=llm, limit=2, now=NOW)
    assert len(out) == 2
    assert all(sp.llm_score is not None for sp in out)


def test_default_embedder_falls_back_in_order(monkeypatch):
    # 装了 sentence-transformers 用它；没装用 fastembed；模型加载失败（比如下载不了）退回词袋，当天照样出推荐
    import warnings

    from robot_times.ranking import embedder as emb

    def missing(name):
        raise ImportError(name)

    def offline(name):
        raise OSError("连不上 Hugging Face")

    monkeypatch.setattr(emb, "SentenceTransformerEmbedder", missing)
    monkeypatch.setattr(emb, "FastEmbedEmbedder", lambda name: HashingEmbedder(dim=8))
    assert emb.default_embedder().dim == 8
    monkeypatch.setattr(emb, "FastEmbedEmbedder", offline)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert isinstance(emb.default_embedder(), HashingEmbedder)
    assert "连不上" in str(w[0].message)
