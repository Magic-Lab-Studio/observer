"""Evaluation API endpoints."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.evaluators import EvaluationCriteria, EvaluatorType, create_evaluator
from app.models.evaluation import Evaluation
from app.models.trace import Span, Trace
from app.websocket import manager

router = APIRouter()


class EvaluationListResponse(BaseModel):
    """Paginated evaluation list response."""

    evaluations: list[EvaluationResponse]
    total: int
    limit: int
    offset: int


class EvaluationCreate(BaseModel):
    """Schema for creating an evaluation."""

    trace_id: str
    span_id: str | None = None
    evaluator_type: str
    score: float | None = None
    criteria: dict | None = None
    result: dict | None = None


class EvaluationRunRequest(BaseModel):
    """Schema for running an evaluation."""

    trace_id: str
    evaluator_type: str
    criteria: list[dict]


class EvaluationResponse(BaseModel):
    """Schema for evaluation response."""

    id: str
    trace_id: str
    span_id: str | None
    evaluator_type: str
    score: float | None
    criteria: dict | None
    result: dict | None
    created_at: datetime


@router.post("/", response_model=EvaluationResponse)
async def create_evaluation(
    eval_data: EvaluationCreate,
    session: AsyncSession = Depends(get_session),
) -> Evaluation:
    """Create a new evaluation."""
    evaluation = Evaluation(
        id=str(uuid.uuid4()),
        trace_id=eval_data.trace_id,
        span_id=eval_data.span_id,
        evaluator_type=eval_data.evaluator_type,
        score=eval_data.score,
        criteria=eval_data.criteria,
        result=eval_data.result,
    )
    session.add(evaluation)
    await session.flush()

    await manager.broadcast({
        "type": "new_evaluation",
        "data": {
            "id": evaluation.id,
            "trace_id": evaluation.trace_id,
            "evaluator_type": evaluation.evaluator_type,
            "score": float(evaluation.score) if evaluation.score is not None else None,
            "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None,
        },
    })

    return evaluation


@router.post("/run", response_model=EvaluationResponse)
async def run_evaluation(
    run_request: EvaluationRunRequest,
    session: AsyncSession = Depends(get_session),
) -> Evaluation:
    """Run an evaluation on a trace using the specified evaluator."""
    # Get the trace
    query = select(Trace).where(Trace.id == run_request.trace_id)
    result = await session.execute(query)
    trace = result.scalar_one_or_none()

    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # Get spans for the trace
    spans_query = select(Span).where(Span.trace_id == run_request.trace_id)
    spans_result = await session.execute(spans_query)
    spans = list(spans_result.scalars().all())

    # Build trace data for evaluator
    trace_data = {
        "id": str(trace.id),
        "name": trace.name,
        "status": trace.status,
        "spans": [
            {
                "id": str(s.id),
                "name": s.name,
                "span_type": s.span_type,
                "tokens_input": s.tokens_input,
                "tokens_output": s.tokens_output,
                "cost_usd": float(s.cost_usd) if s.cost_usd else None,
            }
            for s in spans
        ],
        "total_spans": len(spans),
        "total_cost_usd": sum(float(s.cost_usd) for s in spans if s.cost_usd),
        "max_latency_ms": max(
            (
                (s.end_time - s.start_time).total_seconds() * 1000
                for s in spans
                if s.end_time and s.start_time
            ),
            default=0,
        ),
        "error_count": sum(1 for s in spans if s.status == "error"),
    }

    # Create evaluator and run
    try:
        evaluator_type = EvaluatorType(run_request.evaluator_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid evaluator type: {run_request.evaluator_type}",
        )

    evaluator = create_evaluator(evaluator_type)

    criteria = [
        EvaluationCriteria(
            name=c.get("name", ""),
            description=c.get("description", ""),
            weight=c.get("weight", 1.0),
            threshold=c.get("threshold"),
        )
        for c in run_request.criteria
    ]

    eval_result = await evaluator.evaluate(
        trace_id=run_request.trace_id,
        trace_data=trace_data,
        criteria=criteria,
    )

    # Store the evaluation
    evaluation = Evaluation(
        id=str(uuid.uuid4()),
        trace_id=run_request.trace_id,
        evaluator_type=run_request.evaluator_type,
        score=eval_result.score,
        criteria={"criteria": [c.name for c in criteria]},
        result={
            "scores": eval_result.criteria,
            "details": eval_result.details,
            "passed": eval_result.passed,
        },
    )
    session.add(evaluation)
    await session.flush()

    await manager.broadcast({
        "type": "new_evaluation",
        "data": {
            "id": evaluation.id,
            "trace_id": evaluation.trace_id,
            "evaluator_type": evaluation.evaluator_type,
            "score": float(evaluation.score) if evaluation.score is not None else None,
            "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None,
        },
    })

    return evaluation


@router.get("/summary")
async def get_evaluations_summary(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get aggregated evaluation statistics."""
    # By evaluator type
    query = (
        select(
            Evaluation.evaluator_type,
            func.count(Evaluation.id),
            func.coalesce(func.avg(Evaluation.score), 0),
        )
        .group_by(Evaluation.evaluator_type)
    )
    result = await session.execute(query)
    rows = result.all()

    by_type = {}
    for row in rows:
        by_type[row[0]] = {
            "count": row[1],
            "avg_score": float(row[2]) if row[1] > 0 else None,
        }

    # Pass rate (score >= 0.8)
    pass_query = select(func.count(Evaluation.id)).where(Evaluation.score >= 0.8)
    pass_result = await session.execute(pass_query)
    passed = pass_result.scalar() or 0

    total_query = select(func.count(Evaluation.id))
    total_result = await session.execute(total_query)
    total = total_result.scalar() or 0

    return {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total > 0 else 0,
        "by_type": by_type,
    }


