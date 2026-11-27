"""Cost per merged change (roadmap step 13b).

A trace names the change it worked on in ``vcs.change`` (a pull request), or the commit in
``vcs.commit``, on the trace metadata or on any span. A CI hook reports when a change merged or was
closed unmerged (``vcs_events``). Nothing is dropped silently: spend on abandoned changes is charged
to the merged ones (it was the price of shipping them), spend on changes still open and spend with
no change are reported apart.

Rework: a merged change whose commit produced a dataset run (``version.commit``) is checked case
by case against the run of the same dataset just before it; cases that went from pass to fail are
the change's rework. Merged changes with no run at their commit are counted as not checked, not as
clean.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.case_check import aggregate_repetitions, compare_results
from app.cost import model_of
from app.cost_outcomes import MAX_TRACES, _hours, load_config, owned_rate
from app.models import Span, Trace
from app.models.datasets import Dataset
from app.models.runs import DatasetRun, RunResult
from app.models.vcs import VcsEvent

TOP_CHANGES = 20


def _attr(trace: Trace, spans: list[Any], key: str) -> str:
    meta = trace.meta if isinstance(trace.meta, dict) else {}
    if meta.get(key):
        return str(meta[key])[:128]
    for span in spans:
        attrs = span.attributes if isinstance(span.attributes, dict) else {}
        if attrs.get(key):
            return str(attrs[key])[:128]
    return ""


def _same_commit(a: str, b: str) -> bool:
    """A short and a full hash of one commit match (7 characters at least)."""
    a, b = a.strip().lower(), b.strip().lower()
    return len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a))


async def _case_statuses(session: AsyncSession, run_id: str) -> dict[str, dict[str, Any]]:
    rows = (await session.execute(select(RunResult).where(RunResult.run_id == run_id))).scalars()
    return aggregate_repetitions(
        [
            {
                "item_id": r.item_id,
                "status": r.status,
                "health_score": r.health_score,
                "duration_ms": r.duration_ms,
            }
            for r in rows
        ]
    )


async def rework(
    session: AsyncSession, *, project_id: str, merged: dict[str, str]
) -> dict[str, Any]:
    """Cases that regressed at each merged change's commit (``merged``: change -> commit)."""
    runs = list(
        (
            await session.execute(
                select(DatasetRun)
                .join(Dataset, Dataset.id == DatasetRun.dataset_id)
                .where(Dataset.project_id == project_id)
                .order_by(DatasetRun.created_at)
            )
        ).scalars()
    )
    checked, per_change = 0, []
    for change, commit in sorted(merged.items()):
        at_commit = [
            r
            for r in runs
            if commit and _same_commit(str((r.version or {}).get("commit") or ""), commit)
        ]
        if not at_commit:
            continue
        checked += 1
        regressed: list[str] = []
        compared = 0
        for run in at_commit:
            base = next(
                (
                    b
                    for b in reversed(runs)
                    if b.dataset_id == run.dataset_id
                    and b.created_at < run.created_at
                    and b.id != run.id
                ),
                None,
            )
            if base is None:
                continue
            compared += 1
            diff = compare_results(
                await _case_statuses(session, base.id), await _case_statuses(session, run.id)
            )
            regressed += [c["item_id"] for c in diff["cases"] if c["change"] == "regressed"]
        per_change.append(
            {
                "change": change,
                "commit": commit,
                "runs": len(at_commit),
                "compared": compared,
                "regressed_cases": len(regressed),
                "regressed_items": sorted(set(regressed))[:20],
            }
        )
    return {
        "changes_checked": checked,
        "changes_not_checked": len(merged) - checked,
        "changes_with_regressions": sum(1 for c in per_change if c["regressed_cases"]),
        "regressed_cases": sum(c["regressed_cases"] for c in per_change),
        "changes": sorted(per_change, key=lambda c: -c["regressed_cases"])[:TOP_CHANGES],
    }


