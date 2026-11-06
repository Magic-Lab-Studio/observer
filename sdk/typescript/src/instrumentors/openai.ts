/**
 * OpenAI auto-instrumentation: patches `OpenAI.Chat.Completions.prototype.create`, so every client
 * (created before or after) is traced, streamed calls included.
 */

import type { Span } from "../tracer";
import { OpenAIAccumulator, patchCreate, setTokens } from "./shared";

type Proto = { create?: (...args: unknown[]) => unknown };
let patchedProto: Proto | null = null;
let original: ((...args: unknown[]) => unknown) | null = null;

function finish(span: Span, result: unknown, model: string): void {
  const r = (result && typeof result === "object" ? result : {}) as Record<string, any>;
  const usage = r.usage;
  if (usage) setTokens(span, model, usage.prompt_tokens || 0, usage.completion_tokens || 0);
  const choices = Array.isArray(r.choices) ? r.choices : [];
  const message = choices[0]?.message;
  if (message) span.output = { content: message.content, role: message.role };
  const reasons = choices.map((c: any) => c?.finish_reason).filter((x: unknown) => typeof x === "string");
  if (reasons.length) span.attributes["gen_ai.response.finish_reasons"] = reasons;
  if (typeof r.model === "string") span.attributes["gen_ai.response.model"] = r.model;
  if (typeof r.id === "string") span.attributes["response_id"] = r.id;
}

function completionsProto(mod: any): Proto | null {
  const Completions = mod?.OpenAI?.Chat?.Completions ?? mod?.default?.Chat?.Completions;
  return Completions?.prototype ?? null;
}

/** `mod`: the `openai` module; loaded with `require` when omitted (CommonJS). */
export function instrument(mod?: unknown): void {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const proto = completionsProto(mod ?? require("openai"));
    if (!proto || patchedProto) return;
    original = patchCreate(proto, {
      spanName: "openai.chat.completions.create",
      system: "openai",
      finish,
      accumulator: (model) => new OpenAIAccumulator(model),
    });
    if (original) patchedProto = proto;
  } catch {
    // openai not installed: nothing to instrument
  }
}

export function uninstrument(): void {
  if (patchedProto && original) patchedProto.create = original;
  patchedProto = null;
  original = null;
}
