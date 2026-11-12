"""Time budget per stage (roadmap step 14).

A project sets, in its ``budgets`` configuration section, the longest a stage may take: a span
name (or a ``fnmatch`` pattern such as ``llm.*``, or a span type) and a number of milliseconds,
plus an optional budget for the whole turn. A turn is **over budget** when any span of a budgeted
stage, or the turn itself, took longer. Per stage: the turns that ran it, the ones over budget, and
the p50 and p95 of its longest span per turn. The alert metric ``over_budget_rate`` is the share of
the turns that ran a budgeted stage (or, with a turn budget, of all turns) that went over.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import ProjectConfig
from app.models.trace import Span, Trace

SECTION = "budgets"
MAX_TRACES = 5000


class BudgetsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # stage (span name, fnmatch pattern or span type) -> longest allowed duration in ms
    stages: dict[str, float] = Field(default_factory=dict, max_length=50)
    turn_ms: float | None = Field(None, gt=0, le=3_600_000)

    @field_validator("stages")
    @classmethod
    def _sane(cls, value: dict[str, float]) -> dict[str, float]:
        for key, ms in value.items():
            if not key.strip() or len(key) > 120:
                raise ValueError("a stage name must be 1-120 characters")
            if not 0 < ms <= 3_600_000:
                raise ValueError("a stage budget must be between 0 and 3,600,000 ms")
        return value


async def load_config(session: AsyncSession, project_id: str | None) -> BudgetsConfig:
    if not project_id:
        return BudgetsConfig()
    row = await session.get(ProjectConfig, (project_id, SECTION))
    if row is None or not isinstance(row.value, dict):
        return BudgetsConfig()
    try:
        return BudgetsConfig.model_validate(row.value)
    except ValueError:
        return BudgetsConfig()


def _ms(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    a = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    b = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    return (b - a).total_seconds() * 1000


def _stage_of(span: Any, stages: dict[str, float]) -> str | None:
    name, kind = str(span.name or ""), str(span.span_type or "")
    for stage in stages:
        if stage in (name, kind) or fnmatch.fnmatchcase(name, stage):
            return stage
    return None


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


def evaluate(cfg: BudgetsConfig, traces: list[tuple[Any, list[Any]]]) -> dict[str, Any]:
    """Per stage and per turn, against the budgets (pure: ``(trace, spans)`` pairs)."""
    longest: dict[str, list[float]] = defaultdict(list)
    over_by_stage: dict[str, int] = defaultdict(int)
    measured = over_turns = 0
    for trace, spans in traces:
        per_stage: dict[str, float] = {}
        for span in spans:
            stage = _stage_of(span, cfg.stages)
            ms = _ms(span.start_time, span.end_time)
            if stage is None or ms is None:
                continue
            per_stage[stage] = max(per_stage.get(stage, 0.0), ms)
        over = False
        for stage, ms in per_stage.items():
            longest[stage].append(ms)
            if ms > cfg.stages[stage]:
                over_by_stage[stage] += 1
                over = True
        turn_ms = _ms(trace.start_time, trace.end_time)
        if cfg.turn_ms is not None and turn_ms is not None and turn_ms > cfg.turn_ms:
            over = True
        if per_stage or (cfg.turn_ms is not None and turn_ms is not None):
            measured += 1
            over_turns += over
    stages = [
        {
            "stage": stage,
            "budget_ms": budget,
            "turns": len(longest[stage]),
            "over_budget": over_by_stage[stage],
            "over_rate": round(over_by_stage[stage] / len(longest[stage]), 4)
            if longest[stage] else None,
            "p50_ms": _pct(longest[stage], 0.5),
            "p95_ms": _pct(longest[stage], 0.95),
        }
        for stage, budget in cfg.stages.items()
    ]
    return {
        "turns_measured": measured,
        "over_budget_turns": over_turns,
        "over_budget_rate": round(over_turns / measured, 4) if measured else None,
        "turn_ms": cfg.turn_ms,
        "stages": sorted(stages, key=lambda s: -(s["over_rate"] or 0.0)),
    }


async def summary(
    session: AsyncSession,
    *,
    project_id: str,
    since: datetime,
    environment: str | None = None,
    max_traces: int = MAX_TRACES,
) -> dict[str, Any]:
    cfg = await load_config(session, project_id)
    query = select(Trace).where(Trace.project_id == project_id, Trace.created_at >= since)
    if environment:
        query = query.where(Trace.environment == environment)
    traces = list(
        (await session.execute(query.order_by(Trace.created_at.desc()).limit(max_traces))).scalars()
    )
    spans: dict[str, list[Any]] = defaultdict(list)
    ids = [str(t.id) for t in traces]
    for i in range(0, len(ids), 500):
        rows = await session.execute(select(Span).where(Span.trace_id.in_(ids[i : i + 500])))
        for span in rows.scalars():
            spans[str(span.trace_id)].append(span)
    result = evaluate(cfg, [(t, spans.get(str(t.id), [])) for t in traces])
    return {
        "project_id": project_id,
        "configured": bool(cfg.stages) or cfg.turn_ms is not None,
        "turns": len(traces),
        "truncated": len(traces) >= max_traces,
        **result,
    }
