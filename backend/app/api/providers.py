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

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DbSession
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
    session: AsyncSession = DbSession,
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


# ── Generic provider/model analytics (any instrumented application) ────────────────────


def _first_str(attrs: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    return None


def call_identity(span_type: str, attrs: dict[str, Any]) -> tuple[str, str] | None:
    """``(provider, model)`` of an LLM call, from OpenTelemetry GenAI attributes (both the current
    ``gen_ai.provider.name`` and the older ``gen_ai.system``) or the plain ``model`` attribute.
    Not an LLM call: ``None``. A provider the telemetry does not name is ``unknown``, never
    guessed from the model name."""
    model = _first_str(attrs, "gen_ai.response.model", "gen_ai.request.model", "model")
    is_llm = span_type == "llm" or any(str(k).startswith("gen_ai.") for k in attrs)
    if not is_llm:
        return None
    provider = _first_str(attrs, "gen_ai.provider.name", "gen_ai.system") or "unknown"
    return provider.lower(), model or "unknown"


@router.get("/models")
async def get_provider_models(
    hours: int = Query(24, ge=1, le=720),
    project_id: str | None = Query(None, min_length=1, max_length=128),
    environment: str | None = Query(None, min_length=1, max_length=64),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Calls, errors, latency, tokens and cost per provider and model, from LLM spans.

    Works for any application; needs no provider snapshot and no fallback chain. Latency is the
    span's own duration; spans without an end are counted as calls but not in latency."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = (
        select(
            Span.span_type, Span.attributes, Span.status, Span.start_time, Span.end_time,
            Span.tokens_input, Span.tokens_output, Span.cost_usd, Trace.created_at,
        )
        .join(Trace, Trace.id == Span.trace_id)
        .where(Trace.created_at >= cutoff)
        .order_by(Trace.created_at.desc())
        .limit(_ROW_LIMIT)
    )
    if project_id:
        query = query.where(Trace.project_id == project_id)
    if environment:
        query = query.where(Trace.environment == environment)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    rows = (await session.execute(query)).all()
    for row in rows:
        attrs = row.attributes if isinstance(row.attributes, dict) else {}
        identity = call_identity(row.span_type or "", attrs)
        if identity is None:
            continue
        g = groups.setdefault(identity, {
            "calls": 0, "errors": 0, "ms": [], "tokens_input": 0, "tokens_output": 0,
            "tokens_known": 0, "cost_usd": 0.0, "priced": 0, "last_seen": None,
        })
        g["calls"] += 1
        g["errors"] += row.status == "error"
        if row.start_time is not None and row.end_time is not None:
            g["ms"].append((_aware(row.end_time) - _aware(row.start_time)).total_seconds() * 1000)
        if row.tokens_input is not None or row.tokens_output is not None:
            g["tokens_known"] += 1
            g["tokens_input"] += row.tokens_input or 0
            g["tokens_output"] += row.tokens_output or 0
        if row.cost_usd is not None:
            g["priced"] += 1
            g["cost_usd"] += float(row.cost_usd)
        seen = _aware(row.created_at)
        if seen and (g["last_seen"] is None or seen > g["last_seen"]):
            g["last_seen"] = seen
    models = sorted(
        (
            {
                "provider": provider,
                "model": model,
                "calls": g["calls"],
                "errors": g["errors"],
                "error_rate": g["errors"] / g["calls"],
                "p50_ms": percentile(g["ms"], 0.5),
                "p95_ms": percentile(g["ms"], 0.95),
                "tokens_input": g["tokens_input"] if g["tokens_known"] else None,
                "tokens_output": g["tokens_output"] if g["tokens_known"] else None,
                # null when no call of this model reported a cost (unknown, not free)
                "cost_usd": g["cost_usd"] if g["priced"] else None,
                "priced_calls": g["priced"],
                "last_seen": g["last_seen"].isoformat() if g["last_seen"] else None,
            }
            for (provider, model), g in groups.items()
        ),
        key=lambda m: -m["calls"],
    )
    return {
        "hours": hours,
        "project_id": project_id,
        "environment": environment,
        "calls": sum(m["calls"] for m in models),
        "models": models,
        "truncated": len(rows) >= _ROW_LIMIT,
    }
