"""
Every LLM call in Solaris goes through this module - it's the one seam
where PRISM (Block Convey) tracing gets wired in.

Calls go to OpenRouter's OpenAI-compatible API through the OpenAI SDK.
PRISM's "zero-code proxy" model works by pointing that client at their
proxy URL instead of OpenRouter; the proxy forwards the request and logs
the full exchange. Until we have PRISM's real proxy details,
PRISM_ENABLED=false calls OpenRouter directly, so the app is usable
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
import time
from typing import Optional

import openai
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

AGENT_ID = "solaris-heat-safety"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

REQUEST_TIMEOUT_S = 25.0
MAX_RETRY_DELAY_S = 5.0

# OpenRouter holds slow requests open with keep-alive bytes, so the SDK's read
# timeout never trips; this hard cap must stay under vercel.json's maxDuration (60s)
# or the platform kills the function with a raw 504 instead of our message.
TOTAL_DEADLINE_S = 45.0

# OpenRouter rejects longer fallback lists.
MAX_MODELS = 3

# A rate-limit window longer than this is the free tier's daily cap, not the per-minute one.
DAILY_LIMIT_THRESHOLD_S = 120.0


class LLMUnavailable(Exception):
    """The LLM couldn't produce a reply. `message` is safe to show the worker."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def model_chain() -> list[str]:
    fallbacks = [m.strip() for m in settings.openrouter_fallback_models.split(",") if m.strip()]
    chain = [settings.openrouter_model] + [m for m in fallbacks if m != settings.openrouter_model]
    return chain[:MAX_MODELS]


def seconds_until_reset(exc: openai.APIStatusError, now: Optional[float] = None) -> Optional[float]:
    """Seconds until a 429 clears, from Retry-After or X-RateLimit-Reset; None if not stated."""
    headers = {k.lower(): v for k, v in exc.response.headers.items()}
    body = exc.body if isinstance(exc.body, dict) else {}
    metadata_headers = (body.get("metadata") or {}).get("headers") or {}
    headers.update({k.lower(): str(v) for k, v in metadata_headers.items()})

    try:
        if "retry-after" in headers:
            return max(0.0, float(headers["retry-after"]))
        if "x-ratelimit-reset" in headers:
            reset_ms = float(headers["x-ratelimit-reset"])
            return max(0.0, reset_ms / 1000 - (now if now is not None else time.time()))
    except ValueError:
        pass
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
        base_url = OPENROUTER_BASE_URL
        headers = {}

    # max_retries=0: the SDK's own backoff could outlast Vercel's function timeout.
    return AsyncOpenAI(
        base_url=base_url,
        api_key=settings.openrouter_api_key,
        default_headers=headers,
        timeout=REQUEST_TIMEOUT_S,
        max_retries=0,
    )


async def chat_completion(session_id: str, worker_id: str, request: dict) -> str:
    if not settings.openrouter_api_key:
        # The SDK refuses to build a client without a key, which would surface as a bare 500.
        logger.error("OPENROUTER_API_KEY is not set")
        raise LLMUnavailable("Solaris's AI service isn't available right now. Please try again later.")

    models = model_chain()
    kwargs = {
        **request,
        "model": models[0],
        "extra_body": {
            "models": models,
            # Only route to providers that honour response_format.
            "provider": {"require_parameters": True},
        },
    }

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
        logger.error("OpenRouter gave no reply within %ss (models: %s)", TOTAL_DEADLINE_S, models)
        raise LLMUnavailable("Solaris is taking too long to reply. Please try again in a moment.") from exc
    except openai.RateLimitError as exc:
        logger.error("OpenRouter rate limit: %s", exc.body)
        wait = seconds_until_reset(exc)
        if wait is not None and wait > DAILY_LIMIT_THRESHOLD_S:
            raise LLMUnavailable("Solaris has reached its daily AI usage limit. Please try again later.") from exc
        raise LLMUnavailable(
            "Solaris is getting a lot of messages right now. Please try again in a few seconds."
        ) from exc
    except (openai.APITimeoutError, openai.APIConnectionError) as exc:
        logger.warning("OpenRouter request failed: %r", exc)
        raise LLMUnavailable("Solaris couldn't reach its AI service. Please try again in a moment.") from exc
    except openai.APIStatusError as exc:
        logger.error("OpenRouter returned %s: %s", exc.status_code, exc.body)
        if exc.status_code in (401, 402):
            # Bad/missing key or no credits - an operator problem, not something the worker can retry.
            raise LLMUnavailable("Solaris's AI service isn't available right now. Please try again later.") from exc
        if exc.status_code == 403:
            raise LLMUnavailable("Solaris couldn't reply to that message. Please rephrase and try again.") from exc
        raise LLMUnavailable("Solaris's AI model is unavailable right now. Please try again in a moment.") from exc

    choice = completion.choices[0] if completion.choices else None
    content = choice.message.content if choice and choice.message else None
    if not content:
        # OpenRouter can return 200 with the upstream failure inside the choice.
        error = (choice.model_extra or {}).get("error") if choice else (completion.model_extra or {}).get("error")
        logger.error(
            "OpenRouter reply from %s had no content (finish_reason=%s, error=%s)",
            completion.model,
            choice.finish_reason if choice else None,
            error,
        )
        raise LLMUnavailable("Solaris couldn't put together a reply. Please rephrase and try again.")

    logger.info("OpenRouter reply served by %s", completion.model)
    return content
