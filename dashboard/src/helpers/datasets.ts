/** Pure helpers for the test-case form (no React imports, so Node can test them). */

import type { CaseExpect } from '../types';

export interface CaseForm {
  toolsRequired: string; toolsForbidden: string;
  noError: boolean; noTruncation: boolean; noDegradation: boolean; noFallback: boolean; maxSeconds: string;
  mustContain: string; mustNotContain: string;
}

export const emptyCaseForm = (): CaseForm => ({
  toolsRequired: '', toolsForbidden: '', noError: false, noTruncation: false, noDegradation: false,
  noFallback: false, maxSeconds: '', mustContain: '', mustNotContain: '',
});

/** "a, b ,, a" -> ["a", "b"] (comma or new line separated, trimmed, deduplicated). */
export function splitList(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[,\n]/)) {
    const v = part.trim();
    if (v) seen.add(v);
  }
  return [...seen];
}

export function buildExpect(f: CaseForm): CaseExpect {
  const seconds = Number(f.maxSeconds.replace(',', '.'));
  return {
    tools: { required: splitList(f.toolsRequired), forbidden: splitList(f.toolsForbidden) },
    health: {
      no_error: f.noError, no_truncation: f.noTruncation, no_degradation: f.noDegradation,
      no_fallback: f.noFallback,
      max_duration_ms: f.maxSeconds.trim() !== '' && Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : null,
    },
    text: { must_contain: splitList(f.mustContain), must_not_contain: splitList(f.mustNotContain) },
  };
}

/** True when the form sets at least one expectation (a case with none checks nothing). */
export function hasExpectation(e: CaseExpect): boolean {
  return e.tools.required.length + e.tools.forbidden.length + e.text.must_contain.length + e.text.must_not_contain.length > 0
    || e.health.no_error || e.health.no_truncation || e.health.no_degradation || e.health.no_fallback
    || e.health.max_duration_ms != null;
}
