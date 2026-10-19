import { parseApiDate } from '../dates.ts';
import { localeTag, t } from '../i18n.ts';
import type { Trace } from '../types.ts';

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '–';
  if (ms === 0) return '0 ms';
  if (ms < 1) return '<1 ms';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  const m = Math.floor(ms / 60_000);
  return `${m} min ${Math.round((ms % 60_000) / 1000)} s`;
}

export function fmtPct(rate: number | null | undefined, digits = 1): string {
  if (rate == null || Number.isNaN(rate)) return '–';
  return `${(rate * 100).toFixed(digits)}%`;
}

export function fmtInt(n: number | null | undefined): string {
  return n == null ? '–' : n.toLocaleString(localeTag());
}

/** Dollars and cents from $1, four decimals below; under a cent, three significant digits so a
 * $0.0000012 call does not round to $0.000001 (B4). Display only: the API keeps full precision. */
export function fmtUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '–';
  if (n === 0) return '$0';
  const abs = Math.abs(n);
  if (abs >= 1) return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (abs >= 0.01) return `$${n.toFixed(4)}`;
  const digits = Math.min(20, 2 - Math.floor(Math.log10(abs)));
  return `$${n.toFixed(digits).replace(/(\.\d*?[1-9])0+$/, '$1')}`;
}

export function shortId(id: string | null | undefined, n = 8): string {
  if (!id) return '–';
  const clean = id.replace(/^(session|turn)_/, '');
  return clean.length > n ? `${clean.slice(0, n)}…` : clean;
}

export function timeAgo(value: string): string {
  const s = Math.max(0, Math.floor((Date.now() - parseApiDate(value).getTime()) / 1000));
  if (s < 5) return t('ahora');
  if (s < 60) return t('hace {n} s', { n: s });
  const m = Math.floor(s / 60);
  if (m < 60) return t('hace {n} min', { n: m });
  const h = Math.floor(m / 60);
  if (h < 24) return t('hace {n} h', { n: h });
  return t('hace {n} d', { n: Math.floor(h / 24) });
}

export function clockTime(value: string | number): string {
  const d = parseApiDate(value);
  return d.toLocaleTimeString(localeTag(), { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

export function traceDurationMs(t: Pick<Trace, 'start_time' | 'end_time'>): number | null {
  if (!t.end_time) return null;
  return parseApiDate(t.end_time).getTime() - parseApiDate(t.start_time).getTime();
}

export function stageTimings(t: Trace): Record<string, number> {
  const raw = (t.metadata as { stage_timings_ms?: unknown } | null)?.stage_timings_ms;
  if (!raw || typeof raw !== 'object') return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

export function stageLabel(key: string): string {
  return key.replace(/^(text|audio)\./, '');
}

export function fmtAxisMs(ms: number): string {
  if (ms <= 0) return '0';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${+(ms / 1000).toFixed(1)}s`;
  return `${+(ms / 60_000).toFixed(1)}min`;
}
