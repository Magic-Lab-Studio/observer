"""Analytics API endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DbSession
from app.metrics_core import aware, percentile
from app.metrics_query import QueryError, QueryRequest, run_query
from app.metrics_query import schema as query_schema
from app.models.evaluation import Evaluation
from app.models.trace import Span, Trace

# Spans whose "model" attribute names the model that answered, best first (see manitos_quality).
_MODEL_SPAN_PRIORITY = {"model.answered": 0, "manitos.turn.lifecycle": 1}

router = APIRouter()


class AnalyticsSummary(BaseModel):
    """Summary analytics response: generic trace metrics, valid for any instrumented application.

    ``avg_latency_ms`` is the mean span duration (kept for compatibility); trace latency is
    ``p50_trace_duration_ms``/``p95_trace_duration_ms`` over traces that have finished.
    ``error_count`` counts traces whose status is ``error`` (``unset`` never counts); spans that
    failed inside a trace that succeeded are in ``span_error_count``. Fields that cannot be
    computed from the data are ``null``, never ``0``.
    """

    total_traces: int
    total_spans: int
    total_cost_usd: float
    avg_latency_ms: float
    error_count: int
    error_rate: float
    total_evaluations: int
    avg_evaluation_score: float | None
    completed_traces: int = 0
    running_traces: int = 0
    trace_error_rate: float | None = None
    span_error_count: int = 0
    llm_span_count: int = 0
    tool_span_count: int = 0
    priced_span_count: int = 0
    p50_trace_duration_ms: float | None = None
    p95_trace_duration_ms: float | None = None


class CostByModel(BaseModel):
    """Cost breakdown by model."""

    model: str
    cost_usd: float
    span_count: int


class TraceTimeline(BaseModel):
    """Trace count over time."""

    timestamp: str
    count: int
    cost_usd: float
    error_count: int = 0


class QualityBreakdown(BaseModel):
    """Per-turn occurrence count for one bounded metadata value."""

    key: str
    count: int
    rate: float


class ManitOSQualitySummary(BaseModel):
    """Metadata-only quality indicators emitted by a compatible integration."""

    project_id: str
    environment: str | None
    hours: int
    window_start: str | None = None
    window_end: str | None = None
    total_turns: int
    error_count: int
    error_rate: float
    degraded_count: int
    degraded_rate: float
    truncated_count: int
    truncated_rate: float
    tool_error_count: int
    tool_error_rate: float
    fallback_count: int
    fallback_rate: float
    tts_error_count: int
    tts_error_rate: float
    # null when no turn reported it (B2: a missing first-token time read as "<1 ms")
    avg_duration_ms: float | None
    avg_ttft_ms: float | None
    models: list[QualityBreakdown]
    languages: list[QualityBreakdown]


@router.get("/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    session_id: str | None = Query(None),
    hours: int = Query(24, ge=1, le=720),
    session: AsyncSession = DbSession,
) -> dict:
    """Get analytics summary for the specified time period."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Traces
    trace_query = select(Trace.start_time, Trace.end_time, Trace.status).where(
        Trace.created_at >= cutoff
    )
    if session_id:
        trace_query = trace_query.where(Trace.session_id == session_id)
    trace_rows = (await session.execute(trace_query)).all()
    total_traces = len(trace_rows)
    trace_durations = sorted(
        (row.end_time - row.start_time).total_seconds() * 1000
        for row in trace_rows
        if row.start_time is not None and row.end_time is not None
    )
    running_traces = sum(1 for row in trace_rows if row.end_time is None and row.status != "error")

    # Total spans and cost
    span_query = (
        select(Span.start_time, Span.end_time, Span.cost_usd, Span.status, Span.span_type)
        .join(Trace, Span.trace_id == Trace.id)
        .where(Trace.created_at >= cutoff)
    )
    if session_id:
        span_query = span_query.where(Trace.session_id == session_id)
    result = await session.execute(span_query)
    span_rows = result.all()
    total_spans = len(span_rows)
    total_cost_usd = sum(float(row.cost_usd or 0) for row in span_rows)
    priced_span_count = sum(1 for row in span_rows if row.cost_usd is not None)
    latencies_ms = [
        (row.end_time - row.start_time).total_seconds() * 1000
        for row in span_rows
        if row.start_time is not None and row.end_time is not None
    ]
    avg_latency_ms = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0

    error_count = sum(1 for row in trace_rows if row.status == "error")

    # Evaluations
    eval_query = (
        select(
            func.count(Evaluation.id),
            func.coalesce(func.avg(Evaluation.score), 0),
        )
        .join(Trace, Evaluation.trace_id == Trace.id)
        .where(Trace.created_at >= cutoff)
    )
    if session_id:
        eval_query = eval_query.where(Trace.session_id == session_id)
    result = await session.execute(eval_query)
    eval_data = result.one()
    total_evaluations = eval_data[0] or 0
    avg_score = float(eval_data[1] or 0) if total_evaluations > 0 else None

    return {
        "total_traces": total_traces,
        "total_spans": total_spans,
        "total_cost_usd": total_cost_usd,
        "avg_latency_ms": avg_latency_ms,
        "error_count": error_count,
        "error_rate": error_count / total_traces if total_traces > 0 else 0,
        "total_evaluations": total_evaluations,
        "avg_evaluation_score": avg_score,
        "completed_traces": len(trace_durations),
        "running_traces": running_traces,
        "trace_error_rate": error_count / total_traces if total_traces else None,
        "span_error_count": sum(1 for row in span_rows if row.status == "error"),
        "llm_span_count": sum(1 for row in span_rows if row.span_type == "llm"),
        "tool_span_count": sum(1 for row in span_rows if row.span_type == "tool"),
        "priced_span_count": priced_span_count,
        "p50_trace_duration_ms": percentile(trace_durations, 0.5),
        "p95_trace_duration_ms": percentile(trace_durations, 0.95),
    }


