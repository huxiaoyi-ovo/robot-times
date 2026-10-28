import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx

from robot_times.llm.rerank import LLMClient, parse_json_array, rerank
from robot_times.models import Paper, ScoredPaper
from robot_times.report import evaluate_markdown, load_run, save_run
from robot_times.sources.arxiv_source import build_query, to_paper

W = {"relevance": 0.5, "impact": 0.3, "buzz": 0.2}


def sp(pid: str, rel: float) -> ScoredPaper:
    p = Paper(
        id=pid,
        title=f"Paper {pid}",
        abstract="x",
        authors=["A"],
        published=date(2026, 10, 24),
        url=f"https://arxiv.org/abs/{pid}",
    )
    return ScoredPaper(paper=p, relevance=rel)


def test_to_paper_strips_version_and_whitespace():
    result = SimpleNamespace(
        get_short_id=lambda: "2610.31608v2",
        title="Humanoid  Mobility\n via Contacts",
        summary="Line one\nline two",
        authors=[SimpleNamespace(name="Junzhe He")],
        published=datetime(2026, 10, 22, tzinfo=UTC),
        categories=["cs.RO"],
        pdf_url="https://arxiv.org/pdf/2610.31608v2",
        journal_ref=None,
    )
    p = to_paper(result)
    assert p.id == "2610.31608"
    assert p.title == "Humanoid Mobility via Contacts"
    assert p.abstract == "Line one line two"
    assert p.url == "https://arxiv.org/abs/2610.31608"
    assert build_query(["cs.RO", "cs.LG"]) == "cat:cs.RO OR cat:cs.LG"


def test_parse_json_array_tolerates_fences():
    text = '好的：\n```json\n[{"id": "1", "score": 8}]\n```'
    assert parse_json_array(text) == [{"id": "1", "score": 8}]


def test_rerank_fills_scores_and_summaries():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "m"
        reply = json.dumps(
            [{"id": "2610.00001", "score": 9, "summary_zh": "摘要", "why_zh": "相关"}]
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    llm = LLMClient("https://llm.example/v1", "k", "m", transport=httpx.MockTransport(handler))
    items = [sp("2610.00001", 5.0)]
    rerank(llm, items, "humanoids")
    assert items[0].llm_score == 9.0
    assert items[0].summary_zh == "摘要"


def test_rerank_survives_bad_output():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "抱歉"}}]})

    llm = LLMClient("https://llm.example/v1", "k", "m", transport=httpx.MockTransport(handler))
    items = [sp("2610.00001", 5.0)]
    rerank(llm, items, "")
    assert items[0].llm_score is None


def test_save_load_and_evaluate(tmp_path):
    scored = [sp(f"2610.0000{i}", 10 - i) for i in range(6)]
    json_path, md_path = save_run(scored, tmp_path, "run", W)
    assert [s.paper.id for s in load_run(json_path)] == [s.paper.id for s in scored]
    text = md_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]", 2)
    md_path.write_text(text, encoding="utf-8")
    result = evaluate_markdown(md_path)
    assert result.total == 6
    assert result.picked == 2
    assert result.hits_at[5] == 2
