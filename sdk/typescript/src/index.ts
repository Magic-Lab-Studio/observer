/**
 * LLM Observatory TypeScript SDK
 * Open-source LLM observability
 */

export {
  Tracer,
  trace,
  asyncTrace,
  getCurrentSpan,
  getTracer,
  setTracer,
} from "./tracer";
export type { Span, SpanStatus, TokenUsage, SpanExporter } from "./tracer";
export { instrument, uninstrument } from "./instrumentors";
export { OTLPExporter } from "./exporters";
export type { OTLPExporterOptions } from "./exporters";
export { calculateCost, MODEL_PRICING } from "./pricing";
export type { ModelPricing } from "./pricing";
export {
  OUTCOMES,
  CLOSE_KINDS,
  AGENT_SPAN_TYPES,
  SPAN_TYPE_ATTRIBUTE,
  outcomeAttributes,
  turnAttributes,
  gateAttributes,
  decisionAttributes,
  retrievalAttributes,
  toolAttributes,
  verificationAttributes,
  replanAttributes,
  closeAttributes,
} from "./agent";
export type { Outcome, CloseKind, AgentSpanType, GateStatus } from "./agent";
