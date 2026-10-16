"""Automatic rule-based evaluation of Manitos turns.

A periodic sweep (``OBSERVATORY_AUTO_EVAL_INTERVAL_SEC``, 60 s) scores recent turns that have no
evaluation yet. A sweep rather than a hook on ingestion: the ingest handler broadcasts before its
transaction commits, and a sweep also backfills history. Evaluations are idempotent: the id is
``uuid5(trace_id, rubric)``, so reprocessing a turn updates the same row instead of duplicating it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evaluators.manitos_turn import LEGACY_RUBRICS, RUBRIC, score_turn
from app.metrics_core import load_signals_for
from app.models.evaluation import Evaluation
from app.models.trace import Span, Trace
from app.websocket import manager

logger = logging.getLogger("observatory.auto_eval")

_NAMESPACE = uuid.UUID("6f0c2b52-0c1e-4a5e-9d5e-4f7a1c0a0b11")
EVALUATOR_TYPE = "rule_based"
BATCH = 200
_MAX_PAGES = 25  # bounds one sweep to ~20 000 turns of look-back with the default batch
_BROADCAST_CAP = 20


def auto_eval_enabled() -> bool:
    return os.getenv("OBSERVATORY_AUTO_EVAL", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def evaluation_id(trace_id: str, rubric: str = RUBRIC) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{trace_id}:{rubric}"))


async def evaluate_new_turns(
    session: AsyncSession,
    *,
    project_id: str = "manitos",
    days: float = 7.0,
    batch: int = BATCH,
    now: datetime | None = None,
) -> int:
    """Score up to ``batch`` recent turns without an evaluation; returns how many were created."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    # Walk the window newest-first in pages until a batch of unevaluated turns is found. Looking
    # only at the newest few turns left everything older unscored forever once those were done.
    todo: list[str] = []
    page = max(batch * 4, 200)
    for offset in range(0, page * _MAX_PAGES, page):
        ids = [
            str(tid)
            for tid in (
                await session.execute(
                    select(Trace.id)
                    .where(
                        Trace.project_id == project_id,
                        Trace.created_at >= since,
                        # Only turns that can be scored: ones still waiting for their lifecycle
                        # span must not fill the batch and starve the older, eligible ones.
                        exists().where(
                            Span.trace_id == Trace.id, Span.name == "manitos.turn.lifecycle"
                        ),
                    )
                    .order_by(Trace.created_at.desc())
                    .limit(page)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        ]
        if not ids:
            break
        by_eval = {evaluation_id(tid): tid for tid in ids}
        existing = set(
            (await session.execute(select(Evaluation.id).where(Evaluation.id.in_(list(by_eval)))))
            .scalars()
            .all()
        )
        todo += [tid for eid, tid in by_eval.items() if eid not in existing]
        if len(todo) >= batch or len(ids) < page:
            break
    todo = todo[:batch]
    if not todo:
        return 0
    created: list[Evaluation] = []
    for signals in await load_signals_for(session, todo):
        if "manitos.turn.lifecycle" not in signals.seen_spans:
            continue  # the turn's own signals have not arrived yet; the next pass retries
        scored = score_turn(signals)
        evaluation = Evaluation(
            id=evaluation_id(signals.trace_id),
            trace_id=signals.trace_id,
            evaluator_type=EVALUATOR_TYPE,
            score=scored["score"],
            criteria={"rubric": RUBRIC, "criteria": [c["name"] for c in scored["criteria"]]},
            result=scored,
        )
        session.add(evaluation)
        created.append(evaluation)
    if created:
        # A rubric bump replaces earlier scores of the same turns (they are recomputable).
        legacy = [evaluation_id(e.trace_id, r) for e in created for r in LEGACY_RUBRICS]
        await session.execute(delete(Evaluation).where(Evaluation.id.in_(legacy)))
    await session.commit()
    for evaluation in created[:_BROADCAST_CAP]:
        try:
            await manager.broadcast(
                {
                    "type": "new_evaluation",
                    "data": {
                        "id": evaluation.id,
                        "trace_id": evaluation.trace_id,
                        "evaluator_type": EVALUATOR_TYPE,
                        "score": float(evaluation.score) if evaluation.score is not None else None,
                    },
                }
            )
        except Exception:
            logger.debug("evaluation broadcast failed", exc_info=True)
    return len(created)


async def auto_eval_loop(factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop (same shape as ``retention_loop``); the session factory is injected."""
    if not auto_eval_enabled():
        return
    interval = _float_env("OBSERVATORY_AUTO_EVAL_INTERVAL_SEC", 60.0, 5.0)
    days = _float_env("OBSERVATORY_AUTO_EVAL_DAYS", 7.0, 0.1)
    while True:
        try:
            async with factory() as session:
                count = await evaluate_new_turns(session, days=days)
            if count:
                logger.info("auto-eval: scored %d turns", count)
        except Exception:
            logger.warning("auto-eval pass failed", exc_info=True)
        await asyncio.sleep(interval)
