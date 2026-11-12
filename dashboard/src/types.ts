/**
 * Shared TypeScript types for the LLM Observatory dashboard.
 */

import type { OutcomeInfo } from './helpers/outcomes';

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
  /** How the turn ended (agent contract observer.agent.v1); filled in trace listings. */
  outcome?: string | null;
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

export interface OverviewData {
  from: string; to: string; bucket_ms: number;
  buckets: { t: number; total: number; err: number; p50: number | null; p95: number | null }[];
  total: number; errors: number;
  durations: { count: number; p50: number | null; p95: number | null; max: number | null };
  histogram: { from: number; count: number }[];
  slowest: { id: string; name: string; turn_id: string | null; status: string; duration_ms: number }[];
  stages: { stage: string; avg: number; p95: number; n: number }[];
  samples: number; truncated: boolean;
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
  completed_traces?: number;
  running_traces?: number;
  trace_error_rate?: number | null;
  span_error_count?: number;
  llm_span_count?: number;
  tool_span_count?: number;
  priced_span_count?: number;
  p50_trace_duration_ms?: number | null;
  p95_trace_duration_ms?: number | null;
}

export interface TimelinePoint {
  timestamp: string;
  count: number;
  cost_usd: number;
  error_count?: number;
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
  avg_duration_ms: number | null;
  avg_ttft_ms: number | null;
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

export interface ProviderModelRow {
  provider: string; model: string; calls: number; errors: number; error_rate: number;
  p50_ms: number | null; p95_ms: number | null; tokens_input: number | null; tokens_output: number | null;
  cost_usd: number | null; priced_calls: number; last_seen: string | null;
}
export interface ProviderModelsResponse {
  hours: number; project_id: string | null; environment: string | null; calls: number;
  models: ProviderModelRow[]; truncated: boolean;
}

export interface ScoreGroup {
  key: string; count: number; avg_score: number | null; avg_health: number | null;
  pass_rate: number | null; correctness_known: number;
}
export interface GroundednessBlock {
  count: number; scored: number; avg_score: number | null; ungrounded_rate: number | null;
  unknown: number; not_applicable: number;
}
export interface EvaluationsByModel {
  hours: number; project_id: string; count: number; avg_score: number | null; avg_health: number | null;
  pass_rate: number | null; correctness_known: number;
  models: ScoreGroup[]; providers: ScoreGroup[]; grades: { grade: string; count: number }[];
  groundedness?: GroundednessBlock & { models: (GroundednessBlock & { key: string })[] };
}

export type GroundednessMode = 'off' | 'rules' | 'judge' | 'rules+judge';
export interface GroundednessConfig {
  mode: GroundednessMode;
  context: { span_types: string[]; name_patterns: string[]; include_input: boolean; max_chars_per_span: number; max_chars_per_trace: number };
  applies_to: string[];
  threshold: number;
}
export interface GroundednessSection {
  project_id: string; section: 'groundedness'; value: GroundednessConfig; source: 'setting' | 'default';
  built_modes: GroundednessMode[];
}
export interface TurnCriterion { name: string; weight: number; score: number; detail: string }

export interface QueryField { id: string; label: string }
export interface QuerySchema {
  views: Record<string, { label: string; measures: QueryField[]; dimensions: QueryField[]; filter_only: QueryField[] }>;
  aggregations: string[];
  granularities: string[];
}
export interface QueryResult {
  view: string; from: string; to: string; granularity: string | null; samples: number; truncated: boolean;
  rows: Record<string, string | number | null>[];
}

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

export interface CaseExpect {
  tools: { required: string[]; forbidden: string[] };
  health: { no_error: boolean; no_truncation: boolean; no_degradation: boolean; no_fallback: boolean; max_duration_ms: number | null };
  text: { must_contain: string[]; must_not_contain: string[] };
}
export interface DatasetSummary { id: string; name: string; project_id: string; description: string | null; items: number; created_at: string | null }
export interface DatasetCase {
  id: string; dataset_id: string; source_trace_id: string | null; intent: string | null; tags: string[];
  expect: CaseExpect; checks: string[]; label: 'correct' | 'incorrect' | null; notes: string | null;
  observed: Record<string, unknown>; created_at: string | null;
}
export interface DatasetDetail { id: string; name: string; project_id: string; description: string | null; items: DatasetCase[] }

export interface RunVersion { commit?: string | null; model?: string | null; config?: Record<string, string | number | boolean>; rubric?: string }
export interface RunSummary { pass: number; fail: number; unknown: number; cases: number; pass_rate: number | null; avg_health: number | null }
export interface DatasetRunInfo { id: string; dataset_id: string; name: string | null; version: RunVersion; created_at: string | null; summary: RunSummary }
export type CaseChange = 'regressed' | 'improved' | 'same' | 'unknown' | 'missing';
export interface RunComparison {
  base: DatasetRunInfo; candidate: DatasetRunInfo;
  summary: Record<CaseChange, number>;
  cases: { item_id: string; change: CaseChange; base: { status: string; health_score: number | null } | null; candidate: { status: string; health_score: number | null } | null; health_delta: number | null; duration_delta_ms: number | null }[];
}

export type CaptureMode = 'metadata' | 'redacted' | 'full';
export interface PrivacyReport {
  sampled_spans: number; with_input: number; with_output: number;
  with_content_like_attributes: number; with_sensitive_patterns: number;
}
export interface PrivacyProject {
  project_id: string; capture_mode: CaptureMode | null; source: 'setting' | 'default';
  default_mode: CaptureMode; report: PrivacyReport;
}
export interface PrivacyOverview { modes: CaptureMode[]; note: string; projects: PrivacyProject[] }
export interface IngestProject {
  project_id: string; last_trace_at: string | null; silence_minutes: number | null;
  traces_5m: number; traces_1h: number; traces_24h: number;
}
export interface IngestHealth { checked_at: string; projects: IngestProject[] }

export interface RepeatedCalls {
  tool: string; parent_span_id: string | null; count: number; errors: number;
  arguments_known: boolean; same_output: boolean; span_ids: string[]; window_ms: number | null; severity: 'warning';
}
export interface CriticalPath {
  llm_calls: number; sequential_calls: number; trace_ms: number | null;
  parallelisable: { span_ids: string[]; names: string[]; calls: number; duration_ms: number; latency_share: number | null }[];
}
export interface TraceDiagnostics extends OutcomeInfo { trace_id: string; min_repeats: number; repeated_calls: RepeatedCalls[]; critical_path?: CriticalPath }
/** GET /v1/agent/funnel (roadmap step 14). */
export interface GateFunnel {
  project_id: string; hours: number; turns: number; turns_with_gates: number; truncated: boolean;
  gates: { gate: string; turns: number; passed: number; skipped: number; stopped: number; stop_rate: number; reason_codes: { code: string; count: number }[]; position: number }[];
}
export interface ProjectInfo { project_id: string; trace_count: number; last_trace_at: string | null }

// Governance views (/v1/governance): decision, security, approval and skill spans.
export type GovernanceKind = 'decisions' | 'denials' | 'approvals' | 'skills';
export type GovernanceAttrValue = string | number | boolean | null | (string | number | boolean | null)[];
export interface GovernanceItem {
  span_id: string; trace_id: string; session_id: string | null; turn_id: string | null;
  name: string; span_type: string; status: string; start_time: string | null; duration_ms: number | null;
  attributes: Record<string, GovernanceAttrValue>;
}
export interface GovernanceListing {
  span_type: string; project_id: string; hours: number; total: number; truncated_scan: boolean;
  items: GovernanceItem[];
}
export interface GovernanceSummary {
  project_id: string; hours: number;
  counts: Record<'decision' | 'security' | 'approval' | 'skill', { total: number; by: Record<string, number> }>;
}
export interface GovernanceStep extends GovernanceItem { outcome: string | null }
export interface GovernanceTurn {
  trace_id: string; session_id: string | null; turn_id: string | null; status: string;
  steps: GovernanceStep[]; unanswered_asks: string[]; complete: boolean;
}
