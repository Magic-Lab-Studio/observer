import { describe, it, expect } from "vitest";
import {
  OUTCOMES,
  CLOSE_KINDS,
  outcomeAttributes,
  gateAttributes,
  decisionAttributes,
  retrievalAttributes,
  toolAttributes,
  verificationAttributes,
  closeAttributes,
  turnAttributes,
} from "../src/index";

describe("agent contract attributes", () => {
  it("matches the backend's outcomes and close kinds", () => {
    expect([...OUTCOMES]).toEqual([
      "answered", "degraded", "abstained", "clarification", "refused", "failed",
    ]);
    expect([...CLOSE_KINDS]).toEqual(["answer", "clarify", "abstain", "refuse", "fail"]);
  });

  it("builds an abstention with its gate and close", () => {
    expect(retrievalAttributes({ name: "kb", passages: 4, sources: ["kb:1"] })).toEqual({
      "observer.span.type": "retrieval",
      "retrieval.name": "kb",
      "retrieval.passages": 4,
      "retrieval.sources": ["kb:1"],
    });
    expect(gateAttributes({ name: "coverage", status: "stop", reasonCodes: ["coverage_below_min"] }))
      .toEqual({
        "observer.span.type": "gate",
        "gate.name": "coverage",
        "gate.stage": "coverage",
        "gate.status": "stop",
        "gate.reason_codes": ["coverage_below_min"],
      });
    expect(closeAttributes("abstain")).toEqual({
      "observer.span.type": "close",
      "close.kind": "abstain",
    });
  });

  it("records rejected options with their reason and tool argument names only", () => {
    expect(decisionAttributes({ kind: "route", choice: "orders", rejected: { billing: "low" } }))
      .toMatchObject({ "decision.choice": "orders", "decision.rejected": ["billing:low"] });
    expect(toolAttributes({ name: "lookup", argNames: ["order_id"], errorCode: "not_found" }))
      .toMatchObject({ "tool.arg_names": ["order_id"], "tool.error_code": "not_found" });
    expect(verificationAttributes({ accepted: 3, rejected: 1 })).toMatchObject({
      "verification.claims_accepted": 3,
      "verification.claims_rejected": 1,
      "verification.claims_pruned": 0,
    });
  });

  it("rejects unknown outcomes and close kinds", () => {
    expect(outcomeAttributes("refused", "policy_block")).toEqual({
      "observer.outcome": "refused",
      "observer.outcome.reason": "policy_block",
    });
    // @ts-expect-error not an outcome
    expect(() => outcomeAttributes("great")).toThrow();
    // @ts-expect-error not a close kind
    expect(() => closeAttributes("maybe")).toThrow();
    expect(turnAttributes({ task: "support", userId: "u-1" })).toEqual({
      "observer.task": "support",
      "user.id": "u-1",
    });
  });
});
