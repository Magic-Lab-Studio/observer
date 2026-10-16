"""The metrics query: allow-listed fields, grouping, time buckets, row cap and parity."""

from datetime import datetime, timedelta, timezone

import pytest

from app.metrics_query import QueryRequest, metric_key, validate
from app.models import Evaluation, Span, Trace


async def _turn(
    db,
    *,
    ago_min,
    duration_ms,
    status="ok",
    env="test",
    answered="groq · qwen",
    chain="groq:q=ok@900ms",
    fallback=False,
    intent="chat",
    degraded=False,
    session="s1",
):
    created = datetime.now(timezone.utc) - timedelta(minutes=ago_min)
    trace = Trace(
        name="manitos.turn",
        start_time=created,
        end_time=created + timedelta(milliseconds=duration_ms),
        status=status,
        project_id="manitos",
        environment=env,
        session_id=session,
        created_at=created,
    )
    db.add(trace)
    await db.flush()
    attrs = {"duration_ms": duration_ms, "ttft_ms": duration_ms / 3, "llm_degraded": degraded}
    db.add(
        Span(
            trace_id=trace.id,
            name="manitos.turn.lifecycle",
            span_type="turn",
            start_time=created,
            status=status,
            attributes=attrs,
        )
    )
    db.add(
        Span(
            trace_id=trace.id,
            name="llm.chain",
            span_type="event",
            start_time=created,
            status="ok",
            attributes={"answered_by": answered, "attempts_text": chain},
        )
    )
    db.add(
        Span(
            trace_id=trace.id,
            name="model_routing",
            span_type="event",
            start_time=created,
            status="ok",
            attributes={"intent": intent, "local_fallback": fallback},
        )
    )
    await db.flush()
    return trace


def _q(**kw):
    base = {"view": "turns", "metrics": [{"measure": "count", "aggregation": "count"}], "hours": 6}
    base.update(kw)
    return base


async def _seed(db):
    await _turn(db, ago_min=10, duration_ms=1000)
    await _turn(
        db,
        ago_min=20,
        duration_ms=3000,
        answered="gemini · flash",
        chain="groq:q=429@200ms>gemini:f=ok@2500ms",
        intent="search",
    )
    await _turn(
        db,
        ago_min=30,
        duration_ms=9000,
        status="error",
        env="prod",
        degraded=True,
        fallback=True,
        intent="plan",
        answered="llamacpp · gemma",
        chain="groq:q=skipped_exhausted>llamacpp:-=ok@8500ms",
    )
    await db.commit()


@pytest.mark.anyio
async def test_count_group_by_and_filters(client, db_session):
    await _seed(db_session)
    r = (await client.post("/v1/analytics/query", json=_q())).json()
    assert r["rows"] == [{"count": 3.0}] and r["samples"] == 3 and r["granularity"] is None
    by_env = (await client.post("/v1/analytics/query", json=_q(dimensions=["environment"]))).json()
    assert {x["environment"]: x["count"] for x in by_env["rows"]} == {"test": 2.0, "prod": 1.0}
    prod = (
        await client.post(
            "/v1/analytics/query",
            json=_q(filters=[{"field": "environment", "op": "=", "value": "prod"}]),
        )
    ).json()
    assert prod["rows"][0]["count"] == 1.0
    skip = (
        await client.post(
            "/v1/analytics/query",
            json=_q(filters=[{"field": "intent", "op": "in", "value": ["chat", "search"]}]),
        )
    ).json()
    assert skip["rows"][0]["count"] == 2.0
    sess = (
        await client.post(
            "/v1/analytics/query",
            json=_q(filters=[{"field": "session_id", "op": "!=", "value": "nobody"}]),
        )
    ).json()
    assert sess["rows"][0]["count"] == 3.0  # session_id filters but never groups


@pytest.mark.anyio
async def test_percentiles_rates_and_provider_attempts(client, db_session):
    await _seed(db_session)
    q = _q(
        metrics=[
            {"measure": "duration_ms", "aggregation": "p50"},
            {"measure": "duration_ms", "aggregation": "max"},
            {"measure": "error", "aggregation": "avg"},
            {"measure": "fallback", "aggregation": "sum"},
        ]
    )
    row = (await client.post("/v1/analytics/query", json=q)).json()["rows"][0]
    assert row["p50_duration_ms"] == 3000 and row["max_duration_ms"] == 9000
    assert row["avg_error"] == pytest.approx(1 / 3) and row["sum_fallback"] == 1.0

    attempts = (
        await client.post(
            "/v1/analytics/query",
            json={
                "view": "provider_attempts",
                "hours": 6,
                "dimensions": ["provider"],
                "metrics": [
                    {"measure": "count", "aggregation": "count"},
                    {"measure": "failed", "aggregation": "sum"},
                    {"measure": "ms", "aggregation": "p50"},
                ],
            },
        )
    ).json()
    by = {x["provider"]: x for x in attempts["rows"]}
    assert by["groq"]["count"] == 3.0 and by["groq"]["sum_failed"] == 1.0  # a skip is not a failure
    assert by["gemini"]["p50_ms"] == 2500 and by["llamacpp"]["p50_ms"] == 8500


