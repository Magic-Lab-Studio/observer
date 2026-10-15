"""Alert engine (state machine, cooldown, quota metric) and the /v1/alerts API."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts import (
    DEFAULT_RULES,
    breached,
    cleared,
    ensure_default_rules,
    evaluate_rules,
)
from app.metrics_core import TurnSignals, compute_metric, parse_attempts, percentile
from app.models import AlertEvent, AlertRule, Span, Trace


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _turn(db, *, ago_min: float, duration_ms: float, status="ok", fallback=False, chain=""):
    created = _now() - timedelta(minutes=ago_min)
    trace = Trace(
        name="manitos.turn",
        start_time=created,
        end_time=created + timedelta(milliseconds=duration_ms),
        status=status,
        project_id="manitos",
        environment="test",
        created_at=created,
    )
    db.add(trace)
    await db.flush()
    db.add(
        Span(
            trace_id=trace.id,
            name="manitos.turn.lifecycle",
            span_type="turn",
            start_time=created,
            status=status,
            attributes={"duration_ms": duration_ms, "llm_degraded": False},
        )
    )
    if fallback:
        db.add(
            Span(
                trace_id=trace.id,
                name="model_routing",
                span_type="event",
                start_time=created,
                status="ok",
                attributes={"local_fallback": True, "intent": "chat"},
            )
        )
    if chain:
        db.add(
            Span(
                trace_id=trace.id,
                name="llm.chain",
                span_type="event",
                start_time=created,
                status="ok",
                attributes={"attempts_text": chain, "answered_by": "x · y"},
            )
        )
    await db.flush()


def test_comparators_and_hysteresis():
    assert breached(31, ">", 30) and not breached(30, ">", 30) and breached(30, ">=", 30)
    assert breached(1, "<", 2) and not breached(2, "<", 2)
    assert not cleared(29, ">", 30)  # inside the limit but not by the 10 % margin
    assert cleared(27, ">", 30)
    assert cleared(0, ">", 0) and not cleared(1, ">", 0)  # zero threshold: no margin
    assert cleared(2.2, "<", 2) and not cleared(2.05, "<", 2)


def test_metric_math_matches_the_dashboards_percentile_rule():
    turns = [
        TurnSignals(trace_id=str(i), duration_ms=float(d)) for i, d in enumerate([1, 2, 3, 4, 100])
    ]
    assert compute_metric("p50_latency_ms", turns) == (3.0, 5)
    assert compute_metric("p95_latency_ms", turns) == (100.0, 5)
    assert percentile([], 0.5) is None
    turns[0].status = "error"
    turns[1].fallback = True
    assert compute_metric("error_rate", turns) == (0.2, 5)
    assert compute_metric("fallback_rate", turns) == (0.2, 5)
    attempts = parse_attempts("groq:q=429>cloudflare:l=ok>nvidia:g=skipped_exhausted")
    t = TurnSignals(trace_id="a", attempts=attempts)
    assert compute_metric("provider_failure_rate", [t]) == (0.5, 2)  # skipped does not count
    assert compute_metric("provider_failure_rate", [t], scope="cloudflare") == (0.0, 1)


@pytest.mark.anyio
async def test_rule_fires_resolves_with_margin_and_respects_cooldown(db_session):
    for i in range(12):
        await _turn(db_session, ago_min=5 + i, duration_ms=40000)
    rule = AlertRule(
        name="p95 alto",
        metric="p95_latency_ms",
        comparator=">",
        threshold=30000,
        window_minutes=60,
        min_samples=10,
        cooldown_minutes=30,
        project_id="manitos",
    )
    db_session.add(rule)
    await db_session.commit()

    first = await evaluate_rules(db_session)
    assert (first.opened, first.resolved) == (1, 0)
    again = await evaluate_rules(db_session)  # still breached: no second event
    assert (again.opened, again.resolved) == (0, 0)
    events = (await db_session.execute(select(AlertEvent))).scalars().all()
    assert len(events) == 1 and events[0].state == "open" and events[0].value == 40000

    # The window moves on and the fast turns now dominate: resolved.
    later = _now() + timedelta(minutes=70)
    for i in range(12):
        created = later - timedelta(minutes=5 + i)
        trace = Trace(
            name="manitos.turn",
            start_time=created,
            end_time=created + timedelta(seconds=2),
            status="ok",
            project_id="manitos",
            environment="test",
            created_at=created,
        )
        db_session.add(trace)
    await db_session.commit()
    done = await evaluate_rules(db_session, now=later)
    assert done.resolved == 1
    event = (await db_session.execute(select(AlertEvent))).scalars().one()
    assert event.state == "resolved" and event.resolved_at is not None

    # Cooldown: even if it breaches again right away, no new event until it elapses.
    for i in range(12):
        await _turn(db_session, ago_min=1 + i * 0.1, duration_ms=50000)
    await db_session.commit()
    blocked = await evaluate_rules(db_session, now=later + timedelta(minutes=71))
    assert blocked.opened == 0


@pytest.mark.anyio
async def test_too_few_samples_never_fires(db_session):
    for i in range(3):
        await _turn(db_session, ago_min=i + 1, duration_ms=90000)
    db_session.add(
        AlertRule(
            name="r",
            metric="p95_latency_ms",
            comparator=">",
            threshold=1000,
            window_minutes=60,
            min_samples=10,
            project_id="manitos",
        )
    )
    await db_session.commit()
    assert (await evaluate_rules(db_session)).opened == 0


@pytest.mark.anyio
async def test_provider_quota_rule_reads_the_snapshot(db_session, tmp_path, monkeypatch):
    import json
    import time

    path = tmp_path / "snap.json"
    monkeypatch.setenv("OBSERVATORY_PROVIDERS_SNAPSHOT", str(path))
    snap = {
        "received_at": time.time(),
        "providers": [{"id": "gemini", "out_of_quota": [{"model": "m", "returns_in_sec": 60}]}],
    }
    path.write_text(json.dumps(snap))
    db_session.add(
        AlertRule(
            name="q",
            metric="provider_quota_out",
            comparator=">",
            threshold=0,
            window_minutes=5,
            min_samples=1,
            project_id="manitos",
            cooldown_minutes=0,
        )
    )
    await db_session.commit()
    assert (await evaluate_rules(db_session)).opened == 1
    snap["providers"][0]["out_of_quota"] = []
    snap["received_at"] = time.time()
    path.write_text(json.dumps(snap))
    assert (await evaluate_rules(db_session)).resolved == 1
    path.write_text(json.dumps({**snap, "received_at": time.time() - 3600}))  # stale: no verdict
    assert (await evaluate_rules(db_session)).evaluated == 0


@pytest.mark.anyio
async def test_defaults_seed_once(db_session, monkeypatch):
    assert await ensure_default_rules(db_session) == len(DEFAULT_RULES)
    assert await ensure_default_rules(db_session) == 0
    monkeypatch.setenv("OBSERVATORY_ALERTS_DEFAULTS", "0")


@pytest.mark.anyio
async def test_alerts_api_crud_ack_and_evaluate(client, db_session):
    metrics = (await client.get("/v1/alerts/metrics")).json()
    assert "p95_latency_ms" in {m["id"] for m in metrics}
    bad = await client.post(
        "/v1/alerts/rules", json={"name": "x", "metric": "nope", "threshold": 1}
    )
    assert bad.status_code == 422
    created = await client.post(
        "/v1/alerts/rules",
        json={"name": "errores", "metric": "error_rate", "threshold": 0.1, "min_samples": 2},
    )
    assert created.status_code == 200
    rule_id = created.json()["id"]
    patched = await client.patch(f"/v1/alerts/rules/{rule_id}", json={"threshold": 0.5})
    assert patched.json()["threshold"] == 0.5
    assert (await client.patch("/v1/alerts/rules/none", json={"enabled": False})).status_code == 404

    for i in range(4):
        await _turn(db_session, ago_min=i + 1, duration_ms=1000, status="error")
    await db_session.commit()
    run = (await client.post("/v1/alerts/evaluate")).json()
    assert run["opened"] == 1
    listing = (await client.get("/v1/alerts/events?state=open")).json()
    assert listing["open_count"] == 1
    event_id = listing["events"][0]["id"]
    acked = (await client.post(f"/v1/alerts/events/{event_id}/ack")).json()
    assert acked["acked_at"] is not None
    assert (await client.delete(f"/v1/alerts/rules/{rule_id}")).status_code == 200
    assert (await client.get("/v1/alerts/events")).json()["events"] == []


@pytest.mark.anyio
async def test_retention_also_purges_old_alert_events(db_session):
    from app.retention import purge_older_than

    rule = AlertRule(
        name="r",
        metric="error_rate",
        comparator=">",
        threshold=0.1,
        window_minutes=60,
        min_samples=1,
        project_id="manitos",
    )
    db_session.add(rule)
    await db_session.flush()
    old = _now() - timedelta(days=10)
    db_session.add(
        AlertEvent(
            rule_id=rule.id,
            rule_name="r",
            metric="error_rate",
            comparator=">",
            severity="warning",
            state="resolved",
            threshold=0.1,
            window_minutes=60,
            fired_at=old,
        )
    )
    await db_session.commit()
    counts = await purge_older_than(db_session, 7)
    assert counts["alert_events"] == 1


def test_factory_type_is_the_injected_seam():
    # alerts_loop takes the session factory as a parameter, so tests never touch observatory.db.
    import inspect

    from app.alerts import alerts_loop

    assert list(inspect.signature(alerts_loop).parameters) == ["factory"]
    assert async_sessionmaker is not None and AsyncSession is not None
