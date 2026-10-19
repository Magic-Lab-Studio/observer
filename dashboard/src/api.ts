/**
 * Centralized API client for the LLM Observatory backend.
 */

import type {
  TraceDiagnostics,
  ProviderModelsResponse,
  Trace,
  Span,
  TraceListResponse,
  EvaluationListResponse,
  SummaryData,
  TimelinePoint,
  CostByModel,
  ManitOSQualitySummary,
  ProvidersResponse,
  AlertMetric,
  AlertRule,
  AlertEvent,
  EvaluationsByModel,
  QueryResult,
  QuerySchema,
  CaseExpect,
  DatasetCase,
  DatasetDetail,
  DatasetSummary,
  DatasetRunInfo,
  RunComparison,
  CaptureMode,
  PrivacyOverview,
  IngestHealth,
} from './types';

import { ApiError, classifyStatus, detailFromBody } from './helpers/errors';

const BASE_URL = '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch {
    throw new ApiError('network', null, null);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    // The dev proxy and nginx answer 502/504 when the backend itself is down.
    const kind = response.status === 502 || response.status === 504 ? 'network' : classifyStatus(response.status);
    throw new ApiError(kind, response.status, detailFromBody(body));
  }
  return response.json();
}

export const api = {
  // Traces
  async listTraces(params: {
    limit?: number;
    offset?: number;
    status?: string;
    search?: string;
    project_id?: string;
    environment?: string;
    session_id?: string;
    service_instance_id?: string;
  } = {}): Promise<TraceListResponse> {
    const qs = new URLSearchParams();
    if (params.limit) qs.set('limit', String(params.limit));
    if (params.offset) qs.set('offset', String(params.offset));
    if (params.status) qs.set('status', params.status);
    if (params.search) qs.set('search', params.search);
    if (params.project_id) qs.set('project_id', params.project_id);
    if (params.environment) qs.set('environment', params.environment);
    if (params.session_id) qs.set('session_id', params.session_id);
    if (params.service_instance_id) qs.set('service_instance_id', params.service_instance_id);
    const query = qs.toString();
    return request(`/v1/traces/${query ? `?${query}` : ''}`);
  },

  async getTrace(traceId: string): Promise<Trace> {
    return request(`/v1/traces/${traceId}`);
  },

  async getTraceDiagnostics(traceId: string): Promise<TraceDiagnostics> {
    return request(`/v1/traces/${encodeURIComponent(traceId)}/diagnostics`);
  },
  async getTraceSpans(traceId: string): Promise<Span[]> {
    return request(`/v1/traces/${traceId}/spans`);
  },

  // Evaluations
  async listEvaluations(params: {
    limit?: number;
    offset?: number;
    trace_id?: string;
    evaluator_type?: string;
    min_score?: number;
    max_score?: number;
  } = {}): Promise<EvaluationListResponse> {
    const qs = new URLSearchParams();
    if (params.limit) qs.set('limit', String(params.limit));
    if (params.offset) qs.set('offset', String(params.offset));
    if (params.trace_id) qs.set('trace_id', params.trace_id);
    if (params.evaluator_type) qs.set('evaluator_type', params.evaluator_type);
    if (params.min_score !== undefined) qs.set('min_score', String(params.min_score));
    if (params.max_score !== undefined) qs.set('max_score', String(params.max_score));
    const query = qs.toString();
    return request(`/v1/evaluations/${query ? `?${query}` : ''}`);
  },

  // Analytics
  async getSummary(hours = 168): Promise<SummaryData> {
    return request(`/v1/analytics/summary?hours=${hours}`);
  },

  async getTimeline(hours = 168, intervalMinutes = 360): Promise<TimelinePoint[]> {
    return request(`/v1/analytics/timeline?hours=${hours}&interval_minutes=${intervalMinutes}`);
  },

  async getCostByModel(hours = 168): Promise<CostByModel[]> {
    return request(`/v1/analytics/cost-by-model?hours=${hours}`);
  },

  async getManitOSQuality(hours = 168, projectId = 'manitos', environment?: string): Promise<ManitOSQualitySummary> {
    const qs = new URLSearchParams({ hours: String(hours), project_id: projectId });
    if (environment) qs.set('environment', environment);
    return request(`/v1/analytics/manitos-quality?${qs.toString()}`);
  },

  async getProviders(hours = 24, environment?: string): Promise<ProvidersResponse> {
    const qs = new URLSearchParams({ hours: String(hours) });
    if (environment) qs.set('environment', environment);
    return request(`/v1/providers/?${qs.toString()}`);
  },

  async getProviderModels(hours = 24, environment?: string): Promise<ProviderModelsResponse> {
    const q = new URLSearchParams({ hours: String(hours) });
    if (environment) q.set('environment', environment);
    return request(`/v1/providers/models?${q}`);
  },
  async getEvaluationsByModel(hours = 168): Promise<EvaluationsByModel> {
    return request(`/v1/evaluations/by-model?hours=${hours}`);
  },

  async getQuerySchema(): Promise<QuerySchema> {
    return request('/v1/analytics/query/schema');
  },
  async queryMetrics(body: unknown): Promise<QueryResult> {
    return request('/v1/analytics/query', { method: 'POST', body: JSON.stringify(body) });
  },

  // Datasets (test cases saved from traces; metadata only)
  async listDatasets(): Promise<DatasetSummary[]> {
    return request('/v1/datasets');
  },
  async createDataset(name: string): Promise<DatasetSummary> {
    return request('/v1/datasets', { method: 'POST', body: JSON.stringify({ name }) });
  },
  async getDataset(id: string): Promise<DatasetDetail> {
    return request(`/v1/datasets/${id}`);
  },
  async deleteDataset(id: string): Promise<void> {
    await request(`/v1/datasets/${id}`, { method: 'DELETE' });
  },
  async exportDataset(id: string): Promise<unknown> {
    return request(`/v1/datasets/${id}/export`);
  },
  async addCase(datasetId: string, body: { trace_id?: string; tags: string[]; expect: CaseExpect; label: string | null; notes: string | null }): Promise<DatasetCase> {
    return request(`/v1/datasets/${datasetId}/items`, { method: 'POST', body: JSON.stringify(body) });
  },
  async listRuns(datasetId: string): Promise<DatasetRunInfo[]> {
    return request(`/v1/datasets/${datasetId}/runs`);
  },
  async compareRuns(datasetId: string, base: string, candidate: string): Promise<RunComparison> {
    return request(`/v1/datasets/${datasetId}/compare?base=${base}&candidate=${candidate}`);
  },
  async deleteRun(id: string): Promise<void> {
    await request(`/v1/datasets/runs/${id}`, { method: 'DELETE' });
  },
  async deleteCase(id: string): Promise<void> {
    await request(`/v1/datasets/items/${id}`, { method: 'DELETE' });
  },

  // Privacy (capture mode per project)
  async getPrivacy(): Promise<PrivacyOverview> {
    return request('/v1/privacy/projects');
  },
  async setCaptureMode(project: string, mode: CaptureMode): Promise<void> {
    await request(`/v1/privacy/projects/${encodeURIComponent(project)}`, { method: 'PUT', body: JSON.stringify({ capture_mode: mode }) });
  },
  async clearCaptureMode(project: string): Promise<void> {
    await request(`/v1/privacy/projects/${encodeURIComponent(project)}`, { method: 'DELETE' });
  },
  async enforceCaptureMode(project: string): Promise<{ spans_scanned: number; spans_changed: number }> {
    return request(`/v1/privacy/projects/${encodeURIComponent(project)}/enforce`, { method: 'POST', body: JSON.stringify({ confirm: true }) });
  },

  // Is telemetry arriving?
  async getIngestHealth(): Promise<IngestHealth> {
    return request('/v1/ingest/health');
  },

  // Alerts
  async getAlertMetrics(): Promise<AlertMetric[]> {
    return request('/v1/alerts/metrics');
  },
  async listAlertRules(): Promise<AlertRule[]> {
    return request('/v1/alerts/rules');
  },
  async createAlertRule(rule: Partial<AlertRule>): Promise<AlertRule> {
    return request('/v1/alerts/rules', { method: 'POST', body: JSON.stringify(rule) });
  },
  async updateAlertRule(id: string, patch: Partial<AlertRule>): Promise<AlertRule> {
    return request(`/v1/alerts/rules/${id}`, { method: 'PATCH', body: JSON.stringify(patch) });
  },
  async deleteAlertRule(id: string): Promise<void> {
    await request(`/v1/alerts/rules/${id}`, { method: 'DELETE' });
  },
  async listAlertEvents(state?: 'open' | 'resolved', limit = 100): Promise<{ events: AlertEvent[]; open_count: number }> {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (state) qs.set('state', state);
    return request(`/v1/alerts/events?${qs.toString()}`);
  },
  async ackAlertEvent(id: string): Promise<AlertEvent> {
    return request(`/v1/alerts/events/${id}/ack`, { method: 'POST' });
  },
  async evaluateAlerts(): Promise<{ evaluated: number; opened: number; resolved: number }> {
    return request('/v1/alerts/evaluate', { method: 'POST' });
  },
};
