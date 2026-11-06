import test from 'node:test';
import assert from 'node:assert/strict';
import { passStats, skippedCriteria, verdictOf } from '../src/helpers/evaluations.ts';

const judged = (passed, score = 0.9, extra = {}) => ({ score, result: { passed, ...extra } });

test('P5: an evaluation the judge could not finish is incomplete, never passed, even with a high score', () => {
  assert.equal(verdictOf(judged(null, 0.95)), 'incomplete');
  assert.equal(verdictOf(judged(true)), 'passed');
  assert.equal(verdictOf(judged(false, 0.95)), 'failed');
  // rows without a verdict keep the old rule
  assert.equal(verdictOf({ score: 0.85, result: { grade: 'B' } }), 'passed');
  assert.equal(verdictOf({ score: 0.5, result: null }), 'failed');
  assert.equal(verdictOf({ score: null, result: null }), 'none');
});

test('the pass rate counts decided evaluations only; incomplete ones are counted apart', () => {
  const stats = passStats([judged(true), judged(false), judged(null, 0.99), judged(null, 0.99)]);
  assert.deepEqual(stats, { count: 4, passed: 1, incomplete: 2, passRate: 0.5 });
  assert.equal(passStats([judged(null)]).passRate, null);
});

test('skipped criteria are read with their reason', () => {
  const e = judged(null, 0.9, { details: { skipped_criteria: [
    { criterion: 'output_quality', reason: 'no judge configured', retryable: false }, 'junk'] } });
  assert.deepEqual(skippedCriteria(e), [{ criterion: 'output_quality', reason: 'no judge configured', retryable: false }]);
  assert.deepEqual(skippedCriteria(judged(true)), []);
});
