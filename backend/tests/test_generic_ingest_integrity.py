"""Generic ingestion (``/v1/traces/batch``) against the 2026-10-18 evaluation: A1-A5, M1-M6, B1
and the backend half of C2. Each test fails on the evaluated commit f1d25a5."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select

from app.models.trace import Span, Trace

T0 = 1_760_000_000.0


def _span(name, *, trace_id, id=None, parent=None, start=0.0, end=1.0, status="ok", **extra):
    body = {
        "id": id or str(uuid.uuid4()),
        "trace_id": trace_id,
        "parent_id": parent,
        "name": name,
        "span_type": extra.pop("span_type", "generic"),
        "start_time": T0 + start if isinstance(start, float) else start,
        "end_time": (T0 + end if isinstance(end, float) else end) if end is not None else None,
        "status": status,
    }
    body.update(extra)
    return body


async def _batch(client, *spans):
    return await client.post("/v1/traces/batch", json={"spans": list(spans)})


async def _trace(client, trace_id):
    response = await client.get(f"/v1/traces/{trace_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _spans(client, trace_id):
    response = await client.get(f"/v1/traces/{trace_id}/spans")
    assert response.status_code == 200, response.text
    return {s["name"]: s for s in response.json()}


def _duration(trace):
    from datetime import datetime

    start = datetime.fromisoformat(trace["start_time"])
    end = datetime.fromisoformat(trace["end_time"])
    return (end - start).total_seconds()


# ── A1: the README example (ISO 8601 timestamps, a non-UUID trace ID) ──────────────


@pytest.mark.anyio
async def test_a1_a2_readme_example_is_accepted_and_readable(client):
    response = await client.post(
        "/v1/traces/batch",
        json={
            "spans": [
                {
                    "trace_id": "demo-001",
                    "name": "gpt-4o-call",
                    "span_type": "llm",
                    "start_time": "2025-01-01T00:00:00Z",
                    "end_time": "2025-01-01T00:00:01Z",
                    "status": "ok",
                    "tokens_input": 150,
                    "tokens_output": 50,
                    "cost_usd": 0.000875,
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert "accepted=1" in response.headers["x-observer-spans"]
    trace = await _trace(client, "demo-001")  # addressable by the ID the client used
    assert trace["id"] == response.json()[0]["id"]
    assert trace["status"] == "ok" and _duration(trace) == pytest.approx(1.0)


@pytest.mark.anyio
async def test_a1_epoch_and_iso_timestamps_mean_the_same_instant(client):
    tid = str(uuid.uuid4())
    iso = _span("a", trace_id=tid, start="2026-10-18T12:00:00+00:00", end="2026-10-18T12:00:02Z")
    assert (await _batch(client, iso)).status_code == 200
    stored = (await _spans(client, tid))["a"]
    assert stored["start_time"].startswith("2026-10-18T12:00:00")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "start",
    [1e20, -5, 0, 1_760_000_000_000, "yesterday", "2026-10-18T12:00:00", True, "1e400"],
)
async def test_a5_out_of_range_or_malformed_timestamps_are_422_not_500(client, start):
    response = await _batch(client, _span("a", trace_id=str(uuid.uuid4()), start=start, end=None))
    assert response.status_code == 422, response.text


# ── A2/A3: identifiers ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a3_non_uuid_parents_keep_the_hierarchy(client):
    parent = _span("agent", trace_id="run-7", id="step-root", start=0.0, end=2.0)
    child = _span("tool", trace_id="run-7", id="step-tool", parent="step-root", start=0.5, end=1.0)
    assert (await _batch(client, child, parent)).status_code == 200
    spans = await _spans(client, "run-7")
    assert spans["tool"]["parent_id"] == spans["agent"]["id"]


@pytest.mark.anyio
async def test_a2_same_span_id_in_two_traces_does_not_collide(client):
    first = _span("a", trace_id="t-one", id="span-001")
    second = _span("b", trace_id="t-two", id="span-001")
    assert (await _batch(client, first)).status_code == 200
    response = await _batch(client, second)
    assert "accepted=1" in response.headers["x-observer-spans"]
    assert set(await _spans(client, "t-two")) == {"b"}


@pytest.mark.anyio
@pytest.mark.parametrize("bad", ["x" * 200, "tab\there"])
async def test_a2_values_that_cannot_be_ids_are_rejected(client, bad):
    assert (await _batch(client, _span("a", trace_id=bad))).status_code == 422
    assert (await _batch(client, _span("a", trace_id="t", parent=bad))).status_code == 422


@pytest.mark.anyio
async def test_uppercase_uuid_is_stored_canonical(client):
    tid = str(uuid.uuid4())
    assert (await _batch(client, _span("a", trace_id=tid.upper()))).status_code == 200
    assert (await _trace(client, tid))["id"] == tid


# ── C2 backend half: children first, completion of an incomplete span, retries ──────


@pytest.mark.anyio
async def test_child_sent_before_its_parent_is_stored_and_linked(client):
    """The SDK exports a span when it ends, so children arrive first. With SQLite foreign keys on,
    the parent constraint used to reject them with HTTP 500."""
    tid = str(uuid.uuid4())
    root_id = str(uuid.uuid4())
    child = _span("tool", trace_id=tid, parent=root_id, start=0.1, end=0.2)
    assert (await _batch(client, child)).status_code == 200
    assert (await _trace(client, tid))["status"] == "unset"  # root still running
    root = _span("agent", trace_id=tid, id=root_id, start=0.0, end=1.0)
    assert (await _batch(client, root)).status_code == 200
    spans = await _spans(client, tid)
    assert spans["tool"]["parent_id"] == root_id
    trace = await _trace(client, tid)
    assert trace["name"] == "agent" and trace["status"] == "ok"


@pytest.mark.anyio
async def test_an_incomplete_span_is_completed_by_its_finished_version(client):
    tid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    running = _span("llm", trace_id=tid, id=sid, end=None, status="unset")
    assert (await _batch(client, running)).status_code == 200
    finished = _span(
        "llm", trace_id=tid, id=sid, end=2.5, tokens_input=10, tokens_output=4,
        cost_usd=0.01, output={"text": "hi"},
    )
    response = await _batch(client, finished)
    assert "updated=1" in response.headers["x-observer-spans"]
    span = (await _spans(client, tid))["llm"]
    assert span["status"] == "ok" and span["tokens_input"] == 10 and span["output"]
    trace = await _trace(client, tid)
    assert trace["status"] == "ok" and _duration(trace) == pytest.approx(2.5)


@pytest.mark.anyio
async def test_a_finished_span_is_never_overwritten(client):
    tid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    assert (await _batch(client, _span("llm", trace_id=tid, id=sid, status="error"))).status_code
    replay = _span("llm", trace_id=tid, id=sid, status="ok", output={"forged": True})
    response = await _batch(client, replay)
    assert "conflict=1" in response.headers["x-observer-spans"]
    span = (await _spans(client, tid))["llm"]
    assert span["status"] == "error" and span["output"] is None


@pytest.mark.anyio
async def test_a_retried_batch_is_a_duplicate(client):
    tid = str(uuid.uuid4())
    spans = [_span("a", trace_id=tid), _span("b", trace_id=tid, start=0.2, end=0.4)]
    assert (await _batch(client, *spans)).status_code == 200
    again = await _batch(client, *spans)
    assert again.status_code == 200
    assert again.headers["x-observer-spans"] == "accepted=0,updated=0,duplicate=2,conflict=0"
    assert len(await _spans(client, tid)) == 2


@pytest.mark.anyio
async def test_a_span_id_cannot_be_moved_to_another_trace(client):
    sid = str(uuid.uuid4())
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    await _batch(client, _span("a", trace_id=first, id=sid))
    response = await _batch(client, _span("a", trace_id=second, id=sid))
    assert "conflict=1" in response.headers["x-observer-spans"]
    assert set(await _spans(client, first)) == {"a"}


@pytest.mark.anyio
async def test_capture_mode_applies_to_updates(client, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_CAPTURE_DEFAULT", "metadata")
    tid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    await _batch(client, _span("llm", trace_id=tid, id=sid, end=None, status="unset"))
    secret = {"prompt": "my card is 4111 1111 1111 1111"}
    await _batch(client, _span("llm", trace_id=tid, id=sid, input=secret, output=secret))
    span = (await _spans(client, tid))["llm"]
    assert span["input"] is None and span["output"] is None and span["status"] == "ok"


# ── A4 / M1 / M6: status ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a4_a_later_root_error_turns_the_trace_red(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    await _batch(client, _span("step", trace_id=tid, parent=root, start=0.1, end=0.2))
    assert (await _trace(client, tid))["status"] == "unset"
    await _batch(client, _span("agent", trace_id=tid, id=root, status="error", end=1.0))
    assert (await _trace(client, tid))["status"] == "error"


@pytest.mark.anyio
async def test_a4_an_error_while_the_root_is_still_running_is_an_error(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    await _batch(client, _span("ok-step", trace_id=tid, parent=root, start=0.1, end=0.2))
    await _batch(client, _span("bad-step", trace_id=tid, parent=root, status="error"))
    assert (await _trace(client, tid))["status"] == "error"


@pytest.mark.anyio
async def test_m1_unset_is_not_an_error(client):
    tid = str(uuid.uuid4())
    await _batch(client, _span("a", trace_id=tid, status="unset", end=None))
    assert (await _trace(client, tid))["status"] == "unset"
    summary = (await client.get("/v1/analytics/summary")).json()
    assert summary["error_count"] == 0


@pytest.mark.anyio
async def test_m1_a_finished_root_without_explicit_status_is_ok(client):
    tid = str(uuid.uuid4())
    await _batch(client, _span("a", trace_id=tid, status="unset", end=1.0))
    assert (await _trace(client, tid))["status"] == "ok"


@pytest.mark.anyio
async def test_m6_a_successful_retry_is_an_ok_trace_with_the_failed_attempt_kept(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    attempt_1 = _span("llm attempt 1 (429)", trace_id=tid, parent=root, start=0.0, end=0.1,
                      status="error", attributes={"http.response.status_code": 429})
    attempt_2 = _span("llm attempt 2", trace_id=tid, parent=root, start=1.1, end=2.0)
    agent = _span("agent", trace_id=tid, id=root, start=0.0, end=2.1)
    await _batch(client, attempt_1, attempt_2, agent)
    trace = await _trace(client, tid)
    assert trace["status"] == "ok"
    assert (await _spans(client, tid))["llm attempt 1 (429)"]["status"] == "error"


# ── M2 / M5: interval and name ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_m2_parallel_children_are_not_summed_and_arrival_order_does_not_matter(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    tools = [
        _span(f"tool {i}", trace_id=tid, parent=root, start=0.1, end=3.0) for i in range(3)
    ]
    agent = _span("agent", trace_id=tid, id=root, start=0.0, end=3.2)
    await _batch(client, agent)
    await _batch(client, *tools)  # arrive after the root
    trace = await _trace(client, tid)
    assert _duration(trace) == pytest.approx(3.2)


@pytest.mark.anyio
async def test_m2_m5_the_root_decides_interval_and_name(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    await _batch(client, _span("late child", trace_id=tid, parent=root, start=5.0, end=6.0))
    await _batch(client, _span("agent.run", trace_id=tid, id=root, start=0.0, end=10.0))
    trace = await _trace(client, tid)
    assert trace["name"] == "agent.run"
    assert _duration(trace) == pytest.approx(10.0)


@pytest.mark.anyio
async def test_m2_an_unfinished_trace_has_no_end(client):
    tid = str(uuid.uuid4())
    await _batch(client, _span("agent", trace_id=tid, end=None, status="unset"))
    assert (await _trace(client, tid))["end_time"] is None


# ── M3 / M4 / B1: field invariants ────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    "extra",
    [{"tokens_input": -1}, {"tokens_output": -5}, {"cost_usd": -0.01}],
)
async def test_m3_negative_tokens_and_costs_are_rejected(client, extra):
    response = await _batch(client, _span("a", trace_id=str(uuid.uuid4()), **extra))
    assert response.status_code == 422


@pytest.mark.anyio
async def test_m4_end_before_start_is_rejected(client):
    response = await _batch(client, _span("a", trace_id=str(uuid.uuid4()), start=5.0, end=1.0))
    assert response.status_code == 422
    assert "end_time cannot precede start_time" in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["success", "failed", "OK", ""])
async def test_b1_unsupported_statuses_are_rejected(client, status):
    response = await _batch(client, _span("a", trace_id=str(uuid.uuid4()), status=status))
    assert response.status_code == 422


@pytest.mark.anyio
async def test_a_span_cannot_be_its_own_parent(client):
    sid = str(uuid.uuid4())
    response = await _batch(client, _span("a", trace_id=str(uuid.uuid4()), id=sid, parent=sid))
    assert response.status_code == 422


# ── SDK -> exporter -> backend, end to end ───────────────────────────────────────


@pytest.fixture
def sdk_backend(db_engine, db_session):
    llm_observatory = pytest.importorskip("llm_observatory")
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import get_session
    from app.main import app

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app) as http:
        exporter = llm_observatory.OTLPExporter(endpoint="http://testserver", max_retries=1)
        exporter._client = http
        tracer = llm_observatory.Tracer(batch_size=1000, flush_interval=60)
        tracer.add_exporter(exporter)
        yield llm_observatory, tracer, db_session
        tracer._exporters.clear()
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_sdk_trace_reaches_the_backend_finished(sdk_backend):
    sdk, tracer, db = sdk_backend
    root = tracer.start_trace("agent turn")
    llm = tracer.start_span("claude", span_type="llm", parent=root)
    llm.set_token_usage(100, 20)
    llm.set_cost(0.0006)
    time.sleep(0.01)
    llm.end()
    tool = tracer.start_span("search", span_type="tool", parent=root)
    tool.set_status(sdk.SpanStatus.ERROR)
    tool.end(sdk.SpanStatus.OK)  # must stay an error
    root.end()
    tracer.flush()

    trace = await db.get(Trace, root.trace_id)
    assert trace is not None and trace.name == "agent turn" and trace.status == "ok"
    rows = (await db.execute(select(Span).where(Span.trace_id == root.trace_id))).scalars().all()
    spans = {s.name: s for s in rows}
    assert spans["claude"].end_time is not None and spans["claude"].tokens_input == 100
    assert float(spans["claude"].cost_usd) == pytest.approx(0.0006)
    assert spans["claude"].parent_span_id == root.id
    assert spans["search"].status == "error"
