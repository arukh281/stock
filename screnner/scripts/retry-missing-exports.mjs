#!/usr/bin/env node
/**
 * Re-export companies that lack a .features.json file, then finalize.
 *
 * Usage:
 *   node scripts/retry-missing-exports.mjs <export-directory>
 */

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function hasFeaturesFile(exportDir, companyId) {
  const needle = `__${companyId}__`;
  return readdirSync(exportDir).some(
    (n) => n.includes(needle) && n.endsWith(".features.json"),
  );
}

const dirArg = process.argv[2];
if (!dirArg) {
  console.error("Usage: node scripts/retry-missing-exports.mjs <export-directory>");
  process.exit(1);
}

const exportDir = resolve(repoRoot, dirArg);
const manifestPath = join(exportDir, "export-manifest.json");
if (!existsSync(manifestPath)) {
  console.error(`Missing ${manifestPath}`);
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const missing = (manifest.companies ?? []).filter((c) => !hasFeaturesFile(exportDir, c.companyId));

if (!missing.length) {
  console.log("All companies already have .features.json — nothing to retry.");
  process.exit(0);
}

console.log(`Retrying ${missing.length} missing export(s) …`);
missing.forEach((c, i) => console.log(`  ${i + 1}. ${c.name}`));

const retryManifest = join(exportDir, "export-manifest-retry.json");
writeFileSync(
  retryManifest,
  JSON.stringify({ query: manifest.query, companies: missing }, null, 2),
);

execFileSync("npm", ["run", "screener:export"], {
  stdio: "inherit",
  cwd: repoRoot,
  env: {
    ...process.env,
    SCREENER_EXPORT_ONLY: "1",
    SCREENER_EXPORT_MANIFEST: retryManifest,
    SCREENER_EXPORT_DIR: exportDir,
    SCREENER_EXPORT_WORKER: "0",
    SCREENER_EXPORT_WORKERS: "1",
    SCREENER_EXPORT_SKIP_FINALIZE: "1",
  },
});

execFileSync(process.execPath, [join(repoRoot, "scripts/export-ingest.mjs"), "finalize", exportDir], {
  stdio: "inherit",
  cwd: repoRoot,
});

const stillMissing = (manifest.companies ?? []).filter((c) => !hasFeaturesFile(exportDir, c.companyId));
if (stillMissing.length) {
  console.warn(`\n${stillMissing.length} still missing after retry:`, stillMissing.map((c) => c.name).join(", "));
  process.exit(1);
}

console.log("\nRetry + finalize complete.");
