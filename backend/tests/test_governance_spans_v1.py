"""Governance span types ride manitos.telemetry.v1 unchanged (ManitOS plan 2026-11-02, phase zero).

The contract lives in ManitOS (manitos/contracts/telemetry.py,
docs/wiki/data/observer_event_contract.md):
decision, security, approval and skill spans use the open span_type and attributes fields, so the
envelope keeps its version. If this test fails, the ingest stopped accepting that contract.
"""

import json
from pathlib import Path

import pytest


def payload():
    fixture = Path(__file__).parent / "fixtures/manitos_v1_governance_spans.json"
    return json.loads(fixture.read_text())


@pytest.mark.anyio
async def test_governance_spans_are_accepted_by_the_v1_envelope(client):
    data = payload()
    response = await client.post("/v1/ingest/manitos/traces", json=data)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["accepted_spans"] == len(data["spans"])
    assert body["rejected_spans"] == 0


@pytest.mark.anyio
async def test_governance_span_types_and_attributes_are_kept(client):
    data = payload()
    assert (await client.post("/v1/ingest/manitos/traces", json=data)).status_code == 200
    spans = (await client.get(f"/v1/traces/{data['trace']['id']}/spans")).json()
    by_type = {span["span_type"]: span for span in spans}
    assert set(by_type) == {"decision", "security", "approval", "tool", "skill"}
    assert by_type["decision"]["attributes"]["decision.choice"] == "tool"
    assert by_type["security"]["attributes"]["security.capabilities"] == ["write"]
    assert by_type["approval"]["attributes"]["approval.result"] == "granted"
    assert by_type["tool"]["attributes"]["tool.call_id"] == "call_0001"
    assert by_type["skill"]["attributes"]["skill.scripts"] == 0
