import httpx
from fastapi import APIRouter, HTTPException

from app.models import ChatRequest, ChatResponse, ChatTurn, WorkerStatus
from app.services import llm, store, weather
from app.services.prism import LLMUnavailable

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
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
    )


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
    history = [ChatTurn(**turn) for turn in await store.get_history(session_id)]
    cached_weather = await store.get_weather(session_id) or {}

    return WorkerStatus(
        onboarded=True,
        profile=profile,
        session_id=session_id,
        risk_level=await store.get_risk_level(session_id),
        schedule=await store.get_schedule(session_id),
        acknowledged=await store.get_acknowledged(session_id),
        temperature_c=cached_weather.get("temperature_c"),
        feels_like_c=cached_weather.get("feels_like_c"),
        humidity_pct=cached_weather.get("humidity_pct"),
        history=history,
    )
