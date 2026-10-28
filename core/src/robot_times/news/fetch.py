"""把各个来源抓成统一的条目：{id, title, url, source, sourceId, type, published, excerpt}。

每个来源单独容错：失败的记进 health，不影响其他来源。
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

log = logging.getLogger("robot_times.news")
UA = "Mozilla/5.0 (compatible; robot-times/0.1; personal news digest)"
# 有的站（如 *.substack.com）会拒绝非浏览器的请求头，403 时换成普通浏览器的请求头再试一次
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
AIHOT_UA = "aihot-api/2.0.0 robot-times/0.1"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean(text: Any, limit: int = 600) -> str:
    t = html.unescape(_TAG.sub(" ", str(text or "")))
    return _WS.sub(" ", t).strip()[:limit]


def canonical(url: str) -> str:
    """去掉跟踪参数和末尾斜杠，用来去重。"""
    parts = urlsplit(url.strip())
    query = "&".join(
        q
        for q in parts.query.split("&")
        if q and not q.lower().startswith(("utm_", "ref=", "source="))
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical(url).encode()).hexdigest()[:16]


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    if hasattr(value, "tm_year"):  # feedparser 的 struct_time
        return datetime(*value[:6], tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _item(src: dict, title: str, url: str, published: datetime | None, excerpt: str) -> dict:
    return {
        "id": item_id(url),
        "title": clean(title, 300),
        "url": url,
        "source": src["name"],
        "sourceId": src["id"],
        "type": src["type"],
        "lang": src.get("lang", "en"),
        "published": published.isoformat(timespec="minutes") if published else None,
        "excerpt": clean(excerpt),
    }


def _get(http: httpx.Client, url: str, **kw) -> httpx.Response:
    resp = http.get(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/xml, text/html, */*",
        },
        **kw,
    )
    if resp.status_code == 403:
        resp = http.get(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
                                      "Accept-Language": "en-US,en;q=0.9"}, **kw)  # fmt: skip
    resp.raise_for_status()
    return resp


class Blocked(Exception):
    """来源拒绝了 GitHub 服务器的请求（403），需要看板服务代抓。"""


def _rss(
    http: httpx.Client, src: dict, cache: Callable[[str], bytes | None] | None = None
) -> list[dict]:
    import feedparser

    try:
        content = _get(http, src["url"]).content
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 403:
            raise
        content = cache(src["url"]) if cache else None
        if content is None:
            raise Blocked("被拒绝（403），已交给看板服务代抓，下次运行就能读到") from e
        src["cached"] = True
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"解析不了：{feed.bozo_exception}")
    out = []
    for e in feed.entries[:40]:
        link = e.get("link")
        if not link or not e.get("title"):
            continue
        published = _parse_time(e.get("published_parsed") or e.get("updated_parsed"))
        content = (e.get("content") or [{}])[0].get("value", "")
        excerpt = e.get("summary") or content
        it = _item(src, e.title, link, published, excerpt)
        img = _feed_image(e, content or excerpt)
        if img:
            it["image"] = urljoin(link, img)
        out.append(it)
    return out


_IMG = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
_OG_IMAGE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)',
    re.I,
)


def _feed_image(entry: Any, html_text: str) -> str | None:
    """RSS 条目自带的配图：media:content / media:thumbnail / 图片附件 / 正文里第一张图。"""
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key) or []:
            url = m.get("url")
            if url and not str(m.get("medium", "image")).startswith("video"):
                return url
    for enc in entry.get("enclosures") or entry.get("links") or []:
        if str(enc.get("type", "")).startswith("image/") and enc.get("href"):
            return enc["href"]
    m = _IMG.search(html_text or "")
    if m and not re.search(
        r"pixel|tracking|feeds\.feedburner|1x1|gravatar|emoji", m.group(1), re.I
    ):
        return m.group(1)
    return None


def _html(http: httpx.Client, src: dict) -> list[dict]:
    resp = _get(http, src["url"])
    pattern = re.compile(src["pattern"])
    found = re.findall(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', resp.text, re.S | re.I)
    anchors = [
        (urljoin(src["url"], h), inner) for h, inner in found
    ]  # 相对路径（./slug）先解析成完整地址
    if not anchors or not any(pattern.match(h) for h, _ in anchors):
        hrefs = [h for h, _ in found][:15]  # 没匹配到：把页面长什么样记下来，方便调整规则
        raise ValueError(
            f"列表页没找到文章链接（{len(resp.text)} 字节，{len(found)} 个链接）：{hrefs}"
        )
    seen, out = set(), []
    for url, inner in anchors:
        title = clean(inner, 300)
        if not pattern.match(url) or len(title) < 8 or url in seen:
            continue
        seen.add(url)
        out.append(_item(src, title, url, None, ""))  # 列表页没有日期，按第一次看到的日子算
    for it in out[:8]:  # 列表页没有摘要：读文章页的 description，给大模型判断用
        try:
            page = _get(http, it["url"]).text
            m = re.search(
                r'<meta[^>]+(?:name|property)=["\'](?:og:)?description["\'][^>]+content=["\']([^"\']+)',
                page,
                re.I,
            )
            it["excerpt"] = clean(m.group(1)) if m else ""
            og = _OG_IMAGE.search(page)
            if og:
                it["image"] = urljoin(it["url"], html.unescape(og.group(1)))
        except httpx.HTTPError:
            pass
    return out


def _aihot(http: httpx.Client, src: dict) -> list[dict]:
    resp = http.get(
        src["url"],
        params={"mode": "selected", "window": "24h", "limit": 50},
        headers={"User-Agent": AIHOT_UA, "Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    rows = (data.get("items") or data.get("data") or []) if isinstance(data, dict) else data
    if not rows:
        keys = list(data)[:12] if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"AIHOT 返回里没找到条目，顶层字段：{keys}")
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        links = r.get("links") if isinstance(r.get("links"), dict) else {}
        url = r.get("url") or r.get("sourceUrl") or r.get("link")
        for key in ("original", "source", "url", "canonical", "article", "story", *links):
            v = links.get(key)
            if not url and isinstance(v, str) and v.startswith("http"):
                url = v
        title = r.get("title") or r.get("headline")
        if not (url and title):
            continue
        published = _parse_time(r.get("publishedAt") or r.get("discoveredAt"))
        excerpt = r.get("summary") or r.get("description") or r.get("excerpt") or ""
        if r.get("originalTitle") and r["originalTitle"] != title:
            excerpt = f"原标题：{r['originalTitle']}。{excerpt}"
        it = _item({**src, "lang": "zh"}, title, url, published, excerpt)
        for key in ("image", "cover", "thumbnail", "imageUrl"):
            v = r.get(key)
            if isinstance(v, str) and v.startswith("http"):
                it["image"] = v
                break
        source = r.get("source")
        name = source.get("name") if isinstance(source, dict) else source or r.get("sourceName")
        it["source"] = f"AIHOT（{name}）" if name else "AIHOT"
        out.append(it)
    if rows and not out:
        sample = rows[0] if isinstance(rows[0], dict) else {}
        raise ValueError(
            f"AIHOT 条目里没有标题或链接：links={sample.get('links')}，字段 {list(sample)[:15]}"
        )
    return out


def _hn(http: httpx.Client, src: dict, since: datetime) -> list[dict]:
    out, seen = [], set()
    for q in src.get("queries", []):
        resp = http.get(
            src["url"],
            params={
                "query": q,
                "tags": "story",
                "numericFilters": f"created_at_i>{int(since.timestamp())},points>{src.get('min_points', 40)}",
                "hitsPerPage": 20,
            },
        )
        resp.raise_for_status()
        for h in resp.json().get("hits", []):
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            if url in seen or not h.get("title"):
                continue
            seen.add(url)
            it = _item(src, h["title"], url, _parse_time(h.get("created_at_i")), "")
            it["discussion"] = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            it["points"] = h.get("points")
            out.append(it)
    return out


def fetch_all(
    sources: list[dict],
    now: datetime | None = None,
    news_hours: int = 48,
    deep_days: int = 21,
    transport: httpx.BaseTransport | None = None,
    cache: Callable[[str], bytes | None] | None = None,
) -> tuple[list[dict], list[dict]]:
    """返回 (条目, 每个来源的情况)。快讯只要最近 news_hours 小时的，深度文章看 deep_days 天。

    cache(url)：被拒绝（403）的订阅从这里读看板服务代抓的内容。
    """
    now = now or datetime.now(UTC)
    items: list[dict] = []
    health: list[dict] = []
    with httpx.Client(transport=transport, timeout=20.0, follow_redirects=True) as http:
        for src in sources:
            src = dict(src)
            window = (
                timedelta(hours=news_hours) if src["type"] == "news" else timedelta(days=deep_days)
            )
            since = now - window
            base = {"id": src["id"], "name": src["name"], "url": src["url"]}
            try:
                if src["kind"] == "rss":
                    got = _rss(http, src, cache)
                elif src["kind"] == "html":
                    got = _html(http, src)
                elif src["kind"] == "aihot":
                    got = _aihot(http, src)
                elif src["kind"] == "hn":
                    got = _hn(http, src, since)
                else:
                    raise ValueError(f"不认识的来源类型 {src['kind']}")
                fresh = [
                    it
                    for it in got
                    if not it["published"] or datetime.fromisoformat(it["published"]) >= since
                ]
                items.extend(fresh)
                health.append(
                    {**base, "ok": True, "count": len(fresh), "cached": bool(src.get("cached"))}
                )
            except Blocked as e:
                health.append({**base, "ok": False, "count": 0, "error": str(e), "blocked": True})
            except Exception as e:  # 一个来源坏了不影响其他来源
                log.warning("来源 %s 失败：%s", src["id"], e)
                health.append(
                    {
                        **base,
                        "ok": False,
                        "count": 0,
                        "error": f"{type(e).__name__}: {str(e)[:400]}",
                    }
                )
    return items, health
