"""本地版的后台任务：每日推荐、业界简报、周报。

本地版没有定时服务器，所以打开看板时检查一遍：今天的还没生成就在后台补上，看板上显示进度。
想让它每天自己跑，也可以把 `robot-times local run` 交给系统的计划任务（见 docs/dev/local-setup.md）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from ..cloud import repo
from ..cloud.daily import RELEASE_HOUR, TZ, generated_after_release
from ..cloud.store import Store
from ..cloud.weekly import iso_week

log = logging.getLogger("robot_times.jobs")

KINDS = ("daily", "news", "weekly")
LABELS = {"daily": "今天的推荐", "news": "业界简报", "weekly": "本周回顾"}
RETRY_AFTER = timedelta(hours=3)  # 失败或没有新论文时，隔多久再自动试


def due(store: Store, now: datetime | None = None) -> list[str]:
    """现在该自动跑哪些任务（按顺序）。"""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    today = now.date()
    tried: dict[str, str] = repo.get_json(store, "local_runs") or {}

    def tried_since(kind: str, t: datetime) -> bool:
        at = tried.get(kind)
        return bool(at) and datetime.fromisoformat(at) >= t

    out = []
    release = now.replace(hour=RELEASE_HOUR + 1, minute=0, second=0, microsecond=0)
    has_days = bool(store.rows("select 1 from days limit 1"))
    if not has_days:  # 第一次用：马上生成一份
        if not tried_since("daily", now - RETRY_AFTER):
            out.append("daily")
    elif (
        now >= release
        and not generated_after_release(store, today)
        and not tried_since("daily", max(release, now - RETRY_AFTER))
    ):
        out.append("daily")
    has_news = store.rows("select 1 from news where date = ?", [today.isoformat()])
    if not has_news and not tried_since("news", now - RETRY_AFTER):
        out.append("news")
    # 周报：周日晚上 6 点后写本周的；平时发现上周的还没写，就补上
    target = weekly_target(now)
    weeks = {r["week"] for r in store.rows("select week from weeks")}
    had_days = store.rows(
        "select 1 from days where date >= ? and date <= ? limit 1",
        [(target - timedelta(days=6)).isoformat(), target.isoformat()],
    )
    if iso_week(target) not in weeks and had_days and not tried_since("weekly", now - RETRY_AFTER):
        out.append("weekly")
    return out


def weekly_target(now: datetime) -> date:
    """自动写周报时写哪一周（用那周的周日表示）：周日晚上写本周，其他时候写上一周。"""
    today = now.astimezone(TZ).date()
    if today.isoweekday() == 7:
        return today if now.astimezone(TZ).hour >= 18 else today - timedelta(days=7)
    return today - timedelta(days=today.isoweekday())


class Jobs:
    """同一时间只跑一个任务，其余排队。runners[kind]() 返回一句结果说明。"""

    def __init__(
        self,
        store: Store,
        runners: dict[str, Callable[..., str]],
        *,
        background: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(TZ),
    ) -> None:
        self.store = store
        self.runners = runners
        self.background = background
        self.clock = clock
        self._lock = threading.Lock()
        self._queue: list[tuple[str, bool]] = []
        self._current: str | None = None
        self._last: dict[str, Any] | None = None
        self._started: str | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._current,
                "label": LABELS.get(self._current or "", ""),
                "startedAt": self._started,
                "queue": [k for k, _ in self._queue],
                "last": self._last,
            }

    def auto(self) -> dict[str, Any]:
        """打开看板时调用：今天该有的还没有，就排进队列。"""
        with self._lock:
            busy = {self._current, *(k for k, _ in self._queue)}
        for kind in due(self.store, self.clock()):
            if kind not in busy:
                self._enqueue(kind, False)
        return self.status()

    def start(self, kind: str, force: bool = True) -> dict[str, Any]:
        """看板上点“现在生成”。"""
        if kind not in self.runners:
            raise ValueError(kind)
        with self._lock:
            busy = kind == self._current or any(k == kind for k, _ in self._queue)
        if not busy:
            self._enqueue(kind, force)
        return self.status()

    def _enqueue(self, kind: str, force: bool) -> None:
        with self._lock:
            self._queue.append((kind, force))
            if self._current is not None:
                return
            self._current = "starting"
        if self.background:
            threading.Thread(target=self._drain, name="robot-times-jobs", daemon=True).start()
        else:
            self._drain()

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._current = None
                    self._started = None
                    return
                kind, force = self._queue.pop(0)
                self._current = kind
                self._started = self.clock().isoformat(timespec="seconds")
            log.info("开始：%s", LABELS[kind])
            result: dict[str, Any] = {"kind": kind, "ok": True, "message": ""}
            try:
                result["message"] = self.runners[kind](force=force) or ""
            except Exception as e:  # 一个任务失败不影响下一个
                log.exception("%s 失败", LABELS[kind])
                result.update(ok=False, message=f"{type(e).__name__}: {str(e)[:300]}")
            finally:
                tried = repo.get_json(self.store, "local_runs") or {}
                tried[kind] = self.clock().isoformat(timespec="seconds")
                repo.set_json(self.store, "local_runs", tried)
            result["at"] = self.clock().isoformat(timespec="seconds")
            with self._lock:
                self._last = result
            log.info("完成：%s %s", LABELS[kind], result["message"])
