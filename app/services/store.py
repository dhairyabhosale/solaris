"""
Worker profiles and conversation history.

Uses Redis (app.services.kv) when REDIS_URL is set - required on
Vercel, where a plain in-process dict doesn't reliably survive between
serverless invocations. Falls back to an in-memory dict automatically
when REDIS_URL is absent, so local dev and tests need no setup.
"""

from typing import Optional

from app.config import local_today
from app.models import WorkerProfile
from app.services import kv

_WORKERS: dict[str, WorkerProfile] = {}
_CONVERSATIONS: dict[str, list[dict]] = {}
_RISK_LEVELS: dict[str, str] = {}

_USE_KV = kv.is_configured()


async def save_worker(profile: WorkerProfile) -> None:
    if _USE_KV:
        await kv.set_json(f"worker:{profile.worker_id}", profile.model_dump())
    else:
        _WORKERS[profile.worker_id] = profile


async def get_worker(worker_id: str) -> Optional[WorkerProfile]:
    if _USE_KV:
        data = await kv.get_json(f"worker:{worker_id}")
        return WorkerProfile(**data) if data else None
    return _WORKERS.get(worker_id)


def session_id_for(worker_id: str) -> str:
    """One stable session_id per worker per day - required for PRISM tracing."""
    return f"{worker_id}:{local_today().isoformat()}"


async def get_history(session_id: str) -> list[dict]:
    if _USE_KV:
        data = await kv.get_json(f"history:{session_id}")
        return data or []
    return _CONVERSATIONS.setdefault(session_id, [])


async def append_message(session_id: str, role: str, content: str) -> None:
    if _USE_KV:
        history = await get_history(session_id)
        history.append({"role": role, "content": content})
        await kv.set_json(f"history:{session_id}", history)
    else:
        _CONVERSATIONS.setdefault(session_id, []).append({"role": role, "content": content})


async def get_risk_level(session_id: str) -> Optional[str]:
    if _USE_KV:
        return await kv.get_json(f"risk:{session_id}")
    return _RISK_LEVELS.get(session_id)


async def set_risk_level(session_id: str, risk_level: str) -> None:
    if _USE_KV:
        await kv.set_json(f"risk:{session_id}", risk_level)
    else:
        _RISK_LEVELS[session_id] = risk_level