@router.get("/", response_model=EvaluationListResponse)
async def list_evaluations(
    trace_id: str | None = Query(None),
    evaluator_type: str | None = Query(None),
    min_score: float | None = Query(None, ge=0, le=1),
    max_score: float | None = Query(None, ge=0, le=1),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List evaluations with optional filtering."""
    base_query = select(Evaluation)

    if trace_id:
        base_query = base_query.where(Evaluation.trace_id == trace_id)

    if evaluator_type:
        base_query = base_query.where(Evaluation.evaluator_type == evaluator_type)

    if min_score is not None:
        base_query = base_query.where(Evaluation.score >= min_score)

    if max_score is not None:
        base_query = base_query.where(Evaluation.score <= max_score)

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # Get paginated results
    data_query = base_query.order_by(Evaluation.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(data_query)
    evaluations = list(result.scalars().all())

    return {
        "evaluations": evaluations,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def summarize_scores(rows: list[tuple[Any, Any]]) -> dict[str, Any]:
    """Average score and pass rate overall, per model, per provider and per grade.

    ``rows`` are ``(score, result)`` of rule-based turn evaluations; ``result`` carries the
    model, provider, grade and pass flag written by ``app.evaluators.manitos_turn``.
    """

    def block(values: list[tuple[float, bool]]) -> dict[str, Any]:
        count = len(values)
        return {
            "count": count,
            "avg_score": sum(v[0] for v in values) / count if count else None,
            "pass_rate": sum(1 for v in values if v[1]) / count if count else None,
        }

    overall: list[tuple[float, bool]] = []
    models: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    providers: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    grades: Counter[str] = Counter()
    for score, result in rows:
        if score is None:
            continue
        data = result if isinstance(result, dict) else {}
        item = (float(score), bool(data.get("passed")))
        overall.append(item)
        models[str(data.get("model") or "?")].append(item)
        if data.get("provider"):
            providers[str(data["provider"])].append(item)
        if data.get("grade"):
            grades[str(data["grade"])] += 1

    def listing(groups: dict[str, list[tuple[float, bool]]]) -> list[dict[str, Any]]:
        out = [{"key": key, **block(vals)} for key, vals in groups.items()]
        return sorted(out, key=lambda x: (-x["count"], x["key"]))

    return {
        **block(overall),
        "models": listing(models),
        "providers": listing(providers),
        "grades": [{"grade": g, "count": grades.get(g, 0)} for g in ("A", "B", "C", "D", "F")],
    }


@router.get("/by-model")
async def evaluations_by_model(
    hours: int = Query(168, ge=1, le=720),
    project_id: str = Query("manitos", min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Quality of the automatic rule-based turn evaluations, grouped by model and provider."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = (
        select(Evaluation.score, Evaluation.result)
        .join(Trace, Trace.id == Evaluation.trace_id)
        .where(
            Evaluation.evaluator_type == "rule_based",
            Trace.project_id == project_id,
            Trace.created_at >= cutoff,
        )
        .limit(20000)
    )
    rows = [(score, result) for score, result in (await session.execute(query)).all()]
    return {"hours": hours, "project_id": project_id, **summarize_scores(rows)}


@router.get("/{evaluation_id}", response_model=EvaluationResponse)
async def get_evaluation(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
) -> Evaluation:
    """Get an evaluation by ID."""
    query = select(Evaluation).where(Evaluation.id == evaluation_id)
    result = await session.execute(query)
    evaluation = result.scalar_one_or_none()

    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    return evaluation


@router.delete("/{evaluation_id}")
async def delete_evaluation(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Delete an evaluation."""
    query = select(Evaluation).where(Evaluation.id == evaluation_id)
    result = await session.execute(query)
    evaluation = result.scalar_one_or_none()

    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    await session.delete(evaluation)
    await session.flush()
    return {"detail": "Evaluation deleted"}
