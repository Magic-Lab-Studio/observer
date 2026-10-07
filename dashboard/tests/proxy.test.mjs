import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer as createHttpServer, request } from 'node:http';
import { test } from 'node:test';
import { createServer, preview } from 'vite';
import { getLiveWebSocketUrl } from '../src/urls.ts';

test('live URL uses the current origin and the backend endpoint', () => {
  assert.equal(getLiveWebSocketUrl(), 'ws://localhost/ws/live');
  assert.equal(getLiveWebSocketUrl({ protocol: 'http:', host: '127.0.0.1:5173' }), 'ws://127.0.0.1:5173/ws/live');
  assert.equal(getLiveWebSocketUrl({ protocol: 'https:', host: 'observer.example' }), 'wss://observer.example/ws/live');
});

function upgrade(url) {
  return new Promise((resolve, reject) => {
    const req = request(url, {
      headers: { Connection: 'Upgrade', Upgrade: 'websocket' },
    });
    req.setTimeout(5000, () => req.destroy(new Error('WebSocket upgrade timeout')));
    req.on('error', reject);
    req.on('response', (response) => {
      response.resume();
      reject(new Error(`Expected upgrade, received ${response.statusCode}`));
    });
    req.on('upgrade', (response, socket) => {
      socket.destroy();
      resolve(response.statusCode);
    });
    req.end();
  });
}

for (const mode of ['development', 'preview']) {
  test(`${mode} proxies API and WebSocket requests to the configured backend`, { timeout: 15000 }, async (t) => {
    const received = [];
    const backend = createHttpServer((req, res) => {
      received.push(req.url);
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ path: req.url, method: req.method, host: req.headers.host }));
    });
    backend.on('upgrade', (req, socket) => {
      received.push(req.url);
      socket.end('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');
    });
    backend.listen(0, '127.0.0.1');
    await once(backend, 'listening');
    t.after(() => new Promise((resolve) => backend.close(resolve)));
    const backendOrigin = `http://127.0.0.1:${backend.address().port}`;
    const previous = process.env.OBSERVER_API_URL;
    process.env.OBSERVER_API_URL = backendOrigin;
    t.after(() => {
      if (previous === undefined) delete process.env.OBSERVER_API_URL;
      else process.env.OBSERVER_API_URL = previous;
    });
    const options = { host: '127.0.0.1', port: 0, strictPort: true };
    const vite = mode === 'development'
      ? await createServer({ server: options, logLevel: 'silent' })
      : await preview({ preview: options, logLevel: 'silent' });
    t.after(() => mode === 'development' ? vite.close() : new Promise((resolve) => vite.httpServer.close(resolve)));
    if (mode === 'development') {
      // Vite's listen wrapper maps port 0 to 5173; let the OS select the test port.
      vite.httpServer.listen(0, '127.0.0.1');
      await once(vite.httpServer, 'listening');
    }
    const origin = `http://127.0.0.1:${vite.httpServer.address().port}`;
    const response = await fetch(`${origin}/api/v1/traces?project_id=manitos`, { method: 'POST', body: '{}' });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      path: '/v1/traces?project_id=manitos', method: 'POST', host: new URL(backendOrigin).host,
    });
    assert.equal(await upgrade(`${origin}/ws/live?project_id=manitos`), 101);
    assert.deepEqual(received, ['/v1/traces?project_id=manitos', '/ws/live?project_id=manitos']);
  });
}
