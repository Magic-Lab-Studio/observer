import test from 'node:test';
import assert from 'node:assert/strict';
import { outcomeShares, outcomeTone, whyLines } from '../src/helpers/outcomes.ts';

test('outcome tones: answered is good, failed is bad, not answering warns, unknown is neutral', () => {
  assert.equal(outcomeTone('answered'), 'good');
  assert.equal(outcomeTone('failed'), 'bad');
  for (const o of ['degraded', 'abstained', 'clarification', 'refused']) assert.equal(outcomeTone(o), 'warn');
  assert.equal(outcomeTone('unknown'), 'neutral');
  assert.equal(outcomeTone('cancelled'), 'neutral');  // stopped by the user, not a failure
  assert.equal(outcomeTone(undefined), 'neutral');
});

test('the why panel names the outcome, its reason and the gate that stopped it, without content', () => {
  const lines = whyLines({
    outcome: 'abstained', outcome_source: 'close', outcome_reason: 'coverage_below_min',
    abstained_with_evidence: true, retrieval_passages: 4,
    gates: [
      { name: 'policy', stage: 'policy', status: 'pass', reason_codes: [] },
      { name: 'coverage', stage: 'coverage', status: 'stop', reason_codes: ['coverage_below_min'] },
    ],
  });
  assert.deepEqual(lines.map((l) => l.key), ['outcome', 'reason', 'gate:coverage', 'evidence']);
  assert.equal(lines[0].label, 'Abstenido');
  assert.equal(lines[0].detail, 'paso de cierre');
  assert.equal(lines[2].detail, 'coverage · coverage_below_min');
  assert.equal(lines[3].detail, '4');
});

test('no outcome data means no panel', () => {
  assert.deepEqual(whyLines(null), []);
  assert.deepEqual(whyLines({}), []);
});

test('outcome shares come from an Explore query grouped by outcome', () => {
  const shares = outcomeShares([
    { outcome: 'answered', count: 6 }, { outcome: 'abstained', count: 2 },
    { outcome: 'unknown', count: 2 }, { outcome: 'failed', count: 0 },
  ]);
  assert.deepEqual(shares.map((s) => [s.outcome, s.count]), [['answered', 6], ['abstained', 2], ['unknown', 2]]);
  assert.equal(shares[0].share, 0.6);
  assert.deepEqual(outcomeShares([]), []);
});
