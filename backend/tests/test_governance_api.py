"""Governance views (/v1/governance): ManitOS plan 2026-11-02, task B2."""

import copy
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def governed_turn(*, session_id="session_gov", denied=False, answered=True, extra_attr=False):
    """The contract fixture moved to now, with fresh IDs, optionally varied."""
    data = json.loads(
        (Path(__file__).parent / "fixtures/manitos_v1_governance_spans.json").read_text()
    )
    data = copy.deepcopy(data)
    shift = time.time() - data["trace"]["start_time"] - 5
    data["idempotency_key"] = str(uuid.uuid4())
    data["session_id"] = session_id
    data["trace"]["id"] = str(uuid.uuid4())
    data["trace"]["start_time"] += shift
    data["trace"]["end_time"] += shift
    spans = []
    for span in data["spans"]:
        span["id"] = str(uuid.uuid4())
        span["start_time"] += shift
        span["end_time"] += shift
        if span["span_type"] == "security" and denied:
            span["attributes"]["security.outcome"] = "denied"
            span["attributes"]["security.reason"] = "local_data_egress"
        if span["span_type"] == "approval" and (denied or not answered):
            continue
        if span["span_type"] == "decision" and extra_attr:
            span["attributes"]["prompt"] = "secret user text"
        spans.append(span)
    data["spans"] = spans
    return data


async def ingest(client, data):
    response = await client.post("/v1/ingest/manitos/traces", json=data)
    assert response.status_code == 200, response.text
    return data["trace"]["id"]


@pytest.mark.anyio
async def test_turn_path_rebuilds_decision_permission_tool_result(client):
    trace_id = await ingest(client, governed_turn())
    body = (await client.get(f"/v1/governance/turns/{trace_id}")).json()
    assert [s["span_type"] for s in body["steps"]] == [
        "decision",
        "security",
        "approval",
        "tool",
        "skill",
    ]
    assert [s["outcome"] for s in body["steps"]] == ["tool", "asked", "granted", "ok", "ok"]
    assert body["complete"] is True
    assert body["unanswered_asks"] == []


@pytest.mark.anyio
async def test_turn_path_flags_a_permission_nobody_answered(client):
    trace_id = await ingest(client, governed_turn(answered=False))
    body = (await client.get(f"/v1/governance/turns/{trace_id}")).json()
    assert body["complete"] is False
    assert body["unanswered_asks"] == ["file_master"]


@pytest.mark.anyio
async def test_unknown_turn_is_404(client):
    response = await client.get(f"/v1/governance/turns/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_denials_list_only_denied_checks_by_default(client):
    await ingest(client, governed_turn())
    await ingest(client, governed_turn(denied=True))
    body = (await client.get("/v1/governance/denials")).json()
    assert body["total"] == 1
    assert body["items"][0]["attributes"]["security.reason"] == "local_data_egress"
    asked = (await client.get("/v1/governance/denials", params={"outcome": "asked"})).json()
    assert asked["total"] == 1
    by_cap = (await client.get("/v1/governance/denials", params={"capability": "write"})).json()
    assert by_cap["total"] == 1
    none = (await client.get("/v1/governance/denials", params={"capability": "finance"})).json()
    assert none["total"] == 0


@pytest.mark.anyio
async def test_decisions_filter_and_never_return_keys_outside_the_contract(client):
    await ingest(client, governed_turn(extra_attr=True))
    body = (await client.get("/v1/governance/decisions", params={"kind": "intent"})).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["attributes"]["decision.choice"] == "tool"
    assert "prompt" not in item["attributes"]
    assert "input" not in item and "output" not in item
    other = (await client.get("/v1/governance/decisions", params={"kind": "escalate"})).json()
    assert other["total"] == 0


@pytest.mark.anyio
async def test_approvals_skills_and_session_scope(client):
    await ingest(client, governed_turn(session_id="s1"))
    await ingest(client, governed_turn(session_id="s2"))
    granted = (await client.get("/v1/governance/approvals", params={"result": "granted"})).json()
    assert granted["total"] == 2
    scoped = (await client.get("/v1/governance/approvals", params={"session_id": "s1"})).json()
    assert scoped["total"] == 1 and scoped["items"][0]["session_id"] == "s1"
    skills = (await client.get("/v1/governance/skills", params={"source": "import"})).json()
    assert skills["total"] == 2
    assert skills["items"][0]["attributes"]["skill.sha256"] == "sha256:aa"


@pytest.mark.anyio
async def test_old_spans_and_other_projects_are_left_out(client):
    old = governed_turn()
    for span in old["spans"]:
        span["start_time"] -= 48 * 3600
        span["end_time"] -= 48 * 3600
    old["trace"]["start_time"] -= 48 * 3600
    old["trace"]["end_time"] -= 48 * 3600
    await ingest(client, old)
    other = governed_turn()
    other["project_id"] = "another"
    await ingest(client, other)
    assert (await client.get("/v1/governance/decisions")).json()["total"] == 0
    assert (await client.get("/v1/governance/decisions", params={"hours": 72})).json()["total"] == 1
    assert (await client.get("/v1/governance/decisions", params={"project_id": "another"})).json()[
        "total"
    ] == 1


@pytest.mark.anyio
async def test_summary_counts_each_type_by_outcome(client):
    await ingest(client, governed_turn())
    await ingest(client, governed_turn(denied=True))
    counts = (await client.get("/v1/governance/summary")).json()["counts"]
    assert counts["security"] == {"total": 2, "by": {"asked": 1, "denied": 1}}
    assert counts["approval"] == {"total": 1, "by": {"granted": 1}}
    assert counts["decision"]["by"] == {"model": 2}
    assert counts["skill"]["by"] == {"ok": 2}


@pytest.mark.anyio
async def test_bad_filter_values_are_rejected(client):
    assert (
        await client.get("/v1/governance/denials", params={"outcome": "maybe"})
    ).status_code == 422
    assert (await client.get("/v1/governance/approvals", params={"by": "robot"})).status_code == 422


def test_migration_adds_and_removes_the_span_type_index(tmp_path):
    db = tmp_path / "gov.sqlite"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}

    def alembic(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND,
            env=env,
            check=True,
            capture_output=True,
        )

    def indexes():
        with sqlite3.connect(db) as conn:
            return {row[1] for row in conn.execute("PRAGMA index_list('spans')")}

    alembic("upgrade", "head")
    assert "ix_spans_span_type_start_time" in indexes()
    alembic("downgrade", "-1")
    assert "ix_spans_span_type_start_time" not in indexes()
    alembic("upgrade", "head")
    assert "ix_spans_span_type_start_time" in indexes()
