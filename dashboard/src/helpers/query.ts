// Pure helpers for the metrics explorer and for pages that read /v1/analytics/query. No imports:
// the Node test runner loads this file directly, and it must not construct Dates from values
// (timestamps arrive as epoch milliseconds, already parsed by the caller).

export type QueryView = 'turns' | 'provider_attempts' | 'evaluations';

export interface ExploreState {
  view: QueryView;
  measure: string; // a numeric measure, or "count"
  aggregation: string;
  dimension: string; // "" = no grouping
  granularity: string; // "" = no time series
}

export const DEFAULT_EXPLORE: ExploreState = {
  view: 'turns',
  measure: 'count',
  aggregation: 'count',
  dimension: '',
  granularity: 'auto',
};

const VIEWS: QueryView[] = ['turns', 'provider_attempts', 'evaluations'];
const AGGREGATIONS = ['count', 'sum', 'avg', 'min', 'max', 'p50', 'p95', 'p99'];
const GRANULARITIES = ['', 'auto', 'minute', 'hour', 'day'];
const KEYS = { view: 'view', measure: 'm', aggregation: 'a', dimension: 'd', granularity: 'g' } as const;

export function metricKey(measure: string, aggregation: string): string {
  return aggregation === 'count' && measure === 'count' ? 'count' : `${aggregation}_${measure}`;
}

/** URL query string of a state (defaults are written too, so a shared link is explicit). */
export function stateToSearch(s: ExploreState): string {
  const p = new URLSearchParams();
  p.set(KEYS.view, s.view);
  p.set(KEYS.measure, s.measure);
  p.set(KEYS.aggregation, s.aggregation);
  if (s.dimension) p.set(KEYS.dimension, s.dimension);
  p.set(KEYS.granularity, s.granularity === '' ? 'none' : s.granularity);
  return p.toString();
}

/** Read a state back, discarding anything that is not a known value (links can be edited by hand). */
export function searchToState(search: string): ExploreState {
  const p = new URLSearchParams(search);
  const view = p.get(KEYS.view) as QueryView | null;
  const aggregation = p.get(KEYS.aggregation) ?? '';
  const g = p.get(KEYS.granularity);
  const measure = (p.get(KEYS.measure) ?? '').replace(/[^a-z_]/g, '');
  return {
    view: view && VIEWS.includes(view) ? view : DEFAULT_EXPLORE.view,
    measure: measure || DEFAULT_EXPLORE.measure,
    aggregation: AGGREGATIONS.includes(aggregation) ? aggregation : DEFAULT_EXPLORE.aggregation,
    dimension: (p.get(KEYS.dimension) ?? '').replace(/[^a-z_]/g, ''),
    granularity: g === 'none' ? '' : g && GRANULARITIES.includes(g) ? g : DEFAULT_EXPLORE.granularity,
  };
}

export interface QueryBody {
  view: QueryView;
  metrics: { measure: string; aggregation: string }[];
  dimensions: string[];
  filters: { field: string; op: string; value: string }[];
  hours: number;
  granularity?: string;
  row_limit: number;
}

export function buildRequest(s: ExploreState, opts: { hours: number; environment?: string }): QueryBody {
  const body: QueryBody = {
    view: s.view,
    metrics: [{ measure: s.measure, aggregation: s.aggregation }],
    dimensions: s.dimension ? [s.dimension] : [],
    filters: opts.environment ? [{ field: 'environment', op: '=', value: opts.environment }] : [],
    hours: opts.hours,
    row_limit: 1000,
  };
  if (s.granularity) body.granularity = s.granularity;
  return body;
}

/** The count of a measure name never needs an aggregation other than count. */
export function normalizeMetric(measure: string, aggregation: string): { measure: string; aggregation: string } {
  if (measure === 'count') return { measure, aggregation: 'count' };
  return { measure, aggregation };
}

export function formatMeasure(measure: string, aggregation: string, value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '–';
  if (aggregation === 'count') return String(Math.round(value));
  if (measure.endsWith('_ms') || measure === 'ms') {
    return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
  }
  const flag = ['error', 'degraded', 'truncated', 'tool_error', 'fallback', 'ok', 'failed', 'passed'].includes(measure);
  if (flag && aggregation === 'avg') return `${(value * 100).toFixed(1)} %`;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

const STEP_MS: Record<string, number> = { minute: 60_000, hour: 3_600_000, day: 86_400_000 };

export function granularityStep(g: string): number {
  return STEP_MS[g] ?? STEP_MS.hour;
}

/** Same rule as the backend's "auto": minutes up to 3 h, hours up to 72 h, days beyond. */
export function autoGranularity(hours: number): string {
  return hours <= 3 ? 'minute' : hours <= 72 ? 'hour' : 'day';
}

export function bucketFloor(ms: number, granularity: string): number {
  const step = granularityStep(granularity);
  return Math.floor(ms / step) * step; // epoch-aligned, i.e. UTC boundaries like the backend
}

export interface TimedRow {
  t: number;
  [field: string]: number | string | null;
}

/** One entry per bucket between start and end, with `null` where the backend returned no row. */
export function fillBuckets<T extends { t: number }>(
  rows: T[], startMs: number, endMs: number, granularity: string,
): { t: number; row: T | null }[] {
  const step = granularityStep(granularity);
  const byT = new Map(rows.map((r) => [bucketFloor(r.t, granularity), r]));
  const out: { t: number; row: T | null }[] = [];
  for (let t = bucketFloor(startMs, granularity); t <= endMs && out.length < 2000; t += step) {
    out.push({ t, row: byT.get(t) ?? null });
  }
  return out;
}

/** Rows of (time, dimension, value) -> one object per bucket with a column per dimension value. */
export function pivotSeries(
  rows: { t: number; dim: string; value: number | null }[], maxSeries = 6,
): { data: Record<string, number | null>[]; series: string[] } {
  const totals = new Map<string, number>();
  for (const r of rows) totals.set(r.dim, (totals.get(r.dim) ?? 0) + Math.abs(r.value ?? 0));
  const series = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, maxSeries).map(([k]) => k);
  const byTime = new Map<number, Record<string, number | null>>();
  for (const r of rows) {
    if (!series.includes(r.dim)) continue;
    const entry = byTime.get(r.t) ?? { t: r.t };
    entry[r.dim] = r.value;
    byTime.set(r.t, entry);
  }
  return { data: [...byTime.values()].sort((a, b) => (a.t as number) - (b.t as number)), series };
}

export interface SavedView {
  name: string;
  state: ExploreState;
}

/** Saved views live in localStorage as JSON; anything malformed is dropped. */
export function parseSavedViews(raw: string | null): SavedView[] {
  if (!raw) return [];
  try {
    const list = JSON.parse(raw);
    if (!Array.isArray(list)) return [];
    return list
      .filter((v) => v && typeof v.name === 'string' && v.state && typeof v.state === 'object')
      .map((v) => ({
        name: String(v.name).slice(0, 60),
        state: searchToState(stateToSearch({ ...DEFAULT_EXPLORE, ...v.state })),
      }))
      .slice(0, 30);
  } catch {
    return [];
  }
}
