"""Governance views (``/v1/governance``): decisions, denials, approvals, skills and a turn's path.

ManitOS emits these as ordinary spans of ``manitos.telemetry.v1`` (``span_type`` decision,
security, approval and skill; contract in ManitOS ``docs/wiki/data/observer_event_contract.md``).
Nothing here writes. Rows carry names, status, timing and the contract's attributes only: span
input/output is never returned, and attribute keys outside the contract are dropped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.traces import _resolve_trace_id
from app.db import DbSession
from app.metrics_core import aware
from app.models.trace import Span, Trace

router = APIRouter()

# The attribute keys each governance span type may carry (mirror of the ManitOS contract).
CONTRACT_KEYS: dict[str, frozenset[str]] = {
    "decision": frozenset(
        f"decision.{k}"
        for k in (
            "kind",
            "backend",
            "model",
            "options",
            "scores",
            "choice",
            "confidence",
            "latency_ms",
            "final_by",
            "error",
        )
    ),
    "security": frozenset(
        f"security.{k}" for k in ("outcome", "reason", "capabilities", "tool", "policy", "target_kind")
    ),
    "approval": frozenset(
        f"approval.{k}" for k in ("request_id", "subject", "result", "by", "reason_code")
    ),
    "skill": frozenset(
        f"skill.{k}"
        for k in ("id", "version", "source", "sha256", "capabilities", "scripts", "validation")
    ),
    # ``status`` and ``latency_ms`` are what tool spans carried before the contract.
    "tool": frozenset({"tool.call_id", "tool.args_digest", "status", "latency_ms"}),
}
GOVERNANCE_TYPES = ("decision", "security", "approval", "skill")
# Span types that make up a turn's governed path, in the order they normally happen.
PATH_TYPES = ("decision", "security", "approval", "tool", "skill")

# Rows are filtered on their attributes in Python (JSON operators differ between SQLite and
# Postgres); the scan per request is bounded so a long window cannot load the whole table.
MAX_SCAN = 5000
MAX_PATH_SPANS = 500


def contract_attributes(span_type: str, attrs: dict[str, Any] | None) -> dict[str, Any]:
    keys = CONTRACT_KEYS.get(span_type, frozenset())
    return {k: v for k, v in (attrs or {}).items() if k in keys}


def _ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((aware(end) - aware(start)).total_seconds() * 1000.0, 3)


def _iso(value: datetime | None) -> str | None:
    return aware(value).isoformat() if value is not None else None


def _row(span: Span, trace: Trace | None) -> dict[str, Any]:
    return {
        "span_id": span.id,
        "trace_id": span.trace_id,
        "session_id": trace.session_id if trace else None,
        "turn_id": trace.turn_id if trace else None,
        "name": span.name,
        "span_type": span.span_type,
        "status": span.status,
        "start_time": _iso(span.start_time),
        "duration_ms": _ms(span.start_time, span.end_time),
        "attributes": contract_attributes(span.span_type, span.attributes),
    }


async def _scan(
    session: AsyncSession,
    span_type: str,
    *,
    hours: int,
    project_id: str,
    session_id: str | None,
) -> list[tuple[Span, Trace]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = (
        select(Span, Trace)
        .join(Trace, Trace.id == Span.trace_id)
        .where(Span.span_type == span_type, Span.start_time >= cutoff, Trace.project_id == project_id)
        .order_by(Span.start_time.desc())
        .limit(MAX_SCAN)
    )
    if session_id:
        query = query.where(Trace.session_id == session_id)
    return [(span, trace) for span, trace in (await session.execute(query)).all()]


def _matches(attrs: dict[str, Any], filters: dict[str, str | None]) -> bool:
    for key, wanted in filters.items():
        if wanted is None:
            continue
        value = attrs.get(key)
        if isinstance(value, list):
            if wanted not in value:
                return False
        elif value != wanted:
            return False
    return True


async def _listing(
    session: AsyncSession,
    span_type: str,
    filters: dict[str, str | None],
    *,
    hours: int,
    project_id: str,
    session_id: str | None,
    limit: int,
) -> dict[str, Any]:
    pairs = await _scan(session, span_type, hours=hours, project_id=project_id, session_id=session_id)
    rows = [_row(span, trace) for span, trace in pairs]
    rows = [row for row in rows if _matches(row["attributes"], filters)]
    return {
        "span_type": span_type,
        "project_id": project_id,
        "hours": hours,
        "total": len(rows),
        "truncated_scan": len(pairs) >= MAX_SCAN,
        "items": rows[:limit],
    }


# Factories, not shared objects: FastAPI binds a Query instance to the first parameter that uses
# it, so one instance reused across parameters silently ignored all but one filter.
def _hours() -> Any:
    return Query(24, ge=1, le=720)


def _project() -> Any:
    return Query("manitos", min_length=1, max_length=128)


def _session() -> Any:
    return Query(None, max_length=255)


def _limit() -> Any:
    return Query(100, ge=1, le=1000)


def _value() -> Any:
    return Query(None, max_length=80)



@router.get("/decisions")
async def list_decisions(
    hours: int = _hours(),
    project_id: str = _project(),
    session_id: str | None = _session(),
    kind: str | None = _value(),
    choice: str | None = _value(),
    final_by: str | None = _value(),
    limit: int = _limit(),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Decisions taken by a model, a rule or a person (intent, tool selection, escalation)."""
    filters = {"decision.kind": kind, "decision.choice": choice, "decision.final_by": final_by}
    return await _listing(
        session, "decision", filters, hours=hours, project_id=project_id,
        session_id=session_id, limit=limit,
    )


