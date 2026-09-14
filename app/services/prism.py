"""
Every LLM call in Solaris goes through this module - it's the one seam
where PRISM (Block Convey) tracing gets wired in.

PRISM's "zero-code proxy" model works by pointing the LLM client at their
proxy URL instead of calling Google directly; the proxy transparently
forwards the request to Gemini and logs the full exchange. Until we have
PRISM's real proxy details, PRISM_ENABLED=false in .env makes this call
Gemini directly, so the app is usable immediately. Flip PRISM_ENABLED to
true and fill in PRISM_PROXY_URL / PRISM_API_KEY / PRISM_PROJECT_ID once
we have them - no other file needs to change.

agent_id is intentionally a hardcoded constant, and session_id is passed
in by the caller (see app.services.store.session_id_for) as
"<worker_id>:<date>" - both must stay stable across a worker's whole day
so PRISM groups the conversation into one traceable session.
"""

import httpx

from app.config import settings

AGENT_ID = "solaris-heat-safety"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"


async def generate_content(session_id: str, worker_id: str, payload: dict) -> str:
    if settings.prism_enabled and settings.prism_proxy_url:
        base_url = settings.prism_proxy_url.rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "X-Prism-Api-Key": settings.prism_api_key,
            "X-Prism-Project-Id": settings.prism_project_id,
            "X-Prism-Session-Id": session_id,
            "X-Prism-Agent-Id": AGENT_ID,
            "X-Prism-Worker-Id": worker_id,
        }
    else:
        base_url = GEMINI_BASE_URL
        headers = {"Content-Type": "application/json"}

    url = f"{base_url}/v1beta/models/{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response shape: {data}") from exc
