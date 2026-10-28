"""测试共用的假 Zotero 本地 API。

行为对照 Zotero 源码 chrome/content/zotero/xpcom/server/server_localAPI.js：
- 每个响应都带 Zotero-Server-ID；写请求不带它返回 428
- 写请求需要 Zotero-API-Key；一次性 key 用过即失效
- 多对象写入返回 {"successful", "success", "unchanged", "failed"}
- 写入成功的条目会出现在之后的 items/top 里
"""

import json

import httpx

SID = "srv-1"


class FakeZotero:
    def __init__(
        self,
        remember: bool = True,
        reject_first_write: bool = False,
        library: list[dict] | None = None,
        enabled: bool = True,
    ):
        self.remember = remember
        self.reject_first_write = reject_first_write
        self.enabled = enabled
        self.library = list(library or [{"key": "AAA", "data": {"itemType": "book", "title": "T"}}])
        self.requests: list[httpx.Request] = []
        self.created: list[dict] = []
        self.issued_keys = 0
        self.valid_keys: dict[str, bool] = {}  # key -> remember

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        h = {"Zotero-Server-ID": SID}
        if not self.enabled:
            return httpx.Response(403, headers=h, text="Local API is not enabled")
        path = request.url.path
        is_write = request.method in ("POST", "PUT", "PATCH", "DELETE")
        if is_write and request.headers.get("Zotero-Server-ID") != SID:
            return httpx.Response(428, headers=h, text="Zotero-Server-ID not provided")

        if path == "/api/":
            return httpx.Response(200, headers=h, text="Nothing here")
        if path == "/api/users/0/items/top":
            return httpx.Response(200, headers=h, json=self.library)
        if path == "/api/local/authorize":
            self.issued_keys += 1
            key = f"k{self.issued_keys}"
            self.valid_keys[key] = self.remember
            return httpx.Response(200, headers=h, json={"key": key, "remember": self.remember})
        if path == "/api/users/0/items" and request.method == "POST":
            key = request.headers.get("Zotero-API-Key", "")
            if self.reject_first_write or key not in self.valid_keys:
                self.reject_first_write = False
                return httpx.Response(401, headers=h, text="Invalid or expired API key")
            if not self.valid_keys[key]:
                del self.valid_keys[key]  # 一次性 key 用完作废
            assert request.headers.get("Zotero-Write-Token")
            body = json.loads(request.content)
            start = len(self.created)
            self.created.extend(body)
            success = {str(i): f"K{start + i}" for i in range(len(body))}
            for i, obj in enumerate(body):
                if obj.get("itemType") not in ("note", "attachment"):
                    self.library.append({"key": f"K{start + i}", "data": obj})
            return httpx.Response(
                200,
                headers=h,
                json={"successful": {}, "success": success, "unchanged": {}, "failed": {}},
            )
        return httpx.Response(404, headers=h)
