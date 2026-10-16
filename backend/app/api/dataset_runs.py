"""Runs of a dataset and their comparison (mounted under ``/v1/datasets``).

Observer does not execute the agent. A run is reported by whoever ran the cases (a developer, CI):
which version it was and which trace each case produced. Observer checks every case against its
trace and stores the result, so two versions can be compared case by case.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.datasets import _dataset, _observed
from app.case_check import check_case, compare_results
from app.db import get_session
from app.evaluators.manitos_turn import RUBRIC
from app.models.datasets import DatasetItem
from app.models.runs import DatasetRun, RunResult

router = APIRouter()


class Version(BaseModel):
    commit: str | None = Field(None, max_length=64)
    model: str | None = Field(None, max_length=160)
    config: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=20)

    @field_validator("config")
    @classmethod
    def _short(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if len(key) > 80 or (isinstance(item, str) and len(item) > 200):
                raise ValueError("config keys and values must be short")
        return value


class CaseTrace(BaseModel):
    item_id: str = Field(..., max_length=36)
    trace_id: str = Field(..., max_length=36)


class RunIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    version: Version = Field(default_factory=Version)
    results: list[CaseTrace] = Field(..., min_length=1, max_length=2000)


def _run_dict(run: DatasetRun, results: list[RunResult] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "name": run.name,
        "version": run.version,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if results is not None:
        counts = {"pass": 0, "fail": 0, "unknown": 0}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        decided = counts["pass"] + counts["fail"]
        out["summary"] = {
            **counts,
            "cases": len(results),
            "pass_rate": counts["pass"] / decided if decided else None,
            "avg_health": _mean([r.health_score for r in results]),
        }
    return out


def _mean(values: list[float | None]) -> float | None:
    real = [v for v in values if v is not None]
    return sum(real) / len(real) if real else None


def _result_dict(r: RunResult) -> dict[str, Any]:
    return {
        "item_id": r.item_id,
        "trace_id": r.trace_id,
        "status": r.status,
        "checks": r.checks,
        "health_score": r.health_score,
        "duration_ms": r.duration_ms,
    }


async def _results(session: AsyncSession, run_id: str) -> list[RunResult]:
    rows = await session.execute(select(RunResult).where(RunResult.run_id == run_id))
    return list(rows.scalars())


@router.post("/{dataset_id}/runs")
async def create_run(
    dataset_id: str, body: RunIn, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _dataset(session, dataset_id)
    items = {
        i.id: i
        for i in (
            await session.execute(select(DatasetItem).where(DatasetItem.dataset_id == dataset_id))
        ).scalars()
    }
    unknown = [r.item_id for r in body.results if r.item_id not in items]
    if unknown:
        raise HTTPException(status_code=422, detail=f"cases not in this dataset: {unknown[:5]}")
    if len({r.item_id for r in body.results}) != len(body.results):
        raise HTTPException(status_code=422, detail="a case appears more than once")
    version = {**body.version.model_dump(), "rubric": RUBRIC}
    run = DatasetRun(dataset_id=dataset_id, name=body.name, version=version)
    session.add(run)
    await session.flush()
    stored: list[RunResult] = []
    for entry in body.results:
        observed, _ = await _observed(session, entry.trace_id)
        checked = check_case(items[entry.item_id].expect, observed)
        result = RunResult(
            run_id=run.id,
            item_id=entry.item_id,
            trace_id=entry.trace_id,
            status=checked["status"],
            checks=checked["checks"],
            health_score=observed.get("health_score"),
            duration_ms=observed.get("duration_ms"),
        )
        session.add(result)
        stored.append(result)
    await session.flush()
    return {**_run_dict(run, stored), "results": [_result_dict(r) for r in stored]}


@router.get("/{dataset_id}/runs")
async def list_runs(
    dataset_id: str, session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    await _dataset(session, dataset_id)
    runs = (
        await session.execute(
            select(DatasetRun)
            .where(DatasetRun.dataset_id == dataset_id)
            .order_by(DatasetRun.created_at.desc())
            .limit(100)
        )
    ).scalars()
    return [_run_dict(r, await _results(session, r.id)) for r in runs]


@router.get("/{dataset_id}/compare")
async def compare_runs(
    dataset_id: str,
    base: str = Query(..., max_length=36),
    candidate: str = Query(..., max_length=36),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Case-by-case difference between two runs of the same dataset."""
    runs = {}
    for key, run_id in (("base", base), ("candidate", candidate)):
        run = await session.get(DatasetRun, run_id)
        if run is None or run.dataset_id != dataset_id:
            raise HTTPException(status_code=404, detail=f"{key} run not found in this dataset")
        runs[key] = run
    results = {k: await _results(session, runs[k].id) for k in runs}
    diff = compare_results(
        {r.item_id: _result_dict(r) for r in results["base"]},
        {r.item_id: _result_dict(r) for r in results["candidate"]},
    )
    return {
        "base": _run_dict(runs["base"], results["base"]),
        "candidate": _run_dict(runs["candidate"], results["candidate"]),
        **diff,
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = await session.get(DatasetRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    results = await _results(session, run_id)
    return {**_run_dict(run, results), "results": [_result_dict(r) for r in results]}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    run = await session.get(DatasetRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await session.execute(delete(RunResult).where(RunResult.run_id == run_id))
    await session.delete(run)
    return {"status": "deleted"}
