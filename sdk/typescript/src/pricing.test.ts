import { describe, expect, it } from "vitest";
import { MODEL_PRICING, UNPRICED, calculateCost, ratesOn, resolveModel } from "./pricing";

describe("pricing (evaluation A6)", () => {
  it("prices real dated Claude IDs", () => {
    expect(resolveModel("claude-sonnet-4-5-20250929")).toBe("claude-sonnet-4-5");
    expect(resolveModel("anthropic.claude-haiku-4-5-20251001-v1:0")).toBe("claude-haiku-4-5");
    expect(resolveModel("claude-opus-4-5@20251101")).toBe("claude-opus-4-5");
    expect(calculateCost("claude-sonnet-4-5-20250929", 1_000_000, 1_000_000)).toBeCloseTo(18);
  });
  it("leaves unknown or lookalike IDs unpriced", () => {
    for (const m of ["claude-sonnet-4-5-foo", "claude-sonnet-9", "gpt-4o-2024-05-13", ""]) {
      expect(calculateCost(m, 10, 10)).toBeNull();
    }
  });
  it("applies the Haiku 5.5 long-prompt tier", () => {
    expect(calculateCost("claude-haiku-5-5", 200_000, 1_000_000)).toBeCloseTo(2.6);
  });
});

describe("one price table for the SDKs and the backend (A6)", () => {
  it("every price names its source and the day it was checked", () => {
    for (const [model, p] of Object.entries(MODEL_PRICING)) {
      expect(p.source, model).toMatch(/^https:\/\/|secondary sources/);
      expect(p.checked, model).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
  });
  it("prices verified on the official pages, and models without one stay null with a reason", () => {
    expect(calculateCost("o3", 1_000_000, 1_000_000)).toBeCloseTo(10);
    expect(calculateCost("gemini-2.5-flash", 1_000_000, 0)).toBeCloseTo(0.3);
    for (const m of ["gpt-4", "o1", "claude-3-opus", "claude-3-5-haiku-20241022", "gemini-1.5-pro"]) {
      expect(calculateCost(m, 10, 10), m).toBeNull();
    }
    expect(UNPRICED["claude-3-opus"]).toMatch(/retired/);
  });
  it("applies a dated price change from its start day", () => {
    const p = MODEL_PRICING["gemini-3.8-flash"];
    expect(ratesOn(p, new Date("2026-12-31T12:00:00Z"))).toEqual([0.75, 3.75]);
    expect(ratesOn(p, new Date("2027-01-01T12:00:00Z"))).toEqual([1.5, 7.5]);
    expect(calculateCost("gemini-3.8-flash", 1_000_000, 0, new Date("2027-02-01"))).toBeCloseTo(1.5);
  });
});
