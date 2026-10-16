"""Rule-based turn scoring, the idempotent sweep and the per-model summary."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.evaluations import summarize_scores
from app.auto_eval import evaluate_new_turns, evaluation_id
from app.evaluators.manitos_turn import (
    PASS_THRESHOLD,
    UNKNOWN_CORRECTNESS_CAP,
    grade_for,
    score_turn,
)
from app.metrics_core import TurnSignals
from app.models import Evaluation, Span, Trace


def _signals(**kw) -> TurnSignals:
    return TurnSignals(trace_id="t", **kw)


@pytest.mark.parametrize(
    ("kw", "grade", "passed"),
    [
        ({"duration_ms": 1500.0, "ttft_ms": 900.0}, "A", True),
        ({"duration_ms": 12000.0}, "A", True),
        ({"duration_ms": 45000.0}, "B", True),  # slow alone does not fail a healthy turn
        ({"truncated": True, "degraded": True, "duration_ms": 45000.0}, "D", False),
        ({"status": "error", "duration_ms": 1000.0}, "C", False),  # an error never passes
        ({"tool_error": True}, "C", False),
        ({"fallback": True, "duration_ms": 2000.0}, "A", True),
    ],
)
def test_score_turn_table(kw, grade, passed):
    """``grade`` is the operational-health grade; the overall one is capped (see below)."""
    scored = score_turn(_signals(**kw))
    assert scored["health"]["grade"] == grade and scored["passed"] is passed
    assert 0.0 <= scored["score"] <= 1.0
    assert (scored["score"] >= PASS_THRESHOLD) or not passed


def test_fast_healthy_turn_cannot_be_excellent_while_correctness_is_unknown():
    scored = score_turn(_signals(duration_ms=800.0, ttft_ms=300.0))
    assert scored["health"] == {"score": 1.0, "grade": "A"}
    assert scored["correctness"] == {"status": "unknown", "score": None}
    assert scored["score"] == UNKNOWN_CORRECTNESS_CAP and scored["grade"] == "B"
    assert scored["rubric"] == "manitos_turn@2" and scored["passed"] is True
    names = {c["name"] for c in scored["criteria"]}
    assert {"reliability", "degradation"} <= names and not {"health", "quality"} & names


def test_missing_latency_is_left_out_and_weights_renormalize():
    healthy = score_turn(_signals())
    assert healthy["health"]["score"] == 1.0
    names = {c["name"] for c in healthy["criteria"]}
    assert "latency" not in names and "ttft" not in names


def test_provider_comes_from_the_answering_label():
    scored = score_turn(_signals(answered_by="cloudflare · llama-3.3-70b"))
    assert scored["provider"] == "cloudflare" and scored["model"] == "cloudflare · llama-3.3-70b"
    assert score_turn(_signals(model="gemma"))["provider"] == ""


def test_grade_boundaries():
    scores = (1.0, 0.9, 0.89, 0.7, 0.69, 0.5, 0.49, 0.2, 0.19, 0.0)
    assert [grade_for(x) for x in scores] == list("AABBCCDDFF")
    assert grade_for(0.9) == "A" and grade_for(0.69) == "C" and grade_for(0.19) == "F"


async def _turn(db, *, ago_min=5, status="ok", lifecycle=True, duration_ms=2000, **attrs):
    created = datetime.now(timezone.utc) - timedelta(minutes=ago_min)
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
    if lifecycle:
        db.add(
            Span(
                trace_id=trace.id,
                name="manitos.turn.lifecycle",
                span_type="turn",
                start_time=created,
                status=status,
                attributes={"duration_ms": duration_ms, **attrs},
            )
        )
    db.add(
        Span(
            trace_id=trace.id,
            name="llm.chain",
            span_type="event",
            start_time=created,
            status="ok",
            attributes={"answered_by": "groq · qwen", "attempts_text": "groq:q=ok"},
        )
    )
    await db.flush()
    return trace


@pytest.mark.anyio
async def test_sweep_scores_each_turn_once_and_waits_for_the_lifecycle_span(db_session):
    a = await _turn(db_session)
    await _turn(db_session, status="error", llm_degraded=True)
    pending = await _turn(db_session, lifecycle=False)
    await db_session.commit()

    assert await evaluate_new_turns(db_session) == 2  # the one without lifecycle waits
    assert await evaluate_new_turns(db_session) == 0  # idempotent
    total = (await db_session.execute(select(func.count()).select_from(Evaluation))).scalar()
    assert total == 2
    stored = await db_session.get(Evaluation, evaluation_id(a.id))
    assert stored.evaluator_type == "rule_based" and stored.criteria["rubric"] == "manitos_turn@2"
    assert stored.result["provider"] == "groq" and stored.result["passed"] is True

    db_session.add(
        Span(
            trace_id=pending.id,
            name="manitos.turn.lifecycle",
            span_type="turn",
            start_time=pending.start_time,
            status="ok",
            attributes={"duration_ms": 900},
        )
    )
    await db_session.commit()
    assert await evaluate_new_turns(db_session) == 1  # now it has its own signals


def test_summarize_scores_groups_models_providers_and_grades():
    rows = [
        (0.95, {"model": "groq · q", "provider": "groq", "grade": "A", "passed": True}),
        (0.55, {"model": "groq · q", "provider": "groq", "grade": "C", "passed": False}),
        (0.80, {"model": "llamacpp · g", "provider": "llamacpp", "grade": "B", "passed": True}),
        (None, {}),
    ]
    out = summarize_scores(rows)
    assert out["count"] == 3 and out["pass_rate"] == pytest.approx(2 / 3)
    assert out["correctness_known"] == 0 and out["avg_health"] is None  # old rows: no health axis
    groq = next(m for m in out["models"] if m["key"] == "groq · q")
    assert groq["count"] == 2 and groq["avg_score"] == pytest.approx(0.75)
    assert {p["key"] for p in out["providers"]} == {"groq", "llamacpp"}
    assert {g["grade"]: g["count"] for g in out["grades"]} == {
        "A": 1,
        "B": 1,
        "C": 1,
        "D": 0,
        "F": 0,
    }


@pytest.mark.anyio
async def test_by_model_endpoint_and_route_order(client, db_session):
    await _turn(db_session)
    await db_session.commit()
    await evaluate_new_turns(db_session)
    data = (await client.get("/v1/evaluations/by-model?hours=24")).json()
    assert data["count"] == 1 and data["models"][0]["key"] == "groq · qwen"
    assert data["providers"][0]["key"] == "groq"
    # "by-model" must not be read as an evaluation id
    assert (await client.get("/v1/evaluations/by-model")).status_code == 200


@pytest.mark.anyio
async def test_sweep_reaches_turns_older_than_the_newest_page(db_session):
    # batch=1 pages through the newest 200 turns at a time: older turns must still be scored
    for i in range(205):
        await _turn(db_session, ago_min=1 + i * 0.01)
    await db_session.commit()
    done = 0
    for _ in range(210):
        got = await evaluate_new_turns(db_session, batch=1)
        done += got
        if not got:
            break
    assert done == 205
    total = (await db_session.execute(select(func.count()).select_from(Evaluation))).scalar()
    assert total == 205


@pytest.mark.anyio
async def test_unscorable_recent_turns_do_not_starve_older_eligible_ones(db_session):
    older = [await _turn(db_session, ago_min=600 + i) for i in range(3)]
    for i in range(6):  # newer than every eligible turn, but still without their lifecycle span
        await _turn(db_session, ago_min=1 + i, lifecycle=False)
    await db_session.commit()

    # A batch of 3 used to be filled by the 6 newest turns, which are skipped, scoring nothing.
    assert await evaluate_new_turns(db_session, batch=3) == 3
    for trace in older:
        assert await db_session.get(Evaluation, evaluation_id(trace.id)) is not None


@pytest.mark.anyio
async def test_sweep_replaces_scores_of_the_previous_rubric(db_session):
    turn = await _turn(db_session)
    old = Evaluation(
        id=evaluation_id(turn.id, "manitos_turn@1"),
        trace_id=turn.id,
        evaluator_type="rule_based",
        score=1.0,
        criteria={"rubric": "manitos_turn@1"},
        result={"grade": "A", "passed": True},
    )
    db_session.add(old)
    await db_session.commit()

    assert await evaluate_new_turns(db_session) == 1
    rows = (await db_session.execute(select(Evaluation.id))).scalars().all()
    assert rows == [evaluation_id(turn.id)]  # one score per turn, the new one
    assert await evaluate_new_turns(db_session) == 0


def test_summarize_scores_reports_health_apart_from_the_capped_overall():
    scored = score_turn(_signals(duration_ms=900.0))
    out = summarize_scores([(scored["score"], scored)])
    assert out["avg_score"] == pytest.approx(UNKNOWN_CORRECTNESS_CAP)
    assert out["avg_health"] == pytest.approx(1.0) and out["correctness_known"] == 0
    assert out["models"][0]["avg_health"] == pytest.approx(1.0)
