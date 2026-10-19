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

import re
from dataclasses import dataclass

ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
ANTHROPIC_CHECKED = "2026-10-19"
UNVERIFIED = "unverified (carried over from the July 2026 table)"


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


def _anthropic(inp: float, out: float, **tier: float | int) -> ModelPricing:
    return ModelPricing(inp, out, source=ANTHROPIC_SOURCE, checked=ANTHROPIC_CHECKED, **tier)


MODEL_PRICING: dict[str, ModelPricing] = {
    # OpenAI models
    "gpt-4o": ModelPricing(input_per_1m=2.50, output_per_1m=10.00),
    "gpt-4o-mini": ModelPricing(input_per_1m=0.15, output_per_1m=0.60),
    "gpt-4-turbo": ModelPricing(input_per_1m=10.00, output_per_1m=30.00),
    "gpt-4": ModelPricing(input_per_1m=30.00, output_per_1m=60.00),
    "gpt-3.5-turbo": ModelPricing(input_per_1m=0.50, output_per_1m=1.50),
    "o1": ModelPricing(input_per_1m=15.00, output_per_1m=60.00),
    "o1-mini": ModelPricing(input_per_1m=3.00, output_per_1m=12.00),
    "o3": ModelPricing(input_per_1m=10.00, output_per_1m=40.00),
    "o3-mini": ModelPricing(input_per_1m=1.10, output_per_1m=4.40),
    # Anthropic models (official page, checked on ANTHROPIC_CHECKED)
    "claude-fable-5-1": _anthropic(10.00, 50.00),
    "claude-mythos-5-1": _anthropic(10.00, 50.00),
    "claude-fable-5": _anthropic(10.00, 50.00),
    "claude-mythos-5": _anthropic(10.00, 50.00),
    "claude-opus-5-5": _anthropic(4.00, 20.00),
    "claude-opus-5": _anthropic(5.00, 25.00),
    "claude-opus-4-8": _anthropic(5.00, 25.00),
    "claude-opus-4-7": _anthropic(5.00, 25.00),
    "claude-opus-4-6": _anthropic(5.00, 25.00),
    "claude-opus-4-5": _anthropic(5.00, 25.00),
    "claude-opus-4-1": _anthropic(15.00, 75.00),
    "claude-opus-4-0": _anthropic(15.00, 75.00),
    "claude-sonnet-5-5": _anthropic(2.00, 10.00),
    "claude-sonnet-5": _anthropic(2.00, 10.00),
    "claude-sonnet-4-6": _anthropic(3.00, 15.00),
    "claude-sonnet-4-5": _anthropic(3.00, 15.00),
    "claude-sonnet-4-0": _anthropic(3.00, 15.00),
    "claude-haiku-5-5": _anthropic(
        0.10, 0.50, long_prompt_threshold=100_000, long_input_per_1m=0.50, long_output_per_1m=2.50
    ),
    "claude-haiku-4-5": _anthropic(1.00, 5.00),
    "claude-3-5-haiku": _anthropic(0.80, 4.00),
    # Retired models no longer on the official page
    "claude-3-7-sonnet": ModelPricing(input_per_1m=3.00, output_per_1m=15.00),
    "claude-3-5-sonnet": ModelPricing(input_per_1m=3.00, output_per_1m=15.00),
    "claude-3-opus": ModelPricing(input_per_1m=15.00, output_per_1m=75.00),
    "claude-3-sonnet": ModelPricing(input_per_1m=3.00, output_per_1m=15.00),
    "claude-3-haiku": ModelPricing(input_per_1m=0.25, output_per_1m=1.25),
    # Google models
    "gemini-2.0-flash": ModelPricing(input_per_1m=0.10, output_per_1m=0.40),
    "gemini-2.0-pro": ModelPricing(input_per_1m=1.25, output_per_1m=10.00),
    "gemini-1.5-pro": ModelPricing(input_per_1m=1.25, output_per_1m=5.00),
    "gemini-1.5-flash": ModelPricing(input_per_1m=0.075, output_per_1m=0.30),
}

# Names this table accepted before that are not model IDs Anthropic publishes (kept so existing
# callers keep their prices), and the short aliases of the 4.0 models.
ALIASES: dict[str, str] = {
    "claude-4-opus": "claude-opus-4-0",
    "claude-4-sonnet": "claude-sonnet-4-0",
    "claude-opus-4": "claude-opus-4-0",
    "claude-sonnet-4": "claude-sonnet-4-0",
    "claude-3-5-sonnet-latest": "claude-3-5-sonnet",
    "claude-3-5-haiku-latest": "claude-3-5-haiku",
    "claude-3-7-sonnet-latest": "claude-3-7-sonnet",
    "claude-3-opus-latest": "claude-3-opus",
}
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
) -> float | None:
    """Calculate cost for a model invocation.

    Args:
        model: Model name (e.g., "gpt-4o", "claude-sonnet-4-5-20250929").
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Cost in USD, or None if model pricing is unknown.
    """
    pricing = get_model_pricing(model)
    if pricing is None:
        return None
    inp, out = pricing.input_per_1m, pricing.output_per_1m
    if pricing.long_prompt_threshold is not None and input_tokens > pricing.long_prompt_threshold:
        inp = pricing.long_input_per_1m if pricing.long_input_per_1m is not None else inp
        out = pricing.long_output_per_1m if pricing.long_output_per_1m is not None else out
    return (input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out
