import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite;
let SpanDetail;
before(async () => {
  vite = await createServer({
    server: { middlewareMode: true, hmr: false, watch: null },
    logLevel: 'silent',
  });
  ({ default: SpanDetail } = await vite.ssrLoadModule('/src/components/SpanDetail.tsx'));
});
after(async () => { await vite?.close(); });

function render(name, attributes) {
  return renderToStaticMarkup(createElement(SpanDetail, {
    span: { name, attributes, input: null, output: null },
  }));
}

function attribute(html, key, value) {
  assert.ok(html.includes(`>${key}</dt>`), `Missing attribute label: ${key}`);
  assert.ok(html.includes(`>${value}</dd>`), `Missing attribute value: ${key}`);
}

test('llm.chain renders its scalar attributes as readable values', () => {
  const attributes = {
    answered_by: 'local-model',
    attempt_count: 2,
    attempts_text: 'cloud: HTTP 429\nlocal: completed',
    cloud_out_text: 'Cloud response\nSecond line',
  };
  const html = render('llm.chain', attributes);
  assert.match(html, /Span Detail: llm.chain/);
  for (const [key, value] of Object.entries(attributes)) attribute(html, key, value);
});

test('turn.end_cause renders end_cause and numeric http_status', () => {
  const html = render('turn.end_cause', { end_cause: 'quota_exceeded', http_status: 429 });
  attribute(html, 'end_cause', 'quota_exceeded');
  attribute(html, 'http_status', '429');
});

test('zero, false, null and empty strings are not hidden', () => {
  const html = render('llm.chain', { attempt_count: 0, fallback: false, missing: null, cloud_out_text: '' });
  for (const [key, value] of [['attempt_count', '0'], ['fallback', 'false'], ['missing', 'null'], ['cloud_out_text', '']]) {
    attribute(html, key, value);
  }
});

test('attribute text is escaped and nested attributes remain inspectable', () => {
  const html = render('llm.chain', {
    cloud_out_text: '<script>alert("fixture")</script>',
    nested: { attempts: ['cloud', 'local'] },
  });
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('&quot;nested&quot;'));
  assert.ok(html.includes('&quot;local&quot;'));
});

test('spans without attributes retain input and output without an empty heading', () => {
  for (const attributes of [null, {}]) {
    const html = render('llm', attributes);
    assert.ok(!html.includes('>Attributes<'));
    assert.ok(html.includes('>Input<'));
    assert.ok(html.includes('>Output<'));
  }
});
