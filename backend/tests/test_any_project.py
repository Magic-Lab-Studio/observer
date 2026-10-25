"""Explore, alerts and evaluations for a project that is not Manitos (an SDK-instrumented agent)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.alerts import evaluate_rules
from app.auto_eval import evaluate_new_turns, evaluation_id
from app.evaluators import generic_turn
from app.evaluators.manitos_turn import UNKNOWN_CORRECTNESS_CAP
from app.metrics_core import TurnSignals, load_signals_for
from app.models import AlertEvent, AlertRule, Evaluation, Span, Trace

PROJECT = "my-agent"


async def _agent_trace(
    db,
    *,
    ago_min: float = 5,
    duration_ms: float = 1200,
    status: str = "ok",
    model: str = "qwen2.5:0.5b",
    finish: str = "stop",
    request_only: bool = False,
    project: str = PROJECT,
):
    """A trace shaped like the Python SDK's: an agent root span and an OpenAI LLM span."""
    start = datetime.now(timezone.utc) - timedelta(minutes=ago_min)
    trace = Trace(
        name="agent",
        start_time=start,
        end_time=start + timedelta(milliseconds=duration_ms),
        status=status,
        project_id=project,
        environment="dev",
        created_at=start,
    )
    db.add(trace)
    await db.flush()
    db.add(
        Span(
            trace_id=trace.id,
            name="agent",
            span_type="agent",
            start_time=start,
            status=status,
            attributes={"service.name": project},
        )
    )
    attrs = {"gen_ai.system": "openai", "gen_ai.request.model": model}
    if not request_only:
        attrs |= {"gen_ai.response.model": model, "gen_ai.response.finish_reasons": [finish]}
    db.add(
        Span(
            trace_id=trace.id,
            name="openai.chat.completions.create",
            span_type="llm",
            start_time=start + timedelta(milliseconds=10),
            status=status,
            attributes=attrs,
        )
    )
    await db.flush()
    return trace


@pytest.mark.anyio
async def test_model_and_provider_come_from_genai_attributes(db_session):
    a = await _agent_trace(db_session, model="qwen2.5:1.5b")
    b = await _agent_trace(db_session, model="claude-haiku", request_only=True)
    c = await _agent_trace(db_session, finish="length")
    await db_session.commit()
    by_id = {s.trace_id: s for s in await load_signals_for(db_session, [a.id, b.id, c.id])}
    assert (by_id[a.id].model, by_id[a.id].provider) == ("qwen2.5:1.5b", "openai")
    assert by_id[b.id].model == "claude-haiku"  # the request model when no response model
    assert by_id[c.id].truncated and not by_id[a.id].truncated


@pytest.mark.anyio
async def test_manitos_model_keeps_priority_over_genai_spans(db_session):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    trace = Trace(
        name="manitos.turn", start_time=start, status="ok", project_id="manitos", created_at=start
    )
    db_session.add(trace)
    await db_session.flush()
    for name, attrs in (
        ("openai.chat.completions.create", {"gen_ai.response.model": "raw-model"}),
        ("manitos.turn.lifecycle", {"model": "gemma", "llm_truncated": False}),
    ):
        db_session.add(
            Span(trace_id=trace.id, name=name, start_time=start, status="ok", attributes=attrs)
        )
    db_session.add(
        Span(
            trace_id=trace.id,
            name="x",
            start_time=start,
            status="ok",
            attributes={"gen_ai.response.finish_reasons": ["length"]},
        )
    )
    await db_session.commit()
    (signals,) = await load_signals_for(db_session, [trace.id])
    # The lifecycle span decides the model and truncation of a Manitos turn, as before.
    assert signals.model == "gemma" and signals.provider == "" and signals.truncated is False


@pytest.mark.parametrize(
    ("kw", "grade", "passed", "reliability"),
    [
        ({"duration_ms": 1500.0}, "A", True, 1.0),
        ({"status": "error", "duration_ms": 1500.0}, "C", False, 0.0),
        ({"truncated": True, "duration_ms": 1500.0}, "B", True, 1.0),
        ({"span_error": True, "duration_ms": 1500.0}, "B", True, 0.5),
        ({"duration_ms": 45000.0}, "B", True, 1.0),
    ],
)
def test_generic_rubric_scores(kw, grade, passed, reliability):
    scored = generic_turn.score_trace(TurnSignals(trace_id="t", model="m", provider="openai", **kw))
    assert scored["rubric"] == "generic_turn@1"
    assert scored["health"]["grade"] == grade and scored["passed"] is passed
    assert scored["correctness"] == {"status": "unknown", "score": None}
    assert scored["score"] <= UNKNOWN_CORRECTNESS_CAP
    criteria = {c["name"]: c for c in scored["criteria"]}
    assert set(criteria) == {"reliability", "complete", "latency"}
    assert criteria["reliability"]["score"] == reliability
    assert criteria["complete"]["score"] == (0.0 if kw.get("truncated") else 1.0)
    assert (scored["model"], scored["provider"]) == ("m", "openai")


def test_generic_rubric_without_duration_reweights():
    scored = generic_turn.score_trace(TurnSignals(trace_id="t"))
    assert scored["health"]["score"] == 1.0
    assert {c["name"] for c in scored["criteria"]} == {"reliability", "complete"}


