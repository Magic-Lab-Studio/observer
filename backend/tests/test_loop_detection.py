"""P6 of the 2026-10-18 evaluation: repeated tool calls are reported, as a warning."""

import uuid

import pytest


def _tool(tid, parent, i, args, out, status="ok"):
    return {"id": str(uuid.uuid4()), "trace_id": tid, "parent_id": parent, "name": "lookup_order",
            "span_type": "tool", "start_time": 1_760_000_000.0 + i, "end_time": 1_760_000_000.5 + i,
            "status": status, "input": args, "output": out}


async def _ingest(client, spans):
    assert (await client.post("/v1/traces/batch", json={"spans": spans})).status_code == 200


@pytest.mark.anyio
async def test_fifteen_identical_calls_are_flagged_with_no_progress(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    spans = [_tool(tid, root, i, {"order": "A-17"}, {"found": False}) for i in range(15)]
    spans.append({"id": root, "trace_id": tid, "name": "agent", "span_type": "agent",
                  "start_time": 1_760_000_000.0, "end_time": 1_760_000_020.0, "status": "ok"})
    await _ingest(client, spans)
    data = (await client.get(f"/v1/traces/{tid}/diagnostics")).json()
    [group] = data["repeated_calls"]
    assert group["tool"] == "lookup_order" and group["count"] == 15
    assert group["same_output"] is True and group["severity"] == "warning"
    assert group["window_ms"] == pytest.approx(14_500)


@pytest.mark.anyio
async def test_iterating_over_different_arguments_is_not_a_loop(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    await _ingest(client, [_tool(tid, root, i, {"order": f"A-{i}"}, {"ok": i}) for i in range(15)])
    assert (await client.get(f"/v1/traces/{tid}/diagnostics")).json()["repeated_calls"] == []


@pytest.mark.anyio
async def test_polling_that_progresses_is_reported_without_same_output(client, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_LOOP_MIN_REPEATS", "3")
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    await _ingest(client, [_tool(tid, root, i, {"job": 1}, {"progress": i}) for i in range(4)])
    [group] = (await client.get(f"/v1/traces/{tid}/diagnostics")).json()["repeated_calls"]
    assert group["count"] == 4 and group["same_output"] is False


@pytest.mark.anyio
async def test_diagnostics_of_a_missing_trace_is_404(client):
    assert (await client.get(f"/v1/traces/{uuid.uuid4()}/diagnostics")).status_code == 404
