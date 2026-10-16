import test from 'node:test';
import assert from 'node:assert/strict';
import { silenceText, silenceTone } from '../src/helpers/ingest.ts';

test('silenceText speaks in the right unit', () => {
  assert.equal(silenceText(null), '–');
  assert.equal(silenceText(0.3), '<1 min');
  assert.equal(silenceText(12.4), '12 min');
  assert.equal(silenceText(180), '3 h');
  assert.equal(silenceText(3000), '2 d');
});

test('recent traffic is ok, fresh silence warns, long silence after activity is bad', () => {
  assert.equal(silenceTone(2, 5), 'ok');
  assert.equal(silenceTone(30, 4), 'warn');
  assert.equal(silenceTone(120, 4), 'bad');
});

test('an already idle project is neutral, not an alarm', () => {
  assert.equal(silenceTone(500, 0), 'neutral');
  assert.equal(silenceTone(null, 0), 'neutral');
});
