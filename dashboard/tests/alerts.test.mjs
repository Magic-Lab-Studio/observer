import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  describeRule, formatMetricValue, fromInputValue, notificationBody, rubricLabel, severityRank, toInputValue, windowLabel,
} from '../src/helpers/alerts.ts';
import { setLang } from '../src/i18n.ts';

test('thresholds are typed in percent / seconds and stored as ratio / ms', () => {
  assert.equal(fromInputValue('ratio', 25), 0.25);
  assert.equal(toInputValue('ratio', 0.25), 25);
  assert.equal(fromInputValue('ms', 30), 30000);
  assert.equal(toInputValue('ms', 30000), 30);
  assert.equal(fromInputValue('count', 3), 3);
});

test('values read like a person would say them', () => {
  assert.equal(formatMetricValue('ratio', 0.225), '22.5 %');
  assert.equal(formatMetricValue('ms', 34200), '34.2 s');
  assert.equal(formatMetricValue('ms', 420), '420 ms');
  assert.equal(formatMetricValue('count', 2), '2');
  assert.equal(formatMetricValue('ms', null), '–');
});

test('rules are described with metric, comparator, threshold and window', () => {
  const p95 = { label: 'Latencia p95', unit: 'ms' };
  assert.equal(
    describeRule({ metric: 'p95_latency_ms', comparator: '>', threshold: 30000, window_minutes: 60 }, p95),
    'Latencia p95 > 30.0 s en 1 h',
  );
  assert.equal(
    describeRule({ metric: 'provider_quota_out', comparator: '>', threshold: 0, window_minutes: 5, scope: 'gemini' },
      { label: 'Proveedores sin cuota', unit: 'count' }),
    'Proveedores sin cuota (gemini) > 0',
  );
  assert.equal(windowLabel(45), '45 min');
  assert.equal(windowLabel(1440), '1 d');
  assert.equal(windowLabel(90), '90 min');
});

test('severity orders critical first and notifications show value against threshold', () => {
  assert.deepEqual(['info', 'critical', 'warning'].sort((a, b) => severityRank(a) - severityRank(b)),
    ['critical', 'warning', 'info']);
  assert.equal(
    notificationBody({ rule_name: 'x', severity: 'warning', value: 0.31, threshold: 0.25, comparator: '>', metric: 'fallback_rate' },
      { unit: 'ratio' }),
    '31.0 % > 25.0 %',
  );
});

test('rubrics read as names in both languages', () => {
  assert.equal(rubricLabel('generic_turn@1'), 'Salud operativa genérica (v1)');
  assert.equal(rubricLabel('manitos_turn@2'), 'Turno de Manitos (v2)');
  assert.equal(rubricLabel('custom'), 'custom');
  setLang('en');
  assert.equal(rubricLabel('generic_turn@1'), 'Generic operational health (v1)');
  setLang('es');
});
