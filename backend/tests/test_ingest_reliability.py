import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.alerts import evaluate_rules
from app.models import AlertEvent, AlertRule, Span, Trace


def _batch(span_id, trace_id):
    return {
        "spans": [
            {
                "id": span_id,
                "trace_id": trace_id,
                "name": "s",
                "span_type": "llm",
                "start_time": 1_750_000_000.0,
                "end_time": 1_750_000_001.0,
                "status": "ok",
            }
        ]
    }


@pytest.mark.anyio
async def test_a_retried_batch_does_not_fail_or_duplicate(client, db_session):
    span_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
    first = await client.post("/v1/traces/batch", json=_batch(span_id, trace_id))
    assert first.status_code == 200
    again = await client.post("/v1/traces/batch", json=_batch(span_id, trace_id))
    assert again.status_code == 200  # the lost-answer retry used to be a 500
    count = (await db_session.execute(select(func.count()).select_from(Span))).scalar()
    assert count == 1


@pytest.mark.anyio
async def test_a_batch_with_old_and_new_spans_stores_only_the_new(client, db_session):
    trace_id, old, new = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    await client.post("/v1/traces/batch", json=_batch(old, trace_id))
    both = {"spans": _batch(old, trace_id)["spans"] + _batch(new, trace_id)["spans"]}
    assert (await client.post("/v1/traces/batch", json=both)).status_code == 200
    ids = set((await db_session.execute(select(Span.id))).scalars())
    assert ids == {old, new}


async def _trace(db, project, minutes_ago):
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    db.add(
        Trace(
            name="t",
            start_time=created,
            status="ok",
            project_id=project,
            environment="e",
            created_at=created,
        )
    )
    await db.commit()


@pytest.mark.anyio
async def test_ingest_health_reports_last_trace_and_counts(client, db_session):
    await _trace(db_session, "alive", 2)
    await _trace(db_session, "alive", 30)
    await _trace(db_session, "quiet", 600)
    data = (await client.get("/v1/ingest/health")).json()
    rows = {p["project_id"]: p for p in data["projects"]}
    assert rows["alive"]["traces_5m"] == 1 and rows["alive"]["traces_1h"] == 2
    assert rows["alive"]["silence_minutes"] < 5
    assert rows["quiet"]["traces_24h"] == 1 and rows["quiet"]["silence_minutes"] > 500
    assert data["projects"][0]["project_id"] == "alive"  # most recently heard first


@pytest.mark.anyio
async def test_silence_alert_fires_then_resolves_when_traces_return(db_session):
    await _trace(db_session, "svc", 90)
    rule = AlertRule(
        name="svc silent",
        metric="ingest_silence_minutes",
        comparator=">",
        threshold=30.0,
        window_minutes=60,
        min_samples=10,  # min_samples is ignored
        project_id="svc",
        severity="warning",
        cooldown_minutes=0,
    )
    db_session.add(rule)
    await db_session.commit()
    result = await evaluate_rules(db_session)
    assert result.opened == 1
    event = (await db_session.execute(select(AlertEvent))).scalars().one()
    assert event.state == "open" and event.value > 80
    await _trace(db_session, "svc", 0)  # the sender is back
    again = await evaluate_rules(db_session)
    assert again.resolved == 1
    await db_session.refresh(event)
    assert event.state == "resolved"


@pytest.mark.anyio
async def test_a_project_that_never_sent_anything_is_not_silent(db_session):
    db_session.add(
        AlertRule(
            name="ghost",
            metric="ingest_silence_minutes",
            comparator=">",
            threshold=1.0,
            window_minutes=60,
            min_samples=1,
            project_id="never",
            severity="info",
            cooldown_minutes=0,
        )
    )
    await db_session.commit()
    assert (await evaluate_rules(db_session)).opened == 0
