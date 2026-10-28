"""数据库读写。所有表结构见 schema.sql；看板 Worker 读写的是同一张表。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from ..presets import DEFAULT as DEFAULT_PRESET
from ..presets import PRESETS, get_preset
from .store import Store

DEFAULT_SETTINGS: dict[str, Any] = {
    "preset": DEFAULT_PRESET,  # 方向预设，见 presets.py
    "interests": "",
    "weights": {"relevance": 0.5, "impact": 0.3, "buzz": 0.2},
    "dailyMinutes": 60,
    "top": 12,
    "tag": "Robot Times",
    "siteName": "",  # 报名，留空用方向预设的
    "field": "",  # 领域名（写进大模型的人设），留空用方向预设的
    "weatherCity": "",
    "zoteroCollection": "",  # 加入 Zotero 时放进的文件夹，留空就放在“我的文库”
    "diversity": 0.4,
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_settings(store: Store, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """数据库里的设置优先；没有时用环境变量给的初始值，再没有用默认值。"""
    settings = {**DEFAULT_SETTINGS, **(fallback or {})}
    rows = store.rows("select value from config where key = 'settings'")
    if rows:
        settings.update(json.loads(rows[0]["value"]))
    if not settings.get("categories"):  # 没填 arXiv 分类就用方向预设的
        settings["categories"] = list(get_preset(settings)["categories"])
    return settings


class SettingsError(ValueError):
    """设置的写法不对，消息直接给用户看。"""


_CATEGORY = re.compile(r"^[a-z-]+(\.[A-Za-z-]+)?$")
_FEED = re.compile(r"^https?://[^\s/]+\.\S+$")


def update_settings(s: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """按看板提交的字段更新设置，规则和看板 Worker 的 saveSettings 一样。"""
    s = dict(s)
    if "categories" in b:
        cats = [str(c).strip() for c in (b["categories"] or []) if str(c).strip()]
        if not cats or len(cats) > 12 or not all(_CATEGORY.match(c) for c in cats):
            raise SettingsError("arXiv 分类写法不对，例如 cs.RO、cs.LG、eess.SY。")
        s["categories"] = cats
    if "interests" in b:
        s["interests"] = str(b["interests"])[:2000]
    if "weights" in b:
        w = {}
        for k in ("relevance", "impact", "buzz"):
            try:
                v = float((b["weights"] or {})[k])
            except (KeyError, TypeError, ValueError):
                v = -1
            if not 0 <= v <= 1:
                raise SettingsError("权重要在 0 到 1 之间。")
            w[k] = round(v, 2)
        if sum(w.values()) <= 0:
            raise SettingsError("三个权重不能都是 0。")
        s["weights"] = w
    if "dailyMinutes" in b:
        s["dailyMinutes"] = max(15, min(240, round(_num(b["dailyMinutes"]) or 60)))
    if "top" in b:
        s["top"] = max(3, min(30, round(_num(b["top"]) or 12)))
    if "diversity" in b:
        v = _num(b["diversity"])
        if v is None or not 0 <= v <= 1:
            raise SettingsError("多样性要在 0 到 1 之间。")
        s["diversity"] = round(v, 2)
    if "preset" in b and b["preset"] is not None:
        if b["preset"] not in PRESETS:
            raise SettingsError("没有这个方向预设。")
        s["preset"] = b["preset"]
    for key, limit in (
        ("siteName", 60),
        ("field", 40),
        ("weatherCity", 60),
        ("zoteroCollection", 80),
    ):
        if key in b:
            s[key] = str(b[key] or "").strip()[:limit]
    if "tag" in b:
        s["tag"] = str(b["tag"] or "").strip()[:40] or "Robot Times"
    if "newsFeeds" in b:
        feeds = [str(u).strip() for u in (b["newsFeeds"] or []) if str(u).strip()]
        if len(feeds) > 30:
            raise SettingsError("订阅最多 30 个。")
        bad = next((u for u in feeds if not _FEED.match(u)), None)
        if bad:
            raise SettingsError(f"这个不像 RSS 地址：{bad}")
        s["newsFeeds"] = feeds
    return s


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def save_settings(store: Store, settings: dict[str, Any]) -> None:
    store.run(
        "insert into config (key, value) values ('settings', ?) "
        "on conflict(key) do update set value = excluded.value",
        [json.dumps(settings, ensure_ascii=False)],
    )


def get_json(store: Store, key: str) -> Any:
    rows = store.rows("select value from config where key = ?", [key])
    return json.loads(rows[0]["value"]) if rows else None


def set_json(store: Store, key: str, value: Any) -> None:
    store.run(
        "insert into config (key, value) values (?, ?) "
        "on conflict(key) do update set value = excluded.value",
        [key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))],
    )


def known_paper_ids(store: Store, before: str | None = None) -> set[str]:
    """以前推荐过的论文（before 之前加入的），避免重复推荐；当天重跑时当天的不算。"""
    if before:
        return {r["id"] for r in store.rows("select id from papers where added_on < ?", [before])}
    return {r["id"] for r in store.rows("select id from papers")}


def feedback_texts(store: Store) -> tuple[list[str], list[str]]:
    """返回 (喜欢或加入过的论文文本, 跳过的论文文本)，每篇只按最后一次反馈算。"""
    rows = store.rows(
        "select f.paper_id, f.action, p.data from feedback f "
        "join papers p on p.id = f.paper_id order by f.id"
    )
    last: dict[str, tuple[str, str]] = {}
    for r in rows:
        d = json.loads(r["data"])
        last[r["paper_id"]] = (r["action"], f"{d.get('title', '')}. {d.get('abstract', '')}")
    liked = [t for a, t in last.values() if a in ("like", "add")]
    skipped = [t for a, t in last.values() if a == "skip"]
    return liked, skipped


def notes_by_status(store: Store, status: str) -> list[str]:
    return [
        r["paper_id"]
        for r in store.rows(
            "select paper_id from notes where status = ? order by updated_at desc", [status]
        )
    ]


def upsert_paper(store: Store, data: dict[str, Any]) -> None:
    store.run(
        "insert into papers (id, data, added_on, total) values (?, ?, ?, ?) "
        "on conflict(id) do update set data = excluded.data, total = excluded.total",
        [
            data["id"],
            json.dumps(data, ensure_ascii=False),
            data["addedOn"],
            data["scores"]["total"],
        ],
    )


def save_day(store: Store, day: dict[str, Any]) -> None:
    store.run(
        "insert into days (date, data) values (?, ?) "
        "on conflict(date) do update set data = excluded.data",
        [day["date"], json.dumps(day, ensure_ascii=False)],
    )


def save_week(store: Store, week: dict[str, Any]) -> None:
    store.run(
        "insert into weeks (week, data) values (?, ?) "
        "on conflict(week) do update set data = excluded.data",
        [week["week"], json.dumps(week, ensure_ascii=False)],
    )


def days_between(store: Store, start: str, end: str) -> list[dict[str, Any]]:
    rows = store.rows(
        "select data from days where date >= ? and date <= ? order by date", [start, end]
    )
    return [json.loads(r["data"]) for r in rows]


CHUNK = 90  # D1 每条语句最多绑定 100 个参数


def _chunks(ids: list[str]):
    for i in range(0, len(ids), CHUNK):
        yield ids[i : i + CHUNK]


def papers_by_ids(store: Store, ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for part in _chunks(list(dict.fromkeys(ids))):
        marks = ",".join("?" * len(part))
        for r in store.rows(f"select data from papers where id in ({marks})", part):
            d = json.loads(r["data"])
            out[d["id"]] = d
    return out


def notes_for(store: Store, ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for part in _chunks(list(dict.fromkeys(ids))):
        marks = ",".join("?" * len(part))
        for r in store.rows(f"select * from notes where paper_id in ({marks})", part):
            out[r["paper_id"]] = r
    return out
