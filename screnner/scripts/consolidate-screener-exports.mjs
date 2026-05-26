#!/usr/bin/env node
/**
 * Merge every Screener .xlsx in a directory into one consolidated.json.
 * Skips files without a "Data Sheet" (non-exports / Excel temp files starting with ~$).
 *
 * Usage:
 *   node scripts/consolidate-screener-exports.mjs <export-directory> [output.json]
 *
 * Default output: <export-directory>/consolidated.json
 */

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as XLSX from "xlsx";
import { buildPayloadFromWorkbook } from "./screener-export-parser.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function usage() {
  console.error(`Usage: node scripts/consolidate-screener-exports.mjs <export-directory> [output.json]

Default output: <export-directory>/consolidated.json`);
  process.exit(1);
}

const dirArg = process.argv[2];
if (!dirArg) usage();

const dir = resolve(repoRoot, dirArg);
const outPath = process.argv[3]
  ? resolve(repoRoot, process.argv[3])
  : join(dir, "consolidated.json");

const names = readdirSync(dir)
  .filter((n) => n.endsWith(".xlsx") && !n.startsWith("~$"))
  .sort();

const companies = [];
const skipped = [];

for (const name of names) {
  const filePath = join(dir, name);
  let workbook;
  try {
    workbook = XLSX.read(readFileSync(filePath), { type: "buffer", cellDates: true });
  } catch (e) {
    skipped.push({ file: name, reason: String(e?.message ?? e) });
    continue;
  }
  if (!workbook.Sheets["Data Sheet"]) {
    skipped.push({ file: name, reason: "no Data Sheet" });
    continue;
  }
  try {
    companies.push(buildPayloadFromWorkbook(workbook, name));
  } catch (e) {
    skipped.push({ file: name, reason: String(e?.message ?? e) });
  }
}

const payload = {
  consolidatedAt: new Date().toISOString(),
  sourceDirectory: basename(dir),
  directoryRelative: relative(repoRoot, dir) || ".",
  count: companies.length,
  companies,
  ...(skipped.length ? { skipped } : {}),
};

writeFileSync(outPath, JSON.stringify(payload, null, 2), "utf8");
console.log(`Wrote ${outPath} (${companies.length} workbook(s))`);
if (skipped.length) {
  console.warn(`Skipped ${skipped.length} file(s):`, skipped);
}
