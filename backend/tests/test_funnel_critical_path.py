"""Step 14: the gate funnel (an over-strict gate first, with its reason code) and the critical path
(sequential model calls with no declared dependency are parallelisable; a declared one is not)."""

import time
import uuid

import pytest

from app.critical_path import critical_path


def _span(tid, name, span_type, start, end, *, parent=None, status="ok", attrs=None, sid=None):
    return {"id": sid or str(uuid.uuid4()), "trace_id": tid, "parent_id": parent, "name": name,
            "span_type": span_type, "start_time": start, "end_time": end, "status": status,
            "attributes": {"service.name": "bed", **(attrs or {})}}


def _gate(tid, root, name, start, status, codes=()):
    return _span(tid, name, "gate", start, start + 0.05, parent=root,
                 attrs={"gate.name": name, "gate.stage": name, "gate.status": status,
                        "gate.reason_codes": list(codes)})


async def _turns(client, n_stopped=7, total=12):
    """Twelve turns through retrieval -> coverage -> verification; verification stops seven."""
    spans = []
    t0 = time.time() - 600
    for i in range(total):
        tid, root = str(uuid.uuid4()), str(uuid.uuid4())
        start = t0 + i * 5
        stop = i < n_stopped
        spans += [
            _span(tid, "turn", "agent", start, start + 2, sid=root,
                  attrs={"observer.outcome": "abstained" if stop else "answered"}),
            _gate(tid, root, "retrieval", start + 0.1, "pass"),
            _gate(tid, root, "coverage", start + 0.3, "pass"),
            _gate(tid, root, "verification", start + 0.6,
                  "stop" if stop else "pass", ["claim_unsupported"] if stop else ()),
        ]
    r = await client.post("/v1/traces/batch", json={"spans": spans})
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_over_strict_gate_tops_with_reason(client):
    await _turns(client)
    data = (await client.get("/v1/agent/funnel?project_id=bed&hours=1")).json()
    assert data["turns_with_gates"] == 12
    first = data["gates"][0]
    assert first["gate"] == "verification" and first["stopped"] == 7 and first["turns"] == 12
    assert first["reason_codes"] == [{"code": "claim_unsupported", "count": 7}]
    assert first["position"] == 2  # third gate of the turn
    assert {g["gate"]: g["stopped"] for g in data["gates"]} == {
        "verification": 7, "retrieval": 0, "coverage": 0}


class S:
    def __init__(self, sid, start, end, *, parent="root", depends=None, span_type="llm"):
        self.id, self.name, self.span_type, self.parent_span_id = sid, sid, span_type, parent
        self.start_time, self.end_time = start, end
        self.attributes = {"observer.depends_on": depends} if depends else {}


def test_sequential_siblings_flagged():
    # three independent calls one after another: 0.7 s of a 1.0 s trace
    spans = [S("root", 0.0, 1.0, parent=None, span_type="agent"),
             S("a", 0.05, 0.30), S("b", 0.30, 0.55), S("c", 0.55, 0.75)]
    cp = critical_path(spans)
    assert cp["llm_calls"] == 3 and cp["sequential_calls"] == 2
    run = cp["parallelisable"][0]
    assert run["names"] == ["a", "b", "c"] and run["latency_share"] == pytest.approx(0.7)


def test_declared_dependency_not_flagged():
    spans = [S("root", 0.0, 1.0, parent=None, span_type="agent"),
             S("plan", 0.0, 0.4), S("write", 0.4, 0.9, depends="plan")]
    cp = critical_path(spans)
    assert cp["sequential_calls"] == 1 and cp["parallelisable"] == []


def test_overlapping_calls_are_already_parallel():
    spans = [S("a", 0.0, 0.5), S("b", 0.1, 0.6)]
    assert critical_path(spans)["parallelisable"] == []


