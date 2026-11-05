"""Outcomes end to end: ingested through the SDK batch and OTLP, read back by the API."""

import time
import uuid

import pytest

PROJECT = "agent-outcomes"


def _span(tid, name, span_type, start, end, *, parent=None, status="ok", attrs=None, sid=None):
    return {"id": sid or str(uuid.uuid4()), "trace_id": tid, "parent_id": parent, "name": name,
            "span_type": span_type, "start_time": start, "end_time": end, "status": status,
            "attributes": {"service.name": PROJECT, **(attrs or {})}}


def _abstention(t0):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    return tid, [
        _span(tid, "turn", "agent", t0, t0 + 3, sid=root),
        _span(tid, "search", "retrieval", t0 + 0.1, t0 + 0.5, parent=root,
              attrs={"retrieval.passages": 4, "retrieval.sources": ["kb:12", "kb:40"]}),
        _span(tid, "coverage", "gate", t0 + 0.6, t0 + 0.7, parent=root,
              attrs={"gate.name": "coverage", "gate.stage": "coverage", "gate.status": "stop",
                     "gate.reason_codes": ["coverage_below_min"]}),
        _span(tid, "close", "close", t0 + 2.9, t0 + 3, parent=root,
              attrs={"close.kind": "abstain"}),
    ]


def _answered(t0):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    return tid, [
        _span(tid, "turn", "agent", t0, t0 + 2, sid=root),
        _span(tid, "chat", "llm", t0 + 0.1, t0 + 1.9, parent=root,
              attrs={"gen_ai.request.model": "llama3.2:3b",
                     "gen_ai.response.finish_reasons": ["stop"]}),
    ]


async def _ingest(client, spans):
    r = await client.post("/v1/traces/batch", json={"spans": spans})
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_agent_turns_lists_outcomes_and_reasons_without_content(client):
    t0 = time.time() - 60
    abstained, spans = _abstention(t0)
    answered, more = _answered(t0 + 5)
    await _ingest(client, spans + more)
    data = (await client.get(f"/v1/agent/turns?project_id={PROJECT}")).json()
    assert data["total"] == 2 and data["counts"] == {"abstained": 1, "answered": 1}
    [row] = (await client.get(
        f"/v1/agent/turns?project_id={PROJECT}&outcome=abstained")).json()["items"]
    assert row["trace_id"] == abstained
    assert (row["outcome_source"], row["outcome_reason"]) == ("close", "coverage_below_min")
    assert row["abstained_with_evidence"] is True and row["retrieval_passages"] == 4
    assert "kb:12" not in str(row)  # names and codes only, no sources or content


@pytest.mark.anyio
async def test_diagnostics_reports_outcome_reason(client):
    tid, spans = _abstention(time.time() - 30)
    await _ingest(client, spans)
    data = (await client.get(f"/v1/traces/{tid}/diagnostics")).json()
    assert data["outcome"] == "abstained" and data["outcome_reason"] == "coverage_below_min"
    assert data["gates"][0]["reason_codes"] == ["coverage_below_min"]
    assert data["repeated_calls"] == []


@pytest.mark.anyio
async def test_alert_fires_on_abstained_with_evidence(client):
    _, spans = _abstention(time.time() - 30)
    await _ingest(client, spans)
    rule = {"name": "abstained with evidence", "metric": "abstained_with_evidence_rate",
            "threshold": 0, "min_samples": 1, "window_minutes": 60, "project_id": PROJECT}
    assert (await client.post("/v1/alerts/rules", json=rule)).status_code == 200
    result = (await client.post("/v1/alerts/evaluate")).json()
    assert result["opened"] == 1


@pytest.mark.anyio
async def test_explore_groups_by_outcome(client):
    t0 = time.time() - 60
    _, a = _abstention(t0)
    _, b = _answered(t0 + 4)
    _, c = _answered(t0 + 8)
    await _ingest(client, a + b + c)
    body = {"view": "turns", "metrics": [{"measure": "count", "aggregation": "count"}],
            "dimensions": ["outcome"], "project_id": PROJECT, "hours": 1}
    r = await client.post("/v1/analytics/query", json=body)
    assert r.status_code == 200, r.text
    rows = {row["outcome"]: row["count"] for row in r.json()["rows"]}
    assert rows == {"abstained": 1, "answered": 2}


