import fs from 'node:fs';
import path from 'node:path';
import { test as setup, expect } from '@playwright/test';

const authDir = path.join(process.cwd(), 'playwright', '.auth');
const authFile = path.join(authDir, 'user.json');

/**
 * Runs before other tests. If `playwright/.auth/user.json` already exists, does nothing.
 * Otherwise logs in with SCREENER_USERNAME + SCREENER_PASSWORD (see guided steps in chat / .env.example).
 */
setup('save Screener session', async ({ page }) => {
  if (fs.existsSync(authFile)) {
    return;
  }

  const username = process.env.SCREENER_USERNAME;
  const password = process.env.SCREENER_PASSWORD;

  if (!username?.trim() || !password) {
    throw new Error(
      [
        `No session file at ${authFile} and credentials missing.`,
        'Do one of the following:',
        '  1) Manual (recommended first time): npm run auth:save  — log in in the browser, then close it.',
        '  2) Automated: set SCREENER_USERNAME and SCREENER_PASSWORD in .env, then npm test',
      ].join('\n'),
    );
  }

  fs.mkdirSync(authDir, { recursive: true });

  await page.goto('/login/', { waitUntil: 'domcontentloaded' });
  await page.locator('#id_username').fill(username);
  await page.locator('#id_password').fill(password);
  await page.locator('form[action="/login/"] button[type="submit"]').click();

  await expect(page).not.toHaveURL(/\/login\/?$/i, { timeout: 30_000 });

  await page.context().storageState({ path: authFile });
});
