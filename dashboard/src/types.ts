/**
 * Shared TypeScript types for the LLM Observatory dashboard.
 */

export interface Trace {
  id: string;
  name: string;
  session_id: string | null;
  turn_id: string | null;
  project_id: string | null;
  environment: string | null;
  service_instance_id: string | null;
  schema_version: string;
  start_time: string;
  end_time: string | null;
  status: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface Span {
  id: string;
  trace_id: string;
  parent_id: string | null;
  name: string;
  span_type: string;
  start_time: string;
  end_time: string | null;
  status: string;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  tokens_input: number | null;
  tokens_output: number | null;
  cost_usd: number | null;
  metadata: Record<string, unknown> | null;
  attributes: Record<string, unknown> | null;
}

export interface Evaluation {
  id: string;
  trace_id: string;
  span_id: string | null;
  evaluator_type: string;
  score: number | null;
  criteria: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  created_at: string;
}

export interface SummaryData {
  total_traces: number;
  total_spans: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  error_count: number;
  error_rate: number;
  total_evaluations: number;
  avg_evaluation_score: number;
}

export interface TimelinePoint {
  timestamp: string;
  count: number;
  cost_usd: number;
}

export interface CostByModel {
  model: string;
  cost_usd: number;
  span_count: number;
}

export interface QualityBreakdown {
  key: string;
  count: number;
  rate: number;
}

export interface ManitOSQualitySummary {
  project_id: string;
  environment: string | null;
  hours: number;
  total_turns: number;
  error_count: number;
  error_rate: number;
  degraded_count: number;
  degraded_rate: number;
  truncated_count: number;
  truncated_rate: number;
  tool_error_count: number;
  tool_error_rate: number;
  fallback_count: number;
  fallback_rate: number;
  tts_error_count: number;
  tts_error_rate: number;
  avg_duration_ms: number;
  avg_ttft_ms: number;
  models: QualityBreakdown[];
  languages: QualityBreakdown[];
}

export interface TraceListResponse {
  traces: Trace[];
  total: number;
  limit: number;
  offset: number;
}

export interface EvaluationListResponse {
  evaluations: Evaluation[];
  total: number;
  limit: number;
  offset: number;
}

export interface WebSocketMessage {
  type: string;
  data: unknown;
}

export interface ProviderQuota { model: string; returns_in_sec: number; kind: string }
export interface ProviderModel { model: string; label: string; role: string; fallback: boolean; selectable: boolean }
export interface ProviderInfo {
  id: string; label: string; kind: 'cloud' | 'local'; order: number; active: boolean; has_key: boolean;
  models: ProviderModel[]; data_note: string; out_of_quota: ProviderQuota[];
}
export interface ProviderStats {
  id: string; attempts: number; ok: number; rate_limited: number; timeout: number; error: number; skipped: number;
  answered: number; answered_share: number; success_rate: number | null;
  p50_ms: number | null; p95_ms: number | null; turn_p50_ms: number | null; last_seen: string | null;
  models: { model: string; attempts: number; ok: number; p50_ms: number | null }[];
}
export interface ProvidersResponse {
  hours: number; project_id: string; environment: string | null; turns: number;
  snapshot: { generated_at: number; received_at: number; stale: boolean; age_sec: number; providers: ProviderInfo[] } | null;
  providers: ProviderStats[];
  fallback_depth: { depth: number; count: number }[];
  paths: { path: string; count: number }[];
}

export interface ScoreGroup { key: string; count: number; avg_score: number | null; pass_rate: number | null }
export interface EvaluationsByModel {
  hours: number; project_id: string; count: number; avg_score: number | null; pass_rate: number | null;
  models: ScoreGroup[]; providers: ScoreGroup[]; grades: { grade: string; count: number }[];
}
export interface TurnCriterion { name: string; weight: number; score: number; detail: string }

export interface AlertMetric { id: string; label: string; unit: 'ratio' | 'ms' | 'count'; kind: string }
export interface AlertRule {
  id: string; name: string; metric: string; comparator: '>' | '>=' | '<' | '<='; threshold: number;
  window_minutes: number; min_samples: number; scope: string | null; project_id: string;
  environment: string | null; severity: 'info' | 'warning' | 'critical'; enabled: boolean;
  cooldown_minutes: number; created_at: string | null;
}
export interface AlertEvent {
  id: string; rule_id: string; rule_name: string; metric: string; scope: string | null;
  comparator: string; severity: 'info' | 'warning' | 'critical'; state: 'open' | 'resolved';
  value: number | null; threshold: number; window_minutes: number; fired_at: string | null;
  resolved_at: string | null; acked_at: string | null; details: Record<string, unknown> | null;
}
