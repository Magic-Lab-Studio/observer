/**
 * Model pricing table (per 1M tokens, standard API). Shared across all instrumentors.
 * Same table and lookup rules as the Python SDK (llm_observatory/pricing.py): Anthropic prices
 * from https://platform.claude.com/docs/en/about-claude/pricing checked on 2026-10-19; the
 * others are unverified. An unknown model has no price (null), not a zero cost.
 */

export interface ModelPricing {
  input: number;
  output: number;
  /** Higher tier above this many input tokens (Claude Haiku 5.5). */
  longPromptThreshold?: number;
  longInput?: number;
  longOutput?: number;
}

export const ANTHROPIC_PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing";
export const ANTHROPIC_PRICING_CHECKED = "2026-10-19";

export const MODEL_PRICING: Record<string, ModelPricing> = {
  // OpenAI
  "gpt-4o": { input: 2.5, output: 10.0 },
  "gpt-4o-mini": { input: 0.15, output: 0.6 },
  "gpt-4-turbo": { input: 10.0, output: 30.0 },
  "gpt-4": { input: 30.0, output: 60.0 },
  "gpt-3.5-turbo": { input: 0.5, output: 1.5 },
  "o1": { input: 15.0, output: 60.0 },
  "o1-mini": { input: 3.0, output: 12.0 },
  "o3": { input: 10.0, output: 40.0 },
  "o3-mini": { input: 1.1, output: 4.4 },
  // Anthropic (official page, checked 2026-10-19)
  "claude-fable-5-1": { input: 10.0, output: 50.0 },
  "claude-mythos-5-1": { input: 10.0, output: 50.0 },
  "claude-fable-5": { input: 10.0, output: 50.0 },
  "claude-mythos-5": { input: 10.0, output: 50.0 },
  "claude-opus-5-5": { input: 4.0, output: 20.0 },
  "claude-opus-5": { input: 5.0, output: 25.0 },
  "claude-opus-4-8": { input: 5.0, output: 25.0 },
  "claude-opus-4-7": { input: 5.0, output: 25.0 },
  "claude-opus-4-6": { input: 5.0, output: 25.0 },
  "claude-opus-4-5": { input: 5.0, output: 25.0 },
  "claude-opus-4-1": { input: 15.0, output: 75.0 },
  "claude-opus-4-0": { input: 15.0, output: 75.0 },
  "claude-sonnet-5-5": { input: 2.0, output: 10.0 },
  "claude-sonnet-5": { input: 2.0, output: 10.0 },
  "claude-sonnet-4-6": { input: 3.0, output: 15.0 },
  "claude-sonnet-4-5": { input: 3.0, output: 15.0 },
  "claude-sonnet-4-0": { input: 3.0, output: 15.0 },
  "claude-haiku-5-5": { input: 0.1, output: 0.5, longPromptThreshold: 100_000, longInput: 0.5, longOutput: 2.5 },
  "claude-haiku-4-5": { input: 1.0, output: 5.0 },
  "claude-3-5-haiku": { input: 0.8, output: 4.0 },
  // Retired, no longer on the official page (unverified)
  "claude-3-7-sonnet": { input: 3.0, output: 15.0 },
  "claude-3-5-sonnet": { input: 3.0, output: 15.0 },
  "claude-3-opus": { input: 15.0, output: 75.0 },
  "claude-3-sonnet": { input: 3.0, output: 15.0 },
  "claude-3-haiku": { input: 0.25, output: 1.25 },
  // Google Gemini
  "gemini-2.0-flash": { input: 0.10, output: 0.40 },
  "gemini-2.0-pro": { input: 1.25, output: 10.0 },
  "gemini-1.5-pro": { input: 1.25, output: 5.0 },
  "gemini-1.5-flash": { input: 0.075, output: 0.30 },
};

/** Names accepted before that are not published model IDs, and short aliases of 4.0 models. */
const ALIASES: Record<string, string> = {
  "claude-4-opus": "claude-opus-4-0",
  "claude-4-sonnet": "claude-sonnet-4-0",
  "claude-opus-4": "claude-opus-4-0",
  "claude-sonnet-4": "claude-sonnet-4-0",
  "claude-3-5-sonnet-latest": "claude-3-5-sonnet",
  "claude-3-5-haiku-latest": "claude-3-5-haiku",
  "claude-3-7-sonnet-latest": "claude-3-7-sonnet",
  "claude-3-opus-latest": "claude-3-opus",
};
for (const [alias, target] of Object.entries(ALIASES)) MODEL_PRICING[alias] = MODEL_PRICING[target];

const ANTHROPIC_PREFIX = /^(?:[a-z]{2,4}\.)?anthropic[./]/;
const SNAPSHOT_SUFFIX = /(?:[-@]\d{8})?(?:-v\d+(?::\d+)?)?$/;

/**
 * The pricing key for a model ID, or null. Exact match first; for Anthropic IDs only, the provider
 * prefix and a dated/Bedrock snapshot suffix are removed (claude-sonnet-4-5-20250929 ->
 * claude-sonnet-4-5). OpenAI and Google IDs match exactly.
 */
export function resolveModel(model: string | null | undefined): string | null {
  if (!model) return null;
  const name = model.trim().toLowerCase();
  if (name in MODEL_PRICING) return name;
  let bare = name.replace(ANTHROPIC_PREFIX, "");
  if (!bare.startsWith("claude-")) return null;
  bare = bare.replace(SNAPSHOT_SUFFIX, "");
  return bare in MODEL_PRICING ? bare : null;
}

export function calculateCost(
  model: string,
  inputTokens: number,
  outputTokens: number
): number | null {
  const key = resolveModel(model);
  if (!key) return null;
  const pricing = MODEL_PRICING[key];
  let { input, output } = pricing;
  if (pricing.longPromptThreshold !== undefined && inputTokens > pricing.longPromptThreshold) {
    input = pricing.longInput ?? input;
    output = pricing.longOutput ?? output;
  }
  return (inputTokens / 1_000_000) * input + (outputTokens / 1_000_000) * output;
}
