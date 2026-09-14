import asyncio

import pytest

from app.services import llm


@pytest.mark.parametrize(
    "raw",
    [
        '{"risk_level": "high", "escalate": false, "message": "Break every 45 min."}',
        '```json\n{"risk_level": "high", "escalate": false, "message": "Break every 45 min."}\n```',
        '<think>Feels like 41C, so high.</think>\n{"risk_level": "high", "escalate": false, "message": "Break every 45 min."}',
        'Here is the assessment:\n{"risk_level": "HIGH ", "escalate": "false", "message": "Break every 45 min."}',
    ],
)
def test_parses_structured_reply_across_model_output_styles(raw):
    assert llm._parse_structured_reply(raw) == {
        "risk_level": "high",
        "escalate": False,
        "message": "Break every 45 min.",
    }


def test_string_true_escalate_is_honoured():
    parsed = llm._parse_structured_reply('{"risk_level": "extreme", "escalate": "true", "message": "Stop work."}')
    assert parsed["escalate"] is True


def test_unknown_risk_level_is_dropped_so_the_route_keeps_todays_level():
    parsed = llm._parse_structured_reply('{"risk_level": "severe", "escalate": false, "message": "Hydrate."}')
    assert parsed["risk_level"] is None
    assert parsed["message"] == "Hydrate."


def test_non_json_reply_falls_back_to_plain_text_without_reasoning():
    parsed = llm._parse_structured_reply("<think>hmm</think>Drink water and rest in shade.")
    assert parsed == {"risk_level": None, "escalate": False, "message": "Drink water and rest in shade."}


def test_ask_sends_system_prompt_history_and_schema_in_openai_format(monkeypatch):
    captured = {}

    async def fake_chat_completion(session_id, worker_id, request):
        captured.update(session_id=session_id, worker_id=worker_id, request=request)
        return '{"risk_level": "moderate", "escalate": false, "message": "ok"}'

    monkeypatch.setattr(llm.prism, "chat_completion", fake_chat_completion)

    history = [
        {"role": "user", "content": "Checking in"},
        {"role": "model", "content": "Moderate risk today."},
    ]
    result = asyncio.run(
        llm.ask(
            session_id="w1:2026-09-14",
            worker_id="w1",
            system_prompt="SYSTEM",
            history=history,
            user_message="Can I skip a break?",
        )
    )

    assert result == {"risk_level": "moderate", "escalate": False, "message": "ok"}
    assert captured["session_id"] == "w1:2026-09-14"
    assert captured["worker_id"] == "w1"
    assert captured["request"]["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Checking in"},
        {"role": "assistant", "content": "Moderate risk today."},
        {"role": "user", "content": "Can I skip a break?"},
    ]
    assert captured["request"]["response_format"] == llm.RESPONSE_FORMAT


def test_system_prompt_template_still_formats():
    from app.models import WorkerProfile

    profile = WorkerProfile(worker_id="w1", work_type="roofing", work_start="08:00", work_end="17:00", location="Delhi")
    prompt = llm.build_system_prompt(
        profile, {"location": "Delhi", "temperature_c": 40, "feels_like_c": 45, "humidity_pct": 30}
    )
    assert "roofing" in prompt and "45 deg C" in prompt
    assert '{"risk_level": "low" | "moderate" | "high" | "extreme"' in prompt
