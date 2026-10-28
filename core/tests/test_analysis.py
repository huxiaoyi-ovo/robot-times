"""论文解读：arXiv HTML 解析、提示词、图表挑选、写回数据库。全部离线。"""

import json

import httpx

from robot_times.cloud import repo
from robot_times.cloud.analyze import analyze_papers, carry_over
from robot_times.cloud.store import SqliteStore
from robot_times.llm.analysis import analyze, build_prompt
from robot_times.sources.arxiv_html import ArxivHtml, FullText, _water_fill, parse

PAGE = """<html><body><article class="ltx_document">
<h1 class="ltx_title ltx_title_document">Humanoid Mobility via Attention-based Contacts</h1>
<div class="ltx_authors">Someone</div>
<section id="S1" class="ltx_section"><h2 class="ltx_title">1 Introduction</h2>
<p>Humanoids fall on rough terrain because contact plans are brittle. We learn a contact planner
with attention <math alttext="\\alpha_t"><mi>α</mi></math> over body parts.<span class="ltx_note">footnote</span></p>
<figure id="S1.F1" class="ltx_figure"><img src="2610.31608v1/fig1.png" alt="x">
<figure class="ltx_figure"><img src="2610.31608v1/fig1b.png"></figure>
<figcaption><span class="ltx_tag ltx_tag_figure">Figure 1</span>: Overview of the pipeline.</figcaption></figure>
</section>
<section id="S2" class="ltx_section"><h2 class="ltx_title">2 Experiments</h2>
<p>On Unitree G1 the success rate rises from 61% to 90% over the tracking baseline across 40 trials.</p>
<section class="ltx_subsection"><h3>2.1 Ablation</h3><p>Removing attention drops success to 70%.</p></section>
<figure id="S2.T1" class="ltx_table"><figcaption><span class="ltx_tag ltx_tag_table">Table 1</span>: Success rates.</figcaption>
<table class="ltx_tabular"><tr><th>Method</th><th>Success</th></tr><tr><td>Ours</td><td>90%</td></tr></table></figure>
</section>
<section id="S9" class="ltx_section"><h2 class="ltx_title">Acknowledgments</h2><p>Thanks to everyone who helped us a lot with this.</p></section>
</article></body></html>"""
URL = "https://arxiv.org/html/2610.31608"


def test_parse_sections_figures_and_tables():
    ft = parse(PAGE, URL)
    assert [t for t, _ in ft.sections] == ["1 Introduction", "2 Experiments"]  # 致谢不要
    intro = ft.sections[0][1]
    assert "\\alpha_t" in intro and "footnote" not in intro and "Overview" not in intro
    assert "Removing attention" in ft.sections[1][1]  # 小节并进所在的节
    f1, t1 = ft.figures
    assert f1.label == "Figure 1" and f1.caption == "Overview of the pipeline."
    # 相对地址按页面所在目录解析，子图算在外层的图里
    assert f1.images == [
        "https://arxiv.org/html/2610.31608v1/fig1.png",
        "https://arxiv.org/html/2610.31608v1/fig1b.png",
    ]
    assert t1.kind == "table" and t1.rows == [["Method", "Success"], ["Ours", "90%"]]
    assert parse("<html><body>no article</body></html>", URL) is None


def test_text_budget_keeps_start_of_every_section():
    ft = FullText(URL, [("A", "a " * 5000), ("B", "b " * 50)], [])
    text = ft.text(budget=2000)
    assert "## B\n" + "b " * 49 in text and "省略" in text and len(text) < 2300
    assert _water_fill([100, 100], 1000) == 100


def test_html_client_missing_page_and_rate_limit():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(404) if "none" in str(req.url) else httpx.Response(200, text=PAGE)

    client = ArxivHtml(transport=httpx.MockTransport(handler), interval=0)
    assert client.get("none") is None
    assert client.get("2610.31608").figures[0].images[0].startswith("https://arxiv.org/html/")


