import asyncio

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models import ChatRequest, ChatResponse, ChatTurn, HydrationLogRequest, TraceEntry, TraceLog, WorkerStatus
from app.services import llm, store, weather
from app.services.prism import AGENT_ID, LLMUnavailable

router = APIRouter()

# Rough ml/hour targets in line with the kind of hydration cadence the LLM
# itself already recommends per risk level - not official medical guidance,
# just a consistent number to show worker-facing progress against.
_HYDRATION_ML_PER_HOUR = {"low": 250, "moderate": 350, "high": 500, "extreme": 600}
_DEFAULT_HYDRATION_ML_PER_HOUR = 300


def _shift_hours(work_start: str, work_end: str) -> float:
    start_h, start_m = (int(part) for part in work_start.split(":"))
    end_h, end_m = (int(part) for part in work_end.split(":"))
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60  # overnight shift
    return (end_minutes - start_minutes) / 60


def _hydration_target_ml(risk_level: str, profile) -> int:
    per_hour = _HYDRATION_ML_PER_HOUR.get(risk_level, _DEFAULT_HYDRATION_ML_PER_HOUR)
    hours = _shift_hours(profile.work_start, profile.work_end)
    return round(per_hour * hours / 50) * 50


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, background_tasks: BackgroundTasks) -> ChatResponse:
    profile = await store.get_worker(payload.worker_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Worker not onboarded yet. Call /onboarding first.")

    session_id = store.session_id_for(payload.worker_id)
    history = await store.get_history(session_id)

    try:
        today_weather = await weather.get_current_weather(profile.location)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Couldn't fetch today's weather right now. Please try again in a moment."
        ) from exc
    await store.set_weather(session_id, today_weather)

    system_prompt = llm.build_system_prompt(profile, today_weather)
    try:
        result = await llm.ask(
            session_id=session_id,
            worker_id=payload.worker_id,
            system_prompt=system_prompt,
            history=history,
            user_message=payload.message,
            background_tasks=background_tasks,
        )
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc

    risk_level = result["risk_level"] or await store.get_risk_level(session_id) or "unknown"
    await store.set_risk_level(session_id, risk_level)

    schedule = result["schedule"]
    if schedule is not None:
        await store.set_schedule(session_id, schedule)
        # A revised plan needs a fresh "got it" - the old one was for the old plan.
        await store.set_acknowledged(session_id, False)
    else:
        schedule = await store.get_schedule(session_id)

    await store.append_message(session_id, "user", payload.message)
    await store.append_message(session_id, "model", result["message"])

    return ChatResponse(
        session_id=session_id,
        reply=result["message"],
        risk_level=risk_level,
        escalate=result["escalate"],
        schedule=schedule,
        temperature_c=today_weather["temperature_c"],
        feels_like_c=today_weather["feels_like_c"],
        humidity_pct=today_weather["humidity_pct"],
        peak_heat_hour=today_weather.get("peak_heat_hour"),
        hydration_logged_ml=await store.get_hydration_ml(session_id),
        hydration_target_ml=_hydration_target_ml(risk_level, profile),
    )


@router.post("/worker/{worker_id}/hydration")
async def log_hydration(worker_id: str, payload: HydrationLogRequest) -> dict:
    profile = await store.get_worker(worker_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Worker not onboarded yet. Call /onboarding first.")

    session_id = store.session_id_for(worker_id)
    total = await store.add_hydration_ml(session_id, payload.amount_ml)
    return {"hydration_logged_ml": total}


@router.get("/worker/{worker_id}/traces", response_model=TraceLog)
async def worker_traces(worker_id: str) -> TraceLog:
    profile = await store.get_worker(worker_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Worker not onboarded yet. Call /onboarding first.")

    session_id = store.session_id_for(worker_id)
    traces = [TraceEntry(**t) for t in await store.get_traces(session_id)]
    return TraceLog(session_id=session_id, agent_id=AGENT_ID, traces=traces)


@router.post("/worker/{worker_id}/acknowledge")
async def acknowledge_schedule(worker_id: str) -> dict:
    profile = await store.get_worker(worker_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Worker not onboarded yet. Call /onboarding first.")

    session_id = store.session_id_for(worker_id)
    await store.set_acknowledged(session_id, True)
    return {"acknowledged": True}


@router.get("/worker/{worker_id}/status", response_model=WorkerStatus)
async def worker_status(worker_id: str) -> WorkerStatus:
    profile = await store.get_worker(worker_id)
    if profile is None:
        return WorkerStatus(onboarded=False)

    session_id = store.session_id_for(worker_id)

    # Independent reads - run concurrently instead of paying for 6 sequential
    # round-trips to Redis. This is what the frontend calls on every page
    # load/resume, so its latency is directly felt as "the site is slow."
    raw_history, cached_weather, risk_level, schedule, acknowledged, hydration_ml = await asyncio.gather(
        store.get_history(session_id),
        store.get_weather(session_id),
        store.get_risk_level(session_id),
        store.get_schedule(session_id),
        store.get_acknowledged(session_id),
        store.get_hydration_ml(session_id),
    )
    cached_weather = cached_weather or {}
    history = [ChatTurn(**turn) for turn in raw_history]

    return WorkerStatus(
        onboarded=True,
        profile=profile,
        session_id=session_id,
        risk_level=risk_level,
        schedule=schedule,
        acknowledged=acknowledged,
        temperature_c=cached_weather.get("temperature_c"),
        feels_like_c=cached_weather.get("feels_like_c"),
        humidity_pct=cached_weather.get("humidity_pct"),
        peak_heat_hour=cached_weather.get("peak_heat_hour"),
        hydration_logged_ml=hydration_ml,
        hydration_target_ml=_hydration_target_ml(risk_level or "unknown", profile),
        history=history,
    )
