import test from 'node:test';
import assert from 'node:assert/strict';
import { bucketsFromServer, histogram, histogramFromServer } from '../src/helpers/stats.ts';

test('B5: the server histogram gets the same range names and edges as the client one', () => {
  const server = [0, 1000, 2000, 5000, 10000, 20000, 40000].map((from, i) => ({ from, count: i }));
  const rows = histogramFromServer(server);
  const client = histogram([]);
  assert.deepEqual(rows.map((r) => [r.range, r.from]), client.map((r) => [r.range, r.from]));
  assert.deepEqual(rows.map((r) => r.count), [0, 1, 2, 3, 4, 5, 6]);
  assert.deepEqual(histogramFromServer([]).map((r) => r.count), [0, 0, 0, 0, 0, 0, 0]);
});

test('B5: server buckets become chart rows with ok, error and error rate', () => {
  const [b] = bucketsFromServer([{ t: Date.UTC(2026, 10, 5, 12), total: 4, err: 1, p50: 900, p95: 2000 }], 3600_000);
  assert.equal(b.ok, 3); assert.equal(b.err, 1); assert.equal(b.errRate, 0.25); assert.equal(b.p95, 2000);
  assert.equal(typeof b.label, 'string');
  assert.equal(bucketsFromServer([{ t: 0, total: 0, err: 0, p50: null, p95: null }], 3600_000)[0].errRate, 0);
});
