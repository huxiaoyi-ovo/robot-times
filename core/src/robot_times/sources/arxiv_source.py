"""从 arXiv 官方 API 抓最近的新论文。

arXiv 的使用条款要求每 3 秒最多 1 次请求、单连接；arxiv 库的 Client 已经按这个节奏翻页。
元数据是 CC0，可以存；PDF 不要转存到我们自己的服务器，只给用户链接。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from ..models import Paper

log = logging.getLogger("robot_times.arxiv")

_VERSION = re.compile(r"v\d+$")


def to_paper(result: Any) -> Paper:
    """把 arxiv.Result（或结构相同的对象）转换成 Paper。"""
    short_id = _VERSION.sub("", result.get_short_id())
    return Paper(
        id=short_id,
        title=" ".join(result.title.split()),
        abstract=" ".join(result.summary.split()),
        authors=[a.name for a in result.authors],
        published=result.published.date(),
        categories=list(result.categories),
        url=f"https://arxiv.org/abs/{short_id}",
        pdf_url=result.pdf_url,
        source="arxiv",
        venue=(result.journal_ref or None),
        comment=(getattr(result, "comment", None) or None),
    )


def build_query(categories: Iterable[str]) -> str:
    return " OR ".join(f"cat:{c}" for c in categories)


RETRY_WAITS = (30, 90)  # arXiv 限流（429/503）时等多久再试，单位秒
RSS_URL = "https://rss.arxiv.org/rss/"


def fetch_recent(
    categories: list[str],
    days: int = 2,
    max_results: int = 400,
    now: datetime | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    api: Callable[..., list[Paper]] | None = None,
    http: httpx.Client | None = None,
) -> list[Paper]:
    """抓 `days` 天内提交的新论文，按提交时间倒序。

    先用 arXiv API；被限流（GitHub 的服务器很多人共用，常被 arXiv 返回 429）就等一会儿再试，
    还不行就改用 arXiv 的 RSS：它只有当天发布的新论文，也没有作者备注，但不容易被限流。
    """
    api = api or _fetch_api
    last: Exception | None = None
    for wait in (0, *RETRY_WAITS):
        if wait:
            log.warning("arXiv API 限流，%d 秒后再试：%s", wait, last)
            sleep(wait)
        try:
            return api(categories, days=days, max_results=max_results, now=now)
        except Exception as e:
            if not _throttled(e):
                raise
            last = e
    log.warning("arXiv API 一直限流（%s），改用 RSS 抓今天发布的新论文", last)
    return fetch_rss(categories, http=http)


def _throttled(e: Exception) -> bool:
    """限流或临时连不上，值得等一会儿再试。"""
    if getattr(e, "status", None) in (429, 500, 502, 503, 504):
        return True
    return type(e).__name__ in ("ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout")


def _fetch_api(
    categories: list[str], days: int, max_results: int, now: datetime | None
) -> list[Paper]:
    import arxiv  # 延迟导入，测试里不需要网络

    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
    search = arxiv.Search(
        query=build_query(categories),
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    papers: list[Paper] = []
    seen: set[str] = set()
    for result in client.results(search):
        if result.published < since:
            break
        paper = to_paper(result)
        if paper.id not in seen:
            seen.add(paper.id)
            papers.append(paper)
    return papers


def fetch_rss(categories: list[str], http: httpx.Client | None = None) -> list[Paper]:
    """arXiv 的 RSS：当天发布的新论文和跨类论文（不含旧论文的更新版本）。周末是空的。"""
    import feedparser

    client = http or httpx.Client(timeout=60.0, follow_redirects=True)
    resp = client.get(RSS_URL + "+".join(categories), headers={"User-Agent": UA})
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    papers: list[Paper] = []
    seen: set[str] = set()
    for e in feed.entries:
        if e.get("arxiv_announce_type", "new") not in ("new", "cross"):
            continue
        m = _ID.search(e.get("link", "") or e.get("id", ""))
        if not m or m.group(1) in seen:
            continue
        aid = m.group(1)
        seen.add(aid)
        summary = e.get("summary", "")
        abstract = summary.split("Abstract:", 1)[1] if "Abstract:" in summary else summary
        published = (
            date(*e.published_parsed[:3]) if e.get("published_parsed") else datetime.now(UTC).date()
        )
        papers.append(
            Paper(
                id=aid,
                title=" ".join(e.get("title", "").split()),
                abstract=" ".join(abstract.split()),
                authors=[a.strip() for a in e.get("author", "").split(",") if a.strip()],
                published=published,
                categories=[t["term"] for t in e.get("tags", []) if t.get("term")],
                url=f"https://arxiv.org/abs/{aid}",
                pdf_url=f"https://arxiv.org/pdf/{aid}",
                source="arxiv",
            )
        )
    return papers


_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
UA = "robot-times/0.1 (personal paper digest)"
