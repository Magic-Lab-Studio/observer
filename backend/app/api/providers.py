"""LLM provider chain: what is configured and what actually happened.

Two sources on purpose. The snapshot is metadata the integration pushes (provider order, on/off,
models, data terms, quota cooldowns; never a key). The statistics come from ``llm.chain`` spans
already stored, so they need no new telemetry and work back in time. The fallback-depth idea (how
many calls failed before one answered) follows LiteLLM's ``attempted_fallbacks``; grouping by
provider/model with percentiles follows Langfuse's metrics API.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.metrics_core import classify_attempt as _classify
from app.metrics_core import parse_attempts, percentile
from app.models.trace import Span, Trace

router = APIRouter()

_STALE_AFTER_SEC = 180.0
_ROW_LIMIT = 20000


def _snapshot_path() -> Path:
    return Path(
        os.getenv("OBSERVATORY_PROVIDERS_SNAPSHOT")
        or Path(__file__).resolve().parents[2] / "providers_snapshot.json"
    )


class QuotaWindow(BaseModel):
    model: str = Field("", max_length=160)
    returns_in_sec: int = Field(0, ge=0)
    kind: str = Field("", max_length=32)


class ModelInfo(BaseModel):
    model: str = Field("", max_length=160)
    label: str = Field("", max_length=200)
    role: str = Field("", max_length=32)
    fallback: bool = True
    selectable: bool = True


class ProviderInfo(BaseModel):
    id: str = Field(..., min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    label: str = Field("", max_length=120)
    kind: str = Field("cloud", pattern=r"^(cloud|local)$")
    order: int = Field(0, ge=0, le=100)
    active: bool = False
    has_key: bool = False
    models: list[ModelInfo] = Field(default_factory=list, max_length=40)
    data_note: str = Field("", max_length=600)
    out_of_quota: list[QuotaWindow] = Field(default_factory=list, max_length=40)


class ProviderSnapshot(BaseModel):
    generated_at: float
    providers: list[ProviderInfo] = Field(..., max_length=40)


def read_snapshot() -> dict[str, Any] | None:
    try:
        data = json.loads(_snapshot_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@router.post("/snapshot")
async def post_snapshot(snapshot: ProviderSnapshot) -> dict[str, Any]:
    """Keep the latest picture of the provider chain (atomic write; one small file)."""
    path = _snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = snapshot.model_dump()
    stored["received_at"] = time.time()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".providers-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(stored, fh)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"status": "ok", "providers": len(stored["providers"])}


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def aggregate(rows: list[tuple[dict[str, Any], datetime | None, float | None]]) -> dict[str, Any]:
    """Per-provider and per-model counters.

    Rows are ``(llm.chain attributes, trace created_at, turn duration ms)``.
    """
    providers: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "attempts": 0,
            "ok": 0,
            "rate_limited": 0,
            "timeout": 0,
            "error": 0,
            "skipped": 0,
            "answered": 0,
            "ok_ms": [],
            "turn_ms": [],
            "last_seen": None,
            "models": defaultdict(lambda: {"attempts": 0, "ok": 0, "ok_ms": []}),
        }
    )
    depth: Counter[int] = Counter()
    paths: Counter[str] = Counter()
    turns = 0
    for attrs, created, duration in rows:
        attempts = parse_attempts(str(attrs.get("attempts_text") or ""))
        answered_by = str(attrs.get("answered_by") or "")
        answered = answered_by.split(" · ", 1)[0].strip().lower() if answered_by else ""
        if not attempts and not answered:
            continue
        turns += 1
        failed_before = 0
        order: list[str] = []
        for a in attempts:
            kind = _classify(a["result"])
            p = providers[a["backend"]]
            p["attempts"] += 1
            p[kind] += 1
            m = p["models"][a["model"] or "-"]
            m["attempts"] += 1
            if kind == "ok":
                m["ok"] += 1
                if a["ms"] is not None:
                    p["ok_ms"].append(a["ms"])
                    m["ok_ms"].append(a["ms"])
            if kind in {"rate_limited", "timeout", "error"}:
                failed_before += 1
            if not order or order[-1] != a["backend"]:
                order.append(a["backend"])
            seen = _aware(created)
            if seen and (p["last_seen"] is None or seen > p["last_seen"]):
                p["last_seen"] = seen
        if answered:
            providers[answered]["answered"] += 1
            if duration is not None:
                providers[answered]["turn_ms"].append(duration)
            seen = _aware(created)
            p = providers[answered]
            if seen and (p["last_seen"] is None or seen > p["last_seen"]):
                p["last_seen"] = seen
        depth[min(failed_before, 5)] += 1
        if order:
            paths[" → ".join(order)] += 1

    result = []
    for pid, p in providers.items():
        tried = p["attempts"] - p["skipped"]
        result.append(
            {
                "id": pid,
                "attempts": p["attempts"],
                "ok": p["ok"],
                "rate_limited": p["rate_limited"],
                "timeout": p["timeout"],
                "error": p["error"],
                "skipped": p["skipped"],
                "answered": p["answered"],
                "answered_share": p["answered"] / turns if turns else 0.0,
                "success_rate": p["ok"] / tried if tried else None,
                "p50_ms": percentile(p["ok_ms"], 0.5),
                "p95_ms": percentile(p["ok_ms"], 0.95),
                "turn_p50_ms": percentile(p["turn_ms"], 0.5),
                "last_seen": p["last_seen"].isoformat() if p["last_seen"] else None,
                "models": sorted(
                    (
                        {
                            "model": k,
                            "attempts": v["attempts"],
                            "ok": v["ok"],
                            "p50_ms": percentile(v["ok_ms"], 0.5),
                        }
                        for k, v in p["models"].items()
                    ),
                    key=lambda x: -x["attempts"],
                ),
            }
        )
    result.sort(key=lambda x: -x["attempts"])
    return {
        "turns": turns,
        "providers": result,
        "fallback_depth": [
            {"depth": d, "count": depth.get(d, 0)} for d in range(0, max(depth, default=0) + 1)
        ],
        "paths": [{"path": k, "count": v} for k, v in paths.most_common(8)],
    }


@router.get("/")
async def get_providers(
    hours: int = Query(24, ge=1, le=720),
    project_id: str = Query("manitos", min_length=1, max_length=128),
    environment: str | None = Query(None, min_length=1, max_length=64),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = (
        select(Span.attributes, Trace.created_at, Trace.start_time, Trace.end_time)
        .join(Trace, Trace.id == Span.trace_id)
        .where(Span.name == "llm.chain", Trace.created_at >= cutoff, Trace.project_id == project_id)
        .order_by(Trace.created_at.desc())
        .limit(_ROW_LIMIT)
    )
    if environment:
        query = query.where(Trace.environment == environment)
    rows: list[tuple[dict[str, Any], datetime | None, float | None]] = []
    for attrs, created, start, end in (await session.execute(query)).all():
        duration = None
        if start is not None and end is not None:
            duration = (_aware(end) - _aware(start)).total_seconds() * 1000.0
        rows.append((attrs if isinstance(attrs, dict) else {}, created, duration))

    snapshot = read_snapshot()
    stats = aggregate(rows)
    if snapshot:
        age = time.time() - float(snapshot.get("received_at") or 0)
        snapshot["stale"] = age > _STALE_AFTER_SEC
        snapshot["age_sec"] = int(age)
    return {
        "hours": hours,
        "project_id": project_id,
        "environment": environment,
        "snapshot": snapshot,
        **stats,
    }
