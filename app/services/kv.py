"""
Thin async wrapper around Redis (redis-py's asyncio client), used by
app.services.store when REDIS_URL is configured - required on Vercel,
where a plain in-process dict doesn't survive between serverless
invocations. The Vercel "Redis by Redis" marketplace integration
injects REDIS_URL automatically once connected to the project.
"""

import json
from typing import Any, Optional

import redis.asyncio as redis

from app.config import settings

_client: Optional[redis.Redis] = None


def is_configured() -> bool:
    return bool(settings.redis_url)


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def get_json(key: str) -> Optional[Any]:
    raw = await _get_client().get(key)
    return json.loads(raw) if raw is not None else None


async def set_json(key: str, value: Any) -> None:
    await _get_client().set(key, json.dumps(value))
