import test from 'node:test';
import assert from 'node:assert/strict';
import { exposure, needsEnforcement } from '../src/helpers/privacy.ts';

const report = (o = {}) => ({ sampled_spans: 10, with_input: 0, with_output: 0, with_content_like_attributes: 0, with_sensitive_patterns: 0, ...o });

test('exposure adds what a metadata-only project should not hold', () => {
  assert.equal(exposure(report()), 0);
  assert.equal(exposure(report({ with_input: 2, with_output: 3, with_content_like_attributes: 1 })), 6);
});

test('enforcement is needed only when stored data contradicts the mode', () => {
  assert.equal(needsEnforcement('metadata', report({ with_input: 1 })), true);
  assert.equal(needsEnforcement('metadata', report()), false);
  assert.equal(needsEnforcement('redacted', report({ with_sensitive_patterns: 1 })), true);
  assert.equal(needsEnforcement('redacted', report({ with_input: 5 })), false);
  assert.equal(needsEnforcement('full', report({ with_input: 5, with_sensitive_patterns: 5 })), false);
  assert.equal(needsEnforcement(null, report({ with_input: 5 })), false);
});
