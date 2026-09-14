import asyncio

import httpx
import pytest

from app.services import weather


def _use_transport(monkeypatch, responses):
    """Serve `responses` in order; returns the list of requests the client sent."""
    sent = []
    queue = list(responses)

    def handler(request):
        sent.append(request)
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    real_client = weather._http_client
    monkeypatch.setattr(weather, "_http_client", None)
    mocked = httpx.AsyncClient(timeout=15, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(weather, "_client", lambda: mocked)
    return sent, real_client


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    monkeypatch.setattr(weather, "_geocode_cache", {})
    monkeypatch.setattr(weather, "_weather_cache", {})


class _FakeKV:
    """In-process stand-in for app.services.kv, so tests exercise the same
    code path Redis would (cache-hit skips the network) without needing Redis."""

    def __init__(self):
        self.store = {}

    def is_configured(self):
        return True

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value):
        self.store[key] = value


def _geocode_response():
    return httpx.Response(200, json={"results": [{"latitude": 13.08, "longitude": 80.27, "name": "Chennai"}]})


def _forecast_response(temp=34.0):
    return httpx.Response(
        200,
        json={
            "current": {
                "temperature_2m": temp,
                "relative_humidity_2m": 80,
                "apparent_temperature": temp + 5,
                "time": "2026-09-14T12:00",
            },
            "hourly": {"time": ["2026-09-14T13:00"], "temperature_2m": [temp + 6]},
        },
    )


def test_geocode_is_cached_in_memory_when_kv_not_configured(monkeypatch):
    sent, _ = _use_transport(monkeypatch, [_geocode_response()])

    first = asyncio.run(weather.geocode_location("Chennai"))
    second = asyncio.run(weather.geocode_location("Chennai"))

    assert first == second == (13.08, 80.27, "Chennai")
    assert len(sent) == 1  # second call was a cache hit, no network request


def test_weather_is_cached_in_memory_for_same_location_and_day(monkeypatch):
    sent, _ = _use_transport(monkeypatch, [_geocode_response(), _forecast_response()])

    first = asyncio.run(weather.get_current_weather("Chennai"))
    second = asyncio.run(weather.get_current_weather("Chennai"))

    assert first == second
    assert first["peak_heat_hour"] == 13
    assert len(sent) == 2  # one geocode + one forecast call, never repeated


def test_geocode_uses_kv_when_configured_and_skips_network_on_hit(monkeypatch):
    fake_kv = _FakeKV()
    monkeypatch.setattr(weather, "kv", fake_kv)
    sent, _ = _use_transport(monkeypatch, [_geocode_response()])

    first = asyncio.run(weather.geocode_location("Chennai"))
    second = asyncio.run(weather.geocode_location("Chennai"))

    assert first == second == (13.08, 80.27, "Chennai")
    assert len(sent) == 1
    assert "geocode:chennai" in fake_kv.store


def test_weather_uses_kv_when_configured_and_skips_network_on_hit(monkeypatch):
    fake_kv = _FakeKV()
    monkeypatch.setattr(weather, "kv", fake_kv)
    sent, _ = _use_transport(monkeypatch, [_geocode_response(), _forecast_response()])

    first = asyncio.run(weather.get_current_weather("Chennai"))
    second = asyncio.run(weather.get_current_weather("Chennai"))

    assert first == second
    assert len(sent) == 2
    assert any(k.startswith("weather:chennai:") for k in fake_kv.store)


def test_different_locations_are_cached_separately(monkeypatch):
    sent, _ = _use_transport(
        monkeypatch,
        [_geocode_response(), _forecast_response(temp=34.0), _geocode_response(), _forecast_response(temp=22.0)],
    )

    chennai = asyncio.run(weather.get_current_weather("Chennai"))
    delhi = asyncio.run(weather.get_current_weather("Delhi"))

    assert chennai["temperature_c"] != delhi["temperature_c"]
    assert len(sent) == 4


def test_unresolvable_location_raises_value_error(monkeypatch):
    _use_transport(monkeypatch, [httpx.Response(200, json={"results": []})])

    with pytest.raises(ValueError, match="Could not resolve location"):
        asyncio.run(weather.geocode_location("Nowhereville"))
