"""
Weather lookups via Open-Meteo (free, no API key).

Two calls: geocode the worker's location string to coordinates, then fetch
current temperature/humidity (plus today's hourly forecast, to find the
peak heat hour) for those coordinates. Both are cached per-day in memory
so a busy day of chat traffic for the same worker doesn't hammer the API
or make the risk assessment drift within a day.
"""

from typing import Optional

import httpx

from app.config import local_today

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_geocode_cache: dict[str, tuple[float, float, str]] = {}
_weather_cache: dict[tuple[str, str], dict] = {}


async def geocode_location(location: str) -> tuple[float, float, str]:
    if location in _geocode_cache:
        return _geocode_cache[location]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(GEOCODE_URL, params={"name": location, "count": 1})
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results")
    if not results:
        raise ValueError(f"Could not resolve location '{location}'")

    top = results[0]
    coords = (top["latitude"], top["longitude"], top.get("name", location))
    _geocode_cache[location] = coords
    return coords


def _peak_heat_hour(hourly: dict) -> Optional[int]:
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    valid = [(t, temp) for t, temp in zip(times, temps) if temp is not None]
    if not valid:
        return None

    best_time, _ = max(valid, key=lambda pair: pair[1])
    try:
        return int(best_time.split("T")[1].split(":")[0])
    except (IndexError, ValueError):
        return None


async def get_current_weather(location: str) -> dict:
    cache_key = (location, local_today().isoformat())
    if cache_key in _weather_cache:
        return _weather_cache[cache_key]

    lat, lon, resolved_name = await geocode_location(location)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature",
                "hourly": "temperature_2m",
                "forecast_days": 1,
                "timezone": "auto",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    current = data.get("current", {})
    result = {
        "location": resolved_name,
        "temperature_c": current.get("temperature_2m"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "fetched_at": current.get("time"),
        "peak_heat_hour": _peak_heat_hour(data.get("hourly", {})),
    }
    _weather_cache[cache_key] = result
    return result
