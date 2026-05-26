#!/usr/bin/env node
/**
 * Post-process export folder (ingest leftover xlsx + finalize batch JSON).
 * During Playwright export, each xlsx is ingested immediately — this finishes partial runs.
 *
 * Usage:
 *   node scripts/post-process-export-dir.mjs <export-directory>
 */

import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { postProcessExportDir } from "./export-ingest.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

const dirArg = process.argv[2];
if (!dirArg) {
  console.error(`Usage: node scripts/post-process-export-dir.mjs <export-directory>`);
  process.exit(1);
}

const keepXlsx = process.env.SCREENER_KEEP_XLSX === "1";
const r = postProcessExportDir(resolve(repoRoot, dirArg));

console.log(`\nPost-process complete (${r.count} companies):`);
console.log(`  features.compact.json   → ${r.compactPath}`);
console.log(`  per-company             → ${r.count} × *.features.json`);
console.log(
  keepXlsx
    ? `  .xlsx                   → kept (SCREENER_KEEP_XLSX=1)`
    : `  .xlsx                   → removed after ingest`,
);
console.log(`\n  LLM: attach features.compact.json (screen) + *.features.json (deep-dive)\n`);
