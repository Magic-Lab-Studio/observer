"""Per-project configuration sections (``/v1/projects/{id}/config``), groundedness first.

Each section is validated by its own closed schema: an unknown key is a 422, and a section with no
stored row reads as its defaults (groundedness: ``off``, nothing evaluated or sent anywhere).
``POST /v1/projects/{id}/groundedness/evaluate`` scores the project's traces now instead of waiting
for the periodic sweep.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import budgets, groundedness
from app.db import DbSession
from app.models.settings import ProjectConfig

router = APIRouter()

SECTIONS: dict[str, type[BaseModel]] = {
    groundedness.SECTION: groundedness.GroundednessConfig,
    budgets.SECTION: budgets.BudgetsConfig,  # time budget per stage (roadmap step 14)
}
ProjectId = Path(..., min_length=1, max_length=128)


class EvaluateIn(BaseModel):
    trace_ids: list[str] | None = Field(None, max_length=200)
    force: bool = False


def _schema(section: str) -> type[BaseModel]:
    schema = SECTIONS.get(section)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"unknown section: {section}")
    return schema


def _entry(project_id: str, section: str, value: dict[str, Any], stored: bool) -> dict[str, Any]:
    out = {"project_id": project_id, "section": section, "value": value,
           "source": "setting" if stored else "default"}
    if section == groundedness.SECTION:
        out["built_modes"] = list(groundedness.BUILT_MODES)
    return out


@router.get("/{project_id}/config")
async def get_config(
    project_id: str = ProjectId, session: AsyncSession = DbSession
) -> dict[str, Any]:
    stored = {
        row.section: row.value
        for row in (
            await session.execute(
                select(ProjectConfig).where(ProjectConfig.project_id == project_id)
            )
        ).scalars()
    }
    sections = {}
    for name, schema in SECTIONS.items():
        try:
            value = schema.model_validate(stored[name]) if name in stored else schema()
        except ValidationError:
            value = schema()
        sections[name] = _entry(project_id, name, value.model_dump(), name in stored)
    return {"project_id": project_id, "sections": sections}


@router.get("/{project_id}/config/{section}")
async def get_section(
    section: str, project_id: str = ProjectId, session: AsyncSession = DbSession
) -> dict[str, Any]:
    schema = _schema(section)
    row = await session.get(ProjectConfig, (project_id, section))
    try:
        value = schema.model_validate(row.value) if row is not None else schema()
    except ValidationError:
        value = schema()
    return _entry(project_id, section, value.model_dump(), row is not None)


@router.put("/{project_id}/config/{section}")
async def put_section(
    section: str,
    body: dict[str, Any],
    project_id: str = ProjectId,
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    schema = _schema(section)
    try:
        value = schema.model_validate(body)
    except ValidationError as exc:
        # include_context=False: a custom validator puts its ValueError in ctx, which is not JSON
        raise HTTPException(
            status_code=422, detail=exc.errors(include_url=False, include_context=False)
        ) from None
    if section == groundedness.SECTION and value.mode not in groundedness.BUILT_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"mode {value.mode!r} is not available yet; use one of "
            f"{list(groundedness.BUILT_MODES)}",
        )
    data = value.model_dump()
    row = await session.get(ProjectConfig, (project_id, section))
    if row is None:
        session.add(ProjectConfig(project_id=project_id, section=section, value=data))
    else:
        row.value = data
    await session.flush()
    return _entry(project_id, section, data, True)


@router.delete("/{project_id}/config/{section}")
async def delete_section(
    section: str, project_id: str = ProjectId, session: AsyncSession = DbSession
) -> dict[str, Any]:
    schema = _schema(section)
    await session.execute(
        delete(ProjectConfig).where(
            ProjectConfig.project_id == project_id, ProjectConfig.section == section
        )
    )
    return _entry(project_id, section, schema().model_dump(), False)


@router.post("/{project_id}/groundedness/evaluate")
async def evaluate_now(
    body: EvaluateIn, project_id: str = ProjectId, session: AsyncSession = DbSession
) -> dict[str, Any]:
    """Score the project's ended traces now (or the given ones); 409 while the mode is off."""
    cfg = await groundedness.load_config(session, project_id)
    if cfg.mode == "off":
        raise HTTPException(status_code=409, detail="groundedness is off for this project")
    rows = await groundedness.evaluate_project(
        session, project_id, trace_ids=body.trace_ids, force=body.force
    )
    return {
        "project_id": project_id,
        "scored": len(rows),
        "results": [{"trace_id": r.trace_id, **(r.result or {})} for r in rows],
    }
