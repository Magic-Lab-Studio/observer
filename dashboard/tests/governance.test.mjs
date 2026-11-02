import test from 'node:test';
import assert from 'node:assert/strict';
import { describe, outcomeTone, pathLabels, rowOutcome } from '../src/helpers/governance.ts';

test('outcomes map to tones: denied is bad, asked and abstentions warn, granted is good', () => {
  assert.equal(outcomeTone('denied'), 'bad');
  assert.equal(outcomeTone('expired'), 'bad');
  assert.equal(outcomeTone('asked'), 'warn');
  assert.equal(outcomeTone('__abstain__'), 'warn');
  assert.equal(outcomeTone('granted'), 'good');
  assert.equal(outcomeTone('tool'), 'neutral');
  assert.equal(outcomeTone(null), 'neutral');
});

test('each span type is described from its own contract keys', () => {
  assert.equal(
    describe({ span_type: 'decision', name: 'planner.intent', attributes: { 'decision.kind': 'intent', 'decision.choice': 'tool', 'decision.confidence': 0.9, 'decision.model': 'qwen' } }),
    'intent · tool (0.90) · qwen',
  );
  assert.equal(
    describe({ span_type: 'decision', name: 'x', attributes: { 'decision.kind': 'gate', 'decision.choice': '__abstain__', 'decision.backend': 'rules' } }),
    'gate · abstención · rules',
  );
  assert.equal(
    describe({ span_type: 'security', name: 'x', attributes: { 'security.tool': 'file_master', 'security.reason': 'local_data_egress', 'security.capabilities': ['write', 'network'] } }),
    'file_master · local_data_egress · write, network',
  );
  assert.equal(
    describe({ span_type: 'skill', name: 'x', attributes: { 'skill.id': 'demo', 'skill.version': '1.0.0', 'skill.source': 'import', 'skill.capabilities': ['read'] } }),
    'demo@1.0.0 · import · read',
  );
  assert.equal(describe({ span_type: 'tool', name: 'web_search', attributes: {} }), 'web_search');
});

test('the status column reads the outcome key of each type, falling back to the span status', () => {
  assert.equal(rowOutcome({ span_type: 'security', status: 'ok', attributes: { 'security.outcome': 'denied' } }), 'denied');
  assert.equal(rowOutcome({ span_type: 'approval', status: 'ok', attributes: {} }), 'ok');
  assert.equal(rowOutcome({ span_type: 'tool', status: 'error', attributes: {} }), 'error');
});

test('a turn path reads in time order with Spanish labels', () => {
  assert.deepEqual(
    pathLabels([{ span_type: 'decision' }, { span_type: 'security' }, { span_type: 'approval' }, { span_type: 'tool' }]),
    ['Decisión', 'Permiso', 'Respuesta', 'Herramienta'],
  );
});
