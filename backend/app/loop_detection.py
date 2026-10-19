"""Repeated tool calls in one trace (evaluation 2026-10-18, P6).

A deterministic heuristic, no model involved: calls are grouped by parent span, tool name and
normalized arguments (JSON with sorted keys). A group of at least ``min_repeats`` calls
(``OBSERVATORY_LOOP_MIN_REPEATS``, default 5) is reported as a warning. ``same_output`` says
every call returned the same output (no visible progress), the stronger signal; legitimate
polling or iteration over a list also repeats, so this never fails a trace by itself. In
``metadata`` capture mode inputs are not stored and calls group by name only
(``arguments_known: false``).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from typing import Any


def _digest(value: Any) -> str | None:
    if value is None:
        return None
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        text = str(value)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def min_repeats() -> int:
    try:
        return max(2, int(os.getenv("OBSERVATORY_LOOP_MIN_REPEATS", "5")))
    except ValueError:
        return 5


def repeated_calls(spans: list[Any], threshold: int | None = None) -> list[dict[str, Any]]:
    """Groups of repeated tool calls among ``spans`` (ORM spans or objects with the same fields)."""
    limit = threshold or min_repeats()
    groups: dict[tuple, list[Any]] = defaultdict(list)
    for span in spans:
        if span.span_type != "tool":
            continue
        key = (span.parent_span_id, span.name, _digest(span.inp))
        groups[key].append(span)
    found = []
    for (parent, name, args), calls in groups.items():
        if len(calls) < limit:
            continue
        calls.sort(key=lambda s: s.start_time)
        outputs = {_digest(s.out) for s in calls}
        first, last = calls[0].start_time, calls[-1].end_time or calls[-1].start_time
        found.append(
            {
                "tool": name,
                "parent_span_id": parent,
                "count": len(calls),
                "errors": sum(1 for s in calls if s.status == "error"),
                "arguments_known": args is not None,
                "same_output": len(outputs) == 1 and None not in outputs,
                "span_ids": [str(s.id) for s in calls][:50],
                "window_ms": (last - first).total_seconds() * 1000 if first and last else None,
                "severity": "warning",
            }
        )
    return sorted(found, key=lambda g: -g["count"])