@router.get("/cost-by-model", response_model=list[CostByModel])
async def get_cost_by_model(
    hours: int = Query(24, ge=1, le=720),
    session: AsyncSession = DbSession,
) -> list[dict]:
    """Get cost breakdown by model (from span attributes)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # SQLAlchemy JSON extraction differs between SQLite and PostgreSQL.
    # Aggregate this bounded time-window result in Python for identical
    # semantics on both supported databases.
    query = (
        select(Span.attributes, Span.cost_usd)
        .join(Trace, Span.trace_id == Trace.id)
        .where(Trace.created_at >= cutoff)
    )
    result = await session.execute(query)
    rows = result.all()
    by_model: dict[str, dict[str, str | float | int]] = {}
    for row in rows:
        attributes = row.attributes if isinstance(row.attributes, dict) else {}
        model = str(
            attributes.get("model")
            or attributes.get("gen_ai.response.model")
            or attributes.get("gen_ai.request.model")
            or ""
        ).strip()
        if not model:
            continue
        bucket = by_model.setdefault(model, {"model": model, "cost_usd": 0.0, "span_count": 0})
        bucket["cost_usd"] = float(bucket["cost_usd"]) + float(row.cost_usd or 0)
        bucket["span_count"] = int(bucket["span_count"]) + 1
    return sorted(by_model.values(), key=lambda item: float(item["cost_usd"]), reverse=True)


@router.get("/timeline", response_model=list[TraceTimeline])
async def get_trace_timeline(
    hours: int = Query(24, ge=1, le=720),
    interval_minutes: int = Query(60, ge=5, le=1440),
    session: AsyncSession = DbSession,
) -> list[dict]:
    """Get trace count and cost over time."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Get all traces in the time range
    query = select(Trace).where(Trace.created_at >= cutoff).order_by(Trace.created_at)
    result = await session.execute(query)
    traces = list(result.scalars().all())

    # Get all span costs for traces in the time range (single query, no N+1)
    cost_query = (
        select(
            Span.trace_id,
            func.coalesce(func.sum(Span.cost_usd), 0).label("cost_usd"),
        )
        .join(Trace, Span.trace_id == Trace.id)
        .where(Trace.created_at >= cutoff)
        .group_by(Span.trace_id)
    )
    cost_result = await session.execute(cost_query)
    cost_by_trace = {row.trace_id: float(row.cost_usd) for row in cost_result.all()}

    # Bucket traces by interval
    buckets: dict[str, dict] = {}
    for trace in traces:
        # Truncate to interval
        created = trace.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        step = interval_minutes * 60
        # intervals over 60 min collapsed to hourly buckets (audit 2026-10-17)
        ts = datetime.fromtimestamp(int(created.timestamp()) // step * step, tz=timezone.utc)
        key = ts.isoformat()
        if key not in buckets:
            buckets[key] = {"timestamp": key, "count": 0, "cost_usd": 0, "error_count": 0}
        buckets[key]["count"] += 1
        buckets[key]["error_count"] += 1 if trace.status == "error" else 0
        buckets[key]["cost_usd"] += cost_by_trace.get(trace.id, 0)

    return list(buckets.values())


@router.get("/manitos-quality", response_model=ManitOSQualitySummary)
async def get_manitos_quality(
    hours: int = Query(24, ge=1, le=720),
    project_id: str = Query("manitos", min_length=1, max_length=128),
    environment: str | None = Query(None, min_length=1, max_length=64),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    session: AsyncSession = DbSession,
) -> dict:
    """Aggregate bounded, metadata-only turn quality signals across SQL dialects.

    With ``since`` the aggregation uses a fixed half-open interval
    ``[since, until or now)`` instead of the rolling ``hours`` window, so the
    result is genuinely cumulative over that interval and never mixes turns
    aging out of a sliding cutoff.
    """
    now = datetime.now(timezone.utc)
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        window_start = since
        window_end = until or now
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)
        cutoff = window_start
    else:
        cutoff = now - timedelta(hours=hours)
        window_start = cutoff
        window_end = now
    trace_query = select(Trace.id, Trace.status).where(
        Trace.created_at >= cutoff,
        Trace.created_at < window_end,
        Trace.project_id == project_id,
    )
    if environment:
        trace_query = trace_query.where(Trace.environment == environment)
    trace_rows = (await session.execute(trace_query)).all()
    trace_ids = {str(row.id) for row in trace_rows}
    total_turns = len(trace_ids)
    error_count = sum(1 for row in trace_rows if row.status == "error")

    degraded: set[str] = set()
    truncated: set[str] = set()
    tool_errors: set[str] = set()
    fallbacks: set[str] = set()
    tts_errors: set[str] = set()
    durations: list[float] = []
    ttfts: list[float] = []
    models: dict[str, set[str]] = {}
    languages: dict[str, set[str]] = {}
    answered_model: dict[str, tuple[int, str]] = {}

    if trace_ids:
        span_query = select(Span.trace_id, Span.name, Span.attributes).where(
            Span.trace_id.in_(trace_ids)
        )
        span_rows = (await session.execute(span_query)).all()
        for row in span_rows:
            trace_id = str(row.trace_id)
            attributes = row.attributes if isinstance(row.attributes, dict) else {}
            if row.name == "manitos.turn.lifecycle":
                if attributes.get("llm_degraded") is True:
                    degraded.add(trace_id)
                if attributes.get("llm_truncated") is True:
                    truncated.add(trace_id)
                if attributes.get("tool_error") is True:
                    tool_errors.add(trace_id)
                for key, target in (("duration_ms", durations), ("ttft_ms", ttfts)):
                    value = attributes.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        target.append(float(value))
            if attributes.get("local_fallback") is True:
                fallbacks.add(trace_id)
            if row.name == "tts.synthesis" and attributes.get("status") == "error":
                tts_errors.add(trace_id)
            language = str(attributes.get("language") or "").strip()[:160]
            if language:
                languages.setdefault(language, set()).add(trace_id)
            # One model per turn: the one that wrote the reply (model.answered), else the
            # lifecycle's. Counting "model" from every span gave three spellings per turn and
            # rates summing to 1.95 (audit 2026-10-14); model_routing is the router's choice.
            model = str(attributes.get("model") or "").strip()[:160]
            if model and row.name in _MODEL_SPAN_PRIORITY:
                rank = _MODEL_SPAN_PRIORITY[row.name]
                if trace_id not in answered_model or rank < answered_model[trace_id][0]:
                    answered_model[trace_id] = (rank, model)

    for trace_id, (_rank, model) in answered_model.items():
        models.setdefault(model, set()).add(trace_id)

    def rate(count: int) -> float:
        return count / total_turns if total_turns else 0.0

    def breakdown(values: dict[str, set[str]]) -> list[dict]:
        items = [
            {"key": key, "count": len(ids), "rate": rate(len(ids))} for key, ids in values.items()
        ]
        return sorted(items, key=lambda item: (-item["count"], item["key"]))

    return {
        "project_id": project_id,
        "environment": environment,
        "hours": hours,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "total_turns": total_turns,
        "error_count": error_count,
        "error_rate": rate(error_count),
        "degraded_count": len(degraded),
        "degraded_rate": rate(len(degraded)),
        "truncated_count": len(truncated),
        "truncated_rate": rate(len(truncated)),
        "tool_error_count": len(tool_errors),
        "tool_error_rate": rate(len(tool_errors)),
        "fallback_count": len(fallbacks),
        "fallback_rate": rate(len(fallbacks)),
        "tts_error_count": len(tts_errors),
        "tts_error_rate": rate(len(tts_errors)),
        "avg_duration_ms": sum(durations) / len(durations) if durations else None,
        "avg_ttft_ms": sum(ttfts) / len(ttfts) if ttfts else None,
        "models": breakdown(models),
        "languages": breakdown(languages),
    }