@pytest.mark.anyio
async def test_trace_diagnostics_carry_the_critical_path(client):
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    t0 = time.time() - 60
    spans = [_span(tid, "turn", "agent", t0, t0 + 1.0, sid=root),
             _span(tid, "summarise", "llm", t0 + 0.05, t0 + 0.3, parent=root),
             _span(tid, "translate", "llm", t0 + 0.3, t0 + 0.55, parent=root),
             _span(tid, "tag", "llm", t0 + 0.55, t0 + 0.75, parent=root)]
    assert (await client.post("/v1/traces/batch", json={"spans": spans})).status_code == 200
    stored = (await client.get(f"/v1/traces/{tid}")).json()["id"]
    cp = (await client.get(f"/v1/traces/{stored}/diagnostics")).json()["critical_path"]
    assert cp["parallelisable"][0]["calls"] == 3
    assert cp["parallelisable"][0]["latency_share"] == pytest.approx(0.7, abs=0.01)


async def _timed_turns(client, project):
    """Four turns: retrieval 100/300/120/500 ms, llm.answer 400/400/900/400 ms, turn 1/1/1/1.6 s."""
    spans = []
    t0 = time.time() - 600
    for i, (ret, llm, turn) in enumerate([(0.1, 0.4, 1.0), (0.3, 0.4, 1.0), (0.12, 0.9, 1.0),
                                          (0.5, 0.4, 1.6)]):
        tid, root = str(uuid.uuid4()), str(uuid.uuid4())
        start = t0 + i * 5
        spans += [
            _span(tid, "turn", "agent", start, start + turn, sid=root,
                  attrs={"service.name": project}),
            _span(tid, "retrieval", "retrieval", start, start + ret, parent=root,
                  attrs={"service.name": project}),
            _span(tid, "llm.answer", "llm", start + 0.5, start + 0.5 + llm, parent=root,
                  attrs={"service.name": project}),
        ]
    assert (await client.post("/v1/traces/batch", json={"spans": spans})).status_code == 200


@pytest.mark.anyio
async def test_time_budget_per_stage_counts_the_turns_over(client):
    await _timed_turns(client, "budgeted")
    cfg = {"stages": {"retrieval": 250, "llm.*": 800}, "turn_ms": 1500}
    put = await client.put("/v1/projects/budgeted/config/budgets", json=cfg)
    assert put.status_code == 200, put.text
    data = (await client.get("/v1/agent/budgets?project_id=budgeted&hours=1")).json()
    stages = {s["stage"]: s for s in data["stages"]}
    assert stages["retrieval"]["over_budget"] == 2 and stages["retrieval"]["turns"] == 4
    assert stages["llm.*"]["over_budget"] == 1
    # turn 2 (retrieval), turn 3 (llm), turn 4 (retrieval and the whole turn): three of four
    assert (data["turns_measured"], data["over_budget_turns"]) == (4, 3)
    assert data["over_budget_rate"] == 0.75 and data["configured"] is True
    assert (await client.put("/v1/projects/budgeted/config/budgets",
                             json={"stages": {"x": -1}})).status_code == 422


@pytest.mark.anyio
async def test_no_budgets_means_nothing_is_over(client):
    await _timed_turns(client, "unbudgeted")
    data = (await client.get("/v1/agent/budgets?project_id=unbudgeted&hours=1")).json()
    assert data["configured"] is False and data["over_budget_rate"] is None


@pytest.mark.anyio
async def test_over_budget_rate_alert_opens(client, db_session):
    from app.alerts import evaluate_rules
    from app.models.alerts import AlertRule

    await _timed_turns(client, "alerted")
    await client.put("/v1/projects/alerted/config/budgets", json={"stages": {"retrieval": 250}})
    db_session.add(AlertRule(name="lento", metric="over_budget_rate", comparator=">=",
                             threshold=0.5, window_minutes=60, min_samples=4, project_id="alerted"))
    await db_session.commit()
    assert (await evaluate_rules(db_session)).opened == 1  # 2 of 4 over
