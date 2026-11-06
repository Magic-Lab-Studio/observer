import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  Tracer,
  Span,
  trace,
  asyncTrace,
  getTracer,
  setTracer,
  getCurrentSpan,
  calculateCost,
  MODEL_PRICING,
  SpanExporter,
} from "../src/index";

describe("Tracer", () => {
  let tracer: Tracer;

  beforeEach(() => {
    tracer = new Tracer({ serviceName: "test" });
  });

  it("creates a trace with correct properties", () => {
    const span = tracer.startTrace("test-trace");
    expect(span.name).toBe("test-trace");
    expect(span.spanType).toBe("trace");
    expect(span.id).toBeDefined();
    expect(span.traceId).toBeDefined();
    expect(span.status).toBe("unset");
  });

  it("creates a span with parent", () => {
    const root = tracer.startTrace("root");
    const child = tracer.startSpan("child", { parent: root });
    expect(child.traceId).toBe(root.traceId);
    expect(child.parentId).toBe(root.id);
  });

  it("exports spans in backend format", () => {
    const span = tracer.startTrace("test");
    span.endTime = span.startTime + 1;
    span.tokens = { input: 100, output: 50, total: 150 };

    const exported = tracer.export();
    expect(exported).toHaveLength(1);
    expect(exported[0].trace_id).toBe(span.traceId);
    expect(exported[0].tokens_input).toBe(100);
    expect(exported[0].tokens_output).toBe(50);
    // Should NOT have nested tokens object
    expect(exported[0].tokens).toBeUndefined();
  });

  it("clears spans", () => {
    tracer.startTrace("test");
    expect(tracer.export()).toHaveLength(1);
    tracer.clear();
    expect(tracer.export()).toHaveLength(0);
  });
});

describe("trace function", () => {
  it("wraps sync function", () => {
    const result = trace("test", (span) => {
      span.attributes["key"] = "value";
      return 42;
    });
    expect(result).toBe(42);
  });

  it("captures errors", () => {
    expect(() => {
      trace("test", () => {
        throw new Error("test error");
      });
    }).toThrow("test error");
  });

  it("sets status ok on success", () => {
    let capturedSpan: Span | undefined;
    trace("test", (span) => {
      capturedSpan = span;
      return 42;
    });
    expect(capturedSpan!.status).toBe("ok");
    expect(capturedSpan!.endTime).toBeDefined();
  });
});

describe("asyncTrace function", () => {
  it("wraps async function", async () => {
    const result = await asyncTrace("test", async (span) => {
      span.attributes["key"] = "value";
      return 42;
    });
    expect(result).toBe(42);
  });

  it("captures async errors", async () => {
    await expect(
      asyncTrace("test", async () => {
        throw new Error("async error");
      })
    ).rejects.toThrow("async error");
  });

  it("propagates context via AsyncLocalStorage", async () => {
    let innerSpan: Span | undefined;
    await asyncTrace("outer", async (outerSpan) => {
      expect(getCurrentSpan()?.id).toBe(outerSpan.id);
      await asyncTrace("inner", async (innerSpanArg) => {
        innerSpan = innerSpanArg;
        expect(innerSpanArg.parentId).toBe(outerSpan.id);
        expect(innerSpanArg.traceId).toBe(outerSpan.traceId);
      });
    });
    expect(innerSpan).toBeDefined();
  });
});

describe("Global tracer", () => {
  it("creates default tracer", () => {
    const t = getTracer();
    expect(t).toBeInstanceOf(Tracer);
  });

  it("allows setting custom tracer", () => {
    const custom = new Tracer({ serviceName: "custom" });
    setTracer(custom);
    expect(getTracer()).toBe(custom);
    // Reset
    setTracer(new Tracer());
  });
});

