/**
 * Anthropic auto-instrumentation: patches `Anthropic.Messages.prototype.create` of
 * `@anthropic-ai/sdk`, so every client is traced, streamed calls included.
 */

import type { Span } from "../tracer";
import { AnthropicAccumulator, patchCreate, setTokens } from "./shared";

type Proto = { create?: (...args: unknown[]) => unknown };
let patchedProto: Proto | null = null;
let original: ((...args: unknown[]) => unknown) | null = null;

function finish(span: Span, result: unknown, model: string): void {
  const r = (result && typeof result === "object" ? result : {}) as Record<string, any>;
  if (r.usage) setTokens(span, model, r.usage.input_tokens || 0, r.usage.output_tokens || 0);
  const blocks = Array.isArray(r.content) ? r.content : [];
  const text = blocks.filter((b: any) => b?.type === "text").map((b: any) => b.text).join("\n");
  if (blocks.length) span.output = { content: text };
  if (typeof r.stop_reason === "string") span.attributes["gen_ai.response.finish_reasons"] = [r.stop_reason];
  if (typeof r.model === "string") span.attributes["gen_ai.response.model"] = r.model;
  if (typeof r.id === "string") span.attributes["response_id"] = r.id;
}

function messagesProto(mod: any): Proto | null {
  const Messages = mod?.Anthropic?.Messages ?? mod?.default?.Messages;
  return Messages?.prototype ?? null;
}

/** `mod`: the `@anthropic-ai/sdk` module; loaded with `require` when omitted (CommonJS). */
export function instrument(mod?: unknown): void {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const proto = messagesProto(mod ?? require("@anthropic-ai/sdk"));
    if (!proto || patchedProto) return;
    original = patchCreate(proto, {
      spanName: "anthropic.messages.create",
      system: "anthropic",
      finish,
      accumulator: (model) => new AnthropicAccumulator(model),
    });
    if (original) patchedProto = proto;
  } catch {
    // @anthropic-ai/sdk not installed: nothing to instrument
  }
}

export function uninstrument(): void {
  if (patchedProto && original) patchedProto.create = original;
  patchedProto = null;
  original = null;
}
