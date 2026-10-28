"""论文解读：标题和摘要的中文翻译、一段对整篇论文的解读评论，再挑出关键图表。

正文来自 arXiv 的 HTML 版（只在这里临时用，不保存）；没有 HTML 版时只依据摘要。
"""

from __future__ import annotations

import logging
from typing import Any

from ..presets import get_preset
from ..sources.arxiv_html import FullText
from .rerank import LLMClient, parse_json_object
from .style import STYLE, style_for

log = logging.getLogger("robot_times.analysis")

# 模型默认会先“思考”，思考也算在 max_tokens 里，所以上限要给足，不然长论文会只思考不输出
# 写解读时允许用领域知识评价，但要和论文里的事实分开
JUDGE = (
    "\n- 写解读时可以用你对这个领域的了解（相关工作、常见做法、常见的坑）来评价它，"
    "但要和论文里的事实分开说，推断就写成推断。"
)
SYSTEM = STYLE + JUDGE  # 默认预设（兼容旧代码）

PROMPT = """你的研究兴趣：{interests}
你文献库里和这篇最接近的：{related}

下面是一篇论文的{source}。读完它，输出一个 JSON 对象，字段如下。
title_zh：标题的中文翻译。忠实、通顺，术语保留英文。
abstract_zh：摘要的完整中文翻译，不删不添，按中文习惯断句。
review：一段对整篇论文的解读评论，{length} 字，一段写完，不分段、不列点。像同行读完后跟你说的一段话：
  先用一两句说清它做了什么、做到什么程度（{evidence}）；
  再说你自己的判断：真正新的东西在哪，和已有工作比是本质进步还是工程组合，证据够不够（基线强不强、实验规模够不够、
  关键设计有没有 ablation 支撑），最大的局限或隐含假设是什么；
  最后一句说对你的研究有什么用，或者为什么关系不大。
  不复述论文的自我评价；判断要有依据；自己的推断写成推断。
figures：从下面的图表清单里挑 2 个最值得看的（通常是总体框架图和最关键的结果图或表），按阅读顺序排列。
  每个给 id 和 note，note 一句话说看这张图看什么。清单为空就输出空数组。

数字、平台、基线名称只能来自给你的内容，没有的不要编。

只输出 JSON：{{"title_zh": "...", "abstract_zh": "...", "review": "...", "figures": [{{"id": "F2", "note": "..."}}]}}

论文：{title}
作者：{authors}
{extra}摘要：{abstract}

图表清单：
{figures}

{body}
"""

FULL_SOURCE = "全文（arXiv HTML 版，公式是 LaTeX 原文，过长的节截短了）"
ABSTRACT_SOURCE = "摘要（这篇没有 HTML 版全文，只能依据摘要；评论里说清读全文时要核实什么）"


def _figure_list(ft: FullText | None) -> str:
    if not ft or not ft.figures:
        return "（无）"
    return "\n".join(f"[{f.id}] {f.label}：{f.caption[:280]}" for f in ft.figures)


def build_prompt(
    rec: dict[str, Any], ft: FullText | None, interests: str, preset: dict[str, Any] | None = None
) -> str:
    p = preset or get_preset()
    length = "300–450" if ft else "200–300"
    extra = ""
    if rec.get("venue"):
        extra += f"发表：{rec['venue']}\n"
    if rec.get("comment"):
        extra += f"作者备注：{rec['comment'][:300]}\n"
    authors = rec.get("authors") or []
    return PROMPT.format(
        interests=interests or "（未填写）",
        related="；".join(f"《{t}》" for t in rec.get("related") or []) or "（没有）",
        title=rec["title"],
        authors=", ".join(authors[:12]) + (" 等" if len(authors) > 12 else ""),
        extra=extra,
        abstract=rec.get("abstract", ""),
        figures=_figure_list(ft),
        body=f"正文：\n{ft.text()}" if ft else "",
        length=length,
        evidence=p["evidence"],
        source=FULL_SOURCE if ft else ABSTRACT_SOURCE,
    )


def _cover(ft: FullText | None) -> dict[str, str] | None:
    """版面上的配图：用论文里第一张有图片的图（通常是 Figure 1，机器人实拍或效果总览）。"""
    for f in ft.figures if ft else []:
        if f.kind == "figure" and f.images:
            return {"src": f.images[0], "label": f.label, "caption": f.caption[:300]}
    return None


def _text(v: Any, limit: int = 6000) -> str:
    return str(v or "").strip()[:limit]


def analyze(
    llm: LLMClient,
    rec: dict[str, Any],
    ft: FullText | None,
    interests: str,
    preset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 {titleZh, abstractZh, analysis}：analysis 有一段 review 和带 arXiv 图片地址的图表。"""
    p = preset or get_preset()
    raw = llm.complete(
        build_prompt(rec, ft, interests, p),
        system=style_for(p) + JUDGE,
        json_mode=True,
        max_tokens=32000,
    )
    data = parse_json_object(raw)
    by_id = {f.id: f for f in ft.figures} if ft else {}
    figures = []
    for pick in data.get("figures") or []:
        if not isinstance(pick, dict):
            continue
        f = by_id.get(str(pick.get("id", "")).strip("[] "))
        if f and all(x["id"] != f.id for x in figures):
            figures.append({**f.to_dict(), "note": _text(pick.get("note"), 400)})
    analysis: dict[str, Any] = {"review": _text(data.get("review"), 2000), "figures": figures[:2]}
    analysis["source"] = "full" if ft else "abstract"
    cover = _cover(ft)
    if cover:
        analysis["cover"] = cover
    if ft:
        analysis["html"] = ft.url
    return {
        "titleZh": _text(data.get("title_zh"), 200),
        "abstractZh": _text(data.get("abstract_zh"), 4000),
        "analysis": analysis,
    }
