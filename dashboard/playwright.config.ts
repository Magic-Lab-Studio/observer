/**
 * Browser tests of the dashboard (external evaluation B3), run in CI by the `dashboard-e2e` job.
 *
 * They need a running backend (OBSERVER_API_URL, default http://127.0.0.1:8000) and a built
 * dashboard (`npm run build`); Playwright starts `vite preview` in front of that backend. The
 * global setup plants a project `e2e` with an ok trace, a failed one and an evaluation the judge
 * could not complete. Run: `npm run build && npm run e2e`.
 */
import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.E2E_PORT || 4173);

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // A local Chromium (offline machines); CI installs Playwright's own.
        launchOptions: process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {},
      },
    },
  ],
  webServer: {
    command: `npx vite preview --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
