"""Alert engine: evaluates threshold rules over sliding windows and keeps their events.

Every pass (``OBSERVATORY_ALERT_INTERVAL_SEC``, 60 s) each enabled rule is computed from the
turns of its window. A rule fires when the metric crosses its threshold with at least
``min_samples`` samples, stays open while it does, and resolves when it is back inside the
threshold by a 10 % margin (so a value hovering at the limit does not flap) or when the window
no longer holds enough samples to say. After resolving, the rule waits ``cooldown_minutes``
before it can fire again. Opening and resolving are pushed to the dashboard over ``/ws/live``
as ``alert_event`` messages.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.metrics_core import METRICS, TurnSignals, compute_metric, load_turn_signals
from app.models.alerts import AlertEvent, AlertRule
from app.websocket import manager

logger = logging.getLogger("observatory.alerts")

COMPARATORS = (">", ">=", "<", "<=")
SEVERITIES = ("info", "warning", "critical")
_CLEAR_MARGIN = 0.10

DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {
        "name": "Latencia p95 alta",
        "metric": "p95_latency_ms",
        "comparator": ">",
        "threshold": 30000.0,
        "window_minutes": 60,
        "min_samples": 10,
        "severity": "warning",
    },
    {
        "name": "Errores frecuentes",
        "metric": "error_rate",
        "comparator": ">",
        "threshold": 0.10,
        "window_minutes": 60,
        "min_samples": 10,
        "severity": "critical",
    },
    {
        "name": "Muchos turnos en vía alterna",
        "metric": "fallback_rate",
        "comparator": ">",
        "threshold": 0.25,
        "window_minutes": 60,
        "min_samples": 10,
        "severity": "warning",
    },
    {
        "name": "Proveedor sin cuota",
        "metric": "provider_quota_out",
        "comparator": ">",
        "threshold": 0.0,
        "window_minutes": 5,
        "min_samples": 1,
        "severity": "info",
    },
)


def alerts_enabled() -> bool:
    return os.getenv("OBSERVATORY_ALERTS", "1").strip().lower() not in {"0", "false", "off", "no"}


def alert_interval_sec() -> float:
    try:
        return max(5.0, float(os.getenv("OBSERVATORY_ALERT_INTERVAL_SEC", "60")))
    except ValueError:
        return 60.0


def breached(value: float, comparator: str, threshold: float) -> bool:
    return {
        ">": value > threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        "<=": value <= threshold,
    }[comparator]


def cleared(value: float, comparator: str, threshold: float) -> bool:
    """Back inside the threshold with a margin (no margin when the threshold is zero)."""
    margin = abs(threshold) * _CLEAR_MARGIN
    if comparator in (">", ">="):
        return value <= threshold - margin if margin else not breached(value, comparator, threshold)
    return value >= threshold + margin if margin else not breached(value, comparator, threshold)


def event_to_dict(event: AlertEvent) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "id": event.id,
        "rule_id": event.rule_id,
        "rule_name": event.rule_name,
        "metric": event.metric,
        "scope": event.scope,
        "comparator": event.comparator,
        "severity": event.severity,
        "state": event.state,
        "value": event.value,
        "threshold": event.threshold,
        "window_minutes": event.window_minutes,
        "fired_at": iso(event.fired_at),
        "resolved_at": iso(event.resolved_at),
        "acked_at": iso(event.acked_at),
        "details": event.details,
    }


def rule_to_dict(rule: AlertRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "metric": rule.metric,
        "comparator": rule.comparator,
        "threshold": rule.threshold,
        "window_minutes": rule.window_minutes,
        "min_samples": rule.min_samples,
        "scope": rule.scope,
        "project_id": rule.project_id,
        "environment": rule.environment,
        "severity": rule.severity,
        "enabled": bool(rule.enabled),
        "cooldown_minutes": rule.cooldown_minutes,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }


async def ensure_default_rules(session: AsyncSession) -> int:
    """Seed the default rules into an empty table (``OBSERVATORY_ALERTS_DEFAULTS=0`` skips)."""
    if os.getenv("OBSERVATORY_ALERTS_DEFAULTS", "1").strip().lower() in {"0", "false", "off", "no"}:
        return 0
    existing = (await session.execute(select(AlertRule.id).limit(1))).first()
    if existing:
        return 0
    for spec in DEFAULT_RULES:
        session.add(AlertRule(**spec))
    await session.commit()
    return len(DEFAULT_RULES)


def _quota_value(scope: str) -> tuple[float | None, int, dict[str, Any]]:
    """Providers out of quota according to the integration's latest (non-stale) snapshot."""
    from app.api.providers import read_snapshot

    snapshot = read_snapshot()
    if not snapshot:
        return None, 0, {}
    if time.time() - float(snapshot.get("received_at") or 0) > 180:
        return None, 0, {"stale": True}
    out = [
        p["id"]
        for p in snapshot.get("providers", [])
        if p.get("out_of_quota") and (not scope or p.get("id") == scope)
    ]
    return float(len(out)), 1, {"providers": out}


