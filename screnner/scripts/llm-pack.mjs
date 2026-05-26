#!/usr/bin/env node
/**
 * Build ultra-compact LLM artifacts from features.json:
 *   - features.compact.json  (columnar, minified — best for chat context)
 *   - screening.md           (one table row per company — best for triage)
 *
 * Usage:
 *   node scripts/llm-pack.mjs <features.json>
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Column order for features.compact.json `data` rows */
export const COMPACT_COLUMNS = [
  "name",
  "market_cap_cr",
  "cwip",
  "net_block",
  "receivables",
  "revenue_qtr",
  "cash_quality_5y",
  "cwip_to_net_block",
  "qtr_yoy_pct",
  "passes_cash_quality",
  "inflection_candidate",
  "receivables_warning",
  "revenue_cagr_5y_pct",
  "pat_cagr_3y_pct",
  "debt_to_equity",
  "price",
  "return_1y_pct",
];

function rowFromCompany(c) {
  const f = c.features ?? {};
  const snap = c.latest_snapshot ?? {};
  const forensic = f.forensic ?? {};
  const growth = f.growth ?? {};
  const bs = f.balance_sheet ?? {};
  const price = f.price ?? {};

  return [
    c.company_info?.name ?? c.source?.replace(/\.xlsx$/i, "") ?? "?",
    c.company_info?.market_cap ?? null,
    snap.cwip ?? null,
    snap.net_block ?? null,
    snap.receivables ?? null,
    snap.revenue_latest_qtr ?? null,
    forensic.cash_quality_5y ?? null,
    forensic.cwip_to_net_block_ratio ?? null,
    forensic.qtr_yoy_growth_pct ?? null,
    forensic.passes_cash_quality ?? null,
    forensic.inflection_candidate ?? null,
    forensic.receivables_warning ?? null,
    growth.revenue_cagr_5y_pct ?? null,
    growth.pat_cagr_3y_pct ?? null,
    bs.debt_to_equity ?? null,
    price.latest ?? null,
    price.return_1y_pct ?? null,
  ];
}

function flag(v) {
  if (v === true) return "✓";
  if (v === false) return "—";
  return "";
}

export function buildCompactPack(featuresPayload) {
  const companies = featuresPayload.companies ?? [];
  return {
    v: 1,
    format: "screener-features-compact",
    extractedAt: featuresPayload.extractedAt ?? null,
    count: companies.length,
    columns: COMPACT_COLUMNS,
    legend: {
      cash_quality_5y: "5Y CFO / 5Y PAT (>1 preferred)",
      cwip_to_net_block: "CWIP / Net Block (inflection if ≥0.15 with positive qtr_yoy)",
      passes_cash_quality: "forensic.cash_quality_5y >= 1",
      inflection_candidate: "high CWIP ratio + positive quarterly YoY revenue",
      receivables_warning: "receivables / latest quarter revenue > 1",
    },
    data: companies.map(rowFromCompany),
  };
}

export function buildScreeningMarkdown(featuresPayload) {
  const companies = featuresPayload.companies ?? [];
  const lines = [
    "# Screener export — screening table",
    "",
    `Companies: **${companies.length}** · Use \`features.compact.json\` for full numeric context.`,
    "",
    "| Company | MCap (Cr) | CQ5 | CWIP/NB | Q YoY% | Pass CQ | Inflect | Recv⚠ | Rev 5Y% | D/E | Price | 1Y% |",
    "|---|---:|---:|---:|---:|:---:|:---:|:---:|---:|---:|---:|---:|",
  ];

  for (const c of companies) {
    const f = c.features?.forensic ?? {};
    const g = c.features?.growth ?? {};
    const bs = c.features?.balance_sheet ?? {};
    const p = c.features?.price ?? {};
    const name = c.company_info?.name ?? "?";
    const mc = c.company_info?.market_cap;
    lines.push(
      `| ${name} | ${fmt(mc)} | ${fmt(f.cash_quality_5y)} | ${fmt(f.cwip_to_net_block_ratio)} | ${fmt(f.qtr_yoy_growth_pct)} | ${flag(f.passes_cash_quality)} | ${flag(f.inflection_candidate)} | ${flag(f.receivables_warning)} | ${fmt(g.revenue_cagr_5y_pct)} | ${fmt(bs.debt_to_equity)} | ${fmt(p.latest)} | ${fmt(p.return_1y_pct)} |`,
    );
  }

  lines.push(
    "",
    "**Legend:** CQ5 = cash quality 5y · CWIP/NB = capacity build vs assets · Pass CQ / Inflect / Recv⚠ = boolean flags (✓/—).",
    "",
    "For deep-dive on 2–3 names, attach per-company `*.features.json` (from `npm run screener:xlsx-to-features -- file.xlsx`), not raw `.xlsx`.",
  );
  return lines.join("\n");
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Y" : "N";
  if (typeof v === "number") {
    if (Math.abs(v) >= 100) return String(Math.round(v));
    return String(Math.round(v * 100) / 100);
  }
  return String(v);
}

/** Write only features.compact.json (default export output). */
export function writeCompactPack(featuresPayload, exportDir) {
  const compactPath = join(exportDir, "features.compact.json");
  const compact = buildCompactPack(featuresPayload);
  writeFileSync(compactPath, JSON.stringify(compact), "utf8");
  return {
    compactPath,
    compactBytes: readFileSync(compactPath).length,
    count: compact.count,
  };
}

/** Full pack: compact + screening.md (manual `llm-pack` CLI only). */
export function writeLlmPack(featuresPath) {
  const featuresPayload = JSON.parse(readFileSync(featuresPath, "utf8"));
  const dir = dirname(featuresPath);

  const { compactPath, compactBytes, count } = writeCompactPack(featuresPayload, dir);

  const mdPath = join(dir, "screening.md");
  writeFileSync(mdPath, buildScreeningMarkdown(featuresPayload), "utf8");

  const featuresBytes = readFileSync(featuresPath).length;

  return {
    compactPath,
    mdPath,
    compactBytes,
    featuresBytes,
    count,
  };
}

function usage() {
  console.error(`Usage: node scripts/llm-pack.mjs <features.json>`);
  process.exit(1);
}

const isMain =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const input = process.argv[2];
  if (!input) usage();
  const resolved = input.startsWith("/") ? input : resolve(process.cwd(), input);
  const r = writeLlmPack(resolved);
  console.log(
    `Wrote ${r.compactPath} (${(r.compactBytes / 1024).toFixed(1)} KB, ${Math.round((1 - r.compactBytes / r.featuresBytes) * 100)}% smaller than features.json)`,
  );
  console.log(`Wrote ${r.mdPath} (${r.count} companies)`);
}
