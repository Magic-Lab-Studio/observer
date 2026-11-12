"""Gate funnel and critical path (roadmap step 14).

**Gate funnel**: from the ``gate`` spans of the agent contract (``observer.agent.v1``), how many
turns each gate saw, passed, skipped and stopped, and the reason codes of its stops. Gates are
listed by how many turns they stopped, so an over-strict gate comes first; ``position`` is where the
gate usually sits in a turn (the median of its rank among the turn's gates).

**Critical path** of one trace: language-model calls that ran one after another under the same
parent. A later call that does not declare ``observer.depends_on`` an earlier call of the same run
could have run in parallel with it; the result lists those runs with their share of the trace's
latency. A call that declares the dependency is not flagged: it had to wait.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any

from app.metrics_core import TurnSignals

DEPENDS_ON = "observer.depends_on"


def gate_funnel(turns: list[TurnSignals]) -> list[dict[str, Any]]:
    """Per gate: turns seen, passed, skipped, stopped, stop reason codes and its usual position."""
    seen: Counter[str] = Counter()
    status: dict[str, Counter[str]] = defaultdict(Counter)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    positions: dict[str, list[int]] = defaultdict(list)
    for turn in turns:
        ordered = sorted(turn.gates, key=lambda g: g.get("order", 0.0))
        for rank, gate in enumerate(ordered):
            name = gate.get("name") or gate.get("stage") or "?"
            seen[name] += 1
            state = gate.get("status") or "?"
            status[name][state] += 1
            positions[name].append(rank)
            if state == "stop":
                for code in gate.get("reason_codes") or []:
                    reasons[name][code] += 1
    out = [
        {
            "gate": name,
            "turns": seen[name],
            "passed": status[name]["pass"],
            "skipped": status[name]["skip"],
            "stopped": status[name]["stop"],
            "stop_rate": round(status[name]["stop"] / seen[name], 4),
            "reason_codes": [{"code": c, "count": n} for c, n in reasons[name].most_common(10)],
            "position": median(positions[name]),
        }
        for name in seen
    ]
    return sorted(out, key=lambda g: (-g["stopped"], g["position"], g["gate"]))


def _ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return float(value)


def _depends(span: Any) -> set[str]:
    attrs = span.attributes if isinstance(span.attributes, dict) else {}
    raw = attrs.get(DEPENDS_ON)
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, (list, tuple)):
        return {str(x) for x in raw}
    return set()


def critical_path(spans: list[Any], trace_ms: float | None = None) -> dict[str, Any]:
    """Sequential language-model calls of one trace and the runs that could have been parallel."""
    llm = [s for s in spans if s.span_type == "llm" and _ts(s.start_time) is not None]
    starts = [_ts(s.start_time) for s in spans if _ts(s.start_time) is not None]
    ends = [_ts(s.end_time) for s in spans if _ts(s.end_time) is not None]
    if trace_ms is None and starts and ends:
        trace_ms = (max(ends) - min(starts)) * 1000
    by_parent: dict[Any, list[Any]] = defaultdict(list)
    for span in llm:
        by_parent[span.parent_span_id].append(span)
    sequential = 0
    runs: list[dict[str, Any]] = []
    for siblings in by_parent.values():
        siblings.sort(key=lambda s: _ts(s.start_time) or 0.0)
        run: list[Any] = []
        for span in siblings:
            prev = run[-1] if run else None
            prev_end = _ts(prev.end_time) if prev is not None else None
            starts_after = prev_end is not None and (_ts(span.start_time) or 0.0) >= prev_end
            if prev is not None and starts_after:
                sequential += 1
                if not (_depends(span) & {str(s.id) for s in run}):
                    run.append(span)
                    continue
                _close(run, runs, trace_ms)
                run = [span]  # it waited for the earlier call: a new run starts here
                continue
            _close(run, runs, trace_ms)
            run = [span]
        _close(run, runs, trace_ms)
    return {
        "llm_calls": len(llm),
        "sequential_calls": sequential,
        "trace_ms": round(trace_ms, 1) if trace_ms is not None else None,
        "parallelisable": sorted(runs, key=lambda r: -r["duration_ms"]),
    }


def _close(run: list[Any], runs: list[dict[str, Any]], trace_ms: float | None) -> None:
    """Keep a run of at least two independent sequential calls."""
    if len(run) < 2:
        return
    duration = sum(((_ts(s.end_time) or 0.0) - (_ts(s.start_time) or 0.0)) * 1000 for s in run)
    runs.append(
        {
            "span_ids": [str(s.id) for s in run],
            "names": [str(s.name) for s in run],
            "calls": len(run),
            "duration_ms": round(duration, 1),
            "latency_share": round(duration / trace_ms, 4) if trace_ms else None,
        }
    )
