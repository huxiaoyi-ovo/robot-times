"""每天抓一次当天的天气预报（Open-Meteo，免费、不用密钥），存进数据库给看板右上角用。

地点来自设置里的 weatherCity（城市名，中英文都行）；没填就不抓。看板打开时还会按浏览器定位或
用户在页面上输入的地点取实时天气，这里只是兜底。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from ..cloud import repo
from ..cloud.store import Store

log = logging.getLogger("robot_times.weather")

URL = "https://api.open-meteo.com/v1/forecast"
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"

# WMO 天气代码 → (中文, 图标种类)。图标种类：sun / partly / cloud / fog / rain / snow / storm
CODES: dict[int, tuple[str, str]] = {
    0: ("晴", "sun"), 1: ("晴间多云", "partly"), 2: ("多云", "partly"), 3: ("阴", "cloud"),
    45: ("雾", "fog"), 48: ("雾凇", "fog"),
    51: ("毛毛雨", "rain"), 53: ("毛毛雨", "rain"), 55: ("毛毛雨", "rain"),
    56: ("冻毛毛雨", "rain"), 57: ("冻毛毛雨", "rain"),
    61: ("小雨", "rain"), 63: ("中雨", "rain"), 65: ("大雨", "rain"),
    66: ("冻雨", "rain"), 67: ("冻雨", "rain"),
    71: ("小雪", "snow"), 73: ("中雪", "snow"), 75: ("大雪", "snow"), 77: ("雪粒", "snow"),
    80: ("阵雨", "rain"), 81: ("阵雨", "rain"), 82: ("强阵雨", "rain"),
    85: ("阵雪", "snow"), 86: ("强阵雪", "snow"),
    95: ("雷阵雨", "storm"), 96: ("雷阵雨伴冰雹", "storm"), 99: ("雷阵雨伴冰雹", "storm"),
}  # fmt: skip


def geocode(city: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any] | None:
    """城市名 → {name, lat, lon}；找不到返回 None。"""
    with httpx.Client(transport=transport, timeout=15.0) as http:
        resp = http.get(GEO_URL, params={"name": city, "count": 1, "language": "zh"})
        resp.raise_for_status()
        hits = resp.json().get("results") or []
    if not hits:
        return None
    h = hits[0]
    return {"name": h.get("name") or city, "lat": h["latitude"], "lon": h["longitude"]}


def fetch_weather(
    day: date, place: dict[str, Any], transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    params = {
        "latitude": place["lat"],
        "longitude": place["lon"],
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
    }
    with httpx.Client(transport=transport, timeout=15.0) as http:
        resp = http.get(URL, params=params)
        resp.raise_for_status()
        d = resp.json()["daily"]
    code = int(d["weather_code"][0])
    text, kind = CODES.get(code, ("多云", "cloud"))
    pop = (d.get("precipitation_probability_max") or [None])[0]
    return {
        "date": day.isoformat(),
        "city": place["name"],
        "code": code,
        "text": text,
        "kind": kind,
        "tmax": round(d["temperature_2m_max"][0]),
        "tmin": round(d["temperature_2m_min"][0]),
        "pop": pop,
    }


def save_weather(
    store: Store, day: date, city: str, transport: httpx.BaseTransport | None = None
) -> dict | None:
    """city 为空就不抓；抓不到就算了，不影响别的。"""
    if not city.strip():
        return None
    try:
        place = geocode(city.strip(), transport=transport)
        if place is None:
            log.warning("找不到这个地点：%s", city)
            return None
        w = fetch_weather(day, place, transport=transport)
    except Exception as e:
        log.warning("天气没抓到：%s", e)
        return None
    repo.set_json(store, "weather", w)
    log.info("天气：%s %s %s–%s°", w["city"], w["text"], w["tmin"], w["tmax"])
    return w
