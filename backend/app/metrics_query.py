"""A small, safe query engine over the stored telemetry (the idea of Langfuse's metrics API).

A request names a *view* (turns, provider attempts or turn evaluations), the *metrics* to compute
(a measure plus an aggregation), optional *dimensions* to group by, *filters*, a time range and an
optional time granularity. Every field name is checked against a fixed list per view, so no
column or expression ever comes from the request. Rows are loaded for a bounded window and
aggregated in Python, like the rest of the analytics, so SQLite and PostgreSQL behave the same.
High-cardinality fields (session, turn) can filter but never group.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics_core import (
    ROW_LIMIT,
    TurnSignals,
    aware,
    classify_attempt,
    load_turn_signals,
    percentile,
)
from app.models.evaluation import Evaluation
from app.models.trace import Trace

View = Literal["turns", "provider_attempts", "evaluations"]
Aggregation = Literal["count", "sum", "avg", "min", "max", "p50", "p95", "p99"]
Granularity = Literal["auto", "minute", "hour", "day"]


@dataclass(frozen=True)
class ViewSpec:
    label: str
    measures: dict[str, str]  # numeric fields -> label (Spanish key for the dashboard's t())
    dimensions: dict[str, str]
    filter_only: dict[str, str]


VIEWS: dict[str, ViewSpec] = {
    "turns": ViewSpec(
        label="Turnos",
        measures={
            "duration_ms": "Duración del turno (ms)",
            "ttft_ms": "Primer token (ms)",
            "error": "Error (0/1)",
            "degraded": "Respuesta degradada (0/1)",
            "truncated": "Respuesta truncada (0/1)",
            "tool_error": "Error en herramientas (0/1)",
            "fallback": "Vía alterna (0/1)",
        },
        dimensions={
            "environment": "Entorno",
            "model": "Modelo",
            "provider": "Proveedor",
            "language": "Idioma",
            "intent": "Intención",
            "status": "Estado",
        },
        filter_only={"session_id": "Sesión"},
    ),
    "provider_attempts": ViewSpec(
        label="Intentos de proveedor",
        measures={
            "ms": "Tiempo de la llamada (ms)",
            "ok": "Respondió (0/1)",
            "failed": "Falló (0/1)",
        },
        dimensions={
            "provider": "Proveedor",
            "model": "Modelo",
            "result": "Resultado",
            "environment": "Entorno",
            "intent": "Intención",
        },
        filter_only={"session_id": "Sesión"},
    ),
    "evaluations": ViewSpec(
        label="Evaluaciones de turnos",
        measures={"score": "Puntaje", "passed": "Aprobada (0/1)"},
        dimensions={
            "provider": "Proveedor",
            "model": "Modelo",
            "grade": "Nota",
            "environment": "Entorno",
        },
        filter_only={},
    ),
}


class MetricSpec(BaseModel):
    measure: str = Field(..., max_length=40)  # a numeric field, or "count" with aggregation count
    aggregation: Aggregation


class FilterSpec(BaseModel):
    field: str = Field(..., max_length=40)
    op: Literal["=", "!=", "in"] = "="
    value: str | list[str]

    @model_validator(mode="after")
    def _shape(self) -> FilterSpec:
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError("'in' needs a list of values")
        if self.op != "in" and isinstance(self.value, list):
            raise ValueError("a list of values needs op 'in'")
        return self


class OrderSpec(BaseModel):
    field: str = Field(..., max_length=80)
    direction: Literal["asc", "desc"] = "desc"


class QueryRequest(BaseModel):
    view: View
    metrics: list[MetricSpec] = Field(..., min_length=1, max_length=5)
    dimensions: list[str] = Field(default_factory=list, max_length=2)
    filters: list[FilterSpec] = Field(default_factory=list, max_length=8)
    project_id: str = Field("manitos", min_length=1, max_length=128)
    hours: int = Field(24, ge=1, le=720)
    from_time: datetime | None = None
    to_time: datetime | None = None
    granularity: Granularity | None = None
    order_by: OrderSpec | None = None
    row_limit: int = Field(100, ge=1, le=1000)


class QueryError(ValueError):
    """A request that names something the view does not have."""


def metric_key(m: MetricSpec) -> str:
    return (
        "count"
        if m.aggregation == "count" and m.measure == "count"
        else (f"{m.aggregation}_{m.measure}")
    )


def validate(req: QueryRequest) -> ViewSpec:
    spec = VIEWS[req.view]
    for m in req.metrics:
        if m.aggregation == "count":
            if m.measure not in ("count", *spec.measures):
                raise QueryError(f"unknown measure for {req.view}: {m.measure}")
        elif m.measure not in spec.measures:
            raise QueryError(f"unknown measure for {req.view}: {m.measure}")
    for d in req.dimensions:
        if d not in spec.dimensions:
            hint = " (filter only)" if d in spec.filter_only else ""
            raise QueryError(f"cannot group {req.view} by {d}{hint}")
    for f in req.filters:
        if f.field not in spec.dimensions and f.field not in spec.filter_only:
            raise QueryError(f"cannot filter {req.view} by {f.field}")
    if req.order_by is not None:
        allowed = {metric_key(m) for m in req.metrics} | set(req.dimensions) | {"time"}
        if req.order_by.field not in allowed:
            raise QueryError(f"cannot order by {req.order_by.field}")
    return spec


def resolve_granularity(req: QueryRequest, start: datetime, end: datetime) -> str | None:
    if req.granularity is None:
        return None
    if req.granularity != "auto":
        return req.granularity
    span = end - start
    return (
        "minute" if span <= timedelta(hours=3) else "hour" if span <= timedelta(hours=72) else "day"
    )


def bucket_start(moment: datetime, granularity: str) -> datetime:
    moment = moment.astimezone(timezone.utc)
    if granularity == "minute":
        return moment.replace(second=0, microsecond=0)
    if granularity == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _provider_of(label: str) -> str:
    return label.split(" · ", 1)[0].strip().lower() if " · " in label else ""


def turn_records(turns: list[TurnSignals]) -> list[dict[str, Any]]:
    out = []
    for s in turns:
        answered = s.answered_by or s.model
        out.append(
            {
                "time": s.created_at,
                "environment": s.environment,
                "model": answered,
                "provider": _provider_of(answered),
                "language": s.language,
                "intent": s.intent,
                "status": s.status,
                "session_id": s.session_id,
                "duration_ms": s.duration_ms,
                "ttft_ms": s.ttft_ms,
                "error": 1.0 if s.status == "error" else 0.0,
                "degraded": 1.0 if s.degraded else 0.0,
                "truncated": 1.0 if s.truncated else 0.0,
                "tool_error": 1.0 if s.tool_error else 0.0,
                "fallback": 1.0 if s.fallback else 0.0,
            }
        )
    return out


def attempt_records(turns: list[TurnSignals]) -> list[dict[str, Any]]:
    out = []
    for s in turns:
        for a in s.attempts:
            kind = classify_attempt(a["result"])
            out.append(
                {
                    "time": s.created_at,
                    "environment": s.environment,
                    "intent": s.intent,
                    "session_id": s.session_id,
                    "provider": a["backend"],
                    "model": a["model"],
                    "result": kind,
                    "ms": float(a["ms"]) if a["ms"] is not None else None,
                    "ok": 1.0 if kind == "ok" else 0.0,
                    "failed": 1.0 if kind in {"rate_limited", "timeout", "error"} else 0.0,
                }
            )
    return out


async def evaluation_records(
    session: AsyncSession,
    *,
    project_id: str,
    since: datetime,
    limit: int,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    query = (
        select(Evaluation.score, Evaluation.result, Trace.created_at, Trace.environment)
        .join(Trace, Trace.id == Evaluation.trace_id)
        .where(
            Evaluation.evaluator_type == "rule_based",
            Trace.project_id == project_id,
            Trace.created_at >= since,
        )
        .order_by(Trace.created_at.desc())
        .limit(limit)
    )
    if until is not None:
        query = query.where(Trace.created_at <= until)
    out = []
    for score, result, created, env in (await session.execute(query)).all():
        data = result if isinstance(result, dict) else {}
        out.append(
            {
                "time": aware(created),
                "environment": str(env or ""),
                "provider": str(data.get("provider") or ""),
                "model": str(data.get("model") or ""),
                "grade": str(data.get("grade") or ""),
                "score": float(score) if score is not None else None,
                "passed": 1.0 if data.get("passed") else 0.0,
            }
        )
    return out


def _matches(record: dict[str, Any], f: FilterSpec) -> bool:
    value = str(record.get(f.field) or "")
    if f.op == "in":
        return value in {str(v) for v in f.value}  # type: ignore[union-attr]
    return (value == str(f.value)) == (f.op == "=")


def _aggregate(values: list[float], aggregation: str, rows: int, rows_count: bool) -> float | None:
    """``rows_count``: count the rows (measure "count"); otherwise count the non-null values."""
    if aggregation == "count":
        return float(rows if rows_count else len(values))
    if not values:
        return None
    return {
        "sum": lambda: sum(values),
        "avg": lambda: sum(values) / len(values),
        "min": lambda: min(values),
        "max": lambda: max(values),
        "p50": lambda: percentile(values, 0.5),
        "p95": lambda: percentile(values, 0.95),
        "p99": lambda: percentile(values, 0.99),
    }[aggregation]()


def run_aggregation(
    records: list[dict[str, Any]], req: QueryRequest, granularity: str | None
) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if not all(_matches(r, f) for f in req.filters):
            continue
        key: list[Any] = []
        if granularity:
            key.append(bucket_start(r["time"], granularity).isoformat())
        key.extend(str(r.get(d) or "") for d in req.dimensions)
        groups[tuple(key)].append(r)
    rows: list[dict[str, Any]] = []
    for key, items in groups.items():
        row: dict[str, Any] = {}
        pos = 0
        if granularity:
            row["time"] = key[0]
            pos = 1
        for i, d in enumerate(req.dimensions):
            row[d] = key[pos + i]
        for m in req.metrics:
            if m.measure == "count":
                vals: list[float] = []
            else:
                vals = [x[m.measure] for x in items if x.get(m.measure) is not None]
            row[metric_key(m)] = _aggregate(vals, m.aggregation, len(items), m.measure == "count")
        rows.append(row)
    return rows


async def run_query(session: AsyncSession, req: QueryRequest) -> dict[str, Any]:
    validate(req)
    end = aware(req.to_time) or datetime.now(timezone.utc)
    start = aware(req.from_time) or (end - timedelta(hours=req.hours))
    if start >= end:
        raise QueryError("from_time must be before to_time")
    granularity = resolve_granularity(req, start, end)
    if req.view == "evaluations":
        records = await evaluation_records(
            session, project_id=req.project_id, since=start, limit=ROW_LIMIT, until=end
        )
        loaded = len(records)
    else:
        turns = await load_turn_signals(
            session, project_id=req.project_id, since=start, limit=ROW_LIMIT, until=end
        )
        loaded = len(turns)
        records = turn_records(turns) if req.view == "turns" else attempt_records(turns)
    records = [r for r in records if r["time"] is not None and start <= r["time"] <= end]
    rows = run_aggregation(records, req, granularity)
    order = req.order_by
    if order is not None:
        rows.sort(
            key=lambda r: (r.get(order.field) is None, r.get(order.field)),
            reverse=order.direction == "desc",
        )
    else:
        first = metric_key(req.metrics[0])
        rows.sort(key=lambda r: (r.get("time", ""), -(r.get(first) or 0.0)))
    return {
        "view": req.view,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "granularity": granularity,
        "samples": len(records),
        "truncated": loaded >= ROW_LIMIT,
        "rows": rows[: req.row_limit],
    }


def schema() -> dict[str, Any]:
    """What the explorer UI needs to build a query: views, measures, dimensions, aggregations."""
    return {
        "views": {
            key: {
                "label": v.label,
                "measures": [{"id": k, "label": lbl} for k, lbl in v.measures.items()],
                "dimensions": [{"id": k, "label": lbl} for k, lbl in v.dimensions.items()],
                "filter_only": [{"id": k, "label": lbl} for k, lbl in v.filter_only.items()],
            }
            for key, v in VIEWS.items()
        },
        "aggregations": ["count", "sum", "avg", "min", "max", "p50", "p95", "p99"],
        "granularities": ["auto", "minute", "hour", "day"],
    }
