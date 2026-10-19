"""P1/P3 of the 2026-10-18 evaluation: provider analytics without Manitos conventions."""

import time
import uuid

import pytest


def _llm(trace_id, *, attrs, status="ok", dur=1.0, span_type="llm", tokens=(10, 5), cost=None):
    now = time.time() - 30
    return {
        "id": str(uuid.uuid4()), "trace_id": trace_id, "name": "chat", "span_type": span_type,
        "start_time": now, "end_time": now + dur, "status": status, "attributes": attrs,
        "tokens_input": tokens[0], "tokens_output": tokens[1], "cost_usd": cost,
    }


@pytest.mark.anyio
async def test_calls_grouped_by_provider_and_model_from_gen_ai_attributes(client):
    tid = str(uuid.uuid4())
    spans = [
        _llm(tid, attrs={"gen_ai.provider.name": "anthropic",
                         "gen_ai.request.model": "claude-sonnet-4-5"}, cost=0.002),
        _llm(tid, attrs={"gen_ai.system": "anthropic", "gen_ai.request.model": "claude-sonnet-4-5",
                         "gen_ai.response.model": "claude-sonnet-4-5"}, status="error", dur=3.0),
        _llm(tid, attrs={"gen_ai.system": "openai", "gen_ai.request.model": "gpt-4o-mini"}),
        # an OTLP-style span typed generic but carrying GenAI attributes is still an LLM call
        _llm(tid, span_type="generic",
             attrs={"gen_ai.operation.name": "chat", "gen_ai.request.model": "llama3"}),
        # not an LLM call
        _llm(tid, span_type="tool", attrs={"tool": "search"}, tokens=(None, None)),
    ]
    assert (await client.post("/v1/traces/batch", json={"spans": spans})).status_code == 200
    data = (await client.get("/v1/providers/models")).json()
    by = {(m["provider"], m["model"]): m for m in data["models"]}
    assert data["calls"] == 4
    sonnet = by[("anthropic", "claude-sonnet-4-5")]
    assert sonnet["calls"] == 2 and sonnet["errors"] == 1 and sonnet["error_rate"] == 0.5
    assert sonnet["cost_usd"] == pytest.approx(0.002) and sonnet["priced_calls"] == 1
    assert sonnet["tokens_input"] == 20
    assert by[("openai", "gpt-4o-mini")]["cost_usd"] is None  # unknown, not $0
    assert ("unknown", "llama3") in by  # provider not named: not guessed


@pytest.mark.anyio
async def test_no_llm_calls_is_an_empty_list(client):
    data = (await client.get("/v1/providers/models")).json()
    assert data == {"hours": 24, "project_id": None, "environment": None, "calls": 0,
                    "models": [], "truncated": False}
