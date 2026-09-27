import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Backend the dashboard talks to. The container nginx strips the /api prefix;
// the dev and preview servers must do the same, or every call reached the
// backend as /api/v1/... and returned 404. Live traces use /ws/live.
const apiTarget = process.env.OBSERVER_API_URL || 'http://localhost:8000';

const proxy = {
  '/api': {
    target: apiTarget,
    changeOrigin: true,
    rewrite: (p: string) => p.replace(/^\/api/, ''),
  },
  '/ws': {
    target: apiTarget.replace(/^http/, 'ws'),
    ws: true,
    changeOrigin: true,
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
