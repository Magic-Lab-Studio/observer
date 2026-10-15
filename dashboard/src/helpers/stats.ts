import { parseApiDate } from '../dates';
import type { Trace } from '../types';
import { traceDurationMs } from './format';

export function percentile(sorted: number[], p: number): number | null {
  if (sorted.length === 0) return null;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[idx];
}

export function sortedDurations(traces: Trace[]): number[] {
  return traces.map(traceDurationMs).filter((d): d is number => d != null && d >= 0).sort((a, b) => a - b);
}

export interface Bucket {
  t: number;
  label: string;
  ok: number;
  err: number;
  total: number;
  p50: number | null;
  p95: number | null;
  errRate: number;
}

export function bucketTraces(traces: Trace[], startMs: number, endMs: number, count = 48): Bucket[] {
  const span = Math.max(1, endMs - startMs);
  const size = span / count;
  const groups: Trace[][] = Array.from({ length: count }, () => []);
  for (const tr of traces) {
    const at = parseApiDate(tr.created_at).getTime();
    const i = Math.min(count - 1, Math.floor((at - startMs) / size));
    if (i >= 0) groups[i].push(tr);
  }
  return groups.map((g, i) => {
    const d = sortedDurations(g);
    const err = g.filter((x) => x.status === 'error').length;
    const t = startMs + i * size;
    return {
      t,
      label: bucketLabel(t, span),
      ok: g.length - err,
      err,
      total: g.length,
      p50: percentile(d, 0.5),
      p95: percentile(d, 0.95),
      errRate: g.length ? err / g.length : 0,
    };
  });
}

export function bucketLabel(ms: number, spanMs: number): string {
  const d = parseApiDate(ms);
  if (spanMs <= 36 * 3600_000) {
    return d.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short' });
}

export const LATENCY_EDGES = [1000, 2000, 5000, 10000, 20000, 40000];

export function histogram(durations: number[]): { range: string; count: number; from: number }[] {
  const names = ['< 1 s', '1–2 s', '2–5 s', '5–10 s', '10–20 s', '20–40 s', '> 40 s'];
  const counts = new Array(names.length).fill(0);
  for (const d of durations) {
    let i = LATENCY_EDGES.findIndex((e) => d < e);
    if (i === -1) i = names.length - 1;
    counts[i] += 1;
  }
  return names.map((range, i) => ({ range, count: counts[i], from: i === 0 ? 0 : LATENCY_EDGES[i - 1] }));
}
