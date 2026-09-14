import asyncio
import json
import time

import httpx2
import pytest

from app.services import prism
from app.services.prism import AGENT_ID, LLMUnavailable


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(prism.settings, "openrouter_api_key", "test-key")


def _completion(content, model="nvidia/nemotron-3-super-120b-a12b:free", finish_reason="stop", error=None):
    choice = {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
    if error:
        choice["error"] = error
    return {"id": "gen-1", "object": "chat.completion", "created": 0, "model": model, "choices": [choice]}


def _error(status, message, headers=None, metadata=None):
    body = {"error": {"code": status, "message": message, "metadata": metadata or {}}}
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
    monkeypatch.setattr(prism.settings, "openrouter_api_key", "")
    sent = _use_transport(monkeypatch, [])

    with pytest.raises(LLMUnavailable, match="isn't available right now"):
        _call()
    assert sent == []


def test_successful_call_sends_openrouter_request_with_fallback_models(monkeypatch):
    sent = _use_transport(monkeypatch, [httpx2.Response(200, json=_completion('{"message": "ok"}'))])

    assert _call() == '{"message": "ok"}'

    request = sent[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == prism.model_chain()[0]
    assert body["models"] == prism.model_chain()
    assert len(body["models"]) <= prism.MAX_MODELS
    assert body["provider"] == {"require_parameters": True}
    assert body["response_format"] == {"type": "json_object"}


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
            _error(429, "Rate limit exceeded", headers={"Retry-After": "0"}),
            httpx2.Response(200, json=_completion("second try")),
        ],
    )

    assert _call() == "second try"
    assert len(sent) == 2


def test_daily_free_limit_is_not_retried_and_says_so(monkeypatch):
    reset_ms = str(int((time.time() + 6 * 3600) * 1000))
    sent = _use_transport(
        monkeypatch,
        [_error(429, "Rate limit exceeded: free-models-per-day", headers={"X-RateLimit-Reset": reset_ms})],
    )

    with pytest.raises(LLMUnavailable, match="daily AI usage limit"):
        _call()
    assert len(sent) == 1


def test_daily_limit_reported_only_in_body_metadata_is_recognised(monkeypatch):
    reset_ms = int((time.time() + 6 * 3600) * 1000)
    _use_transport(
        monkeypatch,
        [_error(429, "Rate limit exceeded", metadata={"headers": {"X-RateLimit-Reset": reset_ms}})],
    )

    with pytest.raises(LLMUnavailable, match="daily AI usage limit"):
        _call()


def test_rate_limit_still_failing_after_retry_asks_worker_to_wait(monkeypatch):
    _use_transport(
        monkeypatch,
        [
            _error(429, "Rate limit exceeded", headers={"Retry-After": "0"}),
            _error(429, "Rate limit exceeded", headers={"Retry-After": "0"}),
        ],
    )

    with pytest.raises(LLMUnavailable, match="a lot of messages"):
        _call()


@pytest.mark.parametrize("status", [404, 408, 502, 503])
def test_model_down_or_unroutable_gives_graceful_message(monkeypatch, status):
    _use_transport(monkeypatch, [_error(status, "No endpoints found / provider down")])

    with pytest.raises(LLMUnavailable, match="model is unavailable"):
        _call()


@pytest.mark.parametrize("status", [401, 402])
def test_bad_key_or_no_credits_gives_graceful_message(monkeypatch, status):
    _use_transport(monkeypatch, [_error(status, "Invalid credentials")])

    with pytest.raises(LLMUnavailable, match="isn't available right now"):
        _call()


def test_connection_failure_gives_graceful_message(monkeypatch):
    _use_transport(monkeypatch, [httpx2.ConnectError("unreachable")])

    with pytest.raises(LLMUnavailable, match="couldn't reach"):
        _call()


def test_http_200_with_error_inside_choice_is_treated_as_failure(monkeypatch):
    upstream_error = {"code": 502, "message": "Provider returned error"}
    _use_transport(
        monkeypatch,
        [httpx2.Response(200, json=_completion(None, finish_reason="error", error=upstream_error))],
    )

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


def test_model_chain_dedupes_primary_and_caps_length(monkeypatch):
    monkeypatch.setattr(prism.settings, "openrouter_model", "a:free")
    monkeypatch.setattr(prism.settings, "openrouter_fallback_models", " a:free, b:free ,,c:free,d:free")

    assert prism.model_chain() == ["a:free", "b:free", "c:free"]
