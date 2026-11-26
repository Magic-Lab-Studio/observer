"""Cost per merged change (roadmap step 13b): a hand count on synthetic data."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Span, Trace


async def _turn(db, cost, *, meta=None, span_attrs=None):
    created = datetime.now(timezone.utc) - timedelta(hours=1)
    trace = Trace(
        name="agent.turn",
        start_time=created,
        end_time=created + timedelta(seconds=2),
        status="ok",
        project_id="shop",
        environment="test",
        created_at=created,
        meta=meta,
    )
    db.add(trace)
    await db.flush()
    db.add(
        Span(
            trace_id=trace.id,
            name="llm",
            span_type="llm",
            start_time=created,
            end_time=created + timedelta(seconds=1),
            status="ok",
            cost_usd=cost,
            attributes={"gen_ai.request.model": "gpt-x", **(span_attrs or {})},
        )
    )
    await db.commit()


@pytest.mark.anyio
async def test_merged_abandoned_open_and_unattributed_spend_add_up_by_hand(client, db_session):
    # Change 10 merged after 2 turns (0.30 + 0.20), change 11 abandoned (0.40), change 12 merged
    # with its work found through the merge commit (0.10), change 13 still open (0.25), and one turn
    # that names no change (0.05). Change 14 merged with no traced work.
    await _turn(db_session, 0.30, meta={"vcs.change": "10"})
    await _turn(db_session, 0.20, span_attrs={"vcs.change": "10"})
    await _turn(db_session, 0.40, meta={"vcs.change": "11"})
    await _turn(db_session, 0.10, meta={"vcs.commit": "abc123"})
    await _turn(db_session, 0.25, meta={"vcs.change": "13"})
    await _turn(db_session, 0.05)
    for body in (
        {"project_id": "shop", "change": "10", "event": "merged"},
        {"project_id": "shop", "change": "11", "event": "closed"},
        {"project_id": "shop", "change": "12", "event": "merged", "commit": "abc123"},
        {"project_id": "shop", "change": "14", "event": "merged"},
    ):
        assert (await client.post("/v1/vcs/events", json=body)).status_code == 200

    data = (await client.get("/v1/costs/changes", params={"project_id": "shop"})).json()

    assert data["merged"] == {"changes": 3, "cost_usd": 0.6}
    assert data["abandoned"] == {"changes": 1, "cost_usd": 0.4}
    assert data["open"] == {"changes": 1, "cost_usd": 0.25}
    assert data["unattributed_usd"] == 0.05
    assert data["total_usd"] == pytest.approx(1.30)  # nothing dropped
    assert data["cost_per_merged_change"] == pytest.approx((0.6 + 0.4) / 3)
    statuses = {c["change"]: c["status"] for c in data["changes"]}
    assert statuses == {"10": "merged", "11": "abandoned", "12": "merged", "13": "open"}


@pytest.mark.anyio
async def test_a_hook_reporting_twice_records_once(client):
    body = {"project_id": "shop", "change": "7", "event": "merged", "commit": "f00"}
    first = (await client.post("/v1/vcs/events", json=body)).json()
    again = (await client.post("/v1/vcs/events", json=body)).json()
    assert first["duplicate"] is False and again == {"id": first["id"], "duplicate": True}


@pytest.mark.anyio
async def test_only_merged_or_closed_events(client):
    response = await client.post("/v1/vcs/events", json={"change": "7", "event": "opened"})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_no_merged_change_means_no_cost_per_change(client, db_session):
    await _turn(db_session, 0.5, meta={"vcs.change": "1"})
    data = (await client.get("/v1/costs/changes", params={"project_id": "shop"})).json()
    assert data["cost_per_merged_change"] is None and data["open"]["cost_usd"] == 0.5
