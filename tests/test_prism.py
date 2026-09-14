import asyncio
import json
import time

import httpx
import httpx2
import pytest

from app.services import prism
from app.services.prism import AGENT_ID, LLMUnavailable, _parse_duration_seconds


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(prism.settings, "groq_api_key", "test-key")
    # Disabled by default regardless of the real value in .env, so tests never
    # depend on network access or a real PRISM key.
    monkeypatch.setattr(prism.settings, "prismtrace_api_key", "")


def _use_trace_transport(monkeypatch, responses):
    """Same idea as _use_transport, but for _emit_trace's plain httpx.AsyncClient.

    Uses prism._TRACE_TRANSPORT rather than patching httpx.AsyncClient itself -
    that class is a shared, process-wide symbol the openai SDK also inspects
    (via isinstance checks unrelated to tracing) on every request it builds,
    so replacing it here would break the Groq call in the same test.
    """
    sent = []
    queue = list(responses)

    def handler(request):
        sent.append(request)
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(prism, "_TRACE_TRANSPORT", httpx.MockTransport(handler))
    return sent


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


def test_trace_not_sent_when_prismtrace_api_key_unset(monkeypatch):
    # autouse fixture leaves prismtrace_api_key = "" - the default, disabled state.
    trace_sent = _use_trace_transport(monkeypatch, [])
    _use_transport(monkeypatch, [httpx2.Response(200, json=_completion("ok"))])

    _call()

    assert trace_sent == []


def test_trace_sent_with_documented_shape_when_configured(monkeypatch):
    monkeypatch.setattr(prism.settings, "prismtrace_api_key", "pt-sk-test")
    monkeypatch.setattr(prism.settings, "prismtrace_project_id", "proj-123")
    monkeypatch.setattr(prism.settings, "prismtrace_host", "https://prism-api-prod.up.railway.app")

    trace_sent = _use_trace_transport(monkeypatch, [httpx.Response(200, json={"ok": True})])
    _use_transport(monkeypatch, [httpx2.Response(200, json=_completion("the reply", model="openai/gpt-oss-120b"))])

    assert _call() == "the reply"

    assert len(trace_sent) == 1
    request = trace_sent[0]
    assert str(request.url) == "https://prism-api-prod.up.railway.app/api/traces"
    # Documented gotcha: this is X-PRISMtrace-Key, not an Authorization: Bearer header.
    assert request.headers["x-prismtrace-key"] == "pt-sk-test"
    assert "authorization" not in request.headers

    body = json.loads(request.content)
    assert body["project_id"] == "proj-123"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["output_message"] == "the reply"
    assert body["session_id"] == "w1:2026-09-14"  # groups traces into one trajectory
    assert body["input_messages"] == [{"role": "user", "content": "hi"}]
    assert isinstance(body["latency_ms"], int)
    assert body["agent_id"] == AGENT_ID == "solaris-heat-safety"


def test_trace_delivery_failure_does_not_break_the_chat_reply(monkeypatch):
    monkeypatch.setattr(prism.settings, "prismtrace_api_key", "pt-sk-test")
    trace_sent = _use_trace_transport(monkeypatch, [httpx.ConnectError("prism unreachable")])
    _use_transport(monkeypatch, [httpx2.Response(200, json=_completion("the reply"))])

    assert _call() == "the reply"
    assert len(trace_sent) == 1


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


def test_emit_trace_records_locally_even_when_prism_disabled(monkeypatch):
    from app.services import store

    monkeypatch.setattr(prism.settings, "prismtrace_api_key", "")
    session_id = "trace-record-test:2026-09-14"

    asyncio.run(
        prism._emit_trace(
            session_id=session_id,
            worker_id="w1",
            model="openai/gpt-oss-120b",
            input_messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "Checking in"},
            ],
            output_message="High risk today.",
            latency_ms=900,
        )
    )

    traces = asyncio.run(store.get_traces(session_id))
    assert len(traces) == 1
    assert traces[0]["input"] == "Checking in"
    assert traces[0]["output"] == "High risk today."
    assert traces[0]["latency_ms"] == 900
    assert traces[0]["delivered_to_prism"] is False


def test_emit_trace_records_locally_before_attempting_prism_delivery(monkeypatch):
    from app.services import store

    monkeypatch.setattr(prism.settings, "prismtrace_api_key", "pt-sk-test")
    trace_sent = _use_trace_transport(monkeypatch, [httpx.ConnectError("prism unreachable")])
    session_id = "trace-record-test-2:2026-09-14"

    asyncio.run(
        prism._emit_trace(
            session_id=session_id,
            worker_id="w1",
            model="openai/gpt-oss-120b",
            input_messages=[{"role": "user", "content": "hi"}],
            output_message="ok",
            latency_ms=100,
        )
    )

    traces = asyncio.run(store.get_traces(session_id))
    assert len(traces) == 1
    assert traces[0]["delivered_to_prism"] is True  # reflects that PRISM *was* configured, not delivery success
    assert len(trace_sent) == 1
