/** Plants the data the tests read: project `e2e`, newest, so the dashboard opens on it. */
import { randomUUID } from 'node:crypto';

export const API = (process.env.OBSERVER_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
export const PROJECT = 'e2e';

async function post(path: string, body: unknown): Promise<any> {
  const response = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
  return response.json();
}

function trace(name: string, failed: boolean, startedAgo: number) {
  const traceId = randomUUID();
  const root = randomUUID();
  const now = Date.now() / 1000 - startedAgo;
  const attributes = { 'service.name': PROJECT };
  return {
    traceId,
    spans: [
      { id: root, trace_id: traceId, name, span_type: 'agent', start_time: now - 3, end_time: now,
        status: failed ? 'error' : 'ok', attributes, input: { question: 'precio del plan Team' },
        output: failed ? null : { text: 'El plan Team cuesta 14 USD por usuario.' } },
      { id: randomUUID(), trace_id: traceId, parent_id: root, name: 'search_prices', span_type: 'tool',
        start_time: now - 2.5, end_time: now - 2, status: failed ? 'error' : 'ok', attributes,
        input: { query: 'team plan' }, output: failed ? null : { text: 'Team: 14 USD por usuario' } },
      { id: randomUUID(), trace_id: traceId, parent_id: root, name: 'chat', span_type: 'llm',
        start_time: now - 1.8, end_time: now - 0.2, status: 'ok',
        attributes: { ...attributes, 'gen_ai.request.model': 'gpt-4o-mini' },
        tokens_input: 120, tokens_output: 24 },
    ],
  };
}

export default async function globalSetup(): Promise<void> {
  const health = await fetch(`${API}/health`).catch(() => null);
  if (!health?.ok) throw new Error(`no backend at ${API} (set OBSERVER_API_URL)`);
  const failed = trace('e2e_failed_turn', true, 60);
  const ok = trace('e2e_answer_question', false, 5);
  await post('/v1/traces/batch', { spans: [...failed.spans, ...ok.spans] });
  // No judge is configured in the test backend: the evaluation comes out incomplete (finding P5).
  const evaluation = await post('/v1/evaluations/run', {
    trace_id: ok.traceId, evaluator_type: 'llm_judge', criteria: [],
  });
  if (evaluation.result?.passed !== null) {
    throw new Error(`expected an incomplete evaluation, got ${JSON.stringify(evaluation.result)}`);
  }
}
