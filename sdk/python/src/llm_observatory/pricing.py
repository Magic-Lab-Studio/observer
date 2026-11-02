"""Model pricing table for cost calculation.

Every price carries its source and the date it was checked. An unknown model has no price
(``calculate_cost`` returns ``None``), which is different from a free one: the cost is not
invented.

Lookup (``resolve_model``): an exact key first; then, for Anthropic models only, the provider
prefix (``anthropic.``, ``us.anthropic.``, ``anthropic/``), a Vertex ``@20250929`` or dated
``-20250929`` snapshot suffix and a Bedrock ``-v1:0`` suffix are removed, so
``claude-sonnet-4-5-20250929`` and ``anthropic.claude-sonnet-4-5-20250929-v1:0`` both price as
``claude-sonnet-4-5``. No prefix guessing: ``claude-sonnet-4-5-foo`` stays unknown. OpenAI and
Google IDs match exactly, because their dated snapshots were priced differently from the alias.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
ANTHROPIC_CHECKED = "2026-10-19"
UNVERIFIED = "unverified (carried over from the July 2026 table)"

# The one price table, shared with the Observer backend (which ships a copy and tests it equal).
TABLE_PATH = Path(__file__).with_name("pricing.json")


@dataclass
class ModelPricing:
    """Pricing for a model (per 1M tokens, standard API, no batch or cache discounts)."""

    input_per_1m: float
    output_per_1m: float
    currency: str = "USD"
    source: str = UNVERIFIED
    checked: str | None = None
    # A higher tier for prompts above a threshold (Claude Haiku 5.5: over 100,000 input tokens).
    long_prompt_threshold: int | None = None
    long_input_per_1m: float | None = None
    long_output_per_1m: float | None = None
    provider: str | None = None
    # Later prices by start date: (ISO date, input_per_1m, output_per_1m), oldest first.
    changes: list[tuple[str, float, float]] = field(default_factory=list)

    def rates_on(self, day: date | None = None) -> tuple[float, float]:
        """Input and output price per 1M tokens in force on ``day`` (today when omitted)."""
        when = (day or date.today()).isoformat()
        inp, out = self.input_per_1m, self.output_per_1m
        for start, later_in, later_out in self.changes:
            if when >= start:
                inp, out = later_in, later_out
        return inp, out


def _load_table() -> dict:
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


def _pricing(entry: dict) -> ModelPricing:
    return ModelPricing(
        input_per_1m=float(entry["input_per_1m"]),
        output_per_1m=float(entry["output_per_1m"]),
        source=entry.get("source", UNVERIFIED),
        checked=entry.get("checked"),
        long_prompt_threshold=entry.get("long_prompt_threshold"),
        long_input_per_1m=entry.get("long_input_per_1m"),
        long_output_per_1m=entry.get("long_output_per_1m"),
        provider=entry.get("provider"),
        changes=[
            (c["from"], float(c["input_per_1m"]), float(c["output_per_1m"]))
            for c in sorted(entry.get("changes", []), key=lambda c: c["from"])
        ],
    )


_TABLE = _load_table()
MODEL_PRICING: dict[str, ModelPricing] = {
    key: _pricing(entry) for key, entry in _TABLE["models"].items()
}
# Names this table accepted before that are not model IDs a provider publishes (kept so existing
# callers keep their prices), short aliases, and ManitOS registry IDs.
ALIASES: dict[str, str] = dict(_TABLE["aliases"])
# Models known to have no published price, with the reason (still unpriced: cost stays None).
UNPRICED: dict[str, str] = dict(_TABLE.get("unpriced", {}))
MODEL_PRICING.update({alias: MODEL_PRICING[target] for alias, target in ALIASES.items()})

_ANTHROPIC_PREFIX = re.compile(r"^(?:[a-z]{2,4}\.)?anthropic[./]")
_SNAPSHOT_SUFFIX = re.compile(r"(?:[-@]\d{8})?(?:-v\d+(?::\d+)?)?$")


def resolve_model(model: str | None) -> str | None:
    """The pricing key for a model ID, or None when the model is not priced."""
    if not model:
        return None
    name = model.strip().lower()
    if name in MODEL_PRICING:
        return name
    bare = _ANTHROPIC_PREFIX.sub("", name)
    if not bare.startswith("claude-"):
        return None
    bare = _SNAPSHOT_SUFFIX.sub("", bare)
    return bare if bare in MODEL_PRICING else None


def get_model_pricing(model: str) -> ModelPricing | None:
    """Get pricing for a model.

    Args:
        model: Model name.

    Returns:
        ModelPricing or None if unknown.
    """
    key = resolve_model(model)
    return MODEL_PRICING[key] if key else None


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    day: date | None = None,
) -> float | None:
    """Calculate cost for a model invocation.

    Args:
        model: Model name (e.g., "gpt-4o", "claude-sonnet-4-5-20250929").
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        day: Day of the call, for prices that change by date (today when omitted).

    Returns:
        Cost in USD, or None if model pricing is unknown.
    """
    pricing = get_model_pricing(model)
    if pricing is None:
        return None
    inp, out = pricing.rates_on(day)
    if pricing.long_prompt_threshold is not None and input_tokens > pricing.long_prompt_threshold:
        inp = pricing.long_input_per_1m if pricing.long_input_per_1m is not None else inp
        out = pricing.long_output_per_1m if pricing.long_output_per_1m is not None else out
    return (input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out
