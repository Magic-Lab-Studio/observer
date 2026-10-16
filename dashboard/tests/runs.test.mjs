import test from 'node:test';
import assert from 'node:assert/strict';
import { changeRank, configDiff, fmtDelta, versionLabel } from '../src/helpers/runs.ts';

test('versionLabel shortens the commit and tolerates gaps', () => {
  assert.equal(versionLabel('v2', { commit: 'abcdef1234567', model: 'gemma' }), 'v2 (abcdef12 · gemma)');
  assert.equal(versionLabel(null, { commit: 'abc' }), 'abc');
  assert.equal(versionLabel(null, {}), '–');
  assert.equal(versionLabel('only', {}), 'only');
});

test('configDiff lists only what changed', () => {
  const d = configDiff({ config: { t: 0.2, k: 1 } }, { config: { t: 0.7, k: 1, n: true } });
  assert.deepEqual(d, ['n: – → true', 't: 0.2 → 0.7']);
  assert.deepEqual(configDiff({}, {}), []);
});

test('regressions rank before everything else; deltas carry a sign', () => {
  assert.ok(changeRank('regressed') < changeRank('improved'));
  assert.ok(changeRank('same') < changeRank('missing'));
  assert.equal(fmtDelta(0.25), '+0.25');
  assert.equal(fmtDelta(-0.3), '-0.30');
  assert.equal(fmtDelta(null), '–');
});
