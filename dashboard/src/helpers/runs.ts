/** Pure helpers to present run comparisons (no React imports, so Node can test them). */

import type { CaseChange, RunVersion } from '../types';

/** "abc1234 · gemma" — what identifies a version in lists and headers. */
export function versionLabel(name: string | null, v: RunVersion): string {
  const parts = [v.commit ? v.commit.slice(0, 8) : '', v.model ?? ''].filter(Boolean);
  const id = parts.join(' · ');
  return name ? (id ? `${name} (${id})` : name) : (id || '–');
}

/** Config entries that differ between two versions, as "key: a → b" (missing side shown as –). */
export function configDiff(a: RunVersion, b: RunVersion): string[] {
  const ca = a.config ?? {};
  const cb = b.config ?? {};
  return [...new Set([...Object.keys(ca), ...Object.keys(cb)])].sort()
    .filter((k) => ca[k] !== cb[k])
    .map((k) => `${k}: ${ca[k] ?? '–'} → ${cb[k] ?? '–'}`);
}

/** Worst news first: regressions, then unknowns, improvements, unchanged, missing. */
const ORDER: CaseChange[] = ['regressed', 'unknown', 'improved', 'same', 'missing'];
export const changeRank = (c: CaseChange): number => ORDER.indexOf(c);

export function fmtDelta(v: number | null, digits = 2): string {
  if (v == null) return '–';
  const s = v.toFixed(digits);
  return v > 0 ? `+${s}` : s;
}
