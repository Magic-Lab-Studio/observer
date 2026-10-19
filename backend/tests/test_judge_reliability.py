"""P4/P5 of the 2026-10-18 evaluation: a provider-neutral judge, and no PASS without evidence."""

from __future__ import annotations

import json

import httpx
import pytest

from app.evaluators import LLMJudgeEvaluator
from app.evaluators.judge_backends import JudgeConfig

TRACE = {
    "id": "t", "name": "agent", "status": "ok", "total_spans": 1, "total_cost_usd": 0.001,
    "max_latency_ms": 300, "error_count": 0,
    "spans": [{"id": "s", "name": "llm", "span_type": "llm", "tokens_input": 10,
               "tokens_output": 5, "input": {"q": "hi"}, "output": {"a": "hello"}}],
}


@pytest.fixture
def judge_http(monkeypatch):
    """Route the judge's httpx calls to a handler; record the requests."""
    seen: list[httpx.Request] = []
    state = {"handler": lambda request: httpx.Response(500)}
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        def handle(request):
            seen.append(request)
            return state["handler"](request)

        kwargs["transport"] = httpx.MockTransport(handle)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return state, seen


def _judge(monkeypatch, provider="openai", **env):
    for key in ("OBSERVATORY_JUDGE_PROVIDER", "OBSERVATORY_JUDGE_BASE_URL",
                "OBSERVATORY_JUDGE_API_KEY", "OBSERVATORY_JUDGE_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OBSERVATORY_JUDGE_PROVIDER", provider)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return LLMJudgeEvaluator()


def _answer(level):
    body = {"choices": [{"message": {"content": json.dumps({"level": level})}}]}
    return lambda request: httpx.Response(200, json=body)


@pytest.mark.anyio
async def test_no_judge_means_incomplete_not_passed(monkeypatch):
    result = await _judge(monkeypatch).evaluate("t", TRACE, [])
    assert result.passed is None
    details = result.details
    assert details["evaluation_status"] == "incomplete"
    assert details["skipped_criteria"] and all(
        s["reason"] == "judge_not_configured" and s["retryable"] is False
        for s in details["skipped_criteria"]
    )
    assert "latency" in details["evaluated_criteria"]


@pytest.mark.anyio
async def test_a_failing_judge_is_recorded_as_retryable_not_scored(monkeypatch, judge_http):
    state, _ = judge_http
    state["handler"] = lambda request: httpx.Response(429)
    result = await _judge(monkeypatch, OPENAI_API_KEY="k").evaluate("t", TRACE, [])
    assert result.passed is None
    reasons = {s["reason"] for s in result.details["skipped_criteria"]}
    assert reasons == {"judge_http_429"}
    assert all(s["retryable"] for s in result.details["skipped_criteria"])
    assert not any(c in result.criteria for c in ("output_quality", "safety"))


@pytest.mark.anyio
async def test_an_unknown_level_is_skipped_not_replaced(monkeypatch, judge_http):
    state, _ = judge_http
    state["handler"] = _answer("splendid")
    result = await _judge(monkeypatch, OPENAI_API_KEY="k").evaluate("t", TRACE, [])
    assert result.passed is None
    assert {s["reason"] for s in result.details["skipped_criteria"]} == {"judge_invalid_level"}


@pytest.mark.anyio
async def test_a_complete_judgement_can_pass(monkeypatch, judge_http):
    state, seen = judge_http
    state["handler"] = _answer("excellent")
    result = await _judge(monkeypatch, OPENAI_API_KEY="secret-key").evaluate("t", TRACE, [])
    assert result.details["evaluation_status"] == "complete"
    assert result.passed is True and result.details["skipped_criteria"] == []
    assert seen[0].url.host == "api.openai.com"
    assert result.details["judge"] == {"provider": "openai", "model": "gpt-4o"}


@pytest.mark.anyio
async def test_openai_compatible_local_endpoint_needs_no_key(monkeypatch, judge_http):
    state, seen = judge_http
    state["handler"] = _answer("excellent")
    evaluator = _judge(
        monkeypatch, "openai-compatible",
        OBSERVATORY_JUDGE_BASE_URL="http://127.0.0.1:11434/v1", OBSERVATORY_JUDGE_MODEL="qwen3",
    )
    result = await evaluator.evaluate("t", TRACE, [])
    assert result.passed is True
    assert str(seen[0].url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in seen[0].headers


def test_configuration_problems_are_named(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert JudgeConfig("openai", "gpt-4o", None, None).problem() == "judge_not_configured"
    assert JudgeConfig("openai-compatible", "m", None, None).problem() == "judge_base_url_not_set"
    assert JudgeConfig("openai-compatible", None, None, "http://x/v1").problem() == (
        "judge_model_not_set")
    assert JudgeConfig("bard", "m", "k", None).problem() == "judge_provider_unknown:bard"
    assert JudgeConfig("anthropic", "claude-opus-5-5", None, None).problem() is None


@pytest.mark.anyio
async def test_summary_does_not_count_incomplete_evaluations_as_passed(client, monkeypatch):
    for key in ("OPENAI_API_KEY", "OBSERVATORY_JUDGE_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    trace = await client.post(
        "/v1/traces/", json={"name": "t", "start_time": 1700000000.0, "status": "ok",
                             "spans": [{"name": "llm", "span_type": "llm",
                                        "start_time": 1700000000.0, "end_time": 1700000000.3,
                                        "status": "ok"}]},
    )
    body = {"trace_id": trace.json()["id"], "evaluator_type": "llm_judge", "criteria": []}
    run = await client.post("/v1/evaluations/run", json=body)
    assert run.status_code == 200, run.text
    summary = (await client.get("/v1/evaluations/summary")).json()
    assert summary["incomplete"] == 1 and summary["passed"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (200, {"stop_reason": "end_turn"}, None),
        (200, {"stop_reason": "refusal"}, "judge_refused"),
        (429, None, "judge_http_429"),
    ],
)
async def test_anthropic_judge_through_the_sdk(monkeypatch, status, body, reason):
    anthropic = pytest.importorskip("anthropic")
    try:
        import httpx2 as hx
    except ImportError:
        hx = httpx
    seen = []

    def handler(request):
        seen.append(request)
        if status != 200:
            error = {"type": "error", "error": {"type": "x", "message": "x"}}
            return hx.Response(status, json=error)
        return hx.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": '{"level": "excellent"}'}],
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}, **body,
        })

    real = anthropic.AsyncAnthropic
    def client(**kw):
        transport = hx.MockTransport(handler)
        return real(**{**kw, "max_retries": 0}, http_client=hx.AsyncClient(transport=transport))

    monkeypatch.setattr(anthropic, "AsyncAnthropic", client)
    evaluator = _judge(monkeypatch, "anthropic", OBSERVATORY_JUDGE_API_KEY="k")
    result = await evaluator.evaluate("t", TRACE, [])
    sent = json.loads(seen[0].content)
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    if reason is None:
        assert result.passed is True and result.details["evaluation_status"] == "complete"
    else:
        assert result.passed is None
        assert {s["reason"] for s in result.details["skipped_criteria"]} == {reason}
