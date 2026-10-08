"""Local operator metadata uses the published envelope without schema changes."""

import json
from pathlib import Path

import pytest


def payload():
    path = Path(__file__).parent / "fixtures/local_action_admission_v1.json"
    return json.loads(path.read_text())


@pytest.mark.anyio
async def test_local_action_preflight_is_not_presented_as_execution(client):
    data = payload()
    response = await client.post("/v1/ingest/manitos/traces", json=data)
    assert response.status_code == 200
    traces = (await client.get(f"/v1/traces/?session_id={data['session_id']}")).json()
    assert traces["total"] == 1
    trace = traces["traces"][0]
    assert trace["name"] == "manitos.local_action.admission"
    assert trace["metadata"]["outcome"] == "prepared"
    assert trace["metadata"]["request_id"] == data["trace"]["metadata"]["request_id"]
    assert trace["metadata"]["job_id"] == data["turn_id"]
    spans = (await client.get(f"/v1/traces/{data['trace']['id']}/spans")).json()
    assert len(spans) == 1
    assert spans[0]["input"] is None and spans[0]["output"] is None


@pytest.mark.anyio
async def test_local_action_retry_does_not_duplicate_trace(client):
    data = payload()
    assert (await client.post("/v1/ingest/manitos/traces", json=data)).status_code == 200
    repeated = await client.post("/v1/ingest/manitos/traces", json=data)
    assert repeated.json()["status"] == "duplicate"
    assert repeated.json()["duplicate_spans"] == 1


@pytest.mark.anyio
async def test_conflicting_event_cannot_rewrite_preflight_as_execution(client):
    data = payload()
    assert (await client.post("/v1/ingest/manitos/traces", json=data)).status_code == 200
    data["trace"]["metadata"]["outcome"] = "verified"
    response = await client.post("/v1/ingest/manitos/traces", json=data)
    assert response.status_code == 409
