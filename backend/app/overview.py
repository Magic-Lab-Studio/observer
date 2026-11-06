"""The Overview's distributions computed on the server (evaluation B5 / C3).

The dashboard used to compute the activity buckets, the latency histogram, the slowest traces and
the stage timings from its last 1 000 traces, so a busy 7-day range was a sample. This computes the
same numbers from every trace whose start falls in the range (capped at ``ROW_LIMIT`` rows, and the
response says when it was), with the dashboard's own rules: a trace is placed by its start time,
its duration is end minus start, percentiles are nearest-rank, and the histogram edges are the
dashboard's ``LATENCY_EDGES``. Only scalar columns and the trace's ``stage_timings_ms`` are read,
never a span's input or output.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.metrics_core import aware, percentile

LATENCY_EDGES_MS = (1000, 2000, 5000, 10000, 20000, 40000)  # dashboard helpers/stats.ts
SLOWEST = 6
STAGES = 8
_STAGE_PREFIX = re.compile(r"^(text|audio)\.")


def stage_label(key: str) -> str:
    return _STAGE_PREFIX.sub("", key)


def _duration_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    d = (aware(end) - aware(start)).total_seconds() * 1000.0
    return d if d >= 0 else None


def histogram(durations: list[float]) -> list[dict[str, float | int]]:
    counts = [0] * (len(LATENCY_EDGES_MS) + 1)
    for d in durations:
        i = next((k for k, edge in enumerate(LATENCY_EDGES_MS) if d < edge), len(LATENCY_EDGES_MS))
        counts[i] += 1
    return [
        {"from": 0 if i == 0 else LATENCY_EDGES_MS[i - 1], "count": c} for i, c in enumerate(counts)
    ]


def summarize(
    rows: list[Any], *, start: datetime, end: datetime, buckets: int, truncated: bool
) -> dict[str, Any]:
    """``rows``: ``(id, name, turn_id, status, start_time, end_time, metadata)`` of the traces
    whose start is in ``[start, end]``."""
    start, end = aware(start), aware(end)
    span_ms = max(1.0, (end - start).total_seconds() * 1000.0)
    size = span_ms / buckets
    groups: list[list[tuple[float | None, bool]]] = [[] for _ in range(buckets)]
    durations: list[float] = []
    slowest: list[tuple[float, Any]] = []
    stages: dict[str, list[float]] = defaultdict(list)
    errors = 0
    for row in rows:
        tid, name, turn_id, status, t_start, t_end, meta = row
        at = (aware(t_start) - start).total_seconds() * 1000.0
        d = _duration_ms(t_start, t_end)
        is_error = status == "error"
        errors += is_error
        i = min(buckets - 1, math.floor(at / size))
        if i >= 0:
            groups[i].append((d, is_error))
        if d is not None:
            durations.append(d)
            slowest.append((d, (tid, name, turn_id, status)))
        timings = meta.get("stage_timings_ms") if isinstance(meta, dict) else None
        if isinstance(timings, dict):
            for key, value in timings.items():
                number = isinstance(value, (int, float)) and not isinstance(value, bool)
                if number and math.isfinite(value):
                    stages[stage_label(str(key))].append(float(value))
    out_buckets = []
    for i, group in enumerate(groups):
        ds = [d for d, _ in group if d is not None]
        err = sum(1 for _, e in group if e)
        out_buckets.append(
            {
                "t": int((start.timestamp() * 1000.0) + i * size),
                "total": len(group),
                "err": err,
                "p50": percentile(ds, 0.5),
                "p95": percentile(ds, 0.95),
            }
        )
    slowest.sort(key=lambda x: x[0], reverse=True)
    stage_rows = sorted(
        (
            {"stage": k, "avg": sum(v) / len(v), "p95": percentile(v, 0.95) or 0.0, "n": len(v)}
            for k, v in stages.items()
        ),
        key=lambda r: -r["avg"],
    )[:STAGES]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "bucket_ms": size,
        "buckets": out_buckets,
        "total": len(rows),
        "errors": errors,
        "durations": {
            "count": len(durations),
            "p50": percentile(durations, 0.5),
            "p95": percentile(durations, 0.95),
            "max": max(durations) if durations else None,
        },
        "histogram": histogram(durations),
        "slowest": [
            {"id": str(tid), "name": name, "turn_id": turn_id, "status": status, "duration_ms": d}
            for d, (tid, name, turn_id, status) in slowest[:SLOWEST]
        ],
        "stages": stage_rows,
        "samples": len(rows),
        "truncated": truncated,
    }
