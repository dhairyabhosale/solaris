"""
Every LLM call in Solaris goes through this module - it's the one seam
where PRISM (Block Convey) tracing gets wired in.

Calls go to Groq's OpenAI-compatible API through the OpenAI SDK.
PRISM's "zero-code proxy" model works by pointing that client at their
proxy URL instead of Groq; the proxy forwards the request and logs the
full exchange. Until we have PRISM's real proxy details,
PRISM_ENABLED=false calls Groq directly, so the app is usable
immediately. Flip PRISM_ENABLED to true and fill in PRISM_PROXY_URL /
PRISM_API_KEY / PRISM_PROJECT_ID once we have them - no other file needs
to change.

agent_id is intentionally a hardcoded constant, and session_id is passed
in by the caller (see app.services.store.session_id_for) as
"<worker_id>:<date>" - both must stay stable across a worker's whole day
so PRISM groups the conversation into one traceable session.
"""

import asyncio
import logging
import re
from typing import Optional

import openai
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

AGENT_ID = "solaris-heat-safety"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

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


def _client(session_id: str, worker_id: str) -> AsyncOpenAI:
    if settings.prism_enabled and settings.prism_proxy_url:
        base_url = settings.prism_proxy_url.rstrip("/")
        headers = {
            "X-Prism-Api-Key": settings.prism_api_key,
            "X-Prism-Project-Id": settings.prism_project_id,
            "X-Prism-Session-Id": session_id,
            "X-Prism-Agent-Id": AGENT_ID,
            "X-Prism-Worker-Id": worker_id,
        }
    else:
        base_url = GROQ_BASE_URL
        headers = {}

    # max_retries=0: the SDK's own backoff could outlast Vercel's function timeout.
    return AsyncOpenAI(
        base_url=base_url,
        api_key=settings.groq_api_key,
        default_headers=headers,
        timeout=REQUEST_TIMEOUT_S,
        max_retries=0,
    )


async def chat_completion(session_id: str, worker_id: str, request: dict) -> str:
    if not settings.groq_api_key:
        # The SDK refuses to build a client without a key, which would surface as a bare 500.
        logger.error("GROQ_API_KEY is not set")
        raise LLMUnavailable("Solaris's AI service isn't available right now. Please try again later.")

    kwargs = {**request, "model": settings.groq_model}

    try:
        async with asyncio.timeout(TOTAL_DEADLINE_S), _client(session_id, worker_id) as client:
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

    choice = completion.choices[0] if completion.choices else None
    content = choice.message.content if choice and choice.message else None
    if not content:
        logger.error(
            "Groq reply had no content (finish_reason=%s)", choice.finish_reason if choice else None
        )
        raise LLMUnavailable("Solaris couldn't put together a reply. Please rephrase and try again.")

    return content