# ── Sessions ─────────────────────────────────────────────────────────


class SessionInfo(BaseModel):
    """Session summary."""

    session_id: str
    trace_count: int
    total_cost_usd: float
    created_at: str | None
    last_activity: str | None


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = DbSession,
) -> list[dict]:
    """List unique sessions with aggregated stats."""
    query = (
        select(
            Trace.session_id,
            func.count(Trace.id).label("trace_count"),
            func.coalesce(
                func.sum(
                    select(func.coalesce(func.sum(Span.cost_usd), 0))
                    .where(Span.trace_id == Trace.id)
                    .correlate(Trace)
                    .scalar_subquery()
                ),
                0,
            ).label("total_cost_usd"),
            func.min(Trace.created_at).label("created_at"),
            func.max(Trace.created_at).label("last_activity"),
        )
        .where(Trace.session_id.isnot(None))
        .group_by(Trace.session_id)
        .order_by(func.max(Trace.created_at).desc())
        .limit(limit)
    )
    result = await session.execute(query)
    rows = result.all()

    return [
        {
            "session_id": str(row.session_id),
            "trace_count": row.trace_count,
            "total_cost_usd": float(row.total_cost_usd),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_activity": row.last_activity.isoformat() if row.last_activity else None,
        }
        for row in rows
    ]


@router.get("/projects")
async def list_projects(
    limit: int = Query(100, ge=1, le=500), session: AsyncSession = DbSession
) -> list[dict]:
    """Projects that have traces, the one with the most recent trace first (the dashboard's
    project selector defaults to it)."""
    last = func.max(Trace.created_at)
    rows = (
        await session.execute(
            select(Trace.project_id, func.count(Trace.id), last)
            .where(Trace.project_id.is_not(None))
            .group_by(Trace.project_id)
            .order_by(last.desc(), Trace.project_id)
            .limit(limit)
        )
    ).all()
    return [
        {
            "project_id": str(project_id),
            "trace_count": int(count),
            "last_trace_at": aware(last_at).isoformat() if last_at else None,
        }
        for project_id, count, last_at in rows
    ]


@router.get("/query/schema")
async def get_query_schema() -> dict:
    """Views, measures, dimensions and aggregations the query endpoint accepts."""
    return query_schema()


@router.post("/query")
async def post_query(request: QueryRequest, session: AsyncSession = DbSession) -> dict:
    """Aggregate turns, provider attempts or turn evaluations by dimension and time."""
    try:
        return await run_query(session, request)
    except QueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