@pytest.mark.anyio
async def test_turn_path_includes_gate_retrieval_and_close(client):
    tid, spans = _abstention(time.time() - 30)
    await _ingest(client, spans)
    path = (await client.get(f"/v1/governance/turns/{tid}")).json()
    steps = [(s["span_type"], s["outcome"]) for s in path["steps"]]
    assert steps == [("retrieval", "4 passages"), ("gate", "stop"), ("close", "abstain")]
    assert path["steps"][1]["attributes"]["gate.reason_codes"] == ["coverage_below_min"]


def _otlp_attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, list):
        values = [{"stringValue": v} for v in value]
        return {"key": key, "value": {"arrayValue": {"values": values}}}
    return {"key": key, "value": {"stringValue": str(value)}}


@pytest.mark.anyio
async def test_otlp_metadata_mode_keeps_the_contract(client):
    trace_hex, t0 = uuid.uuid4().hex, int((time.time() - 30) * 1e9)

    def span(sid, name, attrs, parent="", a=0, b=1):
        return {"traceId": trace_hex, "spanId": sid, "parentSpanId": parent, "name": name,
                "startTimeUnixNano": str(t0 + a * 10**8), "endTimeUnixNano": str(t0 + b * 10**8),
                "attributes": [_otlp_attr(k, v) for k, v in attrs.items()]}

    body = {"resourceSpans": [{
        "resource": {"attributes": [_otlp_attr("service.name", "agent-otlp")]},
        "scopeSpans": [{"spans": [
            span("a" * 16, "turn", {}, a=0, b=9),
            span("b" * 16, "search", {"observer.span.type": "retrieval",
                                      "retrieval.passages": 2}, "a" * 16, 1, 2),
            span("c" * 16, "verify", {"observer.span.type": "gate", "gate.name": "verification",
                                      "gate.status": "stop",
                                      "gate.reason_codes": ["claim_unsupported"]}, "a" * 16, 3, 4),
            span("d" * 16, "close", {"observer.span.type": "close", "close.kind": "abstain"},
                 "a" * 16, 8, 9),
        ]}],
    }]}
    r = await client.post("/v1/otlp/v1/traces", json=body)
    assert r.status_code == 200, r.text
    [row] = (await client.get("/v1/agent/turns?project_id=agent-otlp")).json()["items"]
    assert row["outcome"] == "abstained" and row["outcome_reason"] == "claim_unsupported"
    assert row["abstained_with_evidence"] is True


@pytest.mark.anyio
async def test_turn_path_reads_the_declared_type_of_sdk_spans(client):
    t0 = time.time() - 30
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    await _ingest(client, [
        _span(tid, "turn", "agent", t0, t0 + 2, sid=root),
        _span(tid, "check", "generic", t0 + 0.2, t0 + 0.3, parent=root,
              attrs={"observer.span.type": "gate", "gate.status": "stop",
                     "gate.reason_codes": ["policy"]}),
        _span(tid, "close", "Close", t0 + 1.9, t0 + 2, parent=root,
              attrs={"close.kind": "refuse", "observer.outcome.reason": "policy"}),
    ])
    path = (await client.get(f"/v1/governance/turns/{tid}")).json()
    steps = [(s["span_type"], s["outcome"]) for s in path["steps"]]
    assert steps == [("gate", "stop"), ("close", "refuse")]
    assert path["steps"][1]["attributes"]["observer.outcome.reason"] == "policy"


@pytest.mark.anyio
async def test_trace_listing_carries_each_turn_outcome(client):
    tid, spans = _abstention(time.time() - 30)
    await _ingest(client, spans)
    listed = (await client.get(f"/v1/traces/?project_id={PROJECT}")).json()["traces"]
    assert {t["id"]: t["outcome"] for t in listed}[tid] == "abstained"
    single = (await client.get(f"/v1/traces/{tid}")).json()
    assert single["id"] == tid
