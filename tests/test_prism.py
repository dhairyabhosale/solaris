import asyncio
import json
import time

import httpx2
import pytest

from app.services import prism
from app.services.prism import AGENT_ID, LLMUnavailable, _parse_duration_seconds


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(prism.settings, "groq_api_key", "test-key")


def _completion(content, model="openai/gpt-oss-120b", finish_reason="stop"):
    choice = {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": model, "choices": [choice]}


def _error(status, message, headers=None):
    body = {"error": {"message": message, "type": "invalid_request_error"}}
    return httpx2.Response(status, json=body, headers=headers or {})


def _use_transport(monkeypatch, responses):
    """Serve `responses` in order; returns the list of requests the client sent."""
    sent = []
    queue = list(responses)

    def handler(request):
        sent.append(request)
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    real_client = prism.AsyncOpenAI

    def client_with_mock_transport(**kwargs):
        return real_client(**kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(prism, "AsyncOpenAI", client_with_mock_transport)
    return sent


def _call():
    request = {"messages": [{"role": "user", "content": "hi"}], "response_format": {"type": "json_object"}}
    return asyncio.run(prism.chat_completion(session_id="w1:2026-09-14", worker_id="w1", request=request))


def test_missing_api_key_gives_graceful_message_without_calling_out(monkeypatch):
    monkeypatch.setattr(prism.settings, "groq_api_key", "")
    sent = _use_transport(monkeypatch, [])

    with pytest.raises(LLMUnavailable, match="isn't available right now"):
        _call()
    assert sent == []


def test_successful_call_sends_groq_request_with_configured_model(monkeypatch):
    monkeypatch.setattr(prism.settings, "groq_model", "openai/gpt-oss-120b")
    sent = _use_transport(monkeypatch, [httpx2.Response(200, json=_completion('{"message": "ok"}'))])

    assert _call() == '{"message": "ok"}'

    request = sent[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["response_format"] == {"type": "json_object"}
    assert "models" not in body  # OpenRouter-only fallback param - Groq doesn't support it
    assert "provider" not in body


def test_prism_enabled_routes_through_proxy_with_stable_session_and_agent_ids(monkeypatch):
    monkeypatch.setattr(prism.settings, "prism_enabled", True)
    monkeypatch.setattr(prism.settings, "prism_proxy_url", "https://prism-proxy.test/v1/")
    sent = _use_transport(monkeypatch, [httpx2.Response(200, json=_completion("ok"))])

    _call()

    request = sent[0]
    assert str(request.url) == "https://prism-proxy.test/v1/chat/completions"
    assert request.headers["x-prism-session-id"] == "w1:2026-09-14"
    assert request.headers["x-prism-agent-id"] == AGENT_ID == "solaris-heat-safety"
    assert request.headers["x-prism-worker-id"] == "w1"


def test_prism_disabled_sends_no_prism_headers(monkeypatch):
    monkeypatch.setattr(prism.settings, "prism_enabled", False)
    sent = _use_transport(monkeypatch, [httpx2.Response(200, json=_completion("ok"))])

    _call()

    assert not any(name.lower().startswith("x-prism") for name in sent[0].headers)


def test_short_rate_limit_is_retried_once(monkeypatch):
    sent = _use_transport(
        monkeypatch,
        [
            _error(429, "Rate limit reached, please try again in 0s.", headers={"Retry-After": "0"}),
            httpx2.Response(200, json=_completion("second try")),
        ],
    )

    assert _call() == "second try"
    assert len(sent) == 2


def test_daily_limit_via_retry_after_is_not_retried_and_says_so(monkeypatch):
    sent = _use_transport(
        monkeypatch,
        [_error(429, "Rate limit reached for requests-per-day", headers={"Retry-After": "21600"})],
    )

    with pytest.raises(LLMUnavailable, match="daily AI usage limit"):
        _call()
    assert len(sent) == 1


def test_daily_limit_via_reset_requests_duration_header_is_recognised(monkeypatch):
    _use_transport(
        monkeypatch,
        [_error(429, "Rate limit reached", headers={"X-RateLimit-Reset-Requests": "5h59m0.12s"})],
    )

    with pytest.raises(LLMUnavailable, match="daily AI usage limit"):
        _call()


def test_retry_after_takes_priority_over_reset_requests_header(monkeypatch):
    # Both present (Groq sends both on a 429) - Retry-After is the simpler, authoritative one.
    sent = _use_transport(
        monkeypatch,
        [
            _error(429, "short", headers={"Retry-After": "0", "X-RateLimit-Reset-Requests": "5h0m0s"}),
            httpx2.Response(200, json=_completion("recovered")),
        ],
    )

    assert _call() == "recovered"
    assert len(sent) == 2


def test_rate_limit_still_failing_after_retry_asks_worker_to_wait(monkeypatch):
    _use_transport(
        monkeypatch,
        [
            _error(429, "Rate limit reached", headers={"Retry-After": "0"}),
            _error(429, "Rate limit reached", headers={"Retry-After": "0"}),
        ],
    )

    with pytest.raises(LLMUnavailable, match="a lot of messages"):
        _call()


@pytest.mark.parametrize("status", [403, 404, 413, 424, 499, 500, 502, 503])
def test_model_or_provider_failure_gives_graceful_message(monkeypatch, status):
    _use_transport(monkeypatch, [_error(status, "provider-side failure")])

    with pytest.raises(LLMUnavailable, match="model is unavailable"):
        _call()


def test_bad_key_gives_graceful_message(monkeypatch):
    _use_transport(monkeypatch, [_error(401, "Invalid API Key")])

    with pytest.raises(LLMUnavailable, match="isn't available right now"):
        _call()


@pytest.mark.parametrize("status", [400, 422])
def test_schema_or_request_mismatch_gives_graceful_message(monkeypatch, status):
    # e.g. GROQ_MODEL pointed at a model without native structured-output support.
    _use_transport(monkeypatch, [_error(status, "'response_format' of type 'json_schema' is not supported")])

    with pytest.raises(LLMUnavailable, match="couldn't put together a reply"):
        _call()


def test_flex_tier_capacity_exceeded_gives_graceful_message(monkeypatch):
    _use_transport(monkeypatch, [_error(498, "Flex tier capacity exceeded")])

    with pytest.raises(LLMUnavailable, match="a lot of messages"):
        _call()


def test_connection_failure_gives_graceful_message(monkeypatch):
    _use_transport(monkeypatch, [httpx2.ConnectError("unreachable")])

    with pytest.raises(LLMUnavailable, match="couldn't reach"):
        _call()


def test_empty_content_is_treated_as_failure(monkeypatch):
    _use_transport(monkeypatch, [httpx2.Response(200, json=_completion(None, finish_reason="length"))])

    with pytest.raises(LLMUnavailable, match="couldn't put together a reply"):
        _call()


def test_slow_model_hits_hard_deadline_with_graceful_message(monkeypatch):
    monkeypatch.setattr(prism, "TOTAL_DEADLINE_S", 0.05)

    async def never_finishes(request):
        await asyncio.sleep(5)
        return httpx2.Response(200, json=_completion("too late"))

    real_client = prism.AsyncOpenAI
    monkeypatch.setattr(
        prism,
        "AsyncOpenAI",
        lambda **kw: real_client(**kw, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(never_finishes))),
    )

    started = time.monotonic()
    with pytest.raises(LLMUnavailable, match="taking too long"):
        _call()
    assert time.monotonic() - started < 2


def test_deadline_stays_under_vercel_function_limit():
    vercel = json.load(open("vercel.json"))
    assert prism.TOTAL_DEADLINE_S < vercel["functions"]["api/index.py"]["maxDuration"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2m59.56s", 179.56),
        ("45.5s", 45.5),
        ("1h2m3s", 3723.0),
        ("0s", 0.0),
        ("", None),
        ("not-a-duration", None),
    ],
)
def test_parse_duration_seconds(text, expected):
    result = _parse_duration_seconds(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
