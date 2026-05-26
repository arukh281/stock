import 'dotenv/config';
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const authFile = path.join(process.cwd(), 'playwright', '.auth', 'user.json');

const rawProxy = process.env.PLAYWRIGHT_PROXY ?? process.env.HTTPS_PROXY ?? process.env.HTTP_PROXY;
const proxyServer = rawProxy?.trim() || undefined;

export default defineConfig({
  testDir: './tests',
  timeout: Number.parseInt(process.env.SCREENER_TEST_TIMEOUT_MS ?? '', 10) || 120_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers:
    Number.parseInt(process.env.SCREENER_PLAYWRIGHT_WORKERS ?? '', 10) ||
    (process.env.CI ? 1 : undefined),
  reporter: 'list',
  use: {
    baseURL: process.env.SCREENER_BASE_URL ?? 'https://www.screener.in',
    trace: 'on-first-retry',
    acceptDownloads: true,
    ...(proxyServer ? { proxy: { server: proxyServer } } : {}),
  },
  projects: [
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: authFile,
      },
      dependencies: ['setup'],
      testIgnore: /auth\.setup\.ts/,
    },
  ],
});
