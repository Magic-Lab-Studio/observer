/**
 * Centralized API client for the LLM Observatory backend.
 */

import type {
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
} from './types';

const BASE_URL = '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
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
