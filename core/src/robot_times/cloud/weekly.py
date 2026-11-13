"""每周回顾：本周推荐和你的阅读情况 → LLM 写趋势和总结 → 排下周计划。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ..llm.rerank import LLMClient, parse_json_object
from ..planning import week_plan
from ..presets import get_preset
from . import repo
from .daily import _complete_json, today_local
from .store import Store

log = logging.getLogger("robot_times.weekly")


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _titles(ps: list[dict], lang: str = "zh") -> str:
    if lang == "en":
        return "; ".join(f'"{p["title"]}"' for p in ps) or "(none)"
    return "；".join(f"《{p['title']}》" for p in ps) or "（无）"


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _md(d: date, lang: str) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}" if lang == "en" else f"{d.month}月{d.day}日"


def _prompt_en(listing: str, done: list[dict], reading: list[dict], interests: str) -> str:
    return (
        f"Their research interests: {interests or '(not given)'}\n"
        f"Marked as finished this week: {_titles(done, 'en')}\n"
        f"Currently reading this week: {_titles(reading, 'en')}\n\n"
        "Below are the papers recommended to them this week (ID, title, gist). Write this week's review as JSON:\n"
        '{"headline": "...", "summary": "...", "trends": [{"title": "...", "body": "...", "paperIds": ["ID"]}]}\n'
        "- headline: one sentence on what happened in their area this week, at most 16 words, concrete down to "
        "methods or results.\n"
        "- summary: 3 sentences. First: the most notable shift this week. Second: honestly, how much and what they "
        "read this week (say so plainly if nothing). Third: which paper to catch up on first next week, and why.\n"
        "- trends: 2–4 items. It only counts as a trend if at least two papers point to the same thing; write fewer "
        "if there aren't enough. title at most 6 words; body 2 sentences naming the papers, how each does it and "
        "the numbers; paperIds may only use IDs that appear below.\n\n" + listing
    )


def _llm_review(
    llm: LLMClient,
    papers: list[dict],
    done: list[dict],
    reading: list[dict],
    interests: str,
    preset: dict | None = None,
) -> dict[str, Any]:
    sep = ": " if (preset or {}).get("lang") == "en" else "："
    listing = "\n".join(
        f"[{p['id']}] {p['title']}{sep}{p.get('summary') or p['abstract'][:300]}"
        for p in papers[:25]
    )
    if (preset or {}).get("lang") == "en":
        return parse_json_object(
            _complete_json(llm, _prompt_en(listing, done, reading, interests), preset)
        )
    prompt = (
        f"他的研究兴趣：{interests or '（未填写）'}\n"
        f"本周他标记读完的：{_titles(done)}\n"
        f"本周他正在读的：{_titles(reading)}\n\n"
        "下面是本周推荐给他的论文（编号、标题、要点）。写本周回顾，输出 JSON：\n"
        '{"headline": "...", "summary": "...", "trends": [{"title": "...", "body": "...", "paperIds": ["编号"]}]}\n'
        "- headline：一句话说清本周他这个方向发生了什么，不超过 30 字，要具体到方法或结果。\n"
        "- summary：3 句。第一句说本周最值得注意的变化；第二句如实说他这周读了多少、读了什么（没读就直说）；"
        "第三句建议下周先补哪一篇、为什么。\n"
        "- trends：2–4 条。只有至少两篇论文指向同一件事才算趋势，凑不够就少写。"
        "title 不超过 12 字；body 2 句，说清是哪几篇、各自怎么做的、有什么数字；"
        "paperIds 只能用下面出现过的编号。\n\n" + listing
    )
    return parse_json_object(_complete_json(llm, prompt, preset))


def run_weekly(
    store: Store, llm: LLMClient | None, settings: dict, day: date | None = None
) -> dict:
    day = day or today_local()
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    days = repo.days_between(store, monday.isoformat(), sunday.isoformat())
    ids = [pid for d in days for pid in d.get("picks", [])]
    papers = repo.papers_by_ids(store, ids)
    ordered = sorted(papers.values(), key=lambda p: p["scores"]["total"], reverse=True)
    notes = repo.notes_for(store, list(papers))
    done = [papers[i] for i, n in notes.items() if n["status"] == "done"]
    unread = [
        p for p in ordered if notes.get(p["id"], {}).get("status", "todo") in ("todo", "reading")
    ]

    preset = get_preset(settings)
    lang = preset["lang"]
    review: dict[str, Any] = {}
    if llm is not None and ordered:
        try:
            reading = [papers[i] for i, n in notes.items() if n["status"] == "reading"]
            review = _llm_review(llm, ordered, done, reading, settings.get("interests", ""), preset)
        except Exception as e:
            log.warning("LLM 写周报失败，只保存统计和计划：%s", e)
    valid = set(papers)
    trends = [
        {**t, "paperIds": [i for i in t.get("paperIds", []) if i in valid]}
        for t in review.get("trends", [])
        if isinstance(t, dict) and t.get("title")
    ]

    next_monday = monday + timedelta(days=7)
    reading_first = [p["id"] for p in unread if notes.get(p["id"], {}).get("status") == "reading"]
    deep = (reading_first + [p["id"] for p in unread if p["id"] not in reading_first])[:2]
    abstract = [p["id"] for p in unread if p["id"] not in deep][:9]
    minutes = int(settings.get("dailyMinutes", 60))
    week = {
        "week": iso_week(monday),
        "start": monday.isoformat(),
        "end": sunday.isoformat(),
        "range": f"{_md(monday, lang)}–{_md(sunday, lang)}",
        "headline": review.get("headline")
        or (
            f"{len(ordered)} papers recommended this week, {len(done)} finished"
            if lang == "en"
            else f"本周推荐 {len(ordered)} 篇，读完 {len(done)} 篇"
        ),
        "summary": review.get("summary", ""),
        "trends": trends,
        "picks": [p["id"] for p in ordered[:8]],
        "stats": {"recommended": len(ordered), "done": len(done), "days": len(days)},
        "nextWeek": {
            "range": (
                f"From {_md(next_monday, lang)}, about {minutes} minutes a day"
                if lang == "en"
                else f"{_md(next_monday, lang)}起，每天约 {minutes} 分钟"
            ),
            "plan": week_plan(next_monday, minutes, deep, abstract, lang=lang),
        },
        "generatedAt": repo.now_iso(),
    }
    repo.save_week(store, week)
    log.info("已保存周报 %s", week["week"])
    return week
