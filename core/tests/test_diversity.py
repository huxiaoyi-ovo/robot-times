"""多样性：同一方向的论文太多时，让位给分数稍低的其他方向。"""

from datetime import date

import numpy as np

from robot_times.models import Paper, ScoredPaper
from robot_times.pipeline import diversify

W = {"relevance": 1.0, "impact": 0.0, "buzz": 0.0}


def sp(pid: str, rel: float) -> ScoredPaper:
    return ScoredPaper(paper=Paper(pid, pid, "", [], date(2026, 10, 26)), relevance=rel)


def test_mmr_lets_other_directions_in():
    # 前四篇都是人形（向量几乎一样），后两篇是别的方向，分数稍低
    scored = [
        sp("h1", 8.0),
        sp("h2", 7.9),
        sp("h3", 7.8),
        sp("h4", 7.7),
        sp("nav", 7.0),
        sp("hex", 6.9),
    ]
    humanoid, nav, hexa = [1, 0.1, 0], [0.1, 1, 0], [0.1, 0, 1]
    vecs = np.array([humanoid, humanoid, humanoid, humanoid, nav, hexa], dtype=float)
    plain = diversify(scored, vecs, 4, 0.0, W)
    assert [s.paper.id for s in plain] == ["h1", "h2", "h3", "h4"]
    mixed = diversify(scored, vecs, 4, 0.4, W)
    assert {s.paper.id for s in mixed} == {"h1", "h2", "nav", "hex"} or {"h1", "nav", "hex"} <= {
        s.paper.id for s in mixed
    }
    # 结果仍按综合分排
    totals = [s.relevance for s in mixed]
    assert totals == sorted(totals, reverse=True)


def test_diversify_keeps_all_when_few_candidates():
    scored = [sp("a", 5.0), sp("b", 4.0)]
    assert len(diversify(scored, np.eye(2), 12, 0.4, W)) == 2


def test_topic_cap_limits_one_direction():
    # 向量看不出区别（全一样），靠大模型标的方向限制：12 篇里人形最多 4 篇
    names = {"h": "人形机器人", "l": "足式与多足", "m": "机械臂操作"}
    scored = [sp(f"h{i}", 9 - i * 0.1) for i in range(10)]
    scored += [sp(f"l{i}", 6 - i * 0.1) for i in range(4)]
    scored += [sp(f"m{i}", 5.5 - i * 0.1) for i in range(4)]
    for s in scored:
        s.topic = names[s.paper.id[0]]
    picked = diversify(scored, np.ones((len(scored), 3)), 12, 0.4, W)
    assert len(picked) == 12
    assert sum(s.topic == "人形机器人" for s in picked) == 4
    # 其他方向不够时，上限逐步放宽补满
    more = diversify(scored[:14], np.ones((14, 3)), 12, 0.4, W)
    assert len(more) == 12
    assert sum(s.topic == "人形机器人" for s in more) == 8


def test_guess_topic_for_papers_the_llm_did_not_see():
    from robot_times.pipeline import guess_topic

    def paper(title):
        return Paper("x", title, "", [], date(2026, 10, 26))

    assert guess_topic(paper("ViT-H: Generative Video-To-Humanoid Control")) == "人形机器人"
    assert guess_topic(paper("Hexapod gait adaptation on rough terrain")) == "足式与多足"
    assert guess_topic(paper("A note on category theory")) == "其他"
