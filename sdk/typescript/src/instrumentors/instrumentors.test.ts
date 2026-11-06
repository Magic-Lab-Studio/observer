/**
 * The instrumentors against the real `openai` and `@anthropic-ai/sdk` clients with a mocked fetch
 * (no key, no network). Before this, they patched members that do not exist (`require("anthropic")`,
 * `OpenAI.prototype.chat`), so nothing was traced, and their spans never reached an exporter.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import * as openaiModule from "openai";
import * as anthropicModule from "@anthropic-ai/sdk";
import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";
import { instrument, uninstrument } from "./index";
import { Tracer, setTracer, type Span } from "../tracer";

const MESSAGES = [{ role: "user" as const, content: "hi" }];

const json = (body: unknown, status = 200) => async () =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const sse = (text: string) => async () =>
  new Response(text, { status: 200, headers: { "content-type": "text/event-stream" } });

const OPENAI_OK = {
  id: "chatcmpl-1", object: "chat.completion", created: 1, model: "gpt-4o-mini",
  choices: [{ index: 0, message: { role: "assistant", content: "hola" }, finish_reason: "stop" }],
  usage: { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 },
};
const ANTHROPIC_OK = {
  id: "msg_1", type: "message", role: "assistant", model: "claude-sonnet-4-5-20250929",
  content: [{ type: "text", text: "hola" }], stop_reason: "end_turn", stop_sequence: null,
  usage: { input_tokens: 12, output_tokens: 3 },
};

function openaiSse(): string {
  const chunk = (delta: object, finish: string | null = null) => ({
    id: "chatcmpl-9", object: "chat.completion.chunk", created: 1, model: "gpt-4o-mini-2024-07-18",
    choices: [{ index: 0, delta, finish_reason: finish }],
  });
  const events = [chunk({ role: "assistant", content: "" }), chunk({ content: "ho" }),
    chunk({ content: "la" }), chunk({}, "stop"),
    { id: "chatcmpl-9", object: "chat.completion.chunk", created: 1, model: "gpt-4o-mini-2024-07-18",
      choices: [], usage: { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 } }];
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("") + "data: [DONE]\n\n";
}

function anthropicSse(): string {
  const events: [string, object][] = [
    ["message_start", { type: "message_start", message: { id: "msg_9", type: "message", role: "assistant",
      content: [], model: "claude-sonnet-4-5-20250929", stop_reason: null, stop_sequence: null,
      usage: { input_tokens: 12, output_tokens: 1 } } }],
    ["content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "ho" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "la" } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null },
      usage: { output_tokens: 3 } }],
    ["message_stop", { type: "message_stop" }],
  ];
  return events.map(([k, v]) => `event: ${k}\ndata: ${JSON.stringify(v)}\n\n`).join("");
}

let sent: Span[];
let tracer: Tracer;

beforeEach(() => {
  sent = [];
  tracer = new Tracer({ serviceName: "test" });
  tracer.addExporter({ export: (spans) => sent.push(...spans) });
  setTracer(tracer);
  instrument({ openai: true, anthropic: true, modules: { openai: openaiModule, anthropic: anthropicModule } });
});

afterEach(() => {
  uninstrument({ openai: true, anthropic: true });
  setTracer(new Tracer());
});

const llm = () => {
  const spans = sent.filter((s) => s.spanType === "llm");
  expect(spans).toHaveLength(1);
  return spans[0];
};

describe("OpenAI", () => {
  it("traces a call and the span reaches the exporter, finished", async () => {
    const client = new OpenAI({ apiKey: "test", maxRetries: 0, fetch: json(OPENAI_OK) });
    const r = await client.chat.completions.create({ model: "gpt-4o-mini", messages: MESSAGES });
    expect(r.choices[0].message.content).toBe("hola");
    const span = llm();
    expect(span.status).toBe("ok");
    expect(span.endTime).toBeGreaterThanOrEqual(span.startTime);
    expect(span.tokens).toEqual({ input: 7, output: 2, total: 9 });
    expect(span.output).toEqual({ content: "hola", role: "assistant" });
    expect(span.attributes["gen_ai.response.finish_reasons"]).toEqual(["stop"]);
    expect(span.attributes["service.name"]).toBe("test");
  });

  it("keeps the provider's promise helpers", async () => {
    const client = new OpenAI({ apiKey: "test", maxRetries: 0, fetch: json(OPENAI_OK) });
    const { data, response } = await client.chat.completions
      .create({ model: "gpt-4o-mini", messages: MESSAGES })
      .withResponse();
    expect(data.id).toBe("chatcmpl-1");
    expect(response.status).toBe(200);
  });

  it("an error reaches the caller unchanged and the span is an error", async () => {
    const client = new OpenAI({ apiKey: "test", maxRetries: 0,
      fetch: json({ error: { message: "bad", type: "invalid_request_error" } }, 400) });
    await expect(client.chat.completions.create({ model: "gpt-4o-mini", messages: MESSAGES }))
      .rejects.toBeInstanceOf(OpenAI.BadRequestError);
    const span = llm();
    expect(span.status).toBe("error");
    expect(String(span.attributes["error.message"])).toContain("bad");
  });

  it("C1: a streamed call ends its span after the last chunk, with text and tokens", async () => {
    const client = new OpenAI({ apiKey: "test", maxRetries: 0, fetch: sse(openaiSse()) });
    const stream = await client.chat.completions.create({
      model: "gpt-4o-mini", messages: MESSAGES, stream: true, stream_options: { include_usage: true },
    });
    expect(sent).toHaveLength(0); // not ended when the stream is returned
    let text = "";
    for await (const chunk of stream) text += chunk.choices[0]?.delta?.content ?? "";
    expect(text).toBe("hola");
    const span = llm();
    expect(span.status).toBe("ok");
    expect(span.output).toEqual({ role: "assistant", content: "hola" });
    expect(span.tokens).toEqual({ input: 7, output: 2, total: 9 });
    expect(span.attributes["gen_ai.response.streamed"]).toBe(true);
  });

  it("C1: breaking out of a stream early leaves an incomplete span", async () => {
    const client = new OpenAI({ apiKey: "test", maxRetries: 0, fetch: sse(openaiSse()) });
    const stream = await client.chat.completions.create({ model: "gpt-4o-mini", messages: MESSAGES, stream: true });
    let n = 0;
    for await (const _ of stream) if (++n === 2) break;
    const span = llm();
    expect(span.status).toBe("unset");
    expect(span.attributes["observer.stream.incomplete"]).toBe(true);
  });
});

describe("Anthropic", () => {
  it("traces a call (the package is @anthropic-ai/sdk)", async () => {
    const client = new Anthropic({ apiKey: "test", maxRetries: 0, fetch: json(ANTHROPIC_OK) });
    const r = await client.messages.create({ model: "claude-sonnet-4-5", max_tokens: 10, messages: MESSAGES });
    expect(r.usage.output_tokens).toBe(3);
    const span = llm();
    expect(span.status).toBe("ok");
    expect(span.tokens).toEqual({ input: 12, output: 3, total: 15 });
    expect(span.costUsd).toBeGreaterThan(0);
    expect(span.attributes["gen_ai.response.finish_reasons"]).toEqual(["end_turn"]);
  });

  it("C1: a streamed call has the text, tokens and stop reason", async () => {
    const client = new Anthropic({ apiKey: "test", maxRetries: 0, fetch: sse(anthropicSse()) });
    const stream = await client.messages.create({
      model: "claude-sonnet-4-5", max_tokens: 10, messages: MESSAGES, stream: true,
    });
    let events = 0;
    for await (const _ of stream) events++;
    expect(events).toBe(7);
    const span = llm();
    expect(span.status).toBe("ok");
    expect(span.output).toEqual({ content: "hola" });
    expect(span.tokens).toEqual({ input: 12, output: 3, total: 15 });
  });
});

describe("C2: spans still open at shutdown", () => {
  it("are sent as partial, once", async () => {
    const root = tracer.startTrace("cut short");
    const done = tracer.startSpan("plan", { parent: root });
    tracer.endSpan(done);
    tracer.endSpan(done); // ending twice exports once
    tracer.startSpan("claude", { spanType: "llm", parent: root });
    await tracer.shutdown();
    await tracer.shutdown();
    const byName = Object.fromEntries(sent.map((s) => [s.name, s]));
    expect(sent.map((s) => s.name).sort()).toEqual(["claude", "cut short", "plan"]);
    expect(byName.claude.endTime).toBeUndefined();
    expect(byName.claude.status).toBe("unset");
    expect(byName.claude.attributes["observer.unfinished"]).toBe(true);
    expect(byName.plan.attributes["observer.unfinished"]).toBeUndefined();
  });
});
