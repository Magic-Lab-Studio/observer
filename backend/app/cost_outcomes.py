"""Cost per outcome, not per token (roadmap step 13a; no migration).

Every span that carries a cost falls in exactly one bucket, so the buckets add up to the total and
nothing is left out silently:

* ``error``: the span itself ended in error;
* ``repeated``: a repeated tool call after the first one of its group (``app.loop_detection``);
* ``failed``: the turn did not answer (failed, cancelled, refused, abstained, clarification);
* ``unknown``: the turn's outcome is unknown (no evidence either way);
* ``useful``: the rest (answered or degraded turns).

``waste_share`` is ``(error + repeated + failed) / total``. ``cost_per_answered`` divides all the
spend of the window by the turns that answered, so failed work raises it. ``concentration`` groups
the cost by user (``user.id`` on a span, else the trace's ``actor_id_hash``) and gives the share of
the top 5% of users (at least one); cost no user can be named for is reported apart. ``routing``
gives spend and outcomes per model with how the model is priced (``priced``, ``free``, ``local``,
``unpriced``): a local model costs 0 here; an hourly cost for owned capacity is step 13b.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cost import classify, model_of
from app.loop_detection import repeated_calls
from app.metrics_core import load_signals_for
from app.models.trace import Span, Trace

BUCKETS = ("useful", "failed", "error", "repeated", "unknown")
WASTE = ("failed", "error", "repeated")
NOT_ANSWERED = {"failed", "cancelled", "refused", "abstained", "clarification"}
MAX_TRACES = 5000


def bucket_of(span: Any, outcome: str, repeated_ids: set[str]) -> str:
    """The one bucket a costed span belongs to (pure)."""
    if span.status == "error":
        return "error"
    if str(span.id) in repeated_ids:
        return "repeated"
    if outcome in NOT_ANSWERED:
        return "failed"
    if outcome in ("answered", "degraded"):
        return "useful"
    return "unknown"


def top_share(costs: dict[str, float], share: float = 0.05) -> tuple[float | None, int]:
    """Share of the cost spent by the top ``share`` of users (at least one), and how many."""
    total = sum(costs.values())
    if not costs or total <= 0:
        return None, 0
    top = max(1, math.ceil(len(costs) * share))
    spent = sum(sorted(costs.values(), reverse=True)[:top])
    return spent / total, top


def _repeated_ids(spans: list[Any]) -> set[str]:
    """Ids of every repeated call except the first of its group."""
    out: set[str] = set()
    for group in repeated_calls(spans):
        out.update(group["span_ids"][1:])
    return out


async def summary(
    session: AsyncSession,
    *,
    project_id: str | None,
    since: datetime,
    environment: str | None = None,
    max_traces: int = MAX_TRACES,
) -> dict[str, Any]:
    """Spend of the window's traces by bucket, per answered turn, per user and per model."""
    query = select(Trace).where(Trace.created_at >= since)
    if project_id:
        query = query.where(Trace.project_id == project_id)
    if environment:
        query = query.where(Trace.environment == environment)
    traces = list(
        (await session.execute(query.order_by(Trace.created_at.desc()).limit(max_traces))).scalars()
    )
    ids = [str(t.id) for t in traces]
    signals = {s.trace_id: s for s in await load_signals_for(session, ids)} if ids else {}
    spans_by_trace: dict[str, list[Any]] = defaultdict(list)
    for i in range(0, len(ids), 500):
        rows = await session.execute(select(Span).where(Span.trace_id.in_(ids[i : i + 500])))
        for span in rows.scalars():
            spans_by_trace[str(span.trace_id)].append(span)

    buckets = dict.fromkeys(BUCKETS, 0.0)
    outcomes: dict[str, int] = defaultdict(int)
    by_user: dict[str, float] = defaultdict(float)
    unattributed = 0.0
    models: dict[str, dict[str, Any]] = {}
    costed_spans = 0
    for trace in traces:
        tid = str(trace.id)
        outcome = signals[tid].outcome if tid in signals else "unknown"
        outcomes[outcome] += 1
        spans = spans_by_trace.get(tid, [])
        repeated = _repeated_ids(spans)
        user = next(
            (
                str(s.attributes["user.id"])
                for s in spans
                if isinstance(s.attributes, dict) and s.attributes.get("user.id")
            ),
            str(trace.actor_id_hash or ""),
        )
        trace_cost = 0.0
        for span in spans:
            if span.cost_usd is None:
                continue
            cost = float(span.cost_usd)
            costed_spans += 1
            trace_cost += cost
            buckets[bucket_of(span, outcome, repeated)] += cost
            model = model_of(span.attributes if isinstance(span.attributes, dict) else None)
            if model:
                entry = models.setdefault(
                    model,
                    {"model": model, "pricing": classify(model)["status"], "cost_usd": 0.0,
                     "spans": 0, "outcomes": defaultdict(int), "_traces": set()},
                )
                entry["cost_usd"] += cost
                entry["spans"] += 1
                if tid not in entry["_traces"]:
                    entry["_traces"].add(tid)
                    entry["outcomes"][outcome] += 1
        if user:
            by_user[user] += trace_cost
        else:
            unattributed += trace_cost

    total = sum(buckets.values())
    answered = outcomes.get("answered", 0)
    share, top_users = top_share(by_user)
    routing = sorted(
        (
            {k: (dict(v) if k == "outcomes" else round(v, 6) if k == "cost_usd" else v)
             for k, v in e.items() if k != "_traces"}
            for e in models.values()
        ),
        key=lambda e: -e["cost_usd"],
    )
    return {
        "project_id": project_id,
        "since": since.isoformat(),
        "traces": len(traces),
        "truncated": len(traces) >= max_traces,
        "costed_spans": costed_spans,
        "total_usd": round(total, 6),
        "buckets": {k: round(v, 6) for k, v in buckets.items()},
        "waste_share": round(sum(buckets[k] for k in WASTE) / total, 4) if total > 0 else None,
        "outcomes": dict(outcomes),
        "answered_turns": answered,
        "cost_per_answered": round(total / answered, 6) if answered and total > 0 else None,
        "concentration": {
            "users": len(by_user),
            "top_users": top_users,
            "top5_share": round(share, 4) if share is not None else None,
            "unattributed_usd": round(unattributed, 6),
        },
        "routing": routing,
    }


async def alert_value(
    session: AsyncSession,
    metric: str,
    *,
    project_id: str | None,
    environment: str | None,
    since: datetime,
    window_minutes: int,
) -> tuple[float | None, int]:
    """``spend_per_day``, ``cost_per_outcome`` or ``waste_share`` over the window; samples are
    the costed spans (``(None, 0)`` when nothing in the window had a cost)."""
    data = await summary(session, project_id=project_id, since=since, environment=environment)
    samples = int(data["costed_spans"])
    if not samples:
        return None, 0
    if metric == "spend_per_day":
        return data["total_usd"] * 1440.0 / max(1, window_minutes), samples
    if metric == "cost_per_outcome":
        return data["cost_per_answered"], samples
    if metric == "waste_share":
        return data["waste_share"], samples
    return None, 0
