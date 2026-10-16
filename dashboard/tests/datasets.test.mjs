import test from 'node:test';
import assert from 'node:assert/strict';
import { buildExpect, emptyCaseForm, hasExpectation, splitList } from '../src/helpers/datasets.ts';

test('splitList trims, drops empties and duplicates', () => {
  assert.deepEqual(splitList(' a, b ,, a\nc '), ['a', 'b', 'c']);
  assert.deepEqual(splitList(''), []);
});

test('buildExpect converts seconds and ignores nonsense', () => {
  const f = { ...emptyCaseForm(), maxSeconds: '2,5', toolsRequired: 'web_search', noError: true };
  const e = buildExpect(f);
  assert.equal(e.health.max_duration_ms, 2500);
  assert.deepEqual(e.tools.required, ['web_search']);
  assert.equal(buildExpect({ ...emptyCaseForm(), maxSeconds: 'abc' }).health.max_duration_ms, null);
  assert.equal(buildExpect({ ...emptyCaseForm(), maxSeconds: '-3' }).health.max_duration_ms, null);
});

test('a case with no expectation is detected', () => {
  assert.equal(hasExpectation(buildExpect(emptyCaseForm())), false);
  assert.equal(hasExpectation(buildExpect({ ...emptyCaseForm(), mustContain: 'x' })), true);
  assert.equal(hasExpectation(buildExpect({ ...emptyCaseForm(), noFallback: true })), true);
});
