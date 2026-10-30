"""本地版的后台任务：每日推荐、业界简报、周报。

看板在后台运行时，每 10 分钟看一遍（scheduler）：到点了、今天的还没有，就在后台生成。
- 每日推荐：工作日北京时间 9:30 以后（arXiv 8 点或 9 点发布新论文，周末不发布）
- 业界简报：每天 8 点以后
- 本周回顾：周日晚上 6 点以后；平时发现上周的还没写就补上
打开看板时也会检查一遍。失败了 3 小时后再自动试，也可以在看板上点“重试”。
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
NEWS_HOUR = 8  # 业界简报几点以后生成（北京时间）
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
    release = now.replace(hour=RELEASE_HOUR + 1, minute=30, second=0, microsecond=0)
    weekday = now.isoweekday() <= 5  # 周末 arXiv 不发布新论文
    has_days = bool(store.rows("select 1 from days limit 1"))
    if not has_days:  # 第一次用：马上生成一份
        if not tried_since("daily", now - RETRY_AFTER):
            out.append("daily")
    elif (
        weekday
        and now >= release
        and not generated_after_release(store, today)
        and not tried_since("daily", max(release, now - RETRY_AFTER))
    ):
        out.append("daily")
    has_news = store.rows("select 1 from news where date = ?", [today.isoformat()])
    first_news = not store.rows("select 1 from news limit 1")
    if (
        not has_news
        and (now.hour >= NEWS_HOUR or first_news)
        and not tried_since("news", now - RETRY_AFTER)
    ):
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


class WaitForZotero(RuntimeError):
    """Zotero 没开。和别的失败不同，不用等 3 小时再试：Zotero 打开后下一次定时检查就生成。"""


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
                "last": self._last or self._remembered(),
            }

    def _remembered(self) -> dict[str, Any] | None:
        """看板重启过：用记下来的上一次结果（失败了重启也还能看到原因）。"""
        try:
            from .diagnose import history

            h = history(self.store)
        except Exception:
            return None
        return h[0] if h else None

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
            waiting = False
            try:
                result["message"] = self.runners[kind](force=force) or ""
            except (
                WaitForZotero
            ) as e:  # Zotero 没开：不算“试过”，定时检查时 Zotero 一打开就接着生成
                waiting = True
                log.info("%s：%s", LABELS[kind], e)
                result.update(ok=False, message=f"RuntimeError: {e}")
            except Exception as e:  # 一个任务失败不影响下一个
                log.exception("%s 失败", LABELS[kind])
                result.update(ok=False, message=f"{type(e).__name__}: {str(e)[:300]}")
            finally:
                if not waiting:
                    tried = repo.get_json(self.store, "local_runs") or {}
                    tried[kind] = self.clock().isoformat(timespec="seconds")
                    repo.set_json(self.store, "local_runs", tried)
            result["at"] = self.clock().isoformat(timespec="seconds")
            if not result["ok"]:  # 看板上显示原因和办法
                from .diagnose import explain

                result.update(explain(result["message"], kind))
            try:
                from .diagnose import record

                record(self.store, result)
            except Exception:
                log.exception("记录任务结果失败")
            with self._lock:
                self._last = result
            log.info("完成：%s %s", LABELS[kind], result["message"])


def scheduler(
    jobs: Jobs, ready: Callable[[], bool], every: float = 600.0, first: float = 20.0
) -> threading.Thread:
    """后台线程：每 10 分钟看一遍该生成什么（电脑睡眠醒来后也会接着跑）。设置引导没做完时不生成。"""

    def loop() -> None:
        import time

        time.sleep(first)
        while True:
            try:
                if ready():
                    jobs.auto()
            except Exception:
                log.exception("定时检查出错")
            time.sleep(every)

    t = threading.Thread(target=loop, name="robot-times-schedule", daemon=True)
    t.start()
    return t
