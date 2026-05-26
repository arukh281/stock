import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, type Page } from '@playwright/test';

type ScreenRow = {
  companyId: string;
  name: string;
  path: string;
  cells: string[];
};

/**
 * Single-line query (Screener screen DSL, not SQL); avoids parser quirks with newlines.
 */
const DEFAULT_SCREEN_QUERY =
  'Market Capitalization < 5000 AND ' +
  'Sales growth 3Years > 45 AND ' +
  'Profit growth 3Years > 50 AND ' +
  'Cash from operations last year > 0 AND ' +
  'Debt to equity < 1 AND ' +
  'Return on capital employed > 25 AND ' +
  'OPM > 15';

const SCREEN_QUERY = (process.env.SCREENER_QUERY ?? DEFAULT_SCREEN_QUERY).trim();

async function assertNotLoginWall(page: Page) {
  const url = page.url();
  if (/\/login\/?$/i.test(url) || url.includes('/register')) {
    throw new Error(
      'Landed on login/register instead of the screen editor. Your saved session is missing or expired. ' +
        'Delete playwright/.auth/user.json, run npm run auth:save (log in, then close the browser), and try again.',
    );
  }
  const loginGate = page.getByRole('link', { name: 'Login here' });
  if (await loginGate.isVisible().catch(() => false)) {
    throw new Error(
      'Screen page shows “Login here” — not authenticated. Delete playwright/.auth/user.json and run npm run auth:save.',
    );
  }
}

function screenerOrigin(): string {
  return (process.env.SCREENER_BASE_URL ?? 'https://www.screener.in').replace(/\/$/, '');
}

/** Raw custom screen; `/explore/` and home “Screens” do not include `#query-builder`. */
async function openRawScreenEditor(page: Page) {
  const target = `${screenerOrigin()}/screen/raw/`;
  try {
    await page.goto('/screen/raw/', { waitUntil: 'domcontentloaded', timeout: 60_000 });
  } catch (e) {
    const msg = String(e);
    if (msg.includes('ERR_CONNECTION_REFUSED') || msg.includes('ERR_NAME_NOT_RESOLVED') || msg.includes('INTERNET_DISCONNECTED')) {
      throw new Error(
        [
          `Could not open ${target} (${msg.includes('REFUSED') ? 'connection refused' : msg.includes('NAME_NOT_RESOLVED') ? 'DNS failed' : 'offline'}).`,
          `This is a network reachability issue from Playwright’s Chromium, not a Screener login bug.`,
          `Check: Wi‑Fi/VPN, firewall blocking Chromium, and that SCREENER_BASE_URL is correct (now: "${screenerOrigin()}").`,
          `On a corporate network, set PLAYWRIGHT_PROXY or HTTPS_PROXY to your HTTP(S) proxy and retry.`,
        ].join(' '),
        { cause: e },
      );
    }
    throw e;
  }
  await assertNotLoginWall(page);
}

async function fillQueryBuilder(page: Page, text: string) {
  const labeled = page.getByRole('textbox', { name: 'Query' });
  if (await labeled.count()) {
    await labeled.waitFor({ state: 'visible', timeout: 60_000 });
    await labeled.click();
    await labeled.fill(text);
    return;
  }

  const byId = page.locator('#query-builder');
  const cmHost = page.locator('main .cm-editor').first();

  const root =
    (await byId.count()) > 0
      ? byId
      : (await cmHost.count()) > 0
        ? cmHost
        : page.locator('.cm-content').first();

  await root.first().waitFor({ state: 'visible', timeout: 60_000 });

  const scoped = (await byId.count()) > 0 ? byId : root;

  const textarea = scoped.locator('textarea').first();
  const cmContent = scoped.locator('.cm-content').first();

  if (await textarea.count()) {
    await textarea.click();
    await textarea.fill(text);
    return;
  }
  if (await cmContent.count()) {
    await cmContent.click();
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
    await page.keyboard.insertText(text);
    return;
  }

  await scoped.click();
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  await page.keyboard.insertText(text);
}

