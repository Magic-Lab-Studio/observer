"""A6 of the 2026-10-18 evaluation: real Claude model IDs were not priced (exact match only)."""

import pytest

from llm_observatory.pricing import (
    ANTHROPIC_SOURCE,
    calculate_cost,
    get_model_pricing,
    resolve_model,
)


@pytest.mark.parametrize(
    ("model", "key"),
    [
        ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
        ("claude-sonnet-4-5", "claude-sonnet-4-5"),
        ("claude-opus-4-1-20250805", "claude-opus-4-1"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-sonnet-4-20250514", "claude-sonnet-4"),
        ("claude-opus-4-20250514", "claude-opus-4"),
        ("claude-opus-4-5@20251101", "claude-opus-4-5"),
        ("anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5"),
        ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5"),
        ("Claude-Opus-5-5", "claude-opus-5-5"),
        ("claude-4-sonnet", "claude-4-sonnet"),  # legacy key kept for existing callers
    ],
)
def test_real_claude_ids_resolve(model, key):
    assert resolve_model(model) == key


@pytest.mark.parametrize(
    "model",
    ["claude-3-5-haiku-20241022", "claude-3-7-sonnet-20250219",  # retired, no published price
     "claude-sonnet-4-5-foo", "claude-sonnet-9", "claude", "", None, "gpt-4o-2024-05-13", "x"],
)
def test_unknown_models_stay_unpriced(model):
    assert resolve_model(model) is None
    assert calculate_cost(model, 1000, 1000) is None


def test_dated_sonnet_4_5_costs_the_official_price():
    assert calculate_cost("claude-sonnet-4-5-20250929", 1_000_000, 1_000_000) == pytest.approx(18.0)
    pricing = get_model_pricing("claude-sonnet-4-5-20250929")
    assert pricing.source == ANTHROPIC_SOURCE and pricing.checked


def test_haiku_5_5_long_prompt_tier():
    assert calculate_cost("claude-haiku-5-5", 100_000, 0) == pytest.approx(0.01)
    assert calculate_cost("claude-haiku-5-5", 200_000, 1_000_000) == pytest.approx(0.1 + 2.5)


def test_zero_tokens_is_a_known_zero_not_unknown():
    assert calculate_cost("claude-opus-5-5", 0, 0) == 0.0


def test_prices_change_on_their_start_date():
    from datetime import date

    assert calculate_cost("gemini-3.8-flash", 1_000_000, 0, date(2026, 12, 31)) == 0.75
    assert calculate_cost("gemini-3.8-flash", 1_000_000, 0, date(2027, 1, 1)) == 1.50
    assert calculate_cost("gemini-3.8-flash", 0, 1_000_000, date(2027, 1, 1)) == 7.50


def test_manitos_cloud_models_are_priced_or_named_unpriced():
    from llm_observatory.pricing import UNPRICED

    for model in (
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.8-flash",
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-20b",
        "qwen3.8-27b-groq",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    ):
        pricing = get_model_pricing(model)
        assert pricing is not None and pricing.checked and pricing.provider, model
    for model in ("command-a-03-2025", "google/gemma-4-31b-it"):
        assert get_model_pricing(model) is None and UNPRICED[model]
