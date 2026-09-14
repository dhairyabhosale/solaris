from fastapi.testclient import TestClient

from app.main import app
from app.routes import chat as chat_route

client = TestClient(app)


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
        return "Risk level: HIGH. Take a 10-min shaded break every 45 minutes and drink 250ml water each break."

    monkeypatch.setattr(chat_route.weather, "get_current_weather", fake_weather)
    monkeypatch.setattr(chat_route.llm, "ask", fake_ask)

    resp = client.post("/chat", json={"worker_id": "worker-2", "message": "What's today's risk?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "HIGH" in body["reply"]
    assert body["session_id"].startswith("worker-2:")
