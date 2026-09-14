"""
Prompt construction and the LLM call for a worker's daily check-in and
follow-up conversation. All requests are routed through
app.services.prism so tracing is centralized in one place.

The model is asked for structured JSON (OpenAI-style json_schema
response_format, strict mode) so risk_level and escalate are reliably
parseable - the frontend uses them for color-coded UI, and PRISM
evaluation can score them directly instead of scraping prose. Parsing
still tolerates code fences, reasoning tags, and loosely typed values,
since GROQ_MODEL could be pointed at a model without native structured
output support.
"""

import json
import re
from typing import Any, Optional

from fastapi import BackgroundTasks

from app.config import local_now
from app.models import WorkerProfile
from app.services import prism

RISK_LEVELS = ("low", "moderate", "high", "extreme")

SYSTEM_PROMPT_TEMPLATE = """You are Solaris, a heat-safety companion for outdoor and gig workers. \
You are talking directly to a worker on their phone during their shift. Be short, direct, and practical.

Worker profile:
- Work type: {work_type}
- Shift: {work_start} - {work_end}
- Location: {location}

Today's weather in {weather_location}:
- Temperature: {temperature_c} deg C
- Feels like: {feels_like_c} deg C
- Humidity: {humidity_pct}%

Current time: {current_time}

Your job in this conversation:
1. On the first message of the day, determine today's heat risk level - low, moderate, high, or \
extreme - based on the temperature, humidity, and the worker's job type and hours. Give a \
concrete break-and-hydration schedule tuned to that risk level and work type, and fill the \
"schedule" field with it as an ordered list of steps from now until the end of the shift - each \
step a real clock time ("HH:MM", 24-hour, grounded in the current time and shift hours above), \
a short action ("Break", "Hydrate", "Shift ends", etc.), and one concrete detail (how long, how \
much water). If risk is high or extreme, also put a clear, direct escalation warning in "message".
2. For follow-up questions, answer specifically using today's actual risk level (keep reporting \
the same risk_level you already gave today unless the conversation gives you a real reason to \
revise it) and the worker's situation. Only fill "schedule" again if the plan actually changed \
(e.g. they said they can't take a full break and you're giving a revised minimum plan) - for a \
plain question that doesn't change the plan, leave "schedule" as null and answer in "message". \
Never repeat generic advice disconnected from today's numbers.
3. If the worker describes possible heat-illness symptoms (dizziness, confusion, nausea, \
stopped sweating, cramps, rapid heartbeat, headache), immediately give clear first-aid \
guidance in "message": stop working, get to shade or a cool area, hydrate, loosen clothing, and \
seek medical help if symptoms are severe or don't improve. Say clearly that this is general \
safety information, not a medical diagnosis. Set escalate to true and leave "schedule" as null \
for this reply - it's not the moment for a break plan.
4. Keep "message" short - a few sentences, not an essay. Plain text only, no markdown formatting. \
"message" always has something in it, even when "schedule" is filled - a one-line summary or the \
escalation warning, not a repeat of the schedule's own detail text.

Respond with a single JSON object and nothing else, in exactly this shape:
{{"risk_level": "low" | "moderate" | "high" | "extreme", "escalate": true | false, \
"message": "...", "schedule": [{{"time": "HH:MM", "action": "...", "detail": "..."}}, ...] | null}}
"""

SCHEDULE_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "time": {"type": "string"},
        "action": {"type": "string"},
        "detail": {"type": "string"},
    },
    "required": ["time", "action", "detail"],
    "additionalProperties": False,
}

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "solaris_reply",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "risk_level": {"type": "string", "enum": list(RISK_LEVELS)},
                "escalate": {"type": "boolean"},
                "message": {"type": "string"},
                "schedule": {"type": ["array", "null"], "items": SCHEDULE_STEP_SCHEMA},
            },
            "required": ["risk_level", "escalate", "message", "schedule"],
            "additionalProperties": False,
        },
    },
}

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def build_system_prompt(profile: WorkerProfile, weather_data: dict) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        work_type=profile.work_type,
        work_start=profile.work_start,
        work_end=profile.work_end,
        location=profile.location,
        weather_location=weather_data["location"],
        temperature_c=weather_data["temperature_c"],
        feels_like_c=weather_data["feels_like_c"],
        humidity_pct=weather_data["humidity_pct"],
        current_time=local_now().strftime("%H:%M"),
    )


def _first_json_object(text: str) -> Optional[dict]:
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _parse_schedule(value: Any) -> Optional[list[dict]]:
    if not isinstance(value, list):
        return None

    steps = []
    for item in value:
        if not isinstance(item, dict):
            continue
        time_str = str(item.get("time") or "").strip()
        action = str(item.get("action") or "").strip()
        detail = str(item.get("detail") or "").strip()
        # A model that ignores the schema (best-effort mode, or a swapped-in
        # model) can emit junk here - drop steps that don't have all three
        # rather than showing a broken row in the UI.
        if _TIME_RE.match(time_str) and action and detail:
            steps.append({"time": time_str, "action": action, "detail": detail})

    steps.sort(key=lambda s: s["time"])
    return steps or None


def _parse_structured_reply(raw_text: str) -> dict:
    text = _THINK_BLOCK.sub("", raw_text).strip()
    data = _first_json_object(text)
    if data is None:
        return {"risk_level": None, "escalate": False, "message": text, "schedule": None}

    risk_level = str(data.get("risk_level") or "").strip().lower()
    return {
        "risk_level": risk_level if risk_level in RISK_LEVELS else None,
        "escalate": _as_bool(data.get("escalate", False)),
        "message": str(data.get("message") or "").strip() or text,
        "schedule": _parse_schedule(data.get("schedule")),
    }


async def ask(
    session_id: str,
    worker_id: str,
    system_prompt: str,
    history: list[dict],
    user_message: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> dict:
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history:
        role = "assistant" if turn["role"] == "model" else "user"
        messages.append({"role": role, "content": turn["content"]})
    messages.append({"role": "user", "content": user_message})

    raw_text = await prism.chat_completion(
        session_id=session_id,
        worker_id=worker_id,
        request={"messages": messages, "response_format": RESPONSE_FORMAT},
        background_tasks=background_tasks,
    )
    return _parse_structured_reply(raw_text)
