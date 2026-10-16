import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { makeKeyLender } from './proxy-auth';
import type { ProxyReq, Req } from './proxy-auth';

// Backend the dashboard talks to. The container nginx strips the /api prefix;
// the dev and preview servers must do the same, or every call reached the
// backend as /api/v1/... and returned 404. Live traces use /ws/live.
const apiTarget = process.env.OBSERVER_API_URL || 'http://localhost:8000';
// When the backend requires OBSERVATORY_API_KEY, the dev/preview proxy adds it server-side, so the key never
// reaches the browser and the dashboard keeps working (audit 2026-10-14: setting the key broke the dashboard).
// The proxy lends that credential, so it only does so for requests coming from this machine, and the
// servers listen on loopback unless OBSERVER_DASHBOARD_HOST says otherwise (audit 2026-10-16).
const apiKey = process.env.OBSERVATORY_API_KEY || '';
const host = process.env.OBSERVER_DASHBOARD_HOST || '127.0.0.1';
const lendKey = makeKeyLender(apiKey);
// http-proxy's own types are looser than ours; the handler only needs setHeader and socket.
const configure = (p: { on: (event: string, handler: (proxyReq: ProxyReq, req: Req) => void) => unknown }) => {
  p.on('proxyReq', lendKey);
  p.on('proxyReqWs', lendKey);
};

const proxy = {
  '/api': {
    target: apiTarget,
    changeOrigin: true,
    rewrite: (p: string) => p.replace(/^\/api/, ''),
    configure,
  },
  '/ws': {
    target: apiTarget.replace(/^http/, 'ws'),
    ws: true,
    changeOrigin: true,
    configure,
  },
};

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host,
    port: 5173,
    proxy,
  },
  preview: {
    host,
    port: 5173,
    proxy,
  },
});
