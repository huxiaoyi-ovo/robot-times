import sqlite3
from datetime import datetime, timedelta

import httpx
import pytest

from robot_times.cloud import repo, weekly
from robot_times.cloud.store import SqliteStore
from robot_times.local import app
from robot_times.local import jobs as scheduler_jobs
from robot_times.ranking import embedder
from robot_times.sources import arxiv_source
from robot_times.zotero.local_api import ZoteroLocalClient


def daily_due_store(now):
    store = SqliteStore()
    store.run("insert into news (date, data) values (?, '{}')", [now.date().isoformat()])
    return store


def test_auto_discards_due_decision_after_a_newer_completion(monkeypatch):
    now = datetime(2026, 11, 20, 10, tzinfo=scheduler_jobs.TZ)
    store = daily_due_store(now)
    calls = []
    jobs = scheduler_jobs.Jobs(
        store,
        {"daily": lambda force: calls.append(force)},
        background=False,
        clock=lambda: now,
    )
    due = scheduler_jobs.due

    def due_before_manual_completion(*args, **kwargs):
        result = due(*args, **kwargs)
        assert result == ["daily"]
        jobs.start("daily")
        return result

    monkeypatch.setattr(scheduler_jobs, "due", due_before_manual_completion)

    jobs.auto()

    assert calls == [True]


def test_automatic_cooldown_survives_retry_log_persistence_failure(monkeypatch):
    now = datetime(2026, 11, 20, 10, tzinfo=scheduler_jobs.TZ)
    store = daily_due_store(now)
    calls = []
    save = repo.set_json

    def set_json(store, key, value):
        if key == "local_runs":
            raise sqlite3.OperationalError("database is locked")
        save(store, key, value)

    def failed_run(force):
        calls.append(force)
        raise RuntimeError("fixture job failed")

    monkeypatch.setattr(repo, "set_json", set_json)
    jobs = scheduler_jobs.Jobs(store, {"daily": failed_run}, background=False, clock=lambda: now)

    jobs.auto()
    now += timedelta(minutes=10)
    jobs.auto()
    assert calls == [False]
    assert repo.get_json(store, "local_runs") is None

    now += scheduler_jobs.RETRY_AFTER
    jobs.auto()
    assert calls == [False, False]


@pytest.mark.parametrize("record", ["not-json", "[]", '{"daily":"invalid"}', '{"daily":123}'])
def test_invalid_retry_record_does_not_block_scheduling(record):
    now = datetime(2026, 11, 20, 10, tzinfo=scheduler_jobs.TZ)
    store = daily_due_store(now)
    store.run("insert into config (key, value) values ('local_runs', ?)", [record])

    assert scheduler_jobs.due(store, now) == ["daily"]


def test_legacy_naive_retry_time_uses_the_schedule_timezone():
    now = datetime(2026, 11, 20, 10, tzinfo=scheduler_jobs.TZ)
    store = daily_due_store(now)
    repo.set_json(store, "local_runs", {"daily": "2026-11-20T08:00:00"})

    assert scheduler_jobs.due(store, now) == []
    assert scheduler_jobs.due(store, now + timedelta(hours=2)) == ["daily"]


def test_queued_weekly_keeps_its_target_past_the_sunday_boundary(monkeypatch):
    before = datetime(2026, 11, 22, 17, 59, tzinfo=scheduler_jobs.TZ)
    after = before + timedelta(minutes=2)
    store = daily_due_store(before)
    for day in ("2026-11-12", "2026-11-20"):
        repo.save_day(store, {"date": day, "picks": []})
    targets = []

    class WallClock:
        @staticmethod
        def now(tz):
            return after.astimezone(tz)

    def run_weekly(store, llm, settings, day):
        targets.append(day)
        return {"headline": "fixture"}

    monkeypatch.setattr(app, "datetime", WallClock)
    monkeypatch.setattr(app, "llm_from_env", lambda: None)
    monkeypatch.setattr(weekly, "run_weekly", run_weekly)
    jobs = scheduler_jobs.Jobs(
        store, app.make_runners(store, object()), background=False, clock=lambda: before
    )

    jobs.auto()

    assert [day.isoformat() for day in targets] == ["2026-11-15"]


def test_daily_retries_when_zotero_drops_after_a_successful_probe(monkeypatch):
    now = datetime(2026, 11, 20, 10, tzinfo=scheduler_jobs.TZ)
    store = daily_due_store(now)
    disconnected = True

    def request(req):
        headers = {"Zotero-Server-ID": "fixture"}
        if req.url.path == "/api/":
            return httpx.Response(200, headers=headers)
        assert req.url.path == "/api/users/0/items/top"
        if disconnected:
            raise httpx.ConnectError("fixture Zotero closed", request=req)
        return httpx.Response(200, headers=headers, json=[])

    monkeypatch.setattr(embedder, "default_embedder", lambda name: embedder.HashingEmbedder())
    monkeypatch.setattr(arxiv_source, "fetch_recent", lambda *args, **kwargs: [])
    monkeypatch.setattr(app, "llm_from_env", lambda: None)

    zotero = ZoteroLocalClient(transport=httpx.MockTransport(request))
    jobs = scheduler_jobs.Jobs(
        store, app.make_runners(store, zotero), background=False, clock=lambda: now
    )

    first = jobs.auto()
    assert not first["last"]["ok"]
    assert repo.get_json(store, "local_runs") is None

    now += timedelta(minutes=10)
    disconnected = False
    assert jobs.auto()["last"]["ok"]


def test_slow_library_read_counts_as_an_attempt_instead_of_waiting(monkeypatch):
    now = datetime(2026, 11, 20, 10, tzinfo=scheduler_jobs.TZ)
    store = daily_due_store(now)
    reads = []

    def request(req):
        headers = {"Zotero-Server-ID": "fixture"}
        if req.url.path == "/api/":
            return httpx.Response(200, headers=headers)
        assert req.url.path == "/api/users/0/items/top"
        reads.append(req)
        raise httpx.ReadTimeout("fixture library read is slow", request=req)

    monkeypatch.setattr(app, "llm_from_env", lambda: None)

    zotero = ZoteroLocalClient(transport=httpx.MockTransport(request))
    jobs = scheduler_jobs.Jobs(
        store, app.make_runners(store, zotero), background=False, clock=lambda: now
    )

    first = jobs.auto()
    assert not first["last"]["ok"]
    assert "超时" in first["last"]["message"]
    assert "daily" in repo.get_json(store, "local_runs")

    # 还在重试间隔内：不再马上重跑
    now += timedelta(minutes=10)
    jobs.auto()
    assert len(reads) == 1
