"""
Weather lookups via Open-Meteo (free, no API key).

Two calls: geocode the worker's location string to coordinates, then fetch
current temperature/humidity (plus today's hourly forecast, to find the
peak heat hour) for those coordinates.

Both are cached in Redis (via app.services.kv) when REDIS_URL is set -
required on Vercel, where a plain in-process dict is useless as a cache
(each cold serverless instance starts with an empty one, so "cached"
weather was really being re-fetched from Open-Meteo on nearly every
request). Falls back to an in-memory dict for local dev. Geocoding
results never expire (a city's coordinates don't change); weather
results are keyed by day, so they naturally go stale on their own.
"""

from typing import Optional

import httpx

from app.config import local_today
from app.services import kv

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_geocode_cache: dict[str, tuple[float, float, str]] = {}
_weather_cache: dict[tuple[str, str], dict] = {}

# Reused across calls within a warm instance instead of paying a fresh
# TCP+TLS handshake to Open-Meteo on every single request.
_http_client: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=15)
    return _http_client


def _location_key(location: str) -> str:
    return location.strip().lower()


async def geocode_location(location: str) -> tuple[float, float, str]:
    cache_key = f"geocode:{_location_key(location)}"

    if kv.is_configured():
        cached = await kv.get_json(cache_key)
        if cached:
            return tuple(cached)
    elif location in _geocode_cache:
        return _geocode_cache[location]

    resp = await _client().get(GEOCODE_URL, params={"name": location, "count": 1})
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results")
    if not results:
        raise ValueError(f"Could not resolve location '{location}'")

    top = results[0]
    coords = (top["latitude"], top["longitude"], top.get("name", location))

    if kv.is_configured():
        await kv.set_json(cache_key, list(coords))
    else:
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
    today = local_today().isoformat()
    cache_key = f"weather:{_location_key(location)}:{today}"

    if kv.is_configured():
        cached = await kv.get_json(cache_key)
        if cached:
            return cached
    else:
        dict_key = (location, today)
        if dict_key in _weather_cache:
            return _weather_cache[dict_key]

    lat, lon, resolved_name = await geocode_location(location)

    resp = await _client().get(
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

    if kv.is_configured():
        await kv.set_json(cache_key, result)
    else:
        _weather_cache[(location, today)] = result
    return result
