#!/usr/bin/env node
/**
 * Incremental export ingest: xlsx → *.features.json immediately, drop xlsx.
 * finalize → features.compact.json (+ per-company *.features.json; extras removed)
 *
 * Usage:
 *   node scripts/export-ingest.mjs ingest <file.xlsx> <export-directory>
 *   node scripts/export-ingest.mjs finalize <export-directory>
 */

import { execFileSync } from "node:child_process";
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as XLSX from "xlsx";
import { buildPayloadFromWorkbook, stripXlsxFileToDataSheet } from "./screener-export-parser.mjs";
import { buildFeaturesPayload } from "./extract-features.mjs";
import { writeCompactPack } from "./llm-pack.mjs";

const EXTRA_EXPORT_FILES = new Set([
  "consolidated.json",
  "features.json",
  "screening.md",
  "export-manifest.json",
  "export-manifest-retry.json",
]);

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");
const node = process.execPath;

const RAW_COMPANIES_FILE = "_raw-companies.json";
const keepXlsx = process.env.SCREENER_KEEP_XLSX === "1";

function parallelExportWorkers() {
  const n = Number.parseInt(process.env.SCREENER_EXPORT_WORKERS ?? "1", 10);
  return Number.isFinite(n) && n > 1 ? n : 1;
}

function rawCompaniesPath(exportDir) {
  const workers = parallelExportWorkers();
  if (workers > 1) {
    const w = Number.parseInt(process.env.SCREENER_EXPORT_WORKER ?? "0", 10);
    const idx = Number.isFinite(w) ? w : 0;
    return join(exportDir, `_raw-companies-w${idx}.json`);
  }
  return join(exportDir, RAW_COMPANIES_FILE);
}

function loadRawCompanies(exportDir) {
  const p = rawCompaniesPath(exportDir);
  if (!existsSync(p)) return [];
  return JSON.parse(readFileSync(p, "utf8"));
}

function loadAllRawCompanies(exportDir) {
  const merged = [];
  const seen = new Set();
  for (const name of readdirSync(exportDir)) {
    const isWorkerShard = name.startsWith("_raw-companies-w") && name.endsWith(".json");
    if (name !== RAW_COMPANIES_FILE && !isWorkerShard) continue;
    const list = JSON.parse(readFileSync(join(exportDir, name), "utf8"));
    for (const c of list) {
      const key = c.source ?? c.company_info?.name;
      if (key && seen.has(key)) continue;
      if (key) seen.add(key);
      merged.push(c);
    }
  }
  return merged;
}

function saveRawCompanies(exportDir, companies) {
  writeFileSync(rawCompaniesPath(exportDir), JSON.stringify(companies), "utf8");
}

function featuresOutPath(exportDir, xlsxName) {
  return join(exportDir, basename(xlsxName).replace(/\.xlsx$/i, "") + ".features.json");
}

/** Drop batch/archive files — keep features.compact.json and per-company *.features.json. */
export function pruneExportArtifacts(exportDir) {
  const absDir = resolve(exportDir);
  for (const name of readdirSync(absDir)) {
    if (EXTRA_EXPORT_FILES.has(name)) {
      unlinkSync(join(absDir, name));
      continue;
    }
    if (
      name.startsWith("export-failures-w") ||
      name.startsWith("_raw-companies") ||
      name.startsWith(".tmp.")
    ) {
      unlinkSync(join(absDir, name));
    }
  }
}

function cleanupRawShards(absDir) {
  for (const name of readdirSync(absDir)) {
    if (name === RAW_COMPANIES_FILE || name.startsWith("_raw-companies-w")) {
      unlinkSync(join(absDir, name));
    }
  }
}

/** Parse one export xlsx → per-company features JSON; append to raw list; remove xlsx. */
export function ingestExportXlsx(xlsxPath, exportDir) {
  const absXlsx = resolve(xlsxPath);
  const absDir = resolve(exportDir);

  stripXlsxFileToDataSheet(absXlsx);
  const workbook = XLSX.read(readFileSync(absXlsx), { type: "buffer", cellDates: true });
  const company = buildPayloadFromWorkbook(workbook, basename(absXlsx));

  const featuresPath = featuresOutPath(absDir, basename(absXlsx));
  const featuresPayload = buildFeaturesPayload({ companies: [company] }, company.source);
  writeFileSync(featuresPath, JSON.stringify(featuresPayload, null, 2), "utf8");

  const list = loadRawCompanies(absDir);
  const idx = list.findIndex((c) => c.source === company.source);
  if (idx >= 0) list[idx] = company;
  else list.push(company);
  saveRawCompanies(absDir, list);

  if (!keepXlsx) {
    unlinkSync(absXlsx);
  }

  return { featuresPath, companyName: company.company_info?.name ?? basename(absXlsx) };
}

/** Build features.compact.json from ingested companies (partial OK if export was interrupted). */
export function finalizeExportDir(exportDir) {
  const absDir = resolve(exportDir);
  const companies = loadAllRawCompanies(absDir);

  if (!companies.length) {
    throw new Error(
      `No ingested companies in ${absDir}. Run ingest on .xlsx files or re-export.`,
    );
  }

  const featuresPayload = buildFeaturesPayload(
    { companies, sourceDirectory: basename(absDir) },
    null,
  );
  const pack = writeCompactPack(featuresPayload, absDir);

  cleanupRawShards(absDir);
  pruneExportArtifacts(absDir);

  return {
    compactPath: pack.compactPath,
    count: companies.length,
  };
}

