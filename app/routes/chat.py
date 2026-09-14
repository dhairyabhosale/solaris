from fastapi import APIRouter, HTTPException

from app.models import ChatRequest, ChatResponse
from app.services import llm, store, weather

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    profile = store.get_worker(payload.worker_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Worker not onboarded yet. Call /onboarding first.")

    session_id = store.session_id_for(payload.worker_id)
    history = store.get_history(session_id)

    try:
        today_weather = await weather.get_current_weather(profile.location)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    system_prompt = llm.build_system_prompt(profile, today_weather)
    reply = await llm.ask(
        session_id=session_id,
        worker_id=payload.worker_id,
        system_prompt=system_prompt,
        history=history,
        user_message=payload.message,
    )

    store.append_message(session_id, "user", payload.message)
    store.append_message(session_id, "model", reply)

    return ChatResponse(session_id=session_id, reply=reply)
