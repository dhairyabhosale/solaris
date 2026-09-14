"""
Prompt construction and the Gemini call for a worker's daily check-in and
follow-up conversation. All requests are routed through
app.services.prism so tracing is centralized in one place.
"""

from app.models import WorkerProfile
from app.services import prism

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
1. On the first message of the day, state today's heat risk level - low, moderate, high, or \
extreme - based on the temperature, humidity, and the worker's job type and hours. Give a \
concrete break-and-hydration schedule tuned to that risk level and work type (frequency and \
length of breaks, how much water, timing relative to their shift). If risk is high or extreme, \
give a clear, direct escalation warning.
2. For follow-up questions, answer specifically using today's actual risk level and the \
worker's situation - never repeat generic advice disconnected from today's numbers.
3. If the worker describes possible heat-illness symptoms (dizziness, confusion, nausea, \
stopped sweating, cramps, rapid heartbeat, headache), immediately give clear first-aid \
guidance: stop working, get to shade or a cool area, hydrate, loosen clothing, and seek \
medical help if symptoms are severe or don't improve. Say clearly that this is general safety \
information, not a medical diagnosis.
4. Keep replies short - a few sentences or a tight list, not an essay.
"""


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


async def ask(
    session_id: str,
    worker_id: str,
    system_prompt: str,
    history: list[dict],
    user_message: str,
) -> str:
    contents = []
    for turn in history:
        role = "model" if turn["role"] == "model" else "user"
        contents.append({"role": role, "parts": [{"text": turn["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
    }

    return await prism.generate_content(session_id=session_id, worker_id=worker_id, payload=payload)
