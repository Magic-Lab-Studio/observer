"""Span cost from the shared price table when the sender did not compute it (ManitOS plan B3).

The table is ``app/pricing.json``, a copy of the SDK's ``llm_observatory/pricing.json`` (the
backend image is built from ``backend/`` alone); ``tests/test_cost.py`` fails when they differ.

Two numbers per span:
- ``cost_usd``: what the call cost. Zero for a provider used on its free tier
  (``OBSERVATORY_FREE_TIER_PROVIDERS``, comma-separated: groq, google, cloudflare...) and for an
  OpenRouter ``:free`` model.
- ``cost.equivalent_usd`` (attribute): what the same tokens cost at the paid list price, so a
  free-tier deployment still sees its real consumption.

A model with no price keeps ``cost_usd`` empty: unknown is never reported as free.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

TABLE_PATH = Path(__file__).with_name("pricing.json")
EQUIVALENT_KEY = "cost.equivalent_usd"
MODEL_KEYS = ("model", "gen_ai.response.model", "gen_ai.request.model")

# Prefixes ManitOS writes before a model ID ("groq · qwen/qwen3.8-27b", "nvidia:google/...").
PROVIDER_PREFIXES = {
    "groq": "groq",
    "gemini": "google",
    "google": "google",
    "cloudflare": "cloudflare",
    "nvidia": "nvidia",
    "openrouter": "openrouter",
    "cohere": "cohere",
    "ollama": "local",
    "llamacpp": "local",
}


@lru_cache(maxsize=1)
def table() -> dict[str, Any]:
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


def free_tier_providers() -> frozenset[str]:
    raw = os.getenv("OBSERVATORY_FREE_TIER_PROVIDERS", "")
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def split_model(raw: str | None) -> tuple[str | None, str]:
    """(provider from a known prefix or None, bare model ID in lower case)."""
    name = str(raw or "").strip().lower()
    for sep in (" · ", ":"):
        head, found, tail = name.partition(sep)
        if found and head.strip() in PROVIDER_PREFIXES and tail.strip():
            return PROVIDER_PREFIXES[head.strip()], tail.strip()
    return None, name


def classify(raw: str | None) -> dict[str, Any]:
    """How a model name is priced: status priced | free | local | unpriced, with the table key."""
    hinted, name = split_model(raw)
    data = table()
    if hinted == "local" or name.endswith(".gguf"):
        return {
            "status": "local",
            "key": None,
            "provider": "local",
            "reason": "runs on this machine",
        }
    free_variant = name.endswith(":free")
    base = name[: -len(":free")] if free_variant else name
    key = data["aliases"].get(base, base)
    entry = data["models"].get(key)
    provider = hinted or (entry or {}).get("provider")
    if free_variant:
        return {
            "status": "free",
            "key": key if entry else None,
            "provider": provider or "openrouter",
            "reason": "free model variant",
        }
    if entry is None:
        reason = data.get("unpriced", {}).get(key, "not in the price table")
        return {"status": "unpriced", "key": None, "provider": provider, "reason": reason}
    return {"status": "priced", "key": key, "provider": provider, "reason": None}


def _rates(entry: dict[str, Any], day: date) -> tuple[float, float]:
    inp, out = float(entry["input_per_1m"]), float(entry["output_per_1m"])
    for change in sorted(entry.get("changes", []), key=lambda c: c["from"]):
        if day.isoformat() >= change["from"]:
            inp, out = float(change["input_per_1m"]), float(change["output_per_1m"])
    return inp, out


def list_price(key: str, tokens_in: int, tokens_out: int, day: date) -> float:
    entry = table()["models"][key]
    inp, out = _rates(entry, day)
    threshold = entry.get("long_prompt_threshold")
    if threshold is not None and tokens_in > threshold:
        inp = entry.get("long_input_per_1m") or inp
        out = entry.get("long_output_per_1m") or out
    return round((tokens_in / 1_000_000) * inp + (tokens_out / 1_000_000) * out, 6)


def model_of(attributes: dict[str, Any] | None) -> str | None:
    for key in MODEL_KEYS:
        value = (attributes or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def estimate(
    model: str | None, tokens_in: int | None, tokens_out: int | None, when: datetime | None
) -> tuple[float | None, float | None]:
    """(cost_usd, equivalent_usd) for one call; (None, None) when it cannot be priced."""
    if not model or tokens_in is None and tokens_out is None:
        return None, None
    info = classify(model)
    if info["status"] == "local":
        return 0.0, 0.0
    tin, tout = int(tokens_in or 0), int(tokens_out or 0)
    day = (when or datetime.now()).date()
    equivalent = list_price(info["key"], tin, tout, day) if info["key"] else None
    if info["status"] == "free":
        return 0.0, equivalent
    if equivalent is None:
        return None, None
    if (info["provider"] or "") in free_tier_providers():
        return 0.0, equivalent
    return equivalent, equivalent


def fill_span_cost(values: dict[str, Any]) -> dict[str, Any]:
    """Complete ``cost_usd`` (and the equivalent attribute) of a span's stored values in place.

    A cost the sender computed is kept as is. Same input, same output, so a re-sent batch still
    matches what was stored (idempotent ingest).
    """
    if values.get("cost_usd") is not None:
        return values
    attributes = values.get("attributes")
    cost, equivalent = estimate(
        model_of(attributes),
        values.get("tokens_input"),
        values.get("tokens_output"),
        values.get("start_time"),
    )
    if cost is None:
        return values
    values["cost_usd"] = cost
    if equivalent is not None:
        values["attributes"] = {**(attributes or {}), EQUIVALENT_KEY: equivalent}
    return values
