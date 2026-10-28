"""天气：解析 Open-Meteo 的返回，存进数据库；抓不到不报错。"""

from datetime import date

import httpx

from robot_times.cloud import repo
from robot_times.cloud.store import SqliteStore
from robot_times.news.weather import save_weather


def test_save_weather():
    def handler(req: httpx.Request) -> httpx.Response:
        if "geocoding" in req.url.host:
            assert req.url.params["name"] == "杭州"
            return httpx.Response(200, json={"results": [
                {"name": "杭州市", "latitude": 30.274, "longitude": 120.155}]})  # fmt: skip
        assert (
            req.url.params["latitude"] == "30.274" and req.url.params["start_date"] == "2026-10-26"
        )
        return httpx.Response(200, json={"daily": {
            "weather_code": [61], "temperature_2m_max": [21.6], "temperature_2m_min": [14.2],
            "precipitation_probability_max": [80]}})  # fmt: skip

    store = SqliteStore()
    w = save_weather(store, date(2026, 10, 26), "杭州", transport=httpx.MockTransport(handler))
    assert w["text"] == "小雨" and w["kind"] == "rain" and w["tmax"] == 22 and w["tmin"] == 14
    assert repo.get_json(store, "weather")["city"] == "杭州市"


def test_weather_failure_is_ignored():
    store = SqliteStore()
    down = httpx.MockTransport(lambda req: httpx.Response(503))
    assert save_weather(store, date(2026, 10, 26), "杭州", transport=down) is None
    assert repo.get_json(store, "weather") is None


def test_no_city_no_request():
    def fail(req: httpx.Request) -> httpx.Response:
        raise AssertionError("没填城市不该联网")

    assert (
        save_weather(SqliteStore(), date(2026, 10, 26), "", transport=httpx.MockTransport(fail))
        is None
    )
