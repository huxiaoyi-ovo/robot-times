"""Zotero 桌面端本地 API 客户端（需要 Zotero 10 及以上）。

文档：https://www.zotero.org/support/dev/web_api/v3/local_api

要点：
- 地址 http://localhost:23119/api/，用户路径用 users/0。需要在 Zotero 设置 → 高级里
  勾选“允许此计算机上的其他应用程序与 Zotero 通信”，否则返回 403。
- 读不需要认证。写需要本地 API key：POST /api/local/authorize 会在 Zotero 里弹窗，
  用户选 “Always Allow” 后 key 可以一直用；只点 “Allow” 的话 key 只能用一次。
- 写请求必须带 Zotero-Server-ID 头（从任意响应头里读到后缓存）；ID 变了会返回 412。
- 写入格式和 Zotero Web API 一样：POST users/0/items，body 是条目数组（每次最多 50 个）。
"""

from __future__ import annotations

import hashlib
import html
import secrets
import time
from collections.abc import Iterable
from typing import Any

import httpx

from ..models import Paper

USER = "users/0"
MAX_BATCH = 50


class ZoteroError(RuntimeError):
    pass


class ZoteroLocalClient:
    def __init__(
        self,
        base_url: str = "http://localhost:23119/api",
        app_name: str = "The Robot Times",
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.api_key = api_key
        self.key_is_persistent = api_key is not None
        self._server_id: str | None = None
        self._http = httpx.Client(transport=transport, timeout=timeout)

    # ---------- 连接与服务器 ID ----------

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _remember_server_id(self, resp: httpx.Response) -> None:
        sid = resp.headers.get("Zotero-Server-ID")
        if sid:
            self._server_id = sid

    @property
    def server_id(self) -> str:
        if self._server_id is None:
            resp = self._http.get(self._url(""))
            self._check(resp, "连接 Zotero")
            self._remember_server_id(resp)
            if self._server_id is None:
                raise ZoteroError(
                    "Zotero 没有返回 Zotero-Server-ID，请确认是 Zotero 10 及以上版本。"
                )
        return self._server_id

    def ping(self) -> bool:
        try:
            _ = self.server_id
            return True
        except (httpx.HTTPError, ZoteroError):
            return False

    @staticmethod
    def _check(resp: httpx.Response, action: str) -> None:
        if resp.status_code == 403 and ("not enabled" in resp.text or not resp.text):
            raise ZoteroError(
                f"{action}被拒绝（403）。请在 Zotero 设置 → 高级里开启"
                "“允许此计算机上的其他应用程序与 Zotero 通信”。"
            )
        if resp.status_code == 403:
            raise ZoteroError(f"{action}被拒绝（403）：{resp.text[:200]}")
        if resp.status_code >= 400:
            raise ZoteroError(f"{action}失败：HTTP {resp.status_code} {resp.text[:200]}")

    # ---------- 读 ----------

    def top_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        """读取文献库里的顶层条目（不含附件和笔记）。本地 API 默认不分页。"""
        params: dict[str, Any] = {"format": "json"}
        if limit:
            params["limit"] = limit
        resp = self._http.get(self._url(f"{USER}/items/top"), params=params)
        self._check(resp, "读取文献库")
        self._remember_server_id(resp)
        return [
            it
            for it in resp.json()
            if it.get("data", {}).get("itemType") not in ("note", "attachment")
        ]

    def collections(self) -> list[dict[str, Any]]:
        resp = self._http.get(self._url(f"{USER}/collections"), params={"format": "json"})
        self._check(resp, "读取分类")
        return resp.json()

    # ---------- 写权限 ----------

    def authorize(self) -> str:
        """请求写入 key。Zotero 会弹窗，请用户选 Always Allow。"""
        resp = self._http.post(
            self._url("local/authorize"),
            json={"appName": self.app_name},
            headers={"Zotero-Server-ID": self.server_id},
        )
        if resp.status_code == 403 and "denied" in resp.text:
            raise ZoteroError("你在 Zotero 弹窗里拒绝了写入权限。")
        if resp.status_code == 429:
            raise ZoteroError("授权请求太频繁，请稍后再试。")
        self._check(resp, "请求写入权限")
        body = resp.json()
        self.api_key = body["key"]
        self.key_is_persistent = bool(body.get("remember"))
        return self.api_key

    def _write(
        self,
        method: str,
        path: str,
        json: Any = None,
        *,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        # 写入令牌：同一个令牌 12 小时内只会成功写一次，防止重试时重复创建条目
        write_token = secrets.token_hex(16)
        for attempt in range(2):
            if not self.api_key:
                self.authorize()
            resp = self._http.request(
                method,
                self._url(path),
                json=json,
                data=data,
                headers={
                    "Zotero-API-Key": self.api_key or "",
                    "Zotero-Server-ID": self.server_id,
                    **({"Zotero-Write-Token": write_token} if method == "POST" and json else {}),
                    **(headers or {}),
                },
            )
            if not self.key_is_persistent:
                self.api_key = None  # 一次性 key 用完即作废
            if resp.status_code == 401 and attempt == 0:
                self.api_key = None
                continue
            if resp.status_code == 412:
                self._server_id = None
                raise ZoteroError("Zotero 的服务器 ID 变了（可能换了数据目录），请重新运行。")
            self._check(resp, "写入 Zotero")
            return resp
        raise ZoteroError("写入 Zotero 失败：授权无效。")

    def create_items(self, items: list[dict[str, Any]]) -> list[str]:
        """批量创建条目，返回新条目的 key（顺序与输入一致，失败的位置为空字符串）。"""
        keys: list[str] = []
        for start in range(0, len(items), MAX_BATCH):
            batch = items[start : start + MAX_BATCH]
            body = self._write("POST", f"{USER}/items", batch).json()
            keys.extend(parse_write_response(body, len(batch), start))
        return keys

    # ---------- 单个条目 ----------

    def item(self, key: str) -> dict[str, Any] | None:
        """读一个条目；不存在或在回收站里返回 None。"""
        resp = self._http.get(self._url(f"{USER}/items/{key}"), params={"format": "json"})
        if resp.status_code == 404:
            return None
        self._check(resp, "读取条目")
        self._remember_server_id(resp)
        it = resp.json()
        return None if it.get("data", {}).get("deleted") else it

    def children(self, key: str) -> list[dict[str, Any]]:
        resp = self._http.get(self._url(f"{USER}/items/{key}/children"), params={"format": "json"})
        if resp.status_code == 404:
            return []
        self._check(resp, "读取附件")
        return resp.json()

    def find_collection(self, name: str) -> str | None:
        """按名字找文件夹，返回它的 key。"""
        for c in self.collections():
            if str(c.get("data", {}).get("name", "")).strip() == name:
                return c["key"]
        return None

    def update_item(self, item: dict[str, Any], changes: dict[str, Any]) -> None:
        """改条目的部分字段（PATCH）。条目在别处被改过时 Zotero 返回 412，这里报错让用户重试。"""
        version = item.get("version") or item.get("data", {}).get("version")
        resp = self._write(
            "PATCH",
            f"{USER}/items/{item['key']}",
            changes,
            headers={"If-Unmodified-Since-Version": str(version)} if version is not None else {},
        )
        if resp.status_code == 412:
            raise ZoteroError("这个条目刚在 Zotero 里改过，请再试一次。")

    def trash(self, key: str) -> None:
        """移进 Zotero 的回收站（可以恢复），不是彻底删除。"""
        it = self.item(key)
        if it is not None:
            self.update_item(it, {"deleted": 1})

    def upload_file(self, att_key: str, content: bytes, filename: str) -> None:
        """给已有的附件条目上传文件（和 Web API 一样的三步：申请、上传、登记）。"""
        md5 = hashlib.md5(content).hexdigest()  # noqa: S324  Zotero 要求用 MD5 校验
        form = {
            "md5": md5,
            "filename": filename,
            "filesize": len(content),
            "mtime": int(time.time() * 1000),
        }
        path = f"{USER}/items/{att_key}/file"
        auth = self._write("POST", path, data=form, headers={"If-None-Match": "*"}).json()
        if auth.get("exists"):
            return
        body = auth.get("prefix", "").encode() + content + auth.get("suffix", "").encode()
        up = self._http.post(
            auth["url"], content=body, headers={"Content-Type": auth["contentType"]}
        )
        if up.status_code >= 400:
            raise ZoteroError(f"上传 PDF 失败：HTTP {up.status_code}")
        self._write(
            "POST", path, data={"upload": auth["uploadKey"]}, headers={"If-None-Match": "*"}
        )

    # ---------- 高层操作 ----------

    def add_papers(
        self,
        papers: Iterable[Paper],
        notes_html: Iterable[str | None] | None = None,
        tags: Iterable[str] = ("Robot Times",),
        collection_key: str | None = None,
    ) -> list[str]:
        """把论文作为预印本条目加入 Zotero，可选附上一条子笔记（比如中文摘要）。"""
        return add_papers_with(self.create_items, papers, notes_html, tags, collection_key)

    def close(self) -> None:
        self._http.close()


def add_papers_with(
    create_items,
    papers: Iterable[Paper],
    notes_html: Iterable[str | None] | None = None,
    tags: Iterable[str] = ("Robot Times",),
    collection_key: str | None = None,
) -> list[str]:
    """本地 API 和 Web API 共用：先建条目，再给有摘要的条目建子笔记。"""
    papers = list(papers)
    notes = list(notes_html) if notes_html is not None else [None] * len(papers)
    tag_list = [{"tag": t} for t in tags]
    keys = create_items([paper_to_item(p, tag_list, collection_key) for p in papers])
    note_items = [
        {"itemType": "note", "parentItem": key, "note": note, "tags": tag_list}
        for key, note in zip(keys, notes, strict=True)
        if key and note
    ]
    if note_items:
        create_items(note_items)
    return keys


def parse_write_response(body: dict[str, Any], batch_len: int, offset: int) -> list[str]:
    success = body.get("success", {})
    failed = body.get("failed", {})
    keys: list[str] = []
    for i in range(batch_len):
        if str(i) in failed:
            msg = failed[str(i)].get("message", "")
            raise ZoteroError(f"第 {offset + i + 1} 个条目写入失败：{msg}")
        keys.append(success.get(str(i), ""))
    return keys


def paper_to_item(
    paper: Paper, tags: list[dict[str, str]], collection_key: str | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "itemType": "preprint",
        "title": paper.title,
        "creators": [_creator(name) for name in paper.authors],
        "abstractNote": paper.abstract,
        "repository": "arXiv" if paper.source == "arxiv" else "",
        "archiveID": f"arXiv:{paper.id}" if paper.source == "arxiv" else "",
        "date": paper.published.isoformat(),
        "url": paper.url,
        "tags": tags,
    }
    if collection_key:
        item["collections"] = [collection_key]
    return item


def _creator(name: str) -> dict[str, str]:
    parts = name.strip().split()
    if len(parts) >= 2:
        return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}
    return {"creatorType": "author", "name": name.strip()}


def note_html(summary_zh: str, why_zh: str = "", scores: str = "") -> str:
    parts = ["<h2>The Robot Times 摘要</h2>", f"<p>{html.escape(summary_zh)}</p>"]
    if why_zh:
        parts.append(f"<p><b>为什么推荐：</b>{html.escape(why_zh)}</p>")
    if scores:
        parts.append(f"<p><i>{html.escape(scores)}</i></p>")
    return "".join(parts)


def item_text(item: dict[str, Any]) -> str:
    data = item.get("data", {})
    return f"{data.get('title', '')}. {data.get('abstractNote', '')}".strip()
