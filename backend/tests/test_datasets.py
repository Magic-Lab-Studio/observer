from datetime import datetime, timedelta, timezone

import pytest

from app.models import Span, Trace


async def _trace(db, *, tool="web_search", inp="secret user question"):
    created = datetime.now(timezone.utc) - timedelta(minutes=5)
    trace = Trace(
        name="manitos.turn",
        start_time=created,
        end_time=created + timedelta(seconds=2),
        status="ok",
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
            status="ok",
            inp={"text": inp},
            out={"text": "secret answer"},
            attributes={"duration_ms": 2000, "llm_degraded": True},
        )
    )
    db.add(Span(trace_id=trace.id, name=tool, span_type="tool", start_time=created, status="ok"))
    db.add(
        Span(
            trace_id=trace.id,
            name="model_routing",
            span_type="event",
            start_time=created,
            status="ok",
            attributes={"intent": "search"},
        )
    )
    await db.commit()
    return trace


async def _dataset(client, name="regressions"):
    return (await client.post("/v1/datasets", json={"name": name})).json()["id"]


@pytest.mark.anyio
async def test_case_from_a_trace_keeps_metadata_only(client, db_session):
    trace = await _trace(db_session)
    ds = await _dataset(client)
    body = {
        "trace_id": trace.id,
        "tags": ["search", " search ", ""],
        "expect": {
            "tools": {"required": ["web_search"], "forbidden": ["shell_run", "shell_run"]},
            "health": {"no_degradation": True, "max_duration_ms": 5000},
            "text": {"must_contain": ["fuente"]},
        },
        "label": "incorrect",
        "notes": "answered without citing",
    }
    item = (await client.post(f"/v1/datasets/{ds}/items", json=body)).json()
    assert item["intent"] == "search" and item["tags"] == ["search"]
    assert item["expect"]["tools"]["forbidden"] == ["shell_run"]
    assert item["checks"] == ["tools", "health", "text"] and item["label"] == "incorrect"
    assert item["observed"]["tools"] == ["web_search"] and item["observed"]["degraded"] is True
    # Nothing of the trace's input or output reaches the case.
    stored = (await client.get(f"/v1/datasets/{ds}")).json()
    assert "secret" not in str(stored)
    listed = (await client.get("/v1/datasets")).json()
    assert listed[0]["items"] == 1


@pytest.mark.anyio
async def test_validation_unknown_trace_and_unknown_fields(client, db_session):
    ds = await _dataset(client)
    assert (
        await client.post(f"/v1/datasets/{ds}/items", json={"trace_id": "nope"})
    ).status_code == 404
    bad = {"expect": {"tools": {"required": ["x"]}, "response_body": "copied"}}
    assert (await client.post(f"/v1/datasets/{ds}/items", json=bad)).status_code == 422
    assert (
        await client.post(f"/v1/datasets/{ds}/items", json={"label": "maybe"})
    ).status_code == 422
    assert (await client.post("/v1/datasets/missing/items", json={})).status_code == 404


@pytest.mark.anyio
async def test_update_export_and_delete_cascade(client, db_session):
    trace = await _trace(db_session)
    ds = await _dataset(client)
    item = (await client.post(f"/v1/datasets/{ds}/items", json={"trace_id": trace.id})).json()
    assert item["checks"] == []
    patched = (
        await client.patch(
            f"/v1/datasets/items/{item['id']}",
            json={"expect": {"health": {"no_error": True}}, "label": "correct"},
        )
    ).json()
    assert patched["checks"] == ["health"] and patched["label"] == "correct"
    exported = (await client.get(f"/v1/datasets/{ds}/export")).json()
    assert exported["schema"] == "observer.dataset.v1" and len(exported["items"]) == 1
    assert (await client.delete(f"/v1/datasets/items/{item['id']}")).status_code == 200
    assert (await client.get(f"/v1/datasets/{ds}")).json()["items"] == []
    assert (await client.delete(f"/v1/datasets/{ds}")).status_code == 200
    assert (await client.get(f"/v1/datasets/{ds}")).status_code == 404


@pytest.mark.anyio
async def test_cases_outlive_their_trace(client, db_session):
    trace = await _trace(db_session)
    ds = await _dataset(client)
    await client.post(f"/v1/datasets/{ds}/items", json={"trace_id": trace.id})
    await db_session.delete(trace)
    await db_session.commit()
    items = (await client.get(f"/v1/datasets/{ds}")).json()["items"]
    assert len(items) == 1 and items[0]["source_trace_id"] == trace.id
