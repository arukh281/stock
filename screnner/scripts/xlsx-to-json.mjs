#!/usr/bin/env node
/**
 * Convert an .xlsx workbook to JSON.
 * Each sheet becomes an array of row objects (first row = keys).
 * Omits blank rows and strips null/undefined/blank-string fields from each row.
 *
 * Usage:
 *   node scripts/xlsx-to-json.mjs <input.xlsx> [output.json]
 *
 * Example:
 *   node scripts/xlsx-to-json.mjs "downloads/screen-export-2026-05-16T06-17-43-817Z/Alphalogic Tech.__1274690__Alphalogic Tech.xlsx"
 */

import { readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as XLSX from "xlsx";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function usage() {
  console.error(`Usage: node scripts/xlsx-to-json.mjs <input.xlsx> [output.json]

If output is omitted, writes <input-basename>.json next to the .xlsx file.`);
  process.exit(1);
}

const inputArg = process.argv[2];
if (!inputArg) usage();

const inputPath = resolve(repoRoot, inputArg);
let outputPath = process.argv[3]
  ? resolve(repoRoot, process.argv[3])
  : inputPath.replace(/\.xlsx$/i, ".json");

function isEmptyish(value) {
  if (value === null || value === undefined) return true;
  if (typeof value === "string" && value.trim() === "") return true;
  return false;
}

/** Drop null/undefined/blank-string fields; omit rows with nothing left. */
function compactRows(rows) {
  const out = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const compact = {};
    for (const [key, value] of Object.entries(row)) {
      if (isEmptyish(value)) continue;
      compact[key] = value;
    }
    if (Object.keys(compact).length > 0) out.push(compact);
  }
  return out;
}

const buf = readFileSync(inputPath);
const workbook = XLSX.read(buf, { type: "buffer", cellDates: true });

const data = {};
for (const sheetName of workbook.SheetNames) {
  const sheet = workbook.Sheets[sheetName];
  const rows = XLSX.utils.sheet_to_json(sheet, {
    raw: false,
    blankrows: false,
  });
  data[sheetName] = compactRows(rows);
}

const payload = {
  source: basename(inputPath),
  sheets: workbook.SheetNames,
  data,
};

writeFileSync(outputPath, JSON.stringify(payload, null, 2), "utf8");
console.log(`Wrote ${outputPath} (${workbook.SheetNames.length} sheet(s)).`);
