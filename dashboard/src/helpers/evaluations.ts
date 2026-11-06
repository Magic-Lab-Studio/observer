// The verdict of a stored evaluation, read the way the backend summary reads it (finding P5):
// `passed: true|false` is the evaluator's own verdict; `passed: null` means it could not judge
// every criterion (no judge, the judge failed or refused) and is "incomplete", never a pass. Rows
// without a verdict (older ones, or created directly with a score) keep the old rule: >= 0.8.

export type Verdict = 'passed' | 'failed' | 'incomplete' | 'none';

interface Scored { score: number | null; result: Record<string, unknown> | null }

export function verdictOf(e: Scored): Verdict {
  const r = e.result;
  if (r && 'passed' in r) {
    if (r.passed === true) return 'passed';
    if (r.passed === false) return 'failed';
    if (r.passed === null) return 'incomplete';
  }
  if (e.score == null) return 'none';
  return e.score >= 0.8 ? 'passed' : 'failed';
}

export interface Skipped { criterion: string; reason: string; retryable: boolean }

/** Criteria the judge could not score, with why (stored under result.details.skipped_criteria). */
export function skippedCriteria(e: Scored): Skipped[] {
  const details = e.result?.details as { skipped_criteria?: unknown } | undefined;
  const list = Array.isArray(details?.skipped_criteria) ? details.skipped_criteria : [];
  return list.flatMap((s) => {
    if (!s || typeof s !== 'object') return [];
    const o = s as Record<string, unknown>;
    return [{ criterion: String(o.criterion ?? o.name ?? '?'), reason: String(o.reason ?? ''), retryable: o.retryable === true }];
  });
}

/** Pass rate over the decided evaluations only: incomplete ones count apart. */
export function passStats(items: Scored[]): { count: number; passed: number; incomplete: number; passRate: number | null } {
  let passed = 0, incomplete = 0, decided = 0;
  for (const e of items) {
    const v = verdictOf(e);
    if (v === 'incomplete') incomplete += 1;
    else if (v !== 'none') { decided += 1; if (v === 'passed') passed += 1; }
  }
  return { count: items.length, passed, incomplete, passRate: decided ? passed / decided : null };
}
