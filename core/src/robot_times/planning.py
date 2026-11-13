"""阅读计划：把推荐分成精读 / 摘要层 / 扫读，并按每天的分钟数排计划。"""

from __future__ import annotations

from datetime import date, timedelta

# 计划里写给用户看的任务说明，按语言分两份
TEXT = {
    "zh": {
        "weekdays": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        "scan": "扫一遍标题和分数，想读的标“在读”，不相关的点“少推这类”。",
        "abstract": "每篇约 5 分钟：摘要、Figure 1、结果。读完能说出它解决什么问题、核心 idea 是什么。",
        "review": "接着读上次标为“在读”的论文。",
        "deep": "精读第一遍加方法部分：看清输入输出、关键设计，以及 ablation 里哪个部件真正起作用。",
        "note": "用自己的话写 3 到 5 句笔记：问题、核心 idea、关键结果、和你的工作有什么关系。",
        "first": "第一遍", "second": "第二遍", "deep_pass": "精读{}",
        "sat": "回看本周笔记，整理方向脉络，删掉不再相关的。", "sun": "读周报，确认下周计划。",
        "w_scan": "早报扫读", "w_abs": "看摘要和图，挑出值得精读的", "w_catch": "补读本周没看完的推荐",
        "w_note": "写笔记",
    },
    "en": {
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "scan": "Skim the titles and scores; mark the ones you want as “reading”, hit “fewer like this” on the off-topic ones.",
        "abstract": "About 5 minutes each: abstract, Figure 1, results. Afterwards you should be able to say what problem it solves and its core idea.",
        "review": "Pick up the papers you marked as “reading” last time.",
        "deep": "First full pass plus the method: inputs and outputs, the key design, and which component actually matters in the ablation.",
        "note": "Write 3 to 5 sentences in your own words: the problem, the core idea, the key result, how it relates to your work.",
        "first": "first pass", "second": "second pass", "deep_pass": "Close read, {}",
        "sat": "Go back over this week's notes, map out the threads, drop what no longer matters.",
        "sun": "Read the weekly review and confirm next week's plan.",
        "w_scan": "Skim the morning edition", "w_abs": "Abstracts and figures; pick the ones worth a close read",
        "w_catch": "Catch up on this week's unread picks", "w_note": "Write notes",
    },
}  # fmt: skip
WEEKDAYS = TEXT["zh"]["weekdays"]


def _t(lang: str) -> dict:
    return TEXT.get(lang, TEXT["zh"])


def assign_tiers(n: int, abstract_count: int = 4) -> list[str]:
    """排第 1 的精读，接下来几篇看摘要，其余扫读。"""
    return ["deep" if i == 0 else "abstract" if i <= abstract_count else "scan" for i in range(n)]


def _round5(x: float) -> int:
    return max(5, int(round(x / 5.0)) * 5)


def day_plan(
    minutes: int,
    deep: list[str],
    abstract: list[str],
    scan: list[str],
    reading: list[str] | None = None,
    lang: str = "zh",
) -> list[dict]:
    """按 15% 扫读、35% 摘要、40% 精读、10% 笔记分配时间；有“在读”的论文时先接着读。"""
    t = _t(lang)
    reading = [r for r in (reading or []) if r not in deep][:2]
    scan_m = _round5(minutes * 0.15) if scan else 0
    abs_m = _round5(minutes * 0.35) if abstract else 0
    note_m = _round5(minutes * 0.10)
    deep_m = max(5, minutes - scan_m - abs_m - note_m)  # 没有某一层时，省下的时间给精读
    if not deep and abstract:
        abs_m, deep_m = abs_m + deep_m, 0
    plan: list[dict] = []
    if scan:
        plan.append({"minutes": scan_m, "mode": "scan", "paperIds": scan,
                     "task": t["scan"]})  # fmt: skip
    if abstract:
        plan.append({"minutes": abs_m, "mode": "abstract", "paperIds": abstract,
                     "task": t["abstract"]})  # fmt: skip
    if reading:
        cont = min(10, deep_m // 3)
        deep_m -= cont
        plan.append({"minutes": cont, "mode": "review", "paperIds": reading,
                     "task": t["review"]})  # fmt: skip
    if deep:
        plan.append({"minutes": deep_m, "mode": "deep", "paperIds": deep,
                     "task": t["deep"]})  # fmt: skip
    plan.append({"minutes": note_m, "mode": "note", "paperIds": deep or reading,
                 "task": t["note"]})  # fmt: skip
    return plan


def week_plan(
    monday: date, minutes: int, deep_ids: list[str], abstract_ids: list[str], lang: str = "zh"
) -> list[dict]:
    """下周计划：每天 10 分钟扫早报；两篇精读各分两天；周六回看、周日读周报。"""
    t = _t(lang)
    deep_ids = deep_ids[:2]
    abs_ids = list(abstract_ids)
    body = max(10, minutes - 20)
    days: list[dict] = []
    deep_days = {0: (0, t["first"]), 2: (0, t["second"]), 3: (1, t["first"]), 4: (1, t["second"])}
    for i in range(7):
        d = monday + timedelta(days=i)
        label = f"{t['weekdays'][i]} {d.month}/{d.day}"
        if i == 5:
            items = [{"minutes": 30, "mode": "review", "paperIds": [],
                      "task": t["sat"]}]  # fmt: skip
        elif i == 6:
            items = [{"minutes": 15, "mode": "review", "paperIds": [], "task": t["sun"]}]
        else:
            items = [{"minutes": 10, "mode": "scan", "paperIds": [], "task": t["w_scan"]}]
            if i in deep_days and deep_days[i][0] < len(deep_ids):
                idx, which = deep_days[i]
                items.append({"minutes": body, "mode": "deep", "paperIds": [deep_ids[idx]],
                              "task": t["deep_pass"].format(which)})  # fmt: skip
            else:
                picks, abs_ids = abs_ids[:3], abs_ids[3:]
                items.append({"minutes": body, "mode": "abstract", "paperIds": picks,
                              "task": t["w_abs"] if picks else t["w_catch"]})  # fmt: skip
            items.append({"minutes": 10, "mode": "note", "paperIds": [], "task": t["w_note"]})
        days.append({"day": label, "items": items})
    return days
