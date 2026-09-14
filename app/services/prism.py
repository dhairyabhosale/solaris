"""
Every LLM call in Solaris goes through this module - it's the one seam
where PRISM (Block Convey) tracing gets wired in.

Calls go straight to Groq's OpenAI-compatible API through the OpenAI SDK
(there is no proxy to route through - an earlier version of this file
guessed at a "zero-code proxy" model before we'd actually onboarded with
Block Convey; the real integration, per prism.blockconvey.com, is a
side-channel trace POST to their ingest API after each model call).
chat_completion() calls Groq, then best-effort POSTs one trace to
PRISM's /api/traces. Trace delivery failing never breaks a worker's
chat reply - PRISMTRACE_API_KEY being unset just skips it entirely.

agent_id is intentionally a hardcoded constant, and session_id is passed
in by the caller (see app.services.store.session_id_for) as
"<worker_id>:<date>" - both must stay stable across a worker's whole day
so PRISM groups the conversation into one traceable session.
"""

import asyncio
import logging
import re
import time
from typing import Optional

import httpx
import openai
from fastapi import BackgroundTasks
from openai import AsyncOpenAI

from app.config import local_now, settings
from app.services import store

logger = logging.getLogger(__name__)

AGENT_ID = "solaris-heat-safety"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

TRACE_TIMEOUT_S = 5.0

# Test seam: never set outside tests. A test overriding httpx.AsyncClient
# itself corrupts a symbol openai's SDK also reads (it does its own
# isinstance(..., httpx.AsyncClient) check on every request, unrelated to
# tracing) - swapping just the transport here doesn't touch that class.
_TRACE_TRANSPORT: Optional[httpx.BaseTransport] = None

REQUEST_TIMEOUT_S = 25.0
MAX_RETRY_DELAY_S = 5.0

# Groq's inference is fast (that's its whole pitch), but this hard cap still
# has to stay under vercel.json's maxDuration (60s) so a stuck request ends
# in our message rather than the platform's raw 504.
TOTAL_DEADLINE_S = 45.0

# A rate-limit reset longer than this is the daily cap, not the per-minute one.
DAILY_LIMIT_THRESHOLD_S = 120.0

# Groq's X-RateLimit-Reset-* headers use Go-style durations, e.g. "2m59.56s".
_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$")


