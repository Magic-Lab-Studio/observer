"""A trace's status, interval and name, derived from every span stored for it.

Spans of one trace arrive in several batches and in any order (a child finishes, and is exported,
before its parent). Deriving the summary from the first batch left a trace green after a later
error, named it after whichever span arrived first and ended it at the last span of that batch.

Rules, applied after each batch to all stored spans:

* **Roots** are spans without a parent. A span whose parent has not arrived yet is not a root.
* **Status**: if any root is ``error`` the trace is ``error``. If every root has ended, the trace
  is ``ok``: an error in a child that the root survived (a failed attempt that was retried, a tool
  error the agent handled) stays visible at span level but does not fail the operation. Without
  an ended root, any ``error`` span makes the trace ``error``, otherwise it is ``unset``
  (running or incomplete). ``unset`` is never counted as an error.
* **Interval**: start is the earliest span start; once every root has ended, end is the latest
  end of any span (a child that outlives its root, such as detached background work, extends
  the trace), else unknown. Wall-clock time: parallel children are never summed.
* **Name**: the earliest root's name; without a root, the earliest span's name.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trace import Span, Trace


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def refresh_trace_summary(session: AsyncSession, trace: Trace) -> None:
    await session.flush()
    rows = (
        await session.execute(
            select(
                Span.id, Span.parent_span_id, Span.name, Span.start_time, Span.end_time,
                Span.status,
            ).where(Span.trace_id == trace.id)
        )
    ).all()
    if not rows:
        return
    roots = sorted((r for r in rows if r.parent_span_id is None), key=lambda r: _utc(r.start_time))
    trace.start_time = min(_utc(r.start_time) for r in rows)
    if roots:
        trace.name = roots[0].name
    else:
        trace.name = min(rows, key=lambda r: _utc(r.start_time)).name

    roots_ended = bool(roots) and all(r.end_time is not None for r in roots)
    if any(r.status == "error" for r in roots):
        trace.status = "error"
    elif roots_ended:
        trace.status = "ok"
    elif any(r.status == "error" for r in rows):
        trace.status = "error"
    else:
        trace.status = "unset"
    ends = [_utc(r.end_time) for r in rows if r.end_time is not None]
    trace.end_time = max(ends) if roots_ended else None