@pytest.mark.anyio
async def test_time_buckets_order_and_row_limit(client, db_session):
    await _seed(db_session)
    r = (await client.post("/v1/analytics/query", json=_q(granularity="hour", hours=3))).json()
    assert r["granularity"] == "hour" and sum(x["count"] for x in r["rows"]) == 3
    assert all("time" in x for x in r["rows"])
    auto = (await client.post("/v1/analytics/query", json=_q(granularity="auto", hours=1))).json()
    assert auto["granularity"] == "minute"
    ordered = (
        await client.post(
            "/v1/analytics/query",
            json=_q(dimensions=["model"], order_by={"field": "count", "direction": "asc"}),
        )
    ).json()
    assert len(ordered["rows"]) == 3
    limited = (
        await client.post("/v1/analytics/query", json=_q(dimensions=["model"], row_limit=2))
    ).json()
    assert len(limited["rows"]) == 2


@pytest.mark.anyio
async def test_only_allow_listed_fields_are_accepted(client, db_session):
    for body in (
        _q(dimensions=["session_id"]),  # high cardinality: filter only
        _q(dimensions=["nope"]),
        _q(metrics=[{"measure": "evil; drop table", "aggregation": "avg"}]),
        _q(filters=[{"field": "password", "value": "x"}]),
        _q(order_by={"field": "nope"}),
        {"view": "nope", "metrics": [{"measure": "count", "aggregation": "count"}]},
    ):
        assert (await client.post("/v1/analytics/query", json=body)).status_code == 422, body
    bad_shape = _q(filters=[{"field": "environment", "op": "in", "value": "prod"}])
    assert (await client.post("/v1/analytics/query", json=bad_shape)).status_code == 422
    assert (
        await client.post(
            "/v1/analytics/query",
            json=_q(from_time="2026-01-02T00:00:00Z", to_time="2026-01-01T00:00:00Z"),
        )
    ).status_code == 422
    too_many = _q(dimensions=["model", "provider", "status"])
    assert (await client.post("/v1/analytics/query", json=too_many)).status_code == 422


@pytest.mark.anyio
async def test_parity_with_the_manitos_quality_endpoint(client, db_session):
    await _seed(db_session)
    quality = (await client.get("/v1/analytics/manitos-quality?hours=6")).json()
    q = _q(
        metrics=[
            {"measure": "count", "aggregation": "count"},
            {"measure": "error", "aggregation": "avg"},
            {"measure": "degraded", "aggregation": "avg"},
            {"measure": "fallback", "aggregation": "avg"},
            {"measure": "duration_ms", "aggregation": "avg"},
        ]
    )
    row = (await client.post("/v1/analytics/query", json=q)).json()["rows"][0]
    assert row["count"] == quality["total_turns"]
    assert row["avg_error"] == pytest.approx(quality["error_rate"])
    assert row["avg_degraded"] == pytest.approx(quality["degraded_rate"])
    assert row["avg_fallback"] == pytest.approx(quality["fallback_rate"])
    assert row["avg_duration_ms"] == pytest.approx(quality["avg_duration_ms"])


@pytest.mark.anyio
async def test_evaluations_view_and_schema(client, db_session):
    trace = await _turn(db_session, ago_min=5, duration_ms=1000)
    db_session.add(
        Evaluation(
            trace_id=trace.id,
            evaluator_type="rule_based",
            score=0.9,
            result={"provider": "groq", "model": "groq · qwen", "grade": "A", "passed": True},
        )
    )
    await db_session.commit()
    r = (
        await client.post(
            "/v1/analytics/query",
            json={
                "view": "evaluations",
                "hours": 6,
                "dimensions": ["grade"],
                "metrics": [
                    {"measure": "score", "aggregation": "avg"},
                    {"measure": "passed", "aggregation": "avg"},
                ],
            },
        )
    ).json()
    assert r["rows"] == [{"grade": "A", "avg_score": pytest.approx(0.9), "avg_passed": 1.0}]
    schema = (await client.get("/v1/analytics/query/schema")).json()
    assert set(schema["views"]) == {"turns", "provider_attempts", "evaluations"}
    assert "session_id" in {f["id"] for f in schema["views"]["turns"]["filter_only"]}


def test_validate_and_metric_key_are_pure():
    req = QueryRequest.model_validate(_q(metrics=[{"measure": "ttft_ms", "aggregation": "p95"}]))
    assert validate(req).label == "Turnos" and metric_key(req.metrics[0]) == "p95_ttft_ms"


@pytest.mark.anyio
async def test_end_date_is_applied_before_the_row_cap(client, db_session, monkeypatch):
    import app.metrics_query as mq

    monkeypatch.setattr(mq, "ROW_LIMIT", 2)
    # The only turn inside the requested range:
    await _turn(db_session, ago_min=300, duration_ms=1000)
    for i in range(4):  # newer than the range end: they used to fill the cap
        await _turn(db_session, ago_min=10 + i, duration_ms=1000)
    await db_session.commit()
    now = datetime.now(timezone.utc)
    body = _q(
        from_time=(now - timedelta(hours=6)).isoformat(),
        to_time=(now - timedelta(hours=1)).isoformat(),
    )
    res = (await client.post("/v1/analytics/query", json=body)).json()
    assert res["samples"] == 1 and res["rows"][0]["count"] == 1
    assert res["truncated"] is False
    # Inside the range there are more rows than the cap: now it says so.
    body["to_time"] = now.isoformat()
    assert (await client.post("/v1/analytics/query", json=body)).json()["truncated"] is True
