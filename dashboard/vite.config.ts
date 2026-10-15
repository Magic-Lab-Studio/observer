import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Backend the dashboard talks to. The container nginx strips the /api prefix;
// the dev and preview servers must do the same, or every call reached the
// backend as /api/v1/... and returned 404. Live traces use /ws/live.
const apiTarget = process.env.OBSERVER_API_URL || 'http://localhost:8000';
// When the backend requires OBSERVATORY_API_KEY, the dev/preview proxy adds it server-side, so the key never
// reaches the browser and the dashboard keeps working (audit 2026-10-14: setting the key broke the dashboard).
const apiKey = process.env.OBSERVATORY_API_KEY || '';
const authHeaders = apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined;

const proxy = {
  '/api': {
    target: apiTarget,
    changeOrigin: true,
    rewrite: (p: string) => p.replace(/^\/api/, ''),
    headers: authHeaders,
  },
  '/ws': {
    target: apiTarget.replace(/^http/, 'ws'),
    ws: true,
    changeOrigin: true,
    headers: authHeaders,
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
    port: 5173,
    proxy,
  },
  preview: {
    port: 5173,
    proxy,
  },
});
