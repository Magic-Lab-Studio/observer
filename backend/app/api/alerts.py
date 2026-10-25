"""Alert rules and events (``/v1/alerts``)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import evaluate_rules, event_to_dict, rule_to_dict
from app.db import DbSession
from app.metrics_core import METRICS
from app.models.alerts import AlertEvent, AlertRule

router = APIRouter()

Comparator = Literal[">", ">=", "<", "<="]
Severity = Literal["info", "warning", "critical"]


class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    metric: str = Field(..., max_length=40)
    comparator: Comparator = ">"
    threshold: float
    window_minutes: int = Field(60, ge=1, le=10080)
    min_samples: int = Field(5, ge=1, le=100000)
    scope: str | None = Field(None, max_length=128)
    project_id: str = Field("manitos", min_length=1, max_length=128)
    environment: str | None = Field(None, max_length=64)
    severity: Severity = "warning"
    enabled: bool = True
    cooldown_minutes: int = Field(30, ge=0, le=10080)


class RulePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    comparator: Comparator | None = None
    threshold: float | None = None
    window_minutes: int | None = Field(None, ge=1, le=10080)
    min_samples: int | None = Field(None, ge=1, le=100000)
    scope: str | None = Field(None, max_length=128)
    environment: str | None = Field(None, max_length=64)
    severity: Severity | None = None
    enabled: bool | None = None
    cooldown_minutes: int | None = Field(None, ge=0, le=10080)


def _check_metric(metric: str) -> None:
    if metric not in METRICS:
        raise HTTPException(status_code=422, detail=f"unknown metric: {metric}")


@router.get("/metrics")
async def list_metrics() -> list[dict[str, Any]]:
    """The metrics a rule can use (for the rule form)."""
    return [{"id": key, **value} for key, value in METRICS.items()]


@router.get("/rules")
async def list_rules(
    project_id: str | None = Query(None, min_length=1, max_length=128),
    session: AsyncSession = DbSession,
) -> list[dict[str, Any]]:
    query = select(AlertRule).order_by(AlertRule.created_at)
    if project_id:
        query = query.where(AlertRule.project_id == project_id)
    return [rule_to_dict(r) for r in (await session.execute(query)).scalars()]


@router.post("/rules")
async def create_rule(body: RuleIn, session: AsyncSession = DbSession) -> dict[str, Any]:
    _check_metric(body.metric)
    rule = AlertRule(**body.model_dump())
    session.add(rule)
    await session.flush()
    return rule_to_dict(rule)


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: str, body: RulePatch, session: AsyncSession = DbSession
) -> dict[str, Any]:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await session.flush()
    return rule_to_dict(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, session: AsyncSession = DbSession) -> dict[str, str]:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    # Children first: SQLite enforces ON DELETE CASCADE only with foreign keys switched on.
    await session.execute(delete(AlertEvent).where(AlertEvent.rule_id == rule_id))
    await session.delete(rule)
    return {"status": "deleted"}


@router.get("/events")
async def list_events(
    state: Literal["open", "resolved"] | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    project_id: str | None = Query(None, min_length=1, max_length=128),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    query = select(AlertEvent).order_by(AlertEvent.fired_at.desc()).limit(limit)
    count = select(AlertEvent.id).where(AlertEvent.state == "open")
    if state:
        query = query.where(AlertEvent.state == state)
    if project_id:
        rules = select(AlertRule.id).where(AlertRule.project_id == project_id)
        query = query.where(AlertEvent.rule_id.in_(rules))
        count = count.where(AlertEvent.rule_id.in_(rules))
    events = (await session.execute(query)).scalars()
    open_count = len((await session.execute(count)).all())
    return {"events": [event_to_dict(e) for e in events], "open_count": open_count}


@router.post("/events/{event_id}/ack")
async def ack_event(event_id: str, session: AsyncSession = DbSession) -> dict[str, Any]:
    event = await session.get(AlertEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    event.acked_at = datetime.now(timezone.utc)
    await session.flush()
    return event_to_dict(event)


@router.post("/evaluate")
async def evaluate_now(session: AsyncSession = DbSession) -> dict[str, int]:
    """Run one evaluation pass now (the loop does it every minute)."""
    result = await evaluate_rules(session)
    return {"evaluated": result.evaluated, "opened": result.opened, "resolved": result.resolved}
