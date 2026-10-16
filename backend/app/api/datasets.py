"""Test-case datasets built from traces (``/v1/datasets``).

A case is **metadata only**. Nothing is copied from a trace's input or output: ``observed`` holds
the intent, the tool names, the model and the health signals; ``expect`` holds what a person says
the next run must satisfy. Text phrases in ``expect.text`` are written by that person.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.evaluators.manitos_turn import score_turn
from app.metrics_core import load_signals_for
from app.models.datasets import Dataset, DatasetItem
from app.models.runs import DatasetRun, RunResult
from app.models.trace import Span, Trace

router = APIRouter()

EXPORT_SCHEMA = "observer.dataset.v1"
Name = Field(..., min_length=1, max_length=80)


class ToolsExpect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: list[str] = Field(default_factory=list, max_length=20)
    forbidden: list[str] = Field(default_factory=list, max_length=20)


class HealthExpect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_error: bool = False
    no_truncation: bool = False
    no_degradation: bool = False
    no_fallback: bool = False
    max_duration_ms: int | None = Field(None, ge=1, le=3_600_000)


class TextExpect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    must_contain: list[str] = Field(default_factory=list, max_length=20)
    must_not_contain: list[str] = Field(default_factory=list, max_length=20)


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: ToolsExpect = Field(default_factory=ToolsExpect)
    health: HealthExpect = Field(default_factory=HealthExpect)
    text: TextExpect = Field(default_factory=TextExpect)


class DatasetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    project_id: str = Field("manitos", min_length=1, max_length=128)
    description: str | None = Field(None, max_length=500)


class ItemIn(BaseModel):
    trace_id: str | None = Field(None, max_length=36)
    intent: str | None = Field(None, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)
    expect: Expect = Field(default_factory=Expect)
    label: Literal["correct", "incorrect"] | None = None
    notes: str | None = Field(None, max_length=1000)


class ItemPatch(BaseModel):
    tags: list[str] | None = Field(None, max_length=20)
    expect: Expect | None = None
    label: Literal["correct", "incorrect"] | None = None
    notes: str | None = Field(None, max_length=1000)


def _clean(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        v = v.strip()[:200]
        if v:
            seen[v] = None
    return list(seen)


def _clean_expect(expect: Expect) -> dict[str, Any]:
    data = expect.model_dump()
    for group in ("tools", "text"):
        for key, values in data[group].items():
            data[group][key] = _clean(values)
    return data


def checkable(expect: dict[str, Any]) -> list[str]:
    """Which parts of ``expect`` are set (so a later run knows what to verify)."""
    out = []
    if expect["tools"]["required"] or expect["tools"]["forbidden"]:
        out.append("tools")
    health = expect["health"]
    if any(v for v in health.values()):
        out.append("health")
    if expect["text"]["must_contain"] or expect["text"]["must_not_contain"]:
        out.append("text")
    return out


def item_to_dict(item: DatasetItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "dataset_id": item.dataset_id,
        "source_trace_id": item.source_trace_id,
        "intent": item.intent,
        "tags": item.tags or [],
        "expect": item.expect,
        "checks": checkable(item.expect),
        "label": item.label,
        "notes": item.notes,
        "observed": item.observed or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


async def _observed(session: AsyncSession, trace_id: str) -> tuple[dict[str, Any], str | None]:
    """Metadata of a trace for a case (no input/output). Raises 404 when the trace is unknown."""
    trace = await session.get(Trace, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    signals = (await load_signals_for(session, [trace_id]))[0]
    tool_spans = (
        (
            await session.execute(
                select(Span.name).where(Span.trace_id == trace_id, Span.span_type == "tool")
            )
        )
        .scalars()
        .all()
    )
    observed = {
        "status": signals.status,
        "model": signals.answered_by or signals.model,
        "intent": signals.intent,
        "language": signals.language,
        "duration_ms": signals.duration_ms,
        "degraded": signals.degraded,
        "truncated": signals.truncated,
        "tool_error": signals.tool_error,
        "fallback": signals.fallback,
        "tools": sorted(set(tool_spans)),
        "health_score": score_turn(signals)["health"]["score"],
    }
    return observed, signals.intent or None


async def _dataset(session: AsyncSession, dataset_id: str) -> Dataset:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


@router.get("")
async def list_datasets(
    project_id: str = Query("manitos", min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    counts = dict(
        (
            await session.execute(
                select(DatasetItem.dataset_id, func.count()).group_by(DatasetItem.dataset_id)
            )
        ).all()
    )
    rows = (
        await session.execute(
            select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.created_at)
        )
    ).scalars()
    return [
        {
            "id": d.id,
            "name": d.name,
            "project_id": d.project_id,
            "description": d.description,
            "items": counts.get(d.id, 0),
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]


@router.post("")
async def create_dataset(
    body: DatasetIn, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    dataset = Dataset(**body.model_dump())
    session.add(dataset)
    await session.flush()
    return {"id": dataset.id, "name": dataset.name, "project_id": dataset.project_id, "items": 0}


@router.delete("/{dataset_id}")
async def delete_dataset(
    dataset_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    dataset = await _dataset(session, dataset_id)
    # Children first: SQLite enforces ON DELETE CASCADE only with foreign keys switched on.
    run_ids = select(DatasetRun.id).where(DatasetRun.dataset_id == dataset_id)
    await session.execute(delete(RunResult).where(RunResult.run_id.in_(run_ids)))
    await session.execute(delete(DatasetRun).where(DatasetRun.dataset_id == dataset_id))
    await session.execute(delete(DatasetItem).where(DatasetItem.dataset_id == dataset_id))
    await session.delete(dataset)
    return {"status": "deleted"}


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    dataset = await _dataset(session, dataset_id)
    items = (
        await session.execute(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == dataset_id)
            .order_by(DatasetItem.created_at)
        )
    ).scalars()
    return {
        "id": dataset.id,
        "name": dataset.name,
        "project_id": dataset.project_id,
        "description": dataset.description,
        "items": [item_to_dict(i) for i in items],
    }


@router.get("/{dataset_id}/export")
async def export_dataset(
    dataset_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Portable copy of the dataset (the input a regression run reads)."""
    data = await get_dataset(dataset_id, session)
    return {"schema": EXPORT_SCHEMA, **data}


@router.post("/{dataset_id}/items")
async def add_item(
    dataset_id: str, body: ItemIn, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _dataset(session, dataset_id)
    observed: dict[str, Any] = {}
    intent = body.intent
    if body.trace_id:
        observed, found = await _observed(session, body.trace_id)
        intent = intent or found
    item = DatasetItem(
        dataset_id=dataset_id,
        source_trace_id=body.trace_id,
        intent=intent,
        tags=_clean(body.tags),
        expect=_clean_expect(body.expect),
        label=body.label,
        notes=body.notes,
        observed=observed,
    )
    session.add(item)
    await session.flush()
    return item_to_dict(item)


@router.patch("/items/{item_id}")
async def update_item(
    item_id: str, body: ItemPatch, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    item = await session.get(DatasetItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    changes = body.model_dump(exclude_unset=True)
    if "tags" in changes:
        item.tags = _clean(body.tags or [])
    if "expect" in changes and body.expect is not None:
        item.expect = _clean_expect(body.expect)
    if "label" in changes:
        item.label = body.label
    if "notes" in changes:
        item.notes = body.notes
    await session.flush()
    return item_to_dict(item)


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    item = await session.get(DatasetItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    await session.delete(item)
    return {"status": "deleted"}
