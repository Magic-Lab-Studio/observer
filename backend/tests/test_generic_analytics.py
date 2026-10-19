"""C3 of the 2026-10-18 evaluation: traces from an arbitrary application (no Manitos conventions)
produce internally consistent analytics; missing data is null, not zero."""

from __future__ import annotations

import time
import uuid

import pytest


def _span(name, *, trace_id, parent=None, start=0.0, end=1.0, status="ok", **extra):
    now = time.time() - 60
    return {
        "id": extra.pop("id", None) or str(uuid.uuid4()),
        "trace_id": trace_id,
        "parent_id": parent,
        "name": name,
        "span_type": extra.pop("span_type", "generic"),
        "start_time": now + start,
        "end_time": now + end if end is not None else None,
        "status": status,
        **extra,
    }


async def _agent_run(client, *, fails: bool, duration: float):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    spans = [
        _span(
            "claude", trace_id=tid, parent=root, span_type="llm", start=0.1, end=duration - 0.1,
            tokens_input=100, tokens_output=20, cost_usd=0.001,
            attributes={"gen_ai.request.model": "claude-sonnet-4-5"},
        ),
        _span("search", trace_id=tid, parent=root, span_type="tool", start=0.2, end=0.5,
              status="error" if fails else "ok"),
        _span("agent", id=root, trace_id=tid, end=duration, status="error" if fails else "ok"),
    ]
    response = await client.post("/v1/traces/batch", json={"spans": spans})
    assert response.status_code == 200
    return tid


@pytest.mark.anyio
async def test_empty_window_reports_no_data_not_zero(client):
    summary = (await client.get("/v1/analytics/summary")).json()
    assert summary["total_traces"] == 0
    assert summary["trace_error_rate"] is None
    assert summary["p50_trace_duration_ms"] is None


@pytest.mark.anyio
async def test_c3_failed_generic_traces_count_as_errors(client):
    await _agent_run(client, fails=False, duration=2.0)
    await _agent_run(client, fails=True, duration=4.0)
    summary = (await client.get("/v1/analytics/summary")).json()
    assert summary["total_traces"] == 2 and summary["completed_traces"] == 2
    assert summary["error_count"] == 1 and summary["trace_error_rate"] == pytest.approx(0.5)
    assert summary["span_error_count"] == 2  # the failing tool and the failing root
    assert summary["llm_span_count"] == 2 and summary["tool_span_count"] == 2
    assert summary["p50_trace_duration_ms"] == pytest.approx(2000, abs=5)
    assert summary["p95_trace_duration_ms"] == pytest.approx(4000, abs=5)
    assert summary["total_cost_usd"] == pytest.approx(0.002)

    # The list agrees with the cards.
    listed = (await client.get("/v1/traces/?status=error")).json()
    assert listed["total"] == 1

    # The timeline carries the same error.
    timeline = (await client.get("/v1/analytics/timeline")).json()
    assert sum(b["count"] for b in timeline) == 2
    assert sum(b["error_count"] for b in timeline) == 1


@pytest.mark.anyio
async def test_c3_manitos_turn_metrics_stay_empty_for_generic_traces(client):
    """The Manitos quality summary is integration-specific: generic traces are not turns."""
    await _agent_run(client, fails=True, duration=1.0)
    quality = (await client.get("/v1/analytics/manitos-quality")).json()
    assert quality["total_turns"] == 0
    assert quality["avg_ttft_ms"] is None and quality["avg_duration_ms"] is None  # B2


@pytest.mark.anyio
async def test_a_running_trace_is_running_not_failed(client):
    tid = str(uuid.uuid4())
    await client.post(
        "/v1/traces/batch",
        json={"spans": [_span("agent", trace_id=tid, end=None, status="unset")]},
    )
    summary = (await client.get("/v1/analytics/summary")).json()
    assert summary["running_traces"] == 1 and summary["error_count"] == 0
    assert summary["completed_traces"] == 0 and summary["p50_trace_duration_ms"] is None


@pytest.mark.anyio
async def test_cost_by_model_reads_gen_ai_model_attributes(client):
    await _agent_run(client, fails=False, duration=1.0)
    rows = (await client.get("/v1/analytics/cost-by-model")).json()
    expected = {"model": "claude-sonnet-4-5", "cost_usd": pytest.approx(0.001), "span_count": 1}
    assert rows == [expected]