@dataclass
class PassResult:
    opened: int = 0
    resolved: int = 0
    evaluated: int = 0


async def evaluate_rules(session: AsyncSession, *, now: datetime | None = None) -> PassResult:
    """One evaluation pass over every enabled rule; commits, then notifies the dashboard."""
    now = now or datetime.now(timezone.utc)
    result = PassResult()
    notifications: list[dict[str, Any]] = []
    cache: dict[tuple[str, str | None, int], list[TurnSignals]] = {}
    rules = (await session.execute(select(AlertRule).where(AlertRule.enabled.is_(True)))).scalars()
    for rule in list(rules):
        spec = METRICS.get(rule.metric)
        if spec is None or rule.comparator not in COMPARATORS:
            continue
        details: dict[str, Any] = {}
        if spec["kind"] == "quota":
            value, samples, details = _quota_value(rule.scope or "")
            if value is None:
                continue  # no trustworthy snapshot: neither fire nor resolve
        else:
            key = (rule.project_id, rule.environment, int(rule.window_minutes))
            if key not in cache:
                since = now - timedelta(minutes=int(rule.window_minutes))
                cache[key] = await load_turn_signals(
                    session,
                    project_id=rule.project_id,
                    environment=rule.environment,
                    since=since,
                )
            value, samples = compute_metric(rule.metric, cache[key], scope=rule.scope or "")
        result.evaluated += 1
        enough = value is not None and samples >= int(rule.min_samples)
        open_event = (
            (
                await session.execute(
                    select(AlertEvent)
                    .where(AlertEvent.rule_id == rule.id, AlertEvent.state == "open")
                    .order_by(AlertEvent.fired_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        is_breached = enough and breached(float(value), rule.comparator, float(rule.threshold))
        if open_event is None:
            if not is_breached or await _in_cooldown(session, rule, now):
                continue
            event = AlertEvent(
                rule_id=rule.id,
                rule_name=rule.name,
                metric=rule.metric,
                scope=rule.scope,
                comparator=rule.comparator,
                severity=rule.severity,
                state="open",
                value=float(value),
                threshold=float(rule.threshold),
                window_minutes=int(rule.window_minutes),
                fired_at=now,
                details={"samples": samples, **details},
            )
            session.add(event)
            await session.flush()
            result.opened += 1
            notifications.append(event_to_dict(event))
        elif not enough or cleared(float(value), rule.comparator, float(rule.threshold)):
            open_event.state = "resolved"
            open_event.resolved_at = now
            if not enough:
                open_event.details = {**(open_event.details or {}), "resolved_by": "sin_datos"}
            result.resolved += 1
            notifications.append(event_to_dict(open_event))
        elif value is not None:
            open_event.value = float(value)  # still open: keep the latest reading
    await session.commit()
    for payload in notifications:
        try:
            await manager.broadcast({"type": "alert_event", "data": payload})
        except Exception:
            logger.debug("alert broadcast failed", exc_info=True)
    return result


async def _in_cooldown(session: AsyncSession, rule: AlertRule, now: datetime) -> bool:
    if not rule.cooldown_minutes:
        return False
    last = (
        await session.execute(
            select(AlertEvent.resolved_at)
            .where(AlertEvent.rule_id == rule.id, AlertEvent.resolved_at.is_not(None))
            .order_by(AlertEvent.resolved_at.desc())
            .limit(1)
        )
    ).first()
    if not last or last[0] is None:
        return False
    resolved = last[0] if last[0].tzinfo else last[0].replace(tzinfo=timezone.utc)
    return now - resolved < timedelta(minutes=int(rule.cooldown_minutes))


async def alerts_loop(factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop (same shape as ``retention_loop``); the session factory is injected."""
    if not alerts_enabled():
        return
    try:
        async with factory() as session:
            await ensure_default_rules(session)
    except Exception:
        logger.warning("alert defaults failed", exc_info=True)
    while True:
        try:
            async with factory() as session:
                result = await evaluate_rules(session)
            if result.opened or result.resolved:
                logger.info("alerts: opened %d, resolved %d", result.opened, result.resolved)
        except Exception:
            logger.warning("alert pass failed", exc_info=True)
        await asyncio.sleep(alert_interval_sec())
