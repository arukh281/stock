#!/usr/bin/env node
/**
 * Remove all worksheets except "Data Sheet" from Screener .xlsx exports.
 *
 * Usage:
 *   node scripts/strip-xlsx-to-data-sheet.mjs <file.xlsx | export-directory>
 */

import { readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stripXlsxFileToDataSheet } from "./screener-export-parser.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function usage() {
  console.error(`Usage: node scripts/strip-xlsx-to-data-sheet.mjs <file.xlsx | export-directory>`);
  process.exit(1);
}

const targetArg = process.argv[2];
if (!targetArg) usage();

const target = resolve(repoRoot, targetArg);
const st = statSync(target);
const files = st.isDirectory()
  ? readdirSync(target)
      .filter((n) => n.endsWith(".xlsx") && !n.startsWith("~$"))
      .map((n) => join(target, n))
  : [target];

let ok = 0;
const failed = [];

for (const filePath of files) {
  try {
    stripXlsxFileToDataSheet(filePath);
    ok++;
  } catch (e) {
    failed.push({ file: filePath, reason: String(e?.message ?? e) });
  }
}

console.log(`Stripped ${ok} file(s) to "Data Sheet" only.`);
if (failed.length) {
  console.warn(`Failed ${failed.length} file(s):`, failed);
  process.exit(1);
}
