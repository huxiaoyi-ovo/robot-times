"""把本地版的数据导出成 SQL，用来搬到云端版（Cloudflare D1）：

    robot-times local export robot-times.sql
    npx wrangler d1 execute DB --remote --file robot-times.sql     # 在 web/app 里运行

导出的是推荐、解读、周报、业界简报、笔记、反馈、加入过 Zotero 的记录和设置。
都用 insert or replace，云端已有的同一天、同一篇会被本地的覆盖。
"""

from __future__ import annotations

from typing import Any

from ..cloud.store import Store

TABLES: dict[str, list[str]] = {
    "papers": ["id", "data", "added_on", "total"],
    "days": ["date", "data"],
    "weeks": ["week", "data"],
    "news": ["date", "data"],
    "notes": ["paper_id", "status", "note", "flags", "updated_at"],
    "feedback": ["paper_id", "action", "at"],  # 不带自增的 id，云端重新编号
    "zotero_items": ["paper_id", "item_key", "at"],
}
CONFIG_KEYS = ("settings", "seen", "news_seen")  # 运行状态（local_runs 等）不搬


def _lit(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, int | float):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def export_sql(store: Store) -> str:
    lines = ["-- The Robot Times 本地数据导出"]
    for table, cols in TABLES.items():
        verb = "insert" if table == "feedback" else "insert or replace"
        for r in store.rows(f"select {', '.join(cols)} from {table}"):
            vals = ", ".join(_lit(r[c]) for c in cols)
            lines.append(f"{verb} into {table} ({', '.join(cols)}) values ({vals});")
    marks = ", ".join("?" * len(CONFIG_KEYS))
    for r in store.rows(f"select key, value from config where key in ({marks})", list(CONFIG_KEYS)):
        lines.append(
            f"insert or replace into config (key, value) values ({_lit(r['key'])}, {_lit(r['value'])});"
        )
    return "\n".join(lines) + "\n"