/** Parse "N results found" / "No results found" from the screen results header. */
async function parseResultsCount(page: Page): Promise<number | null> {
  const text = await page.locator('main').innerText().catch(() => '');
  const found = text.match(/\b(\d+)\s+results?\s+found\b/i);
  if (found) return Number.parseInt(found[1]!, 10);
  if (/\bno results found\b/i.test(text)) return 0;
  return null;
}

type ScreenQueryOutcome = 'rows' | 'empty';

async function waitForScreenQueryOutcome(page: Page): Promise<ScreenQueryOutcome> {
  const err = page.locator('main li').filter({ hasText: /unknown word|syntax error|invalid query/i });
  const row = page.locator('tr[data-row-company-id]').first();
  const deadline = Date.now() + parseEnvMs('SCREENER_QUERY_WAIT_MS', 90_000);

  while (Date.now() < deadline) {
    if (await err.first().isVisible().catch(() => false)) {
      const msg = await err
        .allInnerTexts()
        .then((xs) => xs.join('; '))
        .catch(() => '');
      throw new Error(
        `Screener rejected the query (no results table). ${msg || 'Fix metric names / syntax and try again.'}`,
      );
    }

    const count = await parseResultsCount(page);
    if (count === 0) return 'empty';

    if (count !== null && count > 0) {
      if (await row.isVisible().catch(() => false)) return 'rows';
    } else if (await row.isVisible().catch(() => false)) {
      return 'rows';
    }

    await page.waitForTimeout(400);
  }

  const count = await parseResultsCount(page);
  if (count === 0) return 'empty';

  throw new Error(
    'Timed out waiting for screen results. If the page shows a login or premium gate, refresh playwright/.auth/user.json.',
  );
}

function emitScreenerResult(companies: { name: string; companyId: string; path: string }[]) {
  const payload = { total: companies.length, companies };
  console.log(`SCREENER_RESULT:${JSON.stringify(payload)}`);
}

async function clickRunScreen(page: Page) {
  const run = page.getByRole('button', { name: /run this query/i });
  if (await run.count()) {
    await run.click();
    return;
  }
  const byFormAction = page.locator('form[action="/screen/raw/"] button[type="submit"]');
  const byMain = page.locator('main form button[type="submit"]');
  const btn = (await byFormAction.count()) > 0 ? byFormAction.first() : byMain.first();
  await btn.click();
}

