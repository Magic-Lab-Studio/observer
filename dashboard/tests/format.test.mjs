import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fmtMs, fmtUsd } from '../src/helpers/format.ts';
import { ApiError, classifyStatus, detailFromBody } from '../src/helpers/errors.ts';
import { setLang } from '../src/i18n.ts';

test('B4: small costs keep three significant digits; larger ones read as money', () => {
  assert.equal(fmtUsd(0.000875), '$0.000875');
  assert.equal(fmtUsd(0.0000012), '$0.0000012');
  assert.equal(fmtUsd(1.2e-7), '$0.00000012');
  assert.equal(fmtUsd(0.05), '$0.0500');
  assert.equal(fmtUsd(1234.5), '$1,234.50');
  assert.equal(fmtUsd(0), '$0');
  assert.equal(fmtUsd(null), '–');
});

test('B2: a missing duration is unavailable, never "<1 ms"', () => {
  assert.equal(fmtMs(null), '–');
  assert.equal(fmtMs(undefined), '–');
  assert.equal(fmtMs(0), '0 ms');
  assert.equal(fmtMs(0.4), '<1 ms');
});

test('M7: a failed request says why instead of always blaming the backend', () => {
  setLang('en');
  assert.equal(classifyStatus(400), 'invalid');
  assert.equal(classifyStatus(422), 'invalid');
  assert.equal(classifyStatus(401), 'auth');
  assert.equal(classifyStatus(403), 'auth');
  assert.equal(classifyStatus(404), 'not_found');
  assert.equal(classifyStatus(429), 'rate_limited');
  assert.equal(classifyStatus(503), 'server');
  assert.equal(detailFromBody({ detail: 'Invalid trace ID format' }), 'Invalid trace ID format');
  assert.equal(detailFromBody({ detail: [{ msg: 'field required' }] }), 'field required');
  assert.equal(detailFromBody(null), null);
  const invalid = new ApiError('invalid', 400, 'Invalid trace ID format');
  assert.match(invalid.message, /Invalid request \(HTTP 400\): Invalid trace ID format/);
  assert.doesNotMatch(invalid.message, /running/);
  assert.match(new ApiError('network', null, null).message, /running/);
  assert.match(new ApiError('auth', 401, null).message, /API key/);
  assert.match(new ApiError('not_found', 404, 'Trace not found').message, /Not found/);
  setLang('es');
});
