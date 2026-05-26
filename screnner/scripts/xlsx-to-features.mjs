#!/usr/bin/env node
/**
 * One Screener export .xlsx → single-company features JSON (for LLM deep-dive).
 * Parses "Data Sheet" with the shared parser — no LLM code required.
 *
 * Usage:
 *   node scripts/xlsx-to-features.mjs <file.xlsx> [output.json]
 *
 * Default output: <same-dir>/<basename>.features.json
 */

import { readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as XLSX from "xlsx";
import { buildPayloadFromWorkbook } from "./screener-export-parser.mjs";
import { buildFeaturesPayload } from "./extract-features.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function usage() {
  console.error(`Usage: node scripts/xlsx-to-features.mjs <file.xlsx> [output.json]

Default output: <same-dir>/<basename>.features.json`);
  process.exit(1);
}

const isMain =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const inputArg = process.argv[2];
  if (!inputArg) usage();

  const inputPath = resolve(repoRoot, inputArg);
  const defaultOut = join(
    dirname(inputPath),
    basename(inputPath).replace(/\.xlsx$/i, "") + ".features.json",
  );
  const outPath = process.argv[3] ? resolve(repoRoot, process.argv[3]) : defaultOut;

  const workbook = XLSX.read(readFileSync(inputPath), { type: "buffer", cellDates: true });
  const company = buildPayloadFromWorkbook(workbook, basename(inputPath));
  const payload = buildFeaturesPayload(
    { companies: [company], sourceDirectory: basename(dirname(inputPath)) },
    basename(inputPath),
  );

  writeFileSync(outPath, JSON.stringify(payload, null, 2), "utf8");
  console.log(`Wrote ${outPath} (1 company, ${(readFileSync(outPath).length / 1024).toFixed(1)} KB)`);
}
