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

Your job in this conversation:
1. On the first message of the day, determine today's heat risk level - low, moderate, high, or \
extreme - based on the temperature, humidity, and the worker's job type and hours. Give a \
concrete break-and-hydration schedule tuned to that risk level and work type (frequency and \
length of breaks, how much water, timing relative to their shift). If risk is high or extreme, \
give a clear, direct escalation warning.
2. For follow-up questions, answer specifically using today's actual risk level (keep reporting \
the same risk_level you already gave today unless the conversation gives you a real reason to \
revise it) and the worker's situation - never repeat generic advice disconnected from today's \
numbers.
3. If the worker describes possible heat-illness symptoms (dizziness, confusion, nausea, \
stopped sweating, cramps, rapid heartbeat, headache), immediately give clear first-aid \
guidance: stop working, get to shade or a cool area, hydrate, loosen clothing, and seek \
medical help if symptoms are severe or don't improve. Say clearly that this is general safety \
information, not a medical diagnosis. Set escalate to true for this reply.
4. Keep the "message" field short - a few sentences or a tight list, not an essay. Plain text \
only, no markdown formatting.

Respond with a single JSON object and nothing else, in exactly this shape:
{{"risk_level": "low" | "moderate" | "high" | "extreme", "escalate": true | false, "message": "..."}}
risk_level is today's level, escalate is true only if this specific reply is urgent \
first-aid/escalation guidance, and message is the text to show the worker.
"""

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
            },
            "required": ["risk_level", "escalate", "message"],
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


def _parse_structured_reply(raw_text: str) -> dict:
    text = _THINK_BLOCK.sub("", raw_text).strip()
    data = _first_json_object(text)
    if data is None:
        return {"risk_level": None, "escalate": False, "message": text}

    risk_level = str(data.get("risk_level") or "").strip().lower()
    return {
        "risk_level": risk_level if risk_level in RISK_LEVELS else None,
        "escalate": _as_bool(data.get("escalate", False)),
        "message": str(data.get("message") or "").strip() or text,
    }


async def ask(
    session_id: str,
    worker_id: str,
    system_prompt: str,
    history: list[dict],
    user_message: str,
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
    )
    return _parse_structured_reply(raw_text)
