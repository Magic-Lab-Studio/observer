"""Capture privacy per project (``/v1/privacy``): the mode, a report of what is stored, enforcement.

The report answers "what did this project actually store?" from the latest spans, so a mode change
can be checked rather than trusted.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capture import (
    MODES,
    apply_capture,
    content_like_keys,
    default_mode,
    has_sensitive_patterns,
)
from app.db import DbSession
from app.models.settings import ProjectSetting
from app.models.trace import Span, Trace

router = APIRouter()

AUDIT_SAMPLE = 2000
MAX_PROJECTS = 50
ENFORCE_BATCH = 500

Mode = Literal["metadata", "redacted", "full"]


class ModeIn(BaseModel):
    capture_mode: Mode


class ConfirmIn(BaseModel):
    confirm: bool = False


async def audit_project(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """What the latest spans of a project actually hold."""
    rows = (
        await session.execute(
            select(Span.inp, Span.out, Span.attributes, Span.meta)
            .join(Trace, Trace.id == Span.trace_id)
            .where(Trace.project_id == project_id)
            .order_by(Span.start_time.desc())
            .limit(AUDIT_SAMPLE)
        )
    ).all()
    with_input = sum(1 for r in rows if r[0])
    with_output = sum(1 for r in rows if r[1])
    return {
        "sampled_spans": len(rows),
        "with_input": with_input,
        "with_output": with_output,
        "with_content_like_attributes": sum(1 for r in rows if content_like_keys(r[2])),
        "with_sensitive_patterns": sum(1 for r in rows if has_sensitive_patterns(*r)),
    }


def _entry(project_id: str, setting: str | None) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "capture_mode": setting,
        "source": "setting" if setting else "default",
        "default_mode": default_mode("full"),
    }


@router.get("/projects")
async def list_projects(session: AsyncSession = DbSession) -> dict[str, Any]:
    """Every known project with its capture mode and what its latest spans hold."""
    settings = {
        pid: mode
        for pid, mode in (
            await session.execute(select(ProjectSetting.project_id, ProjectSetting.capture_mode))
        ).all()
    }
    seen = (
        (await session.execute(select(Trace.project_id).where(Trace.project_id.is_not(None))))
        .scalars()
        .all()
    )
    ids = sorted(set(settings) | set(seen))[:MAX_PROJECTS]
    projects = []
    for pid in ids:
        entry = _entry(pid, settings.get(pid))
        entry["report"] = await audit_project(session, pid)
        projects.append(entry)
    return {
        "modes": list(MODES),
        "note": (
            "Without a project mode each ingestion path keeps its default: the SDK and the Manitos "
            "exporter store what they send, OTLP stores metadata only."
        ),
        "projects": projects,
    }


@router.put("/projects/{project_id}")
async def set_mode(
    project_id: str, body: ModeIn, session: AsyncSession = DbSession
) -> dict[str, Any]:
    if not 0 < len(project_id) <= 128:
        raise HTTPException(status_code=422, detail="invalid project id")
    row = await session.get(ProjectSetting, project_id)
    if row is None:
        session.add(ProjectSetting(project_id=project_id, capture_mode=body.capture_mode))
    else:
        row.capture_mode = body.capture_mode
    await session.flush()
    return _entry(project_id, body.capture_mode)


@router.delete("/projects/{project_id}")
async def clear_mode(
    project_id: str, session: AsyncSession = DbSession
) -> dict[str, Any]:
    """Back to the ingestion paths' defaults."""
    await session.execute(delete(ProjectSetting).where(ProjectSetting.project_id == project_id))
    return _entry(project_id, None)


@router.post("/projects/{project_id}/enforce")
async def enforce_mode(
    project_id: str, body: ConfirmIn, session: AsyncSession = DbSession
) -> dict[str, Any]:
    """Re-apply the project's mode to what is already stored. **Irreversible** for ``metadata``
    (stored input/output are removed) and ``redacted`` (matches are replaced): needs ``confirm``."""
    row = await session.get(ProjectSetting, project_id)
    if row is None:
        raise HTTPException(status_code=409, detail="set a capture mode for this project first")
    if not body.confirm:
        raise HTTPException(status_code=422, detail="this rewrites stored data: send confirm=true")
    mode = str(row.capture_mode)
    scanned = changed = 0
    if mode != "full":
        span_ids = (
            (
                await session.execute(
                    select(Span.id)
                    .join(Trace, Trace.id == Span.trace_id)
                    .where(Trace.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        for i in range(0, len(span_ids), ENFORCE_BATCH):
            chunk = span_ids[i : i + ENFORCE_BATCH]
            for span in (await session.execute(select(Span).where(Span.id.in_(chunk)))).scalars():
                scanned += 1
                inp, out, attributes, meta = apply_capture(
                    mode, inp=span.inp, out=span.out, attributes=span.attributes, meta=span.meta
                )
                if (inp, out, attributes, meta) != (span.inp, span.out, span.attributes, span.meta):
                    span.inp, span.out, span.attributes, span.meta = inp, out, attributes, meta
                    changed += 1
        traces = (
            await session.execute(select(Trace).where(Trace.project_id == project_id))
        ).scalars()
        for trace in traces:
            _, _, _, meta = apply_capture(mode, meta=trace.meta)
            if meta != trace.meta:
                trace.meta = meta
    await session.flush()
    return {
        "project_id": project_id,
        "capture_mode": mode,
        "spans_scanned": scanned,
        "spans_changed": changed,
    }
