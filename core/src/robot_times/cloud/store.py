"""存储：Cloudflare D1（通过 REST API）或本地 SQLite，两者接口相同。

D1 本身就是 SQLite，所以同一套 SQL 两边都能跑；测试用 SQLite，线上用 D1。
"""

from __future__ import annotations

import sqlite3
import threading
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

import httpx

SCHEMA = resources.files("robot_times.cloud").joinpath("schema.sql").read_text(encoding="utf-8")


class Store(Protocol):
    def rows(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]: ...

    def run(self, sql: str, params: tuple | list = ()) -> None: ...


class SqliteStore:
    """本地 SQLite。本地版的看板服务和后台任务在不同线程里用同一个连接，所以加锁。"""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if str(path) != ":memory:":
                self.db.execute("pragma journal_mode = wal")
            self.db.executescript(SCHEMA)

    def rows(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    def run(self, sql: str, params: tuple | list = ()) -> None:
        with self._lock:
            self.db.execute(sql, tuple(params))
            self.db.commit()


class D1Error(RuntimeError):
    pass


class D1Store:
    """通过 Cloudflare REST API 访问 D1。需要一个有 D1 编辑权限的 API token。"""

    def __init__(
        self,
        account_id: str,
        database_id: str,
        api_token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not (account_id and database_id and api_token):
            raise D1Error("缺少 Cloudflare 账号 ID、D1 数据库 ID 或 API token。")
        self.url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self._http = httpx.Client(
            transport=transport, timeout=30.0, headers={"Authorization": f"Bearer {api_token}"}
        )

    def _query(self, sql: str, params: tuple | list) -> list[dict[str, Any]]:
        resp = self._http.post(self.url, json={"sql": sql, "params": [_param(p) for p in params]})
        try:
            body = resp.json()
        except ValueError as e:
            raise D1Error(f"D1 返回了无法解析的内容：HTTP {resp.status_code}") from e
        if resp.status_code >= 400 or not body.get("success", False):
            errors = "; ".join(e.get("message", "") for e in body.get("errors", []))
            raise D1Error(f"D1 查询失败：{errors or resp.status_code}")
        results = body.get("result", [])
        return results[0].get("results", []) if results else []

    def rows(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        return self._query(sql, params)

    def run(self, sql: str, params: tuple | list = ()) -> None:
        self._query(sql, params)


def _param(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)
