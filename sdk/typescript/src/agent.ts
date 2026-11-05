/**
 * The agent span contract `observer.agent.v1` (docs/agent-spans.md).
 *
 * Builders that return the contract's attributes, to merge into a span's `attributes` (or an
 * OpenTelemetry span's) together with its span type. Values are metadata only: names, codes,
 * counts and ids. Arguments, passages and replies are content and belong in input/output.
 *
 *   const span = tracer.startSpan("gate.coverage", "gate");
 *   Object.assign(span.attributes, gateAttributes({ name: "coverage", status: "stop",
 *     reasonCodes: ["coverage_below_min"] }));
 */

export const OUTCOMES = [
  "answered",
  "degraded",
  "abstained",
  "clarification",
  "refused",
  "failed",
  "cancelled",
] as const;
export type Outcome = (typeof OUTCOMES)[number];

export const CLOSE_KINDS = ["answer", "clarify", "abstain", "refuse", "fail", "cancel"] as const;
export type CloseKind = (typeof CLOSE_KINDS)[number];

export const AGENT_SPAN_TYPES = [
  "decision",
  "tool",
  "retrieval",
  "gate",
  "verification",
  "replan",
  "close",
] as const;
export type AgentSpanType = (typeof AGENT_SPAN_TYPES)[number];

export type GateStatus = "pass" | "stop" | "skip";

/** The attribute that names the contract type where the transport has none (OTLP). */
export const SPAN_TYPE_ATTRIBUTE = "observer.span.type";

type Attributes = Record<string, string | number | boolean | string[]>;

function codes(values?: readonly string[]): string[] | undefined {
  const out = (values ?? []).map((v) => String(v).slice(0, 80)).filter((v) => v.trim()).slice(0, 16);
  return out.length ? out : undefined;
}

function compact(type: AgentSpanType | null, attrs: Record<string, unknown>): Attributes {
  const out: Attributes = type ? { [SPAN_TYPE_ATTRIBUTE]: type } : {};
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) out[key] = value as Attributes[string];
  }
  return out;
}

/** How the turn ended; set it on the turn's root span. Throws on an unknown outcome. */
export function outcomeAttributes(outcome: Outcome, reason?: string): Attributes {
  if (!(OUTCOMES as readonly string[]).includes(outcome)) {
    throw new Error(`unknown outcome ${outcome}; one of ${OUTCOMES.join(", ")}`);
  }
  return compact(null, {
    "observer.outcome": outcome,
    "observer.outcome.reason": reason?.slice(0, 160),
  });
}

export function turnAttributes(opts: { task?: string; userId?: string }): Attributes {
  return compact(null, {
    "observer.task": opts.task?.slice(0, 80),
    "user.id": opts.userId?.slice(0, 128),
  });
}

export function gateAttributes(opts: {
  name: string;
  status: GateStatus;
  stage?: string;
  requirement?: string;
  reasonCodes?: readonly string[];
}): Attributes {
  return compact("gate", {
    "gate.name": opts.name,
    "gate.stage": opts.stage ?? opts.name,
    "gate.status": opts.status,
    "gate.requirement": opts.requirement,
    "gate.reason_codes": codes(opts.reasonCodes),
  });
}

export function decisionAttributes(opts: {
  kind: string;
  options?: readonly string[];
  choice?: string;
  rejected?: Record<string, string>;
  confidence?: number;
  finalBy?: string;
}): Attributes {
  const rejected = Object.entries(opts.rejected ?? {}).map(([k, v]) => `${k}:${v}`.slice(0, 120));
  return compact("decision", {
    "decision.kind": opts.kind,
    "decision.options": opts.options?.length ? [...opts.options].slice(0, 16) : undefined,
    "decision.choice": opts.choice,
    "decision.rejected": rejected.length ? rejected.slice(0, 16) : undefined,
    "decision.confidence": opts.confidence,
    "decision.final_by": opts.finalBy,
  });
}

export function retrievalAttributes(opts: {
  name: string;
  passages: number;
  sources?: readonly string[];
  topScore?: number;
  index?: string;
}): Attributes {
  return compact("retrieval", {
    "retrieval.name": opts.name,
    "retrieval.passages": Math.max(0, Math.trunc(opts.passages)),
    "retrieval.sources": opts.sources?.length ? [...opts.sources].slice(0, 32) : undefined,
    "retrieval.top_score": opts.topScore,
    "retrieval.index": opts.index,
  });
}

export function toolAttributes(opts: {
  name: string;
  argNames?: readonly string[];
  resultSize?: number;
  errorCode?: string;
}): Attributes {
  return compact("tool", {
    "tool.name": opts.name,
    "tool.arg_names": opts.argNames?.length ? [...opts.argNames].slice(0, 16) : undefined,
    "tool.result_size": opts.resultSize,
    "tool.error_code": opts.errorCode,
  });
}

export function verificationAttributes(opts: {
  accepted: number;
  rejected?: number;
  pruned?: number;
  reasonCodes?: readonly string[];
}): Attributes {
  return compact("verification", {
    "verification.claims_accepted": opts.accepted,
    "verification.claims_rejected": opts.rejected ?? 0,
    "verification.claims_pruned": opts.pruned ?? 0,
    "verification.reason_codes": codes(opts.reasonCodes),
  });
}

export function replanAttributes(reason: string, attempt?: number): Attributes {
  return compact("replan", { "replan.reason": reason, "replan.attempt": attempt });
}

/** End of the turn's reasoning. Throws on an unknown kind. */
export function closeAttributes(kind: CloseKind, reasonCode?: string): Attributes {
  if (!(CLOSE_KINDS as readonly string[]).includes(kind)) {
    throw new Error(`unknown close kind ${kind}; one of ${CLOSE_KINDS.join(", ")}`);
  }
  return compact("close", { "close.kind": kind, "close.reason_code": reasonCode });
}