class LLMUnavailable(Exception):
    """The LLM couldn't produce a reply. `message` is safe to show the worker."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _parse_duration_seconds(text: str) -> Optional[float]:
    match = _DURATION_RE.match(text.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = match.groups()
    return float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0)


def seconds_until_reset(exc: openai.APIStatusError) -> Optional[float]:
    """How long until a 429 clears, from Retry-After or X-RateLimit-Reset-Requests; None if not stated."""
    headers = {k.lower(): v for k, v in exc.response.headers.items()}

    if "retry-after" in headers:
        try:
            return max(0.0, float(headers["retry-after"]))
        except ValueError:
            pass

    if "x-ratelimit-reset-requests" in headers:
        parsed = _parse_duration_seconds(headers["x-ratelimit-reset-requests"])
        if parsed is not None:
            return parsed

    return None


_groq_client: Optional[AsyncOpenAI] = None


def _client() -> AsyncOpenAI:
    # Reused across calls within a warm instance instead of paying a fresh
    # TLS handshake to Groq on every single chat message.
    # max_retries=0: the SDK's own backoff could outlast Vercel's function timeout.
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncOpenAI(
            base_url=GROQ_BASE_URL,
            api_key=settings.groq_api_key,
            timeout=REQUEST_TIMEOUT_S,
            max_retries=0,
        )
    return _groq_client


async def _emit_trace(
    session_id: str,
    worker_id: str,
    model: str,
    input_messages: list[dict],
    output_message: str,
    latency_ms: int,
) -> None:
    """Record a trace locally (powers the app's own "Live Trace Log"
    panel, independent of PRISM), then best-effort POST it to PRISM too.
    The worker's reply is already decided by the time this runs, so a
    slow/down PRISM must never affect it."""
    user_turns = [m["content"] for m in input_messages if m["role"] == "user"]
    await store.append_trace(
        session_id,
        {
            "timestamp": local_now().isoformat(timespec="seconds"),
            "input": user_turns[-1] if user_turns else "",
            "output": output_message,
            "latency_ms": latency_ms,
            "model": model,
            "delivered_to_prism": bool(settings.prismtrace_api_key),
        },
    )

    if not settings.prismtrace_api_key:
        return

    try:
        async with httpx.AsyncClient(timeout=TRACE_TIMEOUT_S, transport=_TRACE_TRANSPORT) as client:
            resp = await client.post(
                f"{settings.prismtrace_host.rstrip('/')}/api/traces",
                headers={"X-PRISMtrace-Key": settings.prismtrace_api_key},
                json={
                    "project_id": settings.prismtrace_project_id,
                    "model": model,
                    "input_messages": input_messages,
                    "output_message": output_message,
                    "latency_ms": latency_ms,
                    "session_id": session_id,
                    # Not in PRISM's documented trace schema; included for forward
                    # compatibility in case they start accepting/storing it.
                    "agent_id": AGENT_ID,
                    "worker_id": worker_id,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("PRISM trace delivery failed (worker's reply already went out): %r", exc)


async def chat_completion(
    session_id: str,
    worker_id: str,
    request: dict,
    background_tasks: Optional[BackgroundTasks] = None,
) -> str:
    if not settings.groq_api_key:
        # The SDK refuses to build a client without a key, which would surface as a bare 500.
        logger.error("GROQ_API_KEY is not set")
        raise LLMUnavailable("Solaris's AI service isn't available right now. Please try again later.")

    kwargs = {**request, "model": settings.groq_model}
    client = _client()
    started = time.monotonic()

    try:
        async with asyncio.timeout(TOTAL_DEADLINE_S):
            try:
                completion = await client.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                wait = seconds_until_reset(exc)
                wait = 2.0 if wait is None else wait
                if wait > MAX_RETRY_DELAY_S:
                    raise
                await asyncio.sleep(wait)
                completion = await client.chat.completions.create(**kwargs)
    except TimeoutError as exc:
        logger.error("Groq gave no reply within %ss (model: %s)", TOTAL_DEADLINE_S, settings.groq_model)
        raise LLMUnavailable("Solaris is taking too long to reply. Please try again in a moment.") from exc
    except openai.RateLimitError as exc:
        logger.error("Groq rate limit: %s", exc.body)
        wait = seconds_until_reset(exc)
        if wait is not None and wait > DAILY_LIMIT_THRESHOLD_S:
            raise LLMUnavailable("Solaris has reached its daily AI usage limit. Please try again later.") from exc
        raise LLMUnavailable(
            "Solaris is getting a lot of messages right now. Please try again in a few seconds."
        ) from exc
    except (openai.APITimeoutError, openai.APIConnectionError) as exc:
        logger.warning("Groq request failed: %r", exc)
        raise LLMUnavailable("Solaris couldn't reach its AI service. Please try again in a moment.") from exc
    except openai.APIStatusError as exc:
        logger.error("Groq returned %s: %s", exc.status_code, exc.body)
        if exc.status_code == 401:
            # Bad/missing key - an operator problem, not something the worker can retry.
            raise LLMUnavailable("Solaris's AI service isn't available right now. Please try again later.") from exc
        if exc.status_code in (400, 422):
            # Most likely a schema/model mismatch (e.g. GROQ_MODEL changed to one that
            # doesn't support strict structured outputs) - a bug, but not one to blame on the worker.
            raise LLMUnavailable("Solaris couldn't put together a reply. Please rephrase and try again.") from exc
        if exc.status_code == 498:
            # Flex-tier capacity exceeded - a temporary provider-side crunch.
            raise LLMUnavailable(
                "Solaris is getting a lot of messages right now. Please try again in a few seconds."
            ) from exc
        raise LLMUnavailable("Solaris's AI model is unavailable right now. Please try again in a moment.") from exc

    latency_ms = round((time.monotonic() - started) * 1000)

    choice = completion.choices[0] if completion.choices else None
    content = choice.message.content if choice and choice.message else None
    if not content:
        logger.error(
            "Groq reply had no content (finish_reason=%s)", choice.finish_reason if choice else None
        )
        raise LLMUnavailable("Solaris couldn't put together a reply. Please rephrase and try again.")

    trace_kwargs = dict(
        session_id=session_id,
        worker_id=worker_id,
        model=completion.model,
        input_messages=kwargs["messages"],
        output_message=content,
        latency_ms=latency_ms,
    )
    if background_tasks is not None:
        # Send the reply now; record/deliver the trace after the response has
        # gone out. Starlette runs background tasks after the response is
        # sent but before the request is considered finished, so this is
        # still safe on Vercel - the invocation isn't frozen until it's done.
        background_tasks.add_task(_emit_trace, **trace_kwargs)
    else:
        await _emit_trace(**trace_kwargs)

    return content