def span_cost(span: Any, cfg: Any) -> float | None:
    """What a span cost: its price, or a local call's duration at the project's hourly rate."""
    if span.cost_usd is not None:
        return float(span.cost_usd)
    attrs = span.attributes if isinstance(span.attributes, dict) else None
    rate = owned_rate(span, model_of(attrs), cfg)
    return rate * _hours(span) if rate is not None else None


async def summary(
    session: AsyncSession, *, project_id: str, since: datetime, max_traces: int = MAX_TRACES
) -> dict[str, Any]:
    cfg = await load_config(session, project_id)
    traces = list(
        (
            await session.execute(
                select(Trace)
                .where(Trace.created_at >= since, Trace.project_id == project_id)
                .order_by(Trace.created_at.desc())
                .limit(max_traces)
            )
        ).scalars()
    )
    ids = [str(t.id) for t in traces]
    spans_by_trace: dict[str, list[Any]] = defaultdict(list)
    for i in range(0, len(ids), 500):
        rows = await session.execute(select(Span).where(Span.trace_id.in_(ids[i : i + 500])))
        for span in rows.scalars():
            spans_by_trace[str(span.trace_id)].append(span)

    events = list(
        (await session.execute(select(VcsEvent).where(VcsEvent.project_id == project_id))).scalars()
    )
    merged = {e.change for e in events if e.event == "merged"}
    closed = {e.change for e in events if e.event == "closed"} - merged
    by_commit = {e.commit: e.change for e in events if e.commit}

    per_change: dict[str, dict[str, Any]] = {}
    unattributed = 0.0
    for trace in traces:
        spans = spans_by_trace.get(str(trace.id), [])
        cost = sum(c for c in (span_cost(s, cfg) for s in spans) if c is not None)
        change = _attr(trace, spans, "vcs.change")
        commit = _attr(trace, spans, "vcs.commit")
        change = change or by_commit.get(commit, "") or (f"commit:{commit}" if commit else "")
        if not change:
            unattributed += cost
            continue
        entry = per_change.setdefault(change, {"change": change, "cost_usd": 0.0, "traces": 0})
        entry["cost_usd"] += cost
        entry["traces"] += 1

    def status(change: str) -> str:
        return "merged" if change in merged else "abandoned" if change in closed else "open"

    groups: dict[str, dict[str, float]] = {
        k: {"changes": 0, "cost_usd": 0.0} for k in ("merged", "abandoned", "open")
    }
    for change, entry in per_change.items():
        entry["status"] = status(change)
        groups[entry["status"]]["changes"] += 1
        groups[entry["status"]]["cost_usd"] += entry["cost_usd"]
    # A merged change with no traced work still counts as shipped.
    groups["merged"]["changes"] += len(merged - set(per_change))
    shipped = groups["merged"]["changes"]
    charged = groups["merged"]["cost_usd"] + groups["abandoned"]["cost_usd"]
    total = charged + groups["open"]["cost_usd"] + unattributed
    merged_commits = {c: "" for c in merged}
    for e in events:
        if e.event == "merged" and e.commit:
            merged_commits[e.change] = e.commit
    return {
        "project_id": project_id,
        "since": since.isoformat(),
        "traces": len(traces),
        "truncated": len(traces) >= max_traces,
        "total_usd": round(total, 6),
        "merged": {k: round(v, 6) for k, v in groups["merged"].items()},
        "abandoned": {k: round(v, 6) for k, v in groups["abandoned"].items()},
        "open": {k: round(v, 6) for k, v in groups["open"].items()},
        "unattributed_usd": round(unattributed, 6),
        "cost_per_merged_change": round(charged / shipped, 6) if shipped else None,
        "changes": [
            {**e, "cost_usd": round(e["cost_usd"], 6)}
            for e in sorted(per_change.values(), key=lambda e: -e["cost_usd"])[:TOP_CHANGES]
        ],
        "rework": await rework(session, project_id=project_id, merged=merged_commits),
    }
