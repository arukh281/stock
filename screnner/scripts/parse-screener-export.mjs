#!/usr/bin/env node
/**
 * Parse a Screener.in-style multi-sheet export (.xlsx) into structured JSON.
 * - Ignores "Customization".
 * - Reads numbers from "Data Sheet" (annual, quarterly, balance sheet, cash flow, price, derived).
 * - Output: `company_info`, `latest_snapshot`, `derived_signals`, plus raw `statements`.
 *
 * Usage:
 *   node scripts/parse-screener-export.mjs <input.xlsx> [output.json]
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as XLSX from "xlsx";
import { buildPayloadFromWorkbook } from "./screener-export-parser.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function usage() {
  console.error(`Usage: node scripts/parse-screener-export.mjs <input.xlsx> [output.json]

Writes <basename>-structured.json next to the file if output is omitted.`);
  process.exit(1);
}

const inputArg = process.argv[2];
if (!inputArg) usage();

const inputPath = resolve(repoRoot, inputArg);
const defaultOut = inputPath.replace(/\.xlsx$/i, "") + "-structured.json";
const outputPath = process.argv[3]
  ? resolve(repoRoot, process.argv[3])
  : defaultOut;

const workbook = XLSX.read(readFileSync(inputPath), { type: "buffer", cellDates: true });
const payload = buildPayloadFromWorkbook(workbook, inputPath);

writeFileSync(outputPath, JSON.stringify(payload, null, 2), "utf8");
console.log(`Wrote ${outputPath}`);
