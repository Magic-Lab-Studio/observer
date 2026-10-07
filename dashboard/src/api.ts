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
    return request(`/v1/traces${query ? `?${query}` : ''}`);
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
    return request(`/v1/evaluations${query ? `?${query}` : ''}`);
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

  async getManitOSQuality(hours = 168, projectId = 'manitos'): Promise<ManitOSQualitySummary> {
    const qs = new URLSearchParams({ hours: String(hours), project_id: projectId });
    return request(`/v1/analytics/manitos-quality?${qs.toString()}`);
  },
};
