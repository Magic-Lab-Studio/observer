import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canonicalModel, mergeModelRows, parseAttempts, resultTone } from '../src/helpers/chain.ts';

test('parseAttempts reads latency, colons inside model names and skips junk', () => {
  const got = parseAttempts('groq:qwen/qwen3.8-27b=429@412ms>ollama:granite4:3b=ok@900ms>llamacpp:-=ok>junk');
  assert.deepEqual(got.map((a) => [a.backend, a.model, a.result, a.ms]), [
    ['groq', 'qwen/qwen3.8-27b', '429', 412],
    ['ollama', 'granite4:3b', 'ok', 900],
    ['llamacpp', '', 'ok', null],
  ]);
  assert.equal(resultTone('429'), 'warn');
  assert.equal(resultTone('skipped_exhausted'), 'skip');
  assert.equal(resultTone('error:HTTPStatusError'), 'bad');
});

test('the registry id and the answered label of one model merge into one row', () => {
  assert.equal(canonicalModel('qwen3.8-27b-groq'), canonicalModel('groq · qwen/qwen3.8-27b'));
  const rows = mergeModelRows([
    { key: 'groq · qwen/qwen3.8-27b', count: 200, rate: 0.4 },
    { key: 'qwen3.8-27b-groq', count: 180, rate: 0.35 },
    { key: 'gemini-2.5-flash', count: 20, rate: 0.05 },
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].count, 380);
  assert.equal(rows[0].key, 'groq · qwen/qwen3.8-27b');
});
