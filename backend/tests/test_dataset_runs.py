from datetime import datetime, timedelta, timezone

import pytest

from app.case_check import check_case, compare_results
from app.models import Span, Trace


def test_check_case_health_checks_and_unknowns():
    expect = {
        "tools": {"required": ["web_search"], "forbidden": []},
        "health": {"no_error": True, "no_degradation": True, "max_duration_ms": 3000},
        "text": {"must_contain": ["fuente"], "must_not_contain": []},
    }
    ok = {"status": "ok", "degraded": False, "duration_ms": 1200.0, "tools": []}
    res = check_case(expect, ok)
    by = {c["name"]: c["status"] for c in res["checks"]}
    assert by["no_error"] == "pass" and by["max_duration_ms"] == "pass"
    # No tool spans and no reply text: unknown, never a silent pass.
    assert by["required:web_search"] == "unknown" and by["must_contain:fuente"] == "unknown"
    assert res["status"] == "pass" and res["unverified"] == 2

    bad = {**ok, "degraded": True, "duration_ms": 9000.0, "tools": ["calc"]}
    res = check_case(expect, bad)
    by = {c["name"]: c["status"] for c in res["checks"]}
    assert by["no_degradation"] == "fail" and by["max_duration_ms"] == "fail"
    assert by["required:web_search"] == "fail" and res["status"] == "fail"


def test_check_case_with_only_unverifiable_expectations_is_unknown():
    res = check_case({"text": {"must_contain": ["x"]}}, {"status": "ok"})
    assert res["status"] == "unknown"
    assert check_case({}, {"status": "ok"})["status"] == "unknown"


def test_forbidden_tool_present_fails_and_absent_passes_when_tools_are_known():
    expect = {"tools": {"forbidden": ["shell_run", "rm"]}}
    res = check_case(expect, {"tools": ["shell_run", "calc"]})
    by = {c["name"]: c["status"] for c in res["checks"]}
    assert by == {"forbidden:shell_run": "fail", "forbidden:rm": "pass"}


def test_compare_results_classifies_changes():
    def r(status, health=None, ms=None):
        return {"status": status, "health_score": health, "duration_ms": ms}

    base = {
        "a": r("pass", 1.0, 1000),
        "b": r("fail", 0.4),
        "c": r("pass"),
        "d": r("unknown"),
        "e": r("pass"),
    }
    cand = {
        "a": r("fail", 0.7, 4000),
        "b": r("pass", 0.9),
        "c": r("pass"),
        "d": r("pass"),
        "f": r("pass"),
    }
    out = compare_results(base, cand)
    change = {c["item_id"]: c["change"] for c in out["cases"]}
    assert change == {
        "a": "regressed",
        "b": "improved",
        "c": "same",
        "d": "unknown",
        "e": "missing",
        "f": "missing",
    }
    assert out["summary"] == {"regressed": 1, "improved": 1, "same": 1, "unknown": 1, "missing": 2}
    a = next(c for c in out["cases"] if c["item_id"] == "a")
    assert a["health_delta"] == -0.3 and a["duration_delta_ms"] == 3000


async def _trace(db, *, degraded=False, status="ok", duration_ms=1000):
    created = datetime.now(timezone.utc) - timedelta(minutes=5)
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
            attributes={"duration_ms": duration_ms, "llm_degraded": degraded},
        )
    )
    await db.commit()
    return trace


async def _dataset_with_case(client):
    ds = (await client.post("/v1/datasets", json={"name": "d"})).json()["id"]
    item = (
        await client.post(
            f"/v1/datasets/{ds}/items",
            json={"expect": {"health": {"no_degradation": True, "no_error": True}}},
        )
    ).json()
    return ds, item["id"]


@pytest.mark.anyio
async def test_runs_are_checked_stored_and_compared(client, db_session):
    ds, item = await _dataset_with_case(client)
    good = await _trace(db_session)
    bad = await _trace(db_session, degraded=True)
    v1 = {"name": "v1", "version": {"commit": "abc123", "model": "gemma", "config": {"t": 0.2}}}
    r1 = (
        await client.post(
            f"/v1/datasets/{ds}/runs",
            json={**v1, "results": [{"item_id": item, "trace_id": good.id}]},
        )
    ).json()
    r2 = (
        await client.post(
            f"/v1/datasets/{ds}/runs",
            json={
                "name": "v2",
                "version": {"commit": "def456"},
                "results": [{"item_id": item, "trace_id": bad.id}],
            },
        )
    ).json()
    assert r1["version"]["rubric"] == "manitos_turn@2" and r1["version"]["commit"] == "abc123"
    assert r1["results"][0]["status"] == "pass" and r2["results"][0]["status"] == "fail"
    assert r1["summary"]["pass_rate"] == 1.0 and r2["summary"]["pass_rate"] == 0.0

    listed = (await client.get(f"/v1/datasets/{ds}/runs")).json()
    assert {r["name"] for r in listed} == {"v1", "v2"}
    diff = (
        await client.get(f"/v1/datasets/{ds}/compare?base={r1['id']}&candidate={r2['id']}")
    ).json()
    assert diff["summary"]["regressed"] == 1 and diff["cases"][0]["change"] == "regressed"
    assert diff["base"]["version"]["commit"] == "abc123"
    assert (await client.get(f"/v1/datasets/runs/{r1['id']}")).json()["results"][0]["trace_id"]


@pytest.mark.anyio
async def test_run_validation(client, db_session):
    ds, item = await _dataset_with_case(client)
    trace = await _trace(db_session)
    url = f"/v1/datasets/{ds}/runs"
    post = client.post
    assert (
        await post(url, json={"results": [{"item_id": "nope", "trace_id": trace.id}]})
    ).status_code == 422
    assert (
        await post(url, json={"results": [{"item_id": item, "trace_id": "missing"}]})
    ).status_code == 404
    dup = {"results": [{"item_id": item, "trace_id": trace.id}] * 2}
    assert (await post(url, json=dup)).status_code == 422
    assert (await post(url, json={"results": []})).status_code == 422
    long_cfg = {
        "version": {"config": {"k": "x" * 300}},
        "results": [{"item_id": item, "trace_id": trace.id}],
    }
    assert (await post(url, json=long_cfg)).status_code == 422
    other = (await client.post("/v1/datasets", json={"name": "other"})).json()["id"]
    run = (await post(url, json={"results": [{"item_id": item, "trace_id": trace.id}]})).json()
    assert (
        await client.get(f"/v1/datasets/{other}/compare?base={run['id']}&candidate={run['id']}")
    ).status_code == 404


@pytest.mark.anyio
async def test_deleting_a_dataset_removes_its_runs(client, db_session):
    ds, item = await _dataset_with_case(client)
    trace = await _trace(db_session)
    run = (
        await client.post(
            f"/v1/datasets/{ds}/runs", json={"results": [{"item_id": item, "trace_id": trace.id}]}
        )
    ).json()
    assert (await client.delete(f"/v1/datasets/{ds}")).status_code == 200
    assert (await client.get(f"/v1/datasets/runs/{run['id']}")).status_code == 404
