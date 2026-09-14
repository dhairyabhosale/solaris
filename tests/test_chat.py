import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routes import chat as chat_route
from app.services.prism import LLMUnavailable

client = TestClient(app)


def _onboard(worker_id: str) -> None:
    client.post(
        "/onboarding",
        json={
            "worker_id": worker_id,
            "work_type": "general labor",
            "work_start": "07:00",
            "work_end": "16:00",
            "location": "Vellore",
        },
    )


async def _fake_weather(location):
    return {"location": location, "temperature_c": 36.0, "feels_like_c": 40.0, "humidity_pct": 50}


def test_chat_requires_onboarding():
    resp = client.post("/chat", json={"worker_id": "unknown-worker", "message": "hi"})
    assert resp.status_code == 404


def test_chat_returns_reply(monkeypatch):
    client.post(
        "/onboarding",
        json={
            "worker_id": "worker-2",
            "work_type": "mason",
            "work_start": "08:00",
            "work_end": "17:00",
            "location": "Chennai",
        },
    )

    async def fake_weather(location):
        return {
            "location": location,
            "temperature_c": 38.0,
            "feels_like_c": 42.0,
            "humidity_pct": 55,
            "fetched_at": "2026-09-14T12:00",
        }

    async def fake_ask(**kwargs):
        return {
            "risk_level": "high",
            "escalate": False,
            "message": "Take a 10-min shaded break every 45 minutes and drink 250ml water each break.",
        }

    monkeypatch.setattr(chat_route.weather, "get_current_weather", fake_weather)
    monkeypatch.setattr(chat_route.llm, "ask", fake_ask)

    resp = client.post("/chat", json={"worker_id": "worker-2", "message": "What's today's risk?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_level"] == "high"
    assert body["escalate"] is False
    assert "shaded break" in body["reply"]
    assert body["session_id"].startswith("worker-2:")


def test_chat_caches_risk_level_across_turns(monkeypatch):
    client.post(
        "/onboarding",
        json={
            "worker_id": "worker-3",
            "work_type": "roofing",
            "work_start": "08:00",
            "work_end": "17:00",
            "location": "Delhi",
        },
    )

    async def fake_weather(location):
        return {
            "location": location,
            "temperature_c": 40.0,
            "feels_like_c": 45.0,
            "humidity_pct": 60,
            "fetched_at": "2026-09-14T12:00",
        }

    calls = {"n": 0}

    async def fake_ask(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"risk_level": "extreme", "escalate": False, "message": "First message."}
        return {"risk_level": None, "escalate": False, "message": "Follow-up without a fresh risk_level."}

    monkeypatch.setattr(chat_route.weather, "get_current_weather", fake_weather)
    monkeypatch.setattr(chat_route.llm, "ask", fake_ask)

    first = client.post("/chat", json={"worker_id": "worker-3", "message": "Starting shift"})
    second = client.post("/chat", json={"worker_id": "worker-3", "message": "Can't take a break right now"})

    assert first.json()["risk_level"] == "extreme"
    assert second.json()["risk_level"] == "extreme"


def test_chat_returns_503_with_readable_detail_when_llm_unavailable(monkeypatch):
    _onboard("worker-quota")

    async def failing_ask(**kwargs):
        raise LLMUnavailable("Solaris has reached its daily AI usage limit. Please try again later.")

    monkeypatch.setattr(chat_route.weather, "get_current_weather", _fake_weather)
    monkeypatch.setattr(chat_route.llm, "ask", failing_ask)

    resp = client.post("/chat", json={"worker_id": "worker-quota", "message": "Hello"})
    assert resp.status_code == 503
    assert "daily AI usage limit" in resp.json()["detail"]


def test_failed_chat_does_not_record_the_turn(monkeypatch):
    _onboard("worker-no-history")

    async def failing_ask(**kwargs):
        raise LLMUnavailable("busy")

    monkeypatch.setattr(chat_route.weather, "get_current_weather", _fake_weather)
    monkeypatch.setattr(chat_route.llm, "ask", failing_ask)

    client.post("/chat", json={"worker_id": "worker-no-history", "message": "Hello"})
    status = client.get("/worker/worker-no-history/status").json()
    assert status["history"] == []


def test_chat_returns_502_when_weather_service_is_down(monkeypatch):
    _onboard("worker-weather-down")

    async def weather_down(location):
        raise httpx.ConnectError("open-meteo unreachable")

    monkeypatch.setattr(chat_route.weather, "get_current_weather", weather_down)

    resp = client.post("/chat", json={"worker_id": "worker-weather-down", "message": "Hello"})
    assert resp.status_code == 502
    assert "weather" in resp.json()["detail"]
