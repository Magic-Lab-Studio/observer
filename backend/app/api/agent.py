"""Agent views (``/v1/agent``): how each turn ended and why, from the agent span contract.

``GET /turns`` answers "why did the agent not answer?" for a project without reading content:
each turn's outcome (``answered``, ``abstained``, ...), where it was read from, the reason code
and whether an abstention had retrieved evidence. ``GET /contract`` publishes the contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from app import agent_contract
from app.critical_path import gate_funnel
from app.db import DbSession
from app.metrics_core import ROW_LIMIT, load_turn_signals
from app.outcomes import turn_outcome

router = APIRouter()

_OUTCOME_FILTER = "^(" + "|".join((*agent_contract.OUTCOMES, agent_contract.UNKNOWN)) + ")$"


@router.get("/contract")
async def contract() -> dict[str, Any]:
    return agent_contract.describe()


@router.get("/funnel")
async def funnel(
    project_id: str = Query(..., min_length=1, max_length=128),
    hours: float = Query(24, gt=0, le=24 * 90),
    environment: str | None = Query(None, max_length=64),
    session=DbSession,
) -> dict[str, Any]:
    """Gate funnel (roadmap step 14): per gate, the turns it saw, passed, skipped and stopped, and
    the reason codes of its stops; the gate that stops most turns first."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    turns = await load_turn_signals(
        session, project_id=project_id, since=since, environment=environment
    )
    gated = [t for t in turns if t.gates]
    return {
        "project_id": project_id,
        "hours": hours,
        "turns": len(turns),
        "turns_with_gates": len(gated),
        "truncated": len(turns) >= ROW_LIMIT,
        "gates": gate_funnel(gated),
    }


@router.get("/turns")
async def list_turns(
    project_id: str = Query(..., min_length=1, max_length=128),
    hours: float = Query(24, gt=0, le=24 * 90),
    outcome: str | None = Query(None, pattern=_OUTCOME_FILTER),
    environment: str | None = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=1000),
    session=DbSession,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    turns = await load_turn_signals(
        session, project_id=project_id, since=since, environment=environment
    )
    counts: dict[str, int] = {}
    for s in turns:
        counts[s.outcome] = counts.get(s.outcome, 0) + 1
    picked = [s for s in turns if outcome is None or s.outcome == outcome]
    return {
        "project_id": project_id,
        "hours": hours,
        "total": len(turns),
        # the newest ROW_LIMIT turns of the window were read; counts cover only those
        "truncated": len(turns) >= ROW_LIMIT,
        "counts": counts,
        "items": [
            {
                "trace_id": s.trace_id,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "model": s.answered_by or s.model,
                "duration_ms": s.duration_ms,
                "status": s.status,
                **turn_outcome(s),
            }
            for s in picked[:limit]
        ],
    }
