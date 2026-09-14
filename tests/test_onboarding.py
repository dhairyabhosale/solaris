from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_onboarding_creates_profile():
    resp = client.post(
        "/onboarding",
        json={
            "worker_id": "worker-1",
            "work_type": "construction - rebar",
            "work_start": "07:00",
            "work_end": "16:00",
            "location": "Chennai",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_id"] == "worker-1"
    assert "rebar" in body["message"]