/** Rebuild compact from legacy consolidated.json (then prune extras). */
function refreshFromConsolidated(absDir) {
  const consolidatedPath = join(absDir, "consolidated.json");
  if (!existsSync(consolidatedPath)) return null;

  const consolidated = JSON.parse(readFileSync(consolidatedPath, "utf8"));
  const featuresPayload = buildFeaturesPayload(consolidated, basename(consolidatedPath));
  const pack = writeCompactPack(featuresPayload, absDir);
  pruneExportArtifacts(absDir);

  return {
    compactPath: pack.compactPath,
    count: consolidated.count ?? consolidated.companies?.length ?? 0,
    ingestedXlsx: 0,
  };
}

function refreshFromFeaturesJson(absDir) {
  const featuresPath = join(absDir, "features.json");
  if (!existsSync(featuresPath)) return null;

  const featuresPayload = JSON.parse(readFileSync(featuresPath, "utf8"));
  const pack = writeCompactPack(featuresPayload, absDir);
  pruneExportArtifacts(absDir);

  return {
    compactPath: pack.compactPath,
    count: featuresPayload.count ?? featuresPayload.companies?.length ?? 0,
    ingestedXlsx: 0,
  };
}

/** Ingest any leftover xlsx, then finalize (CLI post-process / interrupted exports). */
export function postProcessExportDir(exportDir) {
  const absDir = resolve(exportDir);
  if (!statSync(absDir).isDirectory()) {
    throw new Error(`Not a directory: ${absDir}`);
  }

  const xlsxFiles = readdirSync(absDir)
    .filter((n) => n.endsWith(".xlsx") && !n.startsWith("~$"))
    .map((n) => join(absDir, n));

  let ingested = 0;
  for (const filePath of xlsxFiles) {
    try {
      ingestExportXlsx(filePath, absDir);
      ingested++;
      console.log(`  ✓ ${basename(filePath)} → ${basename(featuresOutPath(absDir, basename(filePath)))}`);
    } catch (e) {
      console.warn(`  ✗ ${basename(filePath)}:`, e?.message ?? e);
    }
  }

  if (ingested) {
    console.log(`Ingested ${ingested} .xlsx file(s).`);
  }

  const raw = loadAllRawCompanies(absDir);
  if (raw.length > 0) {
    const result = finalizeExportDir(absDir);
    return { ...result, ingestedXlsx: ingested };
  }

  const refreshed = refreshFromConsolidated(absDir);
  if (refreshed) {
    console.log("Rebuilt features.compact.json from consolidated.json (extras removed).");
    return refreshed;
  }

  const fromFeatures = refreshFromFeaturesJson(absDir);
  if (fromFeatures) {
    console.log("Rebuilt features.compact.json from features.json (extras removed).");
    return { ...fromFeatures, ingestedXlsx: ingested };
  }

  if (
    readdirSync(absDir).some((n) => n.endsWith(".features.json")) &&
    existsSync(join(absDir, "features.compact.json"))
  ) {
    pruneExportArtifacts(absDir);
    const n = readdirSync(absDir).filter((f) => f.endsWith(".features.json")).length;
    console.log("Pruned extras; kept *.features.json + features.compact.json.");
    return { compactPath: join(absDir, "features.compact.json"), count: n, ingestedXlsx: ingested };
  }

  throw new Error(
    `Nothing to process in ${absDir}. Need .xlsx, _raw-companies*.json, or *.features.json.`,
  );
}

function usage() {
  console.error(`Usage:
  node scripts/export-ingest.mjs ingest <file.xlsx> <export-directory>
  node scripts/export-ingest.mjs finalize <export-directory>
  node scripts/export-ingest.mjs post-process <export-directory>`);
  process.exit(1);
}

const isMain =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const cmd = process.argv[2];
  if (cmd === "ingest") {
    const xlsx = process.argv[3];
    const dir = process.argv[4];
    if (!xlsx || !dir) usage();
    const r = ingestExportXlsx(resolve(repoRoot, xlsx), resolve(repoRoot, dir));
    console.log(`→ ${r.featuresPath}${keepXlsx ? " (xlsx kept)" : " (xlsx removed)"}`);
  } else if (cmd === "finalize") {
    const dir = process.argv[3];
    if (!dir) usage();
    const r = finalizeExportDir(resolve(repoRoot, dir));
    console.log(`\nFinalized ${r.count} companies → ${r.compactPath}`);
    console.log(`  (+ per-company *.features.json; extras removed)`);
  } else if (cmd === "post-process") {
    const dir = process.argv[3];
    if (!dir) usage();
    const r = postProcessExportDir(resolve(repoRoot, dir));
    console.log(`\nDone (${r.count} companies, ${r.ingestedXlsx} xlsx ingested this run).`);
  } else {
    usage();
  }
}