function sanitizeFilePart(name: string): string {
  const s = name.replace(/[/\\?%*:|"<>]/g, '_').replace(/\s+/g, ' ').trim();
  return (s || 'company').slice(0, 120);
}

function parseEnvMs(key: string, fallback: number): number {
  const v = Number.parseInt(process.env[key] ?? '', 10);
  return Number.isFinite(v) && v >= 0 ? v : fallback;
}

function parseEnvInt(key: string, fallback: number): number {
  const v = Number.parseInt(process.env[key] ?? '', 10);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

async function recoverIfRateLimited(page: Page, targetUrl: string): Promise<void> {
  const heading = page.getByRole('heading', { name: /too many requests/i });
  const body = page.getByText(/unable to process your requests this quickly/i);
  const waitMs = parseEnvMs('SCREENER_RATE_LIMIT_WAIT_MS', 75_000);
  const maxRounds = Math.max(1, parseEnvMs('SCREENER_RATE_LIMIT_RETRIES', 4));

  for (let round = 0; round < maxRounds; round++) {
    const limited =
      (await heading.isVisible().catch(() => false)) || (await body.first().isVisible().catch(() => false));
    if (!limited) return;
    await page.waitForTimeout(waitMs);
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  }

  if ((await heading.isVisible().catch(() => false)) || (await body.first().isVisible().catch(() => false))) {
    throw new Error(
      'Screener rate limit (“Too many requests”). Increase SCREENER_EXPORT_DELAY_MS (try 12000–20000), ' +
        'SCREENER_RATE_LIMIT_WAIT_MS, or run fewer companies with SCREENER_EXPORT_MAX.',
    );
  }
}

function companyPageUrl(row: ScreenRow): string {
  const p = row.path.trim();
  if (p.startsWith('http')) return p;
  const origin = screenerOrigin();
  if (p.startsWith('/')) return `${origin}${p}`;
  return `${origin}/${p}`;
}

/** “Export to Excel” in `#top` (same intent as //*[@id="top"]/div[1]/form/button/span). */
async function exportCompanyExcel(page: Page, row: ScreenRow, outDir: string): Promise<string> {
  const targetUrl = companyPageUrl(row);
  const downloadTimeout = parseEnvMs('SCREENER_DOWNLOAD_TIMEOUT_MS', 180_000);
  const maxAttempts = parseEnvInt('SCREENER_EXPORT_RETRIES', 3);
  const retryWaitMs = parseEnvMs('SCREENER_EXPORT_RETRY_WAIT_MS', 20_000);
  let lastErr: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
      await recoverIfRateLimited(page, targetUrl);

      const loginHere = page.getByRole('link', { name: 'Login here' });
      if (await loginHere.isVisible().catch(() => false)) {
        throw new Error(`Not logged in on company page (${targetUrl}). Refresh playwright/.auth/user.json.`);
      }

      const exportBtn = page
        .locator(
          '#top form button[formaction*="/user/company/export/"], #top form button[aria-label="Export to Excel"]',
        )
        .first();
      const btnTimeout = parseEnvMs('SCREENER_EXPORT_BUTTON_TIMEOUT_MS', 45_000);
      await exportBtn.waitFor({ state: 'visible', timeout: btnTimeout });

      const [download] = await Promise.all([
        page.waitForEvent('download', { timeout: downloadTimeout }),
        exportBtn.click(),
      ]);

      const suggested = download.suggestedFilename() || `company-${row.companyId}.xlsx`;
      const safeSuggested = suggested.replace(/[/\\?%*:|"<>]/g, '_');
      const destPath = path.join(
        outDir,
        `${sanitizeFilePart(row.name)}__${row.companyId}__${safeSuggested}`,
      );
      await download.saveAs(destPath);
      const st = await fs.promises.stat(destPath).catch(() => null);
      if (!st?.size) {
        throw new Error(`Saved file is missing or empty: ${destPath} (suggested: ${suggested})`);
      }
      return destPath;
    } catch (e) {
      lastErr = e;
      if (attempt < maxAttempts) {
        console.warn(
          `${logPrefix()}  ${row.name}: export attempt ${attempt}/${maxAttempts} failed — retrying in ${retryWaitMs / 1000}s …`,
        );
        await recoverIfRateLimited(page, targetUrl);
        await page.waitForTimeout(retryWaitMs);
      }
    }
  }

  throw lastErr;
}

type ExportManifest = {
  query: string;
  companies: { companyId: string; name: string; path: string }[];
};

function filterForWorker<T>(items: T[], workerIndex: number, workerCount: number): T[] {
  if (workerCount <= 1) return items;
  return items.filter((_, i) => i % workerCount === workerIndex);
}

function exportWorkerEnv() {
  const worker = Number.parseInt(process.env.SCREENER_EXPORT_WORKER ?? '0', 10);
  const workers = Number.parseInt(process.env.SCREENER_EXPORT_WORKERS ?? '1', 10);
  return {
    worker: Number.isFinite(worker) ? worker : 0,
    workers: Number.isFinite(workers) && workers > 0 ? workers : 1,
  };
}

function logPrefix(): string {
  const { worker, workers } = exportWorkerEnv();
  return workers > 1 ? `[worker ${worker + 1}/${workers}] ` : '';
}

function exportTimeoutMs(companyCount: number): number {
  const perCompanyMs = Number.parseInt(process.env.SCREENER_EXPORT_PER_COMPANY_MS ?? '', 10);
  const budget = Number.isFinite(perCompanyMs) && perCompanyMs > 0 ? perCompanyMs : 120_000;
  const exportDelayMs = parseEnvMs('SCREENER_EXPORT_DELAY_MS', 8_000);
  const { workers } = exportWorkerEnv();
  const perWorker = Math.ceil(companyCount / Math.max(1, workers));
  return (
    300_000 +
    perWorker * (budget + exportDelayMs) +
    perWorker * parseEnvMs('SCREENER_RATE_LIMIT_WAIT_MS', 75_000) * 0.25
  );
}

type ExportFailure = { companyId: string; name: string; error: string };

async function exportCompaniesToJson(
  page: Page,
  toExport: ScreenRow[],
  outDir: string,
): Promise<{
  saved: { companyId: string; name: string; featuresJson: string }[];
  failures: ExportFailure[];
}> {
  const exportDelayMs = parseEnvMs('SCREENER_EXPORT_DELAY_MS', 10_000);
  const ingestScript = path.join(process.cwd(), 'scripts', 'export-ingest.mjs');
  const prefix = logPrefix();
  const { worker } = exportWorkerEnv();
  const saved: { companyId: string; name: string; featuresJson: string }[] = [];
  const failures: ExportFailure[] = [];

  for (let i = 0; i < toExport.length; i++) {
    const row = toExport[i]!;

    try {
      const destPath = await exportCompanyExcel(page, row, outDir);
      const out = execFileSync(process.execPath, [ingestScript, 'ingest', destPath, outDir], {
        encoding: 'utf8',
        cwd: process.cwd(),
      });
      const featuresJson =
        out.match(/→\s+(.+)/)?.[1]?.trim() ?? destPath.replace(/\.xlsx$/i, '.features.json');
      saved.push({ companyId: row.companyId, name: row.name, featuresJson });
      console.log(`${prefix}  ${row.name}: .features.json written, xlsx removed`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      failures.push({ companyId: row.companyId, name: row.name, error: msg });
      console.error(`${prefix}  FAILED ${row.name}: ${msg}`);
    }

    if (i < toExport.length - 1) {
      await page.waitForTimeout(exportDelayMs);
    }
  }

  if (failures.length) {
    const failPath = path.join(outDir, `export-failures-w${worker}.json`);
    fs.writeFileSync(failPath, JSON.stringify(failures, null, 2), 'utf8');
    console.warn(`${prefix}Logged ${failures.length} failure(s) → ${failPath}`);
  }

  return { saved, failures };
}

async function runExportWorker(page: Page) {
  const manifestPath = process.env.SCREENER_EXPORT_MANIFEST;
  const outDir = process.env.SCREENER_EXPORT_DIR;
  if (!manifestPath || !outDir) {
    throw new Error('SCREENER_EXPORT_MANIFEST and SCREENER_EXPORT_DIR are required for export-only mode.');
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as ExportManifest;
  const { worker, workers } = exportWorkerEnv();
  const all = manifest.companies as ScreenRow[];
  const toExport = filterForWorker(all, worker, workers);

  test.setTimeout(
    Number.parseInt(process.env.SCREENER_TEST_TIMEOUT_MS ?? '', 10) || exportTimeoutMs(toExport.length),
  );

  console.log(`${logPrefix()}Starting export of ${toExport.length} companies → ${outDir}`);

  const { saved, failures } = await exportCompaniesToJson(page, toExport, outDir);

  if (process.env.SCREENER_EXPORT_SKIP_FINALIZE === '1') {
    console.log(
      `${logPrefix()}Done ${saved.length}/${toExport.length} companies` +
        (failures.length ? ` (${failures.length} failed)` : '') +
        '. Finalize runs after all workers finish.',
    );
    if (failures.length) {
      throw new Error(
        `${failures.length} export(s) failed: ${failures.map((f) => f.name).join(', ')}`,
      );
    }
    return;
  }

  const ingestScript = path.join(process.cwd(), 'scripts', 'export-ingest.mjs');
  await test.step('Finalize batch (features.compact.json)', () => {
    execFileSync(process.execPath, [ingestScript, 'finalize', outDir], {
      stdio: 'inherit',
      cwd: process.cwd(),
    });
  });
}

test('run custom screen query and extract result rows', async ({ page }) => {
  if (process.env.SCREENER_EXPORT_ONLY === '1') {
    await runExportWorker(page);
    return;
  }

  const listOnly = process.env.SCREENER_LIST_ONLY === '1';
  const maxExport = Number.parseInt(process.env.SCREENER_EXPORT_MAX ?? '', 10);
  const exportCap = Number.isFinite(maxExport) && maxExport > 0 ? maxExport : undefined;

  if (listOnly) {
    test.setTimeout(parseEnvMs('SCREENER_LIST_TIMEOUT_MS', 120_000));
  }

  await openRawScreenEditor(page);
  await fillQueryBuilder(page, SCREEN_QUERY);
  await clickRunScreen(page);
  await page.waitForLoadState('domcontentloaded').catch(() => {});
  const outcome = await waitForScreenQueryOutcome(page);

  if (outcome === 'empty') {
    if (listOnly) {
      emitScreenerResult([]);
      return;
    }
    throw new Error(
      'Query returned 0 companies. Relax filters or fix the query, then run export again.',
    );
  }

  const rows = page.locator('tr[data-row-company-id]');
  await expect(rows.first()).toBeVisible();

  const extracted: ScreenRow[] = await rows.evaluateAll((trs) =>
    trs.map((tr) => {
      const id = tr.getAttribute('data-row-company-id') ?? '';
      const cells = Array.from(tr.querySelectorAll('td'));
      const texts = cells.map((td) => td.textContent?.trim() ?? '');
      const nameLink = tr.querySelector('td.text a[href*="/company/"]');
      const name = nameLink?.textContent?.trim() ?? texts[1] ?? '';
      const href = nameLink?.getAttribute('href') ?? '';
      return { companyId: id, name, path: href, cells: texts };
    }),
  );

  const withLinks = extracted.filter((r) => r.path && r.path.includes('/company/'));
  expect(withLinks.length).toBeGreaterThan(0);

  if (listOnly) {
    emitScreenerResult(
      withLinks.map((r) => ({
        name: r.name,
        companyId: r.companyId,
        path: r.path,
      })),
    );
    return;
  }

  const toExport = exportCap ? withLinks.slice(0, exportCap) : withLinks;
  test.setTimeout(
    Number.parseInt(process.env.SCREENER_TEST_TIMEOUT_MS ?? '', 10) ||
      Math.ceil(exportTimeoutMs(toExport.length)),
  );

  if (process.env.SCREENER_DEBUG_JSON === '1') {
    console.log(JSON.stringify(extracted, null, 2));
  }

  await test.info().attach('screen-results.json', {
    body: JSON.stringify(extracted, null, 2),
    contentType: 'application/json',
  });

  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const outDir = path.join(process.cwd(), 'downloads', `screen-export-${stamp}`);
  fs.mkdirSync(outDir, { recursive: true });

  const { saved, failures } = await exportCompaniesToJson(page, toExport, outDir);
  if (failures.length) {
    throw new Error(
      `${failures.length} export(s) failed: ${failures.map((f) => f.name).join(', ')}`,
    );
  }

  const ingestScript = path.join(process.cwd(), 'scripts', 'export-ingest.mjs');
  await test.step('Finalize batch (features.compact.json)', () => {
    execFileSync(process.execPath, [ingestScript, 'finalize', outDir], {
      stdio: 'inherit',
      cwd: process.cwd(),
    });
  });

  const featuresCompactPath = path.join(outDir, 'features.compact.json');
  const manifest = {
    outDir,
    count: saved.length,
    files: saved,
    featuresCompactJson: featuresCompactPath,
  };

  await test.info().attach('exports-manifest.json', {
    body: JSON.stringify(manifest, null, 2),
    contentType: 'application/json',
  });

  await test.info().attach('features.compact.json', {
    path: featuresCompactPath,
    contentType: 'application/json',
  });

  console.log(`\nExport folder: ${outDir}`);
  console.log(`  features.compact.json   → ${featuresCompactPath}`);
  console.log(`  per-company *.features.json (${saved.length} files)`);
  console.log(`  (attach compact + shortlisted *.features.json to LLM)\n`);
});
