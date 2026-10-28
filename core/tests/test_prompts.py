"""提示词：风格说明作为 system 消息发出，Python 和看板两边一致，精排能用上文献库和“怎么读”。"""

import json
import re
from datetime import date
from pathlib import Path

import httpx

from robot_times.llm.rerank import LLMClient
from robot_times.llm.style import STYLE, style_for
from robot_times.models import Paper
from robot_times.pipeline import recommend
from robot_times.presets import JS_FIELDS, get_preset, js_module
from robot_times.ranking.embedder import HashingEmbedder

ROOT = Path(__file__).resolve().parents[2]


def test_worker_presets_are_generated_from_python():
    js = (ROOT / "web" / "app" / "src" / "presets.js").read_text(encoding="utf-8")
    assert js == js_module(), (
        "改了预设或写作模板后要重新生成 presets.js（见 presets.py 的 js_module）"
    )


class _Recorder:
    def __init__(self):
        self.seen = []

    def complete(self, prompt, system="", **kw):
        self.seen.append((prompt, system))
        return "[]"


def test_presets_fill_every_prompt():
    from robot_times.llm import analysis, rerank
    from robot_times.models import Paper as P
    from robot_times.models import ScoredPaper
    from robot_times.presets import PRESETS

    assert style_for("robotics") == STYLE and "whole-body control" in STYLE
    for key in PRESETS:
        p = get_preset({"preset": key})
        assert set(JS_FIELDS) <= set(p) and "{" not in style_for(p)
        assert p["topics"][-1] == "其他"
        llm = _Recorder()

        sp = ScoredPaper(
            paper=P(
                id="2610.00001", title="t", abstract="a", authors=[], published=date(2026, 10, 1)
            ),
            relevance=5.0,
        )
        rerank.rerank(llm, [sp], "", preset=p)
        assert p["example"]["why_zh"] in llm.seen[0][0] and llm.seen[0][1] == style_for(p)
        assert p["evidence"] in analysis.build_prompt({"title": "t"}, None, "", p)
    custom = get_preset(
        {"preset": "general", "siteName": "The Vision Times", "field": "计算机视觉"}
    )
    assert custom["site_name"] == "The Vision Times" and "做计算机视觉的" in style_for(custom)
    assert get_preset({"preset": "不存在"})["site_name"] == "The Robot Times"


def test_client_sends_style_as_system_message():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    c = LLMClient("https://x", "k", "m", transport=httpx.MockTransport(handler))
    assert c.complete("hi") == "ok"
    assert seen[0]["messages"] == [
        {"role": "system", "content": STYLE},
        {"role": "user", "content": "hi"},
    ]


class RecordingLLM:
    def __init__(self):
        self.prompts = []

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        ids = re.findall(r"^\[(\d{4}\.\d{5})\]", prompt, re.M)
        return json.dumps(
            [
                {"id": i, "score": 8, "summary_zh": "s", "why_zh": "w", "tip_zh": "先看 Fig. 2"}
                for i in ids
            ]
        )


def test_rerank_gets_library_neighbours_and_returns_tip():
    library = [
        {"data": {"itemType": "journalArticle", "title": "SuperMimic humanoid motion imitation",
                  "abstractNote": "humanoid whole-body motion imitation", "dateAdded": "2026-10-01T00:00:00Z"}},
        {"data": {"itemType": "journalArticle", "title": "Protein folding",
                  "abstractNote": "protein structure", "dateAdded": "2026-10-01T00:00:00Z"}},
    ]  # fmt: skip
    cands = [
        Paper(id="2610.00001", title="Humanoid motion imitation from video",
              abstract="humanoid whole-body motion imitation", authors=[], published=date(2026, 10, 24),
              comment="Accepted to CoRL 2026"),
    ]  # fmt: skip
    llm = RecordingLLM()
    out = recommend(cands, library, HashingEmbedder(), llm=llm, interests="人形")
    prompt = llm.prompts[0]
    assert "他库里最接近的：《SuperMimic humanoid motion imitation》" in prompt
    assert "作者备注：Accepted to CoRL 2026" in prompt
    assert out[0].tip_zh == "先看 Fig. 2"
    assert out[0].related[0] == "SuperMimic humanoid motion imitation"


def test_json_mode_falls_back_when_unsupported():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    c = LLMClient("https://x", "k", "m", transport=httpx.MockTransport(handler))
    assert c.complete("p", json_mode=True) == "{}"
    assert [("response_format" in b) for b in bodies] == [True, False]


def test_rerank_accepts_object_form_and_records_failures():
    from robot_times.llm.rerank import parse_json_array, rerank
    from robot_times.models import ScoredPaper

    assert parse_json_array('{"papers": [{"id": "1"}]}') == [{"id": "1"}]

    class Flaky:
        def complete(self, prompt, **kw):
            if "2610.00006" in prompt:
                return '{"papers": [{"id": "2610.00006", "score'  # 被截断
            ids = re.findall(r"^\[(\d{4}\.\d{5})\]", prompt, re.M)
            return json.dumps({"papers": [{"id": i, "score": 5, "summary_zh": "s"} for i in ids]})

    items = [
        ScoredPaper(Paper(id=f"2610.0000{i}", title="t", abstract="a", authors=[], published=date(2026, 10, 1)), 5.0)
        for i in range(10)
    ]  # fmt: skip
    errors: list[str] = []
    rerank(Flaky(), items, "", errors=errors)
    assert [sp.summary_zh for sp in items[:5]] == ["s"] * 5
    assert all(sp.summary_zh == "" for sp in items[5:])
    assert len(errors) == 1 and errors[0].startswith("2610.00005 等 5 篇")
