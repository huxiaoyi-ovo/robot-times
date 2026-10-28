"""Zotero Web API 客户端（api.zotero.org）。

云端单人版用它读写文献库：写进去的条目会通过 Zotero 官方同步出现在你电脑上。
文档：https://www.zotero.org/support/dev/web_api/v3/basics

要点：
- 地址 https://api.zotero.org/users/<userID>/...，请求头带 Zotero-API-Key 和 Zotero-API-Version: 3
- userID 和 key 在 https://www.zotero.org/settings/keys 生成（key 需要勾选“允许写入”）
- 读取默认每页 25 条、最多 100 条，用 start 翻页，总数在 Total-Results 响应头里
- 写入格式和本地 API 一样（POST items，每次最多 50 个），带 Zotero-Write-Token 防止重试时重复创建
- 服务端繁忙会返回 429 或带 Backoff / Retry-After 头，按要求等待
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterable
from typing import Any

import httpx

from ..models import Paper
from .local_api import (
    MAX_BATCH,
    ZoteroError,
    add_papers_with,
    parse_write_response,
)

PAGE = 100


class ZoteroWebClient:
    def __init__(
        self,
        user_id: str,
        api_key: str,
        base_url: str = "https://api.zotero.org",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        sleep=time.sleep,
    ) -> None:
        if not user_id or not api_key:
            raise ZoteroError(
                "缺少 Zotero 用户 ID 或 API key（在 zotero.org/settings/keys 生成）。"
            )
        self.prefix = f"users/{user_id}"
        self.base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"Zotero-API-Key": api_key, "Zotero-API-Version": "3"},
        )

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(3):
            resp = self._http.request(method, url, **kw)
            wait = resp.headers.get("Retry-After") or resp.headers.get("Backoff")
            if resp.status_code in (429, 503) and attempt < 2:
                self._sleep(min(float(wait or 5), 60))
                continue
            if resp.status_code == 403:
                raise ZoteroError("Zotero 拒绝访问（403）：API key 不对，或者没有勾选读写权限。")
            if resp.status_code >= 400:
                raise ZoteroError(f"Zotero 请求失败：HTTP {resp.status_code} {resp.text[:200]}")
            if wait and resp.status_code < 400:
                self._sleep(min(float(wait), 60))  # 成功但要求放慢节奏
            return resp
        raise ZoteroError("Zotero 一直繁忙，请稍后再试。")

    def key_access(self) -> dict[str, Any]:
        """只读查询这个 key 对个人文献库的权限：{"library": bool, "notes": bool, "write": bool}。"""
        data = self._request("GET", "keys/current").json()
        user = (data.get("access") or {}).get("user") or {}
        return {k: bool(user.get(k)) for k in ("library", "notes", "write")}

    def top_items(self) -> list[dict[str, Any]]:
        """读取整个文献库的顶层条目（自动翻页）。"""
        items: list[dict[str, Any]] = []
        start = 0
        while True:
            resp = self._request(
                "GET",
                f"{self.prefix}/items/top",
                params={"format": "json", "limit": PAGE, "start": start},
            )
            page = resp.json()
            items.extend(
                it
                for it in page
                if it.get("data", {}).get("itemType") not in ("note", "attachment")
            )
            total = int(resp.headers.get("Total-Results", len(items)))
            start += PAGE
            if not page or start >= total:
                return items

    def create_items(self, items: list[dict[str, Any]]) -> list[str]:
        keys: list[str] = []
        for start in range(0, len(items), MAX_BATCH):
            batch = items[start : start + MAX_BATCH]
            resp = self._request(
                "POST",
                f"{self.prefix}/items",
                json=batch,
                headers={"Zotero-Write-Token": secrets.token_hex(16)},
            )
            keys.extend(parse_write_response(resp.json(), len(batch), start))
        return keys

    def add_papers(
        self,
        papers: Iterable[Paper],
        notes_html: Iterable[str | None] | None = None,
        tags: Iterable[str] = ("Robot Times",),
        collection_key: str | None = None,
    ) -> list[str]:
        return add_papers_with(self.create_items, papers, notes_html, tags, collection_key)

    def close(self) -> None:
        self._http.close()
