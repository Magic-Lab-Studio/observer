/**
 * What the OpenAI and Anthropic instrumentors share: patching a resource's `create`, ending the
 * span through the tracer (so exporters receive it), and streamed responses (evaluation C1).
 *
 * `create` returns the provider's `APIPromise`. The patched method returns an `APIPromise` too
 * (through its `_thenUnwrap`), so `.withResponse()` and friends keep working. With
 * `stream: true` the value is the provider's `Stream`; it is handed back wrapped so iterating it
 * feeds an accumulator, and the span ends when the stream does:
 * - status ok when the stream is exhausted;
 * - error when iterating throws;
 * - unset with `observer.stream.incomplete` when the caller breaks out early.
 * A stream never iterated stays open and is sent as partial at shutdown.
 */

import { getTracer, type Span } from "../tracer";
import { calculateCost } from "../pricing";

const MAX_TEXT = 100_000;

export abstract class StreamAccumulator {
  text = "";
  finishReasons: string[] = [];
  responseModel?: string;
  responseId?: string;
  inputTokens?: number;
  outputTokens?: number;

  constructor(readonly model: string) {}

  abstract add(chunk: unknown): void;
  abstract output(): Record<string, unknown>;

  protected appendText(value: unknown): void {
    if (typeof value === "string" && value && this.text.length < MAX_TEXT) {
      this.text += value.slice(0, MAX_TEXT - this.text.length);
    }
  }
}

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj | undefined =>
  v && typeof v === "object" ? (v as Obj) : undefined;
const num = (v: unknown): number | undefined => (typeof v === "number" ? v : undefined);
const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);

export class OpenAIAccumulator extends StreamAccumulator {
  add(chunk: unknown): void {
    const c = obj(chunk);
    if (!c) return;
    this.responseModel = str(c.model) ?? this.responseModel;
    this.responseId = str(c.id) ?? this.responseId;
    for (const choice of (c.choices as unknown[]) ?? []) {
      const ch = obj(choice);
      this.appendText(obj(ch?.delta)?.content);
      const reason = str(ch?.finish_reason);
      if (reason) this.finishReasons.push(reason);
    }
    const usage = obj(c.usage);
    if (usage) {
      this.inputTokens = num(usage.prompt_tokens);
      this.outputTokens = num(usage.completion_tokens);
    }
  }

  output(): Record<string, unknown> {
    return { role: "assistant", content: this.text };
  }
}

export class AnthropicAccumulator extends StreamAccumulator {
  add(event: unknown): void {
    const e = obj(event);
    if (!e) return;
    if (e.type === "message_start") {
      const m = obj(e.message);
      this.responseModel = str(m?.model) ?? this.responseModel;
      this.responseId = str(m?.id) ?? this.responseId;
      const u = obj(m?.usage);
      this.inputTokens = num(u?.input_tokens);
      this.outputTokens = num(u?.output_tokens);
    } else if (e.type === "content_block_delta") {
      this.appendText(obj(e.delta)?.text);
    } else if (e.type === "message_delta") {
      const reason = str(obj(e.delta)?.stop_reason);
      if (reason) this.finishReasons.push(reason);
      const out = num(obj(e.usage)?.output_tokens);
      if (out !== undefined) this.outputTokens = out;
    }
  }

  output(): Record<string, unknown> {
    return { content: this.text };
  }
}

export function setTokens(span: Span, model: string, input: number, output: number): void {
  span.tokens = { input, output, total: input + output };
  span.attributes["gen_ai.usage.input_tokens"] = input;
  span.attributes["gen_ai.usage.output_tokens"] = output;
  const cost = calculateCost(model, input, output);
  if (cost !== null) span.costUsd = cost;
}

export function failSpan(span: Span, error: unknown): void {
  if (span.endTime) return;
  span.status = "error";
  span.attributes["error.type"] =
    error instanceof Error ? error.constructor.name : typeof error;
  span.attributes["error.message"] = error instanceof Error ? error.message : String(error);
  getTracer().endSpan(span);
}

