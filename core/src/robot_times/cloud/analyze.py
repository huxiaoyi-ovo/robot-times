"""给当天推荐的每篇论文写解读（中文标题、摘要翻译、读全文后的解读和评论、关键图表），写回 papers 表。

arXiv 的 HTML 版按 3 秒一次逐篇读；读到一篇就交给大模型，几篇同时写。
已经有解读的论文不重写（当天重跑推荐时省时间），除非 force。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..llm.analysis import analyze
from ..llm.rerank import LLMClient
from . import repo
from .store import Store

log = logging.getLogger("robot_times.analyze")
WORKERS = 4
KEYS = ("titleZh", "abstractZh", "analysis")


def carry_over(store: Store, records: list[dict[str, Any]]) -> None:
    """当天重跑推荐时，沿用数据库里已经写好的解读。"""
    old = repo.papers_by_ids(store, [r["id"] for r in records])
    for rec in records:
        prev = old.get(rec["id"]) or {}
        rec.update({k: prev[k] for k in KEYS if prev.get(k)})


def analyze_papers(
    store: Store,
    llm: LLMClient,
    records: list[dict[str, Any]],
    interests: str,
    html: Any,  # 需要 get(arxiv_id) -> FullText | None
    problems: list[str] | None = None,
    force: bool = False,
    preset: dict[str, Any] | None = None,
) -> int:
    """返回这次新写了几篇。records 会被就地更新，已经带着解读的跳过。"""
    todo = [rec for rec in records if force or not rec.get("analysis")]
    if not todo:
        return 0
    log.info("写解读：%d 篇", len(todo))
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = []
        for rec in todo:
            ft = html.get(rec["id"]) if (rec.get("source") or "arxiv") == "arxiv" else None
            futures.append((rec, ft, pool.submit(analyze, llm, rec, ft, interests, preset)))
        retry = []
        for rec, ft, f in futures:  # 写数据库放在主线程
            try:
                rec.update(f.result())
            except Exception as e:  # 偶尔超时或输出不完整：等其他篇写完再试一次
                log.warning("解读 %s 失败，稍后重试：%s", rec["id"], e)
                retry.append((rec, ft, pool.submit(analyze, llm, rec, ft, interests, preset)))
                continue
            repo.upsert_paper(store, rec)
            log.info("解读完成 %s（%s）", rec["id"], "全文" if ft else "只有摘要")
        for rec, ft, f in retry:
            try:
                rec.update(f.result())
            except Exception as e:  # 一篇失败不影响其他篇和当天推荐
                log.warning("解读 %s 失败：%s", rec["id"], e)
                errors.append(f"{rec['id']} {type(e).__name__} {str(e)[:160]}")
                continue
            repo.upsert_paper(store, rec)
            log.info("解读完成 %s（重试，%s）", rec["id"], "全文" if ft else "只有摘要")
    # 失败原因记进数据库，方便排查（GitHub 的运行日志不好拿）
    repo.set_json(store, "analyze_errors", {"at": repo.now_iso(), "errors": errors})
    if errors and problems is not None:
        problems.append(f"有 {len(errors)} 篇论文的解读没写成：{errors[0]}")
    return len(todo) - len(errors)