class FakeLLM:
    def __init__(self, figures=("F2", "F1", "F9")):
        self.prompts, self.kw, self.figures = [], [], figures

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        self.kw.append(kw)
        return json.dumps(
            {"title_zh": "基于接触注意力的人形机器人运动", "abstract_zh": "摘要译文", "tldr": "一句话",
             "review": "一段评论",
             "figures": [{"id": f, "note": f"看 {f}"} for f in self.figures]},
            ensure_ascii=False,
        )  # fmt: skip


REC = {"id": "2610.31608", "title": "Humanoid Mobility via Attention-based Contacts", "tier": "deep",
       "authors": ["A", "B"], "abstract": "We learn contacts.", "related": ["Old paper"], "venue": "arXiv cs.RO",
       "addedOn": "2026-10-26", "scores": {"total": 7.0}}  # fmt: skip


def test_prompt_full_text_vs_abstract_only():
    ft = parse(PAGE, URL)
    full = build_prompt(REC, ft, "人形全身控制")
    assert "[F1] Figure 1：Overview of the pipeline." in full and "## 2 Experiments" in full
    assert "人形全身控制" in full and "《Old paper》" in full and "300–450" in full
    short = build_prompt({**REC, "tier": "scan"}, None, "")
    assert "只能依据摘要" in short and "正文：" not in short and "200–300" in short


def test_analyze_maps_figures_and_skips_unknown_ids():
    ft = parse(PAGE, URL)
    llm = FakeLLM()
    out = analyze(llm, REC, ft, "")
    assert out["titleZh"].startswith("基于") and out["abstractZh"] == "摘要译文"
    a = out["analysis"]
    assert [f["id"] for f in a["figures"]] == ["F2", "F1"]  # 按大模型给的顺序，没有的 F9 丢掉
    assert (
        a["figures"][0]["rows"] and a["figures"][1]["images"] and a["figures"][1]["note"] == "看 F1"
    )
    assert a["review"] == "一段评论" and a["source"] == "full" and a["html"] == URL
    assert a["cover"] == {
        "src": "https://arxiv.org/html/2610.31608v1/fig1.png",
        "label": "Figure 1",
        "caption": "Overview of the pipeline.",
    }
    assert llm.kw[0]["json_mode"] and llm.kw[0]["max_tokens"] >= 6000
    assert "领域的了解" in llm.kw[0]["system"]


def test_analyze_papers_writes_back_and_skips_done():
    store = SqliteStore()
    recs = [dict(REC), {**REC, "id": "2610.00001", "source": "openreview"}]
    for r in recs:
        repo.upsert_paper(store, r)

    class Html:
        def __init__(self):
            self.asked = []

        def get(self, pid):
            self.asked.append(pid)
            return parse(PAGE, URL)

    html, llm = Html(), FakeLLM()
    assert analyze_papers(store, llm, recs, "", html) == 2
    assert html.asked == ["2610.31608"]  # 不是 arXiv 的论文不去读 HTML
    saved = repo.papers_by_ids(store, ["2610.31608", "2610.00001"])
    assert saved["2610.31608"]["analysis"]["source"] == "full"
    assert saved["2610.00001"]["analysis"]["source"] == "abstract"

    # 当天重跑：沿用已经写好的解读，不再调用大模型
    again = [dict(REC), {**REC, "id": "2610.00001", "source": "openreview"}]
    carry_over(store, again)
    n = len(llm.prompts)
    assert analyze_papers(store, llm, again, "", html) == 0 and len(llm.prompts) == n
    assert analyze_papers(store, llm, again, "", html, force=True) == 2


def test_analyze_papers_reports_failures():
    class Broken:
        def complete(self, prompt, **kw):
            raise RuntimeError("boom")

    class NoHtml:
        def get(self, pid):
            return None

    store, problems = SqliteStore(), []
    assert analyze_papers(store, Broken(), [dict(REC)], "", NoHtml(), problems) == 0
    assert "解读没写成" in problems[0]


def test_parse_json_object_repairs_unescaped_quotes():
    from robot_times.llm.rerank import parse_json_object

    broken = '{"critique": "作者说"关键在帧"，但没证明", "tldr": "一句"}'
    assert parse_json_object(broken)["tldr"] == "一句"
    import pytest

    with pytest.raises(ValueError):
        parse_json_object("")