function finishStream(
  span: Span,
  acc: StreamAccumulator,
  outcome: "complete" | "incomplete" | "error",
  error?: unknown
): void {
  if (span.endTime) return;
  span.attributes["gen_ai.response.streamed"] = true;
  if (acc.text) span.output = acc.output();
  if (acc.finishReasons.length) span.attributes["gen_ai.response.finish_reasons"] = acc.finishReasons;
  if (acc.responseModel) span.attributes["gen_ai.response.model"] = acc.responseModel;
  if (acc.responseId) span.attributes["response_id"] = acc.responseId;
  if (acc.inputTokens !== undefined || acc.outputTokens !== undefined) {
    setTokens(span, acc.model, acc.inputTokens ?? 0, acc.outputTokens ?? 0);
  }
  if (outcome === "error") {
    failSpan(span, error);
    return;
  }
  if (outcome === "complete") span.status = "ok";
  else span.attributes["observer.stream.incomplete"] = true;
  getTracer().endSpan(span);
}

/** The provider's stream, iterated through the accumulator; every other member is its own. */
export function wrapStream<T extends object>(stream: T, span: Span, acc: StreamAccumulator): T {
  const wrapIterator = (it: AsyncIterator<unknown>): AsyncIterableIterator<unknown> => ({
    async next() {
      let r: IteratorResult<unknown>;
      try {
        r = await it.next();
      } catch (error) {
        finishStream(span, acc, "error", error);
        throw error;
      }
      if (r.done) finishStream(span, acc, "complete");
      else {
        try {
          acc.add(r.value);
        } catch (e) {
          span.attributes["observer.instrumentation_error"] = String(e).slice(0, 500);
        }
      }
      return r;
    },
    async return(value?: unknown) {
      finishStream(span, acc, "incomplete");
      return it.return ? it.return(value) : { done: true, value };
    },
    [Symbol.asyncIterator]() {
      return this;
    },
  });
  return new Proxy(stream, {
    get(target, prop) {
      if (prop === Symbol.asyncIterator) {
        return () => wrapIterator((target as AsyncIterable<unknown>)[Symbol.asyncIterator]());
      }
      const value = Reflect.get(target, prop, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

interface Thenable {
  then: (ok?: (v: unknown) => unknown, err?: (e: unknown) => unknown) => unknown;
  _thenUnwrap?: (fn: (v: unknown) => unknown) => unknown;
}

/**
 * Replace `proto.create` with a version that opens a span, ends it from the result (or the
 * stream) and returns what the provider returned. Returns the original for `unpatch`.
 */
export function patchCreate(
  proto: { create?: (...args: unknown[]) => unknown },
  options: {
    spanName: string;
    system: string;
    finish: (span: Span, result: unknown, model: string) => void;
    accumulator: (model: string) => StreamAccumulator;
  }
): ((...args: unknown[]) => unknown) | null {
  const original = proto.create;
  if (typeof original !== "function") return null;
  if ((original as { __observerOriginal?: unknown }).__observerOriginal) return null; // patched
  const patched = function (this: unknown, ...args: unknown[]): unknown {
    const params = obj(args[0]) ?? {};
    const model = str(params.model) ?? "unknown";
    const span = getTracer().startSpan(options.spanName, {
      spanType: "llm",
      attributes: {
        "gen_ai.system": options.system,
        "gen_ai.provider.name": options.system,
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
      },
    });
    span.input = { model, messages: params.messages };
    let result: unknown;
    try {
      result = original.apply(this, args);
    } catch (error) {
      failSpan(span, error);
      throw error;
    }
    const streamed = params.stream === true;
    const onValue = (value: unknown): unknown => {
      if (streamed && value && typeof value === "object") {
        return wrapStream(value as object, span, options.accumulator(model));
      }
      try {
        options.finish(span, value, model);
        span.status = "ok";
      } catch (e) {
        span.status = "ok"; // the call succeeded; only reading it for telemetry failed
        span.attributes["observer.instrumentation_error"] = String(e).slice(0, 500);
      }
      getTracer().endSpan(span);
      return value;
    };
    const p = result as Thenable | undefined;
    if (p && typeof p.then === "function") {
      if (typeof p._thenUnwrap === "function") {
        // One APIPromise instance parses the body once, so the error watcher and the caller share
        // the returned promise (watching the original as well would read the body twice).
        const out = p._thenUnwrap(onValue) as Thenable;
        out.then(undefined, (error) => failSpan(span, error));
        return out;
      }
      return Promise.resolve(p).then(onValue, (error) => {
        failSpan(span, error);
        throw error;
      });
    }
    return onValue(result);
  };
  (patched as unknown as { __observerOriginal: unknown }).__observerOriginal = original;
  proto.create = patched;
  return original;
}
