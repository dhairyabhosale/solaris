"""
In-memory storage for worker profiles and conversation history.

Deliberately not persistent (state resets on restart) - fine for an
overnight hackathon demo. Swap for SQLite/a real DB later without
touching the routes, since everything goes through this module.
"""

from datetime import date
from typing import Optional

from app.models import WorkerProfile

_WORKERS: dict[str, WorkerProfile] = {}
_CONVERSATIONS: dict[str, list[dict]] = {}


def save_worker(profile: WorkerProfile) -> None:
    _WORKERS[profile.worker_id] = profile


def get_worker(worker_id: str) -> Optional[WorkerProfile]:
    return _WORKERS.get(worker_id)


def session_id_for(worker_id: str) -> str:
    """One stable session_id per worker per day - required for PRISM tracing."""
    return f"{worker_id}:{date.today().isoformat()}"


def get_history(session_id: str) -> list[dict]:
    return _CONVERSATIONS.setdefault(session_id, [])


def append_message(session_id: str, role: str, content: str) -> None:
    get_history(session_id).append({"role": role, "content": content})
