import asyncio

import pytest

from app.services import llm


@pytest.mark.parametrize(
    "raw",
    [
        '{"risk_level": "high", "escalate": false, "message": "Break every 45 min.", "schedule": null}',
        '```json\n{"risk_level": "high", "escalate": false, "message": "Break every 45 min.", "schedule": null}\n```',
        '<think>Feels like 41C, so high.</think>\n'
        '{"risk_level": "high", "escalate": false, "message": "Break every 45 min.", "schedule": null}',
        'Here is the assessment:\n'
        '{"risk_level": "HIGH ", "escalate": "false", "message": "Break every 45 min.", "schedule": null}',
    ],
)
def test_parses_structured_reply_across_model_output_styles(raw):
    assert llm._parse_structured_reply(raw) == {
        "risk_level": "high",
        "escalate": False,
        "message": "Break every 45 min.",
        "schedule": None,
    }


def test_string_true_escalate_is_honoured():
    parsed = llm._parse_structured_reply(
        '{"risk_level": "extreme", "escalate": "true", "message": "Stop work.", "schedule": null}'
    )
    assert parsed["escalate"] is True


def test_unknown_risk_level_is_dropped_so_the_route_keeps_todays_level():
    parsed = llm._parse_structured_reply(
        '{"risk_level": "severe", "escalate": false, "message": "Hydrate.", "schedule": null}'
    )
    assert parsed["risk_level"] is None
    assert parsed["message"] == "Hydrate."


def test_non_json_reply_falls_back_to_plain_text_without_reasoning():
    parsed = llm._parse_structured_reply("<think>hmm</think>Drink water and rest in shade.")
    assert parsed == {
        "risk_level": None,
        "escalate": False,
        "message": "Drink water and rest in shade.",
        "schedule": None,
    }


def test_valid_schedule_is_parsed_and_sorted_by_time():
    raw = (
        '{"risk_level": "high", "escalate": false, "message": "Plan for today.", "schedule": ['
        '{"time": "11:00", "action": "Break", "detail": "15 min shade"},'
        '{"time": "09:00", "action": "Hydrate", "detail": "500 ml water"}'
        "]}"
    )
    parsed = llm._parse_structured_reply(raw)
    assert parsed["schedule"] == [
        {"time": "09:00", "action": "Hydrate", "detail": "500 ml water"},
        {"time": "11:00", "action": "Break", "detail": "15 min shade"},
    ]


def test_schedule_steps_missing_a_field_are_dropped_not_shown_broken():
    raw = (
        '{"risk_level": "high", "escalate": false, "message": "Plan.", "schedule": ['
        '{"time": "09:00", "action": "Break", "detail": "15 min"},'
        '{"time": "bad-time", "action": "Break", "detail": "oops"},'
        '{"time": "11:00", "action": "", "detail": "oops"}'
        "]}"
    )
    parsed = llm._parse_structured_reply(raw)
    assert parsed["schedule"] == [{"time": "09:00", "action": "Break", "detail": "15 min"}]


def test_empty_schedule_list_becomes_none():
    raw = '{"risk_level": "low", "escalate": false, "message": "All good.", "schedule": []}'
    assert llm._parse_structured_reply(raw)["schedule"] is None


def test_non_list_schedule_becomes_none():
    raw = '{"risk_level": "low", "escalate": false, "message": "All good.", "schedule": "not a list"}'
    assert llm._parse_structured_reply(raw)["schedule"] is None


def test_ask_sends_system_prompt_history_and_schema_in_openai_format(monkeypatch):
    captured = {}

    async def fake_chat_completion(session_id, worker_id, request, background_tasks=None):
        captured.update(session_id=session_id, worker_id=worker_id, request=request)
        return '{"risk_level": "moderate", "escalate": false, "message": "ok", "schedule": null}'

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

    assert result == {"risk_level": "moderate", "escalate": False, "message": "ok", "schedule": None}
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
    assert '"schedule":' in prompt
