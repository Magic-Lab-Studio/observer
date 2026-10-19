import { describe, expect, it } from "vitest";
import { calculateCost, resolveModel } from "./pricing";

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
