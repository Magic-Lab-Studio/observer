import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  DEFAULT_EXPLORE, autoGranularity, bucketFloor, buildRequest, fillBuckets, formatMeasure, metricKey,
  parseSavedViews, pivotSeries, searchToState, stateToSearch,
} from '../src/helpers/query.ts';

test('metric keys match the backend naming', () => {
  assert.equal(metricKey('count', 'count'), 'count');
  assert.equal(metricKey('duration_ms', 'p95'), 'p95_duration_ms');
  assert.equal(metricKey('error', 'avg'), 'avg_error');
});

test('a state survives the round trip through the URL and bad values fall back to defaults', () => {
  const s = { view: 'provider_attempts', measure: 'ms', aggregation: 'p95', dimension: 'provider', granularity: 'hour' };
  assert.deepEqual(searchToState(stateToSearch(s)), s);
  assert.deepEqual(searchToState(stateToSearch({ ...s, granularity: '' })).granularity, '');
  assert.deepEqual(searchToState('view=evil&a=drop&g=weekly&m=ms;--'), { ...DEFAULT_EXPLORE, measure: 'ms' });
  assert.deepEqual(searchToState(''), DEFAULT_EXPLORE);
});

test('the request carries the range, the environment filter and only what was chosen', () => {
  const body = buildRequest({ ...DEFAULT_EXPLORE, dimension: 'model', granularity: '' }, { hours: 24, environment: 'prod' });
  assert.deepEqual(body.dimensions, ['model']);
  assert.deepEqual(body.filters, [{ field: 'environment', op: '=', value: 'prod' }]);
  assert.equal(body.granularity, undefined);
  assert.equal(buildRequest(DEFAULT_EXPLORE, { hours: 6 }).granularity, 'auto');
  assert.equal(buildRequest(DEFAULT_EXPLORE, { hours: 6 }).filters.length, 0);
});

test('values read like a person would say them', () => {
  assert.equal(formatMeasure('duration_ms', 'p95', 1530), '1.53 s');
  assert.equal(formatMeasure('ms', 'p50', 420), '420 ms');
  assert.equal(formatMeasure('error', 'avg', 0.125), '12.5 %');
  assert.equal(formatMeasure('count', 'count', 41), '41');
  assert.equal(formatMeasure('score', 'avg', 0.84567), '0.85');
  assert.equal(formatMeasure('error', 'sum', 3), '3');
  assert.equal(formatMeasure('x', 'avg', null), '–');
});

test('buckets are epoch-aligned and gaps are kept as empty entries', () => {
  assert.equal(autoGranularity(2), 'minute');
  assert.equal(autoGranularity(24), 'hour');
  assert.equal(autoGranularity(168), 'day');
  const H = 3_600_000;
  assert.equal(bucketFloor(5 * H + 123, 'hour'), 5 * H);
  const filled = fillBuckets([{ t: 2 * H, n: 1 }, { t: 4 * H, n: 2 }], 1 * H + 5, 5 * H + 9, 'hour');
  assert.deepEqual(filled.map((f) => f.t / H), [1, 2, 3, 4, 5]);
  assert.deepEqual(filled.map((f) => f.row?.n ?? null), [null, 1, null, 2, null]);
});

test('pivot keeps the biggest series and one object per time bucket', () => {
  const rows = [
    { t: 1, dim: 'a', value: 5 }, { t: 1, dim: 'b', value: 1 }, { t: 2, dim: 'a', value: 7 },
    { t: 2, dim: 'c', value: 2 },
  ];
  const { data, series } = pivotSeries(rows, 2);
  assert.deepEqual(series, ['a', 'c']);
  assert.deepEqual(data, [{ t: 1, a: 5 }, { t: 2, a: 7, c: 2 }]);
});

test('saved views drop malformed entries and re-sanitise the state', () => {
  assert.deepEqual(parseSavedViews(null), []);
  assert.deepEqual(parseSavedViews('not json'), []);
  const raw = JSON.stringify([
    { name: 'p95 por modelo', state: { view: 'turns', measure: 'duration_ms', aggregation: 'p95', dimension: 'model', granularity: 'hour' } },
    { name: 7, state: {} }, { nope: true }, { name: 'bad', state: { view: 'evil', aggregation: 'drop' } },
  ]);
  const views = parseSavedViews(raw);
  assert.equal(views.length, 2);
  assert.equal(views[0].state.dimension, 'model');
  assert.equal(views[1].state.view, 'turns'); // sanitised
});
