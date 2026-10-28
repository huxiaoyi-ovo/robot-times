"""阅读计划：把推荐分成精读 / 摘要层 / 扫读，并按每天的分钟数排计划。"""

from __future__ import annotations

from datetime import date, timedelta

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


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
) -> list[dict]:
    """按 15% 扫读、35% 摘要、40% 精读、10% 笔记分配时间；有“在读”的论文时先接着读。"""
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
                     "task": "扫一遍标题和分数，想读的标“在读”，不相关的点“少推这类”。"})  # fmt: skip
    if abstract:
        plan.append({"minutes": abs_m, "mode": "abstract", "paperIds": abstract,
                     "task": "每篇约 5 分钟：摘要、Figure 1、结果。读完能说出它解决什么问题、核心 idea 是什么。"})  # fmt: skip
    if reading:
        cont = min(10, deep_m // 3)
        deep_m -= cont
        plan.append({"minutes": cont, "mode": "review", "paperIds": reading,
                     "task": "接着读上次标为“在读”的论文。"})  # fmt: skip
    if deep:
        plan.append({"minutes": deep_m, "mode": "deep", "paperIds": deep,
                     "task": "精读第一遍加方法部分：看清输入输出、关键设计，以及 ablation 里哪个部件真正起作用。"})  # fmt: skip
    plan.append({"minutes": note_m, "mode": "note", "paperIds": deep or reading,
                 "task": "用自己的话写 3 到 5 句笔记：问题、核心 idea、关键结果、和你的工作有什么关系。"})  # fmt: skip
    return plan


def week_plan(
    monday: date, minutes: int, deep_ids: list[str], abstract_ids: list[str]
) -> list[dict]:
    """下周计划：每天 10 分钟扫早报；两篇精读各分两天；周六回看、周日读周报。"""
    deep_ids = deep_ids[:2]
    abs_ids = list(abstract_ids)
    body = max(10, minutes - 20)
    days: list[dict] = []
    deep_days = {0: (0, "第一遍"), 2: (0, "第二遍"), 3: (1, "第一遍"), 4: (1, "第二遍")}
    for i in range(7):
        d = monday + timedelta(days=i)
        label = f"{WEEKDAYS[i]} {d.month}/{d.day}"
        if i == 5:
            items = [{"minutes": 30, "mode": "review", "paperIds": [],
                      "task": "回看本周笔记，整理方向脉络，删掉不再相关的。"}]  # fmt: skip
        elif i == 6:
            items = [
                {"minutes": 15, "mode": "review", "paperIds": [], "task": "读周报，确认下周计划。"}
            ]
        else:
            items = [{"minutes": 10, "mode": "scan", "paperIds": [], "task": "早报扫读"}]
            if i in deep_days and deep_days[i][0] < len(deep_ids):
                idx, which = deep_days[i]
                items.append({"minutes": body, "mode": "deep", "paperIds": [deep_ids[idx]],
                              "task": f"精读{which}"})  # fmt: skip
            else:
                picks, abs_ids = abs_ids[:3], abs_ids[3:]
                items.append({"minutes": body, "mode": "abstract", "paperIds": picks,
                              "task": "看摘要和图，挑出值得精读的" if picks else "补读本周没看完的推荐"})  # fmt: skip
            items.append({"minutes": 10, "mode": "note", "paperIds": [], "task": "写笔记"})
        days.append({"day": label, "items": items})
    return days