describe("Tracer exporter integration", () => {
  let tracer: Tracer;
  let mockExporter: SpanExporter;

  beforeEach(() => {
    tracer = new Tracer({ serviceName: "test" });
    mockExporter = {
      export: vi.fn(),
    };
  });

  it("calls exporter when trace ends via trace()", () => {
    tracer.addExporter(mockExporter);
    setTracer(tracer);

    trace("test", (span) => {
      span.attributes["key"] = "value";
      return 42;
    });

    expect(mockExporter.export).toHaveBeenCalledTimes(1);
    const exportedSpans = (mockExporter.export as any).mock.calls[0][0];
    expect(exportedSpans).toHaveLength(1);
    expect(exportedSpans[0].status).toBe("ok");

    setTracer(new Tracer());
  });

  it("calls exporter when async trace ends", async () => {
    tracer.addExporter(mockExporter);
    setTracer(tracer);

    await asyncTrace("test", async (span) => {
      return "done";
    });

    expect(mockExporter.export).toHaveBeenCalledTimes(1);
    setTracer(new Tracer());
  });

  it("calls exporter on endSpan()", () => {
    tracer.addExporter(mockExporter);
    const span = tracer.startTrace("test");
    tracer.endSpan(span);

    expect(mockExporter.export).toHaveBeenCalledTimes(1);
    expect(span.endTime).toBeDefined();
  });

  it("does not double-end a span", () => {
    tracer.addExporter(mockExporter);
    const span = tracer.startTrace("test");
    span.endTime = 100;
    tracer.endSpan(span);

    // endTime should not change
    expect(span.endTime).toBe(100);
    expect(mockExporter.export).toHaveBeenCalledTimes(1);
  });

  it("supports multiple exporters", () => {
    const exporter2: SpanExporter = { export: vi.fn() };
    tracer.addExporter(mockExporter);
    tracer.addExporter(exporter2);

    const span = tracer.startTrace("test");
    tracer.endSpan(span);

    expect(mockExporter.export).toHaveBeenCalledTimes(1);
    expect(exporter2.export).toHaveBeenCalledTimes(1);
  });

  it("removeExporter works", () => {
    tracer.addExporter(mockExporter);
    tracer.removeExporter(mockExporter);

    const span = tracer.startTrace("test");
    tracer.endSpan(span);

    expect(mockExporter.export).not.toHaveBeenCalled();
  });

  it("flush calls exporter flush", async () => {
    const flushExporter: SpanExporter = {
      export: vi.fn(),
      flush: vi.fn(),
    };
    tracer.addExporter(flushExporter);

    await tracer.flush();

    expect(flushExporter.flush).toHaveBeenCalledTimes(1);
  });

  it("exporter errors do not break tracing", () => {
    const badExporter: SpanExporter = {
      export: () => {
        throw new Error("export failed");
      },
    };
    tracer.addExporter(badExporter);

    // Should not throw
    const result = trace("test", () => 42);
    expect(result).toBe(42);
  });
});

describe("Pricing", () => {
  it("calculates cost for known OpenAI model", () => {
    const cost = calculateCost("gpt-4o", 1000, 500);
    // (1000/1M)*2.5 + (500/1M)*10.0 = 0.0025 + 0.005 = 0.0075
    expect(cost).toBeCloseTo(0.0075, 6);
  });

  it("calculates cost for known Anthropic model", () => {
    const cost = calculateCost("claude-sonnet-4-5", 1000, 500);
    expect(cost).toBeCloseTo(0.0105, 6);
  });

  it("returns null for unknown model", () => {
    expect(calculateCost("unknown-model", 1000, 500)).toBeNull();
  });

  it("has pricing for all expected models", () => {
    const expected = [
      "gpt-4o", "gpt-4o-mini", "o3",
      "claude-4-opus", "claude-4-sonnet", "claude-sonnet-4-5", "claude-haiku-4-5",
      "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.8-flash",
    ];
    for (const model of expected) {
      expect(MODEL_PRICING[model]).toBeDefined();
      expect(MODEL_PRICING[model].input).toBeGreaterThan(0);
      expect(MODEL_PRICING[model].output).toBeGreaterThan(0);
    }
  });
});

describe("Service and environment travel with every span (finding P2)", () => {
  it("sends service.name, the environment and shared attributes; the span's own value wins", () => {
    const sent: Span[] = [];
    const tracer = new Tracer({
      serviceName: "billing-agent",
      environment: "staging",
      attributes: { "observer.task": "refunds", "vcs.commit": "abc" },
    });
    tracer.addExporter({ export: (spans) => sent.push(...spans) });
    const span = tracer.startSpan("lookup", { attributes: { "vcs.commit": "def" } });
    tracer.endSpan(span);
    expect(sent[0].attributes).toEqual({
      "service.name": "billing-agent",
      "deployment.environment.name": "staging",
      "observer.task": "refunds",
      "vcs.commit": "def",
    });
    expect(span.attributes).toEqual({ "vcs.commit": "def" }); // the span itself is not changed
    expect(tracer.export()[0].attributes).toEqual(sent[0].attributes);
  });

  it("defaults to llm-observatory without an environment", () => {
    const sent: Span[] = [];
    const tracer = new Tracer();
    tracer.addExporter({ export: (spans) => sent.push(...spans) });
    tracer.endSpan(tracer.startSpan("x"));
    expect(sent[0].attributes).toEqual({ "service.name": "llm-observatory" });
  });
});
