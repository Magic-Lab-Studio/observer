/**
 * Model pricing (per 1M tokens, standard paid API, no batch or cache discounts), read from
 * `pricing.json`: the same table the Python SDK and the backend use (backend/tests/test_cost.py
 * checks the three copies are equal). Every price names its source and the day it was checked.
 * An unknown model has no price (null), not a zero cost; `UNPRICED` says why for known ones.
 */

import table from "./pricing.json";

export interface ModelPricing {
  input: number;
  output: number;
  /** Higher tier above this many input tokens (Claude Haiku 5.5). */
  longPromptThreshold?: number;
  longInput?: number;
  longOutput?: number;
  /** Later prices by start date (ISO day), oldest first. */
  changes?: { from: string; input: number; output: number }[];
  provider?: string;
  source?: string;
  checked?: string;
}

interface Entry {
  provider?: string;
  input_per_1m: number;
  output_per_1m: number;
  source?: string;
  checked?: string;
  long_prompt_threshold?: number;
  long_input_per_1m?: number;
  long_output_per_1m?: number;
  changes?: { from: string; input_per_1m: number; output_per_1m: number }[];
}

export const ANTHROPIC_PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing";

export const MODEL_PRICING: Record<string, ModelPricing> = {};
for (const [key, raw] of Object.entries(table.models as Record<string, Entry>)) {
  MODEL_PRICING[key] = {
    input: raw.input_per_1m,
    output: raw.output_per_1m,
    longPromptThreshold: raw.long_prompt_threshold,
    longInput: raw.long_input_per_1m,
    longOutput: raw.long_output_per_1m,
    changes: (raw.changes ?? [])
      .map((c) => ({ from: c.from, input: c.input_per_1m, output: c.output_per_1m }))
      .sort((a, b) => a.from.localeCompare(b.from)),
    provider: raw.provider,
    source: raw.source,
    checked: raw.checked,
  };
}

/** Models known to have no published price, with the reason (their cost stays null). */
export const UNPRICED: Record<string, string> = { ...(table.unpriced as Record<string, string>) };

/** Names accepted before that are not published model IDs, short aliases, ManitOS registry IDs. */
const ALIASES = table.aliases as Record<string, string>;
for (const [alias, target] of Object.entries(ALIASES)) MODEL_PRICING[alias] = MODEL_PRICING[target];

/** Input and output price per 1M tokens in force on `day` (today when omitted). */
export function ratesOn(pricing: ModelPricing, day: Date = new Date()): [number, number] {
  const when = day.toISOString().slice(0, 10);
  let { input, output } = pricing;
  for (const change of pricing.changes ?? []) {
    if (when >= change.from) ({ input, output } = change);
  }
  return [input, output];
}

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
  outputTokens: number,
  day: Date = new Date()
): number | null {
  const key = resolveModel(model);
  if (!key) return null;
  const pricing = MODEL_PRICING[key];
  let [input, output] = ratesOn(pricing, day);
  if (pricing.longPromptThreshold !== undefined && inputTokens > pricing.longPromptThreshold) {
    input = pricing.longInput ?? input;
    output = pricing.longOutput ?? output;
  }
  return (inputTokens / 1_000_000) * input + (outputTokens / 1_000_000) * output;
}