@pytest.mark.anyio
async def test_sweep_scores_every_project_with_the_right_rubric(db_session):
    ok = await _agent_trace(db_session)
    failed = await _agent_trace(db_session, status="error")
    cut = await _agent_trace(db_session, finish="length", model="qwen2.5:1.5b")
    fresh = await _agent_trace(db_session, ago_min=0)  # just ended: waits to settle
    other = await _agent_trace(db_session, project="another-agent")
    await db_session.commit()

    assert await evaluate_new_turns(db_session) == 4
    assert await evaluate_new_turns(db_session) == 0
    rows = {
        e.trace_id: e for e in (await db_session.execute(select(Evaluation))).scalars().all()
    }
    assert set(rows) == {ok.id, failed.id, cut.id, other.id} and fresh.id not in rows
    assert rows[ok.id].id == evaluation_id(ok.id, generic_turn.RUBRIC)
    assert rows[ok.id].criteria["rubric"] == "generic_turn@1"
    assert rows[ok.id].result["passed"] is True and rows[failed.id].result["passed"] is False
    assert rows[cut.id].result["model"] == "qwen2.5:1.5b"
    assert {c["name"]: c["score"] for c in rows[cut.id].result["criteria"]}["complete"] == 0.0

    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert await evaluate_new_turns(db_session, now=later) == 1  # the fresh one, once settled


@pytest.mark.anyio
async def test_query_groups_a_non_manitos_project_by_model(client, db_session):
    for i in range(3):
        await _agent_trace(db_session, ago_min=5 + i, model="qwen2.5:0.5b")
    for i in range(2):
        await _agent_trace(db_session, ago_min=10 + i, model="qwen2.5:1.5b", duration_ms=3000)
    await db_session.commit()
    body = {
        "view": "turns",
        "project_id": PROJECT,
        "hours": 6,
        "metrics": [{"measure": "count", "aggregation": "count"}],
        "dimensions": ["model"],
    }
    r = (await client.post("/v1/analytics/query", json=body)).json()
    assert r["samples"] == 5
    assert {x["model"]: x["count"] for x in r["rows"]} == {"qwen2.5:0.5b": 3.0, "qwen2.5:1.5b": 2.0}
    body["dimensions"] = ["provider"]
    r = (await client.post("/v1/analytics/query", json=body)).json()
    assert r["rows"] == [{"provider": "openai", "count": 5.0}]
    # The default project is still Manitos, which has nothing here.
    del body["project_id"]
    assert (await client.post("/v1/analytics/query", json=body)).json()["samples"] == 0


@pytest.mark.anyio
async def test_projects_endpoint_lists_newest_first(client, db_session):
    await _agent_trace(db_session, ago_min=30, project="manitos")
    await _agent_trace(db_session, ago_min=2)
    await _agent_trace(db_session, ago_min=3)
    await db_session.commit()
    projects = (await client.get("/v1/analytics/projects")).json()
    assert [(p["project_id"], p["trace_count"]) for p in projects] == [
        (PROJECT, 2),
        ("manitos", 1),
    ]
    assert projects[0]["last_trace_at"] > projects[1]["last_trace_at"]


@pytest.mark.anyio
async def test_alert_rule_on_another_project_fires_on_error_rate(client, db_session):
    for i in range(6):
        await _agent_trace(db_session, ago_min=1 + i, status="error" if i < 3 else "ok")
    await _agent_trace(db_session, ago_min=2, status="ok", project="manitos")
    await db_session.commit()
    created = await client.post(
        "/v1/alerts/rules",
        json={
            "name": "Errores de my-agent",
            "metric": "error_rate",
            "threshold": 0.25,
            "min_samples": 5,
            "project_id": PROJECT,
        },
    )
    assert created.status_code == 200 and created.json()["project_id"] == PROJECT
    db_session.add(
        AlertRule(name="manitos", metric="error_rate", threshold=0.25, min_samples=1)
    )
    await db_session.commit()

    result = await evaluate_rules(db_session)
    assert (result.evaluated, result.opened) == (2, 1)
    event = (await db_session.execute(select(AlertEvent))).scalars().one()
    assert event.rule_name == "Errores de my-agent" and event.value == 0.5

    rules = (await client.get("/v1/alerts/rules", params={"project_id": PROJECT})).json()
    assert [r["name"] for r in rules] == ["Errores de my-agent"]
    events = (await client.get("/v1/alerts/events", params={"project_id": PROJECT})).json()
    assert events["open_count"] == 1 and len(events["events"]) == 1
    none = (await client.get("/v1/alerts/events", params={"project_id": "manitos"})).json()
    assert none == {"events": [], "open_count": 0}


@pytest.mark.anyio
async def test_evaluations_list_filters_by_project(client, db_session):
    await _agent_trace(db_session)
    await _agent_trace(db_session, project="manitos")
    await db_session.commit()
    assert await evaluate_new_turns(db_session) == 2
    listed = (await client.get("/v1/evaluations/", params={"project_id": PROJECT})).json()
    assert listed["total"] == 1
    by_model = (
        await client.get("/v1/evaluations/by-model", params={"project_id": PROJECT})
    ).json()
    assert by_model["count"] == 1 and by_model["models"][0]["key"] == "qwen2.5:0.5b"
    assert (await client.get("/v1/evaluations/")).json()["total"] == 2