@router.get("/denials")
async def list_denials(
    hours: int = _hours(),
    project_id: str = _project(),
    session_id: str | None = _session(),
    outcome: str = Query("denied", pattern="^(denied|asked|allowed)$"),
    reason: str | None = _value(),
    tool: str | None = _value(),
    capability: str | None = _value(),
    limit: int = _limit(),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Security checks; by default the ones that stopped an action."""
    filters = {
        "security.outcome": outcome,
        "security.reason": reason,
        "security.tool": tool,
        "security.capabilities": capability,
    }
    return await _listing(
        session, "security", filters, hours=hours, project_id=project_id,
        session_id=session_id, limit=limit,
    )


@router.get("/approvals")
async def list_approvals(
    hours: int = _hours(),
    project_id: str = _project(),
    session_id: str | None = _session(),
    result: str | None = Query(None, pattern="^(granted|denied|expired)$"),
    by: str | None = Query(None, pattern="^(user|standing|admin)$"),
    limit: int = _limit(),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Answers to permission requests: who answered and how."""
    filters = {"approval.result": result, "approval.by": by}
    return await _listing(
        session, "approval", filters, hours=hours, project_id=project_id,
        session_id=session_id, limit=limit,
    )


@router.get("/skills")
async def list_skills(
    hours: int = _hours(),
    project_id: str = _project(),
    source: str | None = Query(None, pattern="^(built-in|local|import)$"),
    validation: str | None = _value(),
    limit: int = _limit(),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Skill imports and loads, with their hash, declared capabilities and validation."""
    filters = {"skill.source": source, "skill.validation": validation}
    return await _listing(
        session, "skill", filters, hours=hours, project_id=project_id, session_id=None, limit=limit,
    )


@router.get("/summary")
async def governance_summary(
    hours: int = _hours(),
    project_id: str = _project(),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Counts per governance span type and per outcome, for the panel's header."""
    counts: dict[str, Any] = {}
    outcome_key = {
        "decision": "decision.final_by",
        "security": "security.outcome",
        "approval": "approval.result",
        "skill": "skill.validation",
    }
    for span_type in GOVERNANCE_TYPES:
        pairs = await _scan(session, span_type, hours=hours, project_id=project_id, session_id=None)
        by_value: dict[str, int] = {}
        for span, _trace in pairs:
            value = (span.attributes or {}).get(outcome_key[span_type])
            label = str(value) if isinstance(value, (str, int, float, bool)) else "unknown"
            by_value[label] = by_value.get(label, 0) + 1
        counts[span_type] = {"total": len(pairs), "by": dict(sorted(by_value.items()))}
    return {"project_id": project_id, "hours": hours, "counts": counts}


@router.get("/turns/{trace_id}")
async def turn_path(trace_id: str, session: AsyncSession = DbSession) -> dict[str, Any]:
    """A turn's governed path in time order: decision -> security -> approval -> tool -> skill.

    Each step names what it decided (``outcome``) so the panel can draw the chain without reading
    attributes; ``complete`` says whether a tool that was asked for permission got an answer.
    """
    stored = await _resolve_trace_id(session, trace_id)
    trace = await session.get(Trace, stored)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    spans = (
        await session.execute(
            select(Span)
            .where(Span.trace_id == stored, Span.span_type.in_(PATH_TYPES))
            .order_by(Span.start_time, Span.id)
            .limit(MAX_PATH_SPANS)
        )
    ).scalars().all()
    steps = []
    for span in spans:
        row = _row(span, trace)
        row["outcome"] = _outcome(span.span_type, row["attributes"], span.status)
        steps.append(row)
    asked = {
        s["attributes"].get("security.tool")
        for s in steps
        if s["span_type"] == "security" and s["attributes"].get("security.outcome") == "asked"
    }
    answered = {
        s["attributes"].get("approval.subject") for s in steps if s["span_type"] == "approval"
    }
    return {
        "trace_id": trace.id,
        "session_id": trace.session_id,
        "turn_id": trace.turn_id,
        "status": trace.status,
        "steps": steps,
        "unanswered_asks": sorted(t for t in asked - answered if t),
        "complete": not (asked - answered),
    }


def _outcome(span_type: str, attrs: dict[str, Any], status: str) -> str | None:
    if span_type == "decision":
        return attrs.get("decision.choice")
    if span_type == "security":
        return attrs.get("security.outcome")
    if span_type == "approval":
        return attrs.get("approval.result")
    if span_type == "skill":
        return attrs.get("skill.validation")
    return status
