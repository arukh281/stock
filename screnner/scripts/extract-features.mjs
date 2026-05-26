#!/usr/bin/env node
/**
 * Compress consolidated.json into LLM-friendly features.json.
 * Keeps forensic signals + compact trend arrays instead of full statement tables.
 *
 * Usage:
 *   node scripts/extract-features.mjs <consolidated.json> [output.json]
 *
 * Default output: <same-dir>/features.json
 */

import { readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { canonicalKeyForLabel } from "./screener-export-parser.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");

function usage() {
  console.error(`Usage: node scripts/extract-features.mjs <consolidated.json> [output.json]

Default output: <same-directory>/features.json`);
  process.exit(1);
}

function round(n, digits = 2) {
  if (!Number.isFinite(n)) return null;
  const f = 10 ** digits;
  return Math.round(n * f) / f;
}

function findLineValues(table, canonicalKey) {
  if (!table?.lines || !canonicalKey) return null;
  for (const line of table.lines) {
    if (canonicalKeyForLabel(line.label) === canonicalKey) return line.values ?? null;
  }
  return null;
}

function findLineValuesByLabel(table, labelNeedle) {
  if (!table?.lines) return null;
  const needle = labelNeedle.toLowerCase();
  for (const line of table.lines) {
    if (line.label?.toLowerCase().includes(needle)) return line.values ?? null;
  }
  return null;
}

function seriesFor(table, canonicalKey, periods) {
  const values = findLineValues(table, canonicalKey);
  if (!values || !periods?.length) return null;
  return periods.map((p) => (Number.isFinite(values[p]) ? round(values[p]) : null));
}

function seriesForLabel(table, labelNeedle, periods) {
  const values = findLineValuesByLabel(table, labelNeedle);
  if (!values || !periods?.length) return null;
  return periods.map((p) => (Number.isFinite(values[p]) ? round(values[p]) : null));
}

function latestFinite(values, periods) {
  if (!values || !periods?.length) return null;
  for (let i = periods.length - 1; i >= 0; i--) {
    const v = values[periods[i]];
    if (Number.isFinite(v)) return v;
  }
  return null;
}

function sumOverLabels(values, labels) {
  if (!values) return null;
  let sum = 0;
  let any = false;
  for (const lab of labels) {
    const v = values[lab];
    if (Number.isFinite(v)) {
      sum += v;
      any = true;
    }
  }
  return any ? sum : null;
}

function cagr(values, periods, years) {
  if (!values || !periods?.length || periods.length < years + 1) return null;
  const endLab = periods[periods.length - 1];
  const startLab = periods[periods.length - 1 - years];
  const end = values[endLab];
  const start = values[startLab];
  if (!Number.isFinite(end) || !Number.isFinite(start) || start <= 0) return null;
  return round(((end / start) ** (1 / years) - 1) * 100);
}

function yoyPct(values, periods) {
  if (!values || !periods || periods.length < 2) return null;
  const latest = values[periods[periods.length - 1]];
  const prev = values[periods[periods.length - 2]];
  if (!Number.isFinite(latest) || !Number.isFinite(prev) || prev === 0) return null;
  return round(((latest - prev) / prev) * 100);
}

function ratio(n, d) {
  if (!Number.isFinite(n) || !Number.isFinite(d) || d === 0) return null;
  return round(n / d, 4);
}

function pct(n, d) {
  if (!Number.isFinite(n) || !Number.isFinite(d) || d === 0) return null;
  return round((n / d) * 100);
}

function daysOutstanding(numerator, annualRevenue) {
  if (!Number.isFinite(numerator) || !Number.isFinite(annualRevenue) || annualRevenue === 0) {
    return null;
  }
  return round((numerator / annualRevenue) * 365);
}

function computeCashQuality5y(statements) {
  const annual = statements.profitAndLossAnnual;
  const cf = statements.cashFlow;
  if (!annual?.periods?.length) return null;
  const labels = annual.periods.slice(-Math.min(5, annual.periods.length));
  const patVals = findLineValues(annual, "pat");
  const cfoVals = findLineValues(cf, "cfo");
  const pat = sumOverLabels(patVals, labels);
  const cfo = sumOverLabels(cfoVals, labels);
  if (!Number.isFinite(pat) || pat === 0 || !Number.isFinite(cfo)) return null;
  return round(cfo / pat, 4);
}

function computeCwipRatio(snapshot) {
  return ratio(snapshot.cwip, snapshot.net_block);
}

function computeQtrYoY(statements) {
  const q = statements.quarterly;
  if (!q?.periods || q.periods.length < 5) return null;
  const rev = findLineValues(q, "revenue");
  if (!rev) return null;
  const latest = rev[q.periods[q.periods.length - 1]];
  const yoy = rev[q.periods[q.periods.length - 5]];
  if (!Number.isFinite(latest) || !Number.isFinite(yoy) || yoy === 0) return null;
  return round(((latest - yoy) / yoy) * 100);
}

function cfoToPatByYear(statements, maxYears = 5) {
  const annual = statements.profitAndLossAnnual;
  const cf = statements.cashFlow;
  if (!annual?.periods?.length) return null;
  const labels = annual.periods.slice(-Math.min(maxYears, annual.periods.length));
  const pat = findLineValues(annual, "pat");
  const cfo = findLineValues(cf, "cfo");
  const out = {};
  for (const lab of labels) {
    const p = pat?.[lab];
    const c = cfo?.[lab];
    if (Number.isFinite(p) && p !== 0 && Number.isFinite(c)) out[lab] = round(c / p, 3);
  }
  return Object.keys(out).length ? out : null;
}

function priceReturns(statements) {
  const price = statements.priceByPeriod;
  if (!price?.periods?.length || !price.lines?.[0]?.values) return null;
  const periods = price.periods;
  const values = price.lines[0].values;
  const latest = latestFinite(values, periods);
  if (!Number.isFinite(latest)) return null;

  const out = { latest: round(latest) };
  for (const [years, label] of [
    [1, "return_1y_pct"],
    [3, "return_3y_pct"],
    [5, "return_5y_pct"],
  ]) {
    const idx = periods.length - 1 - years;
    if (idx < 0) continue;
    const past = values[periods[idx]];
    if (Number.isFinite(past) && past !== 0) {
      out[label] = round(((latest - past) / past) * 100);
    }
  }
  return out;
}

export function extractCompany(company) {
  const { company_info, latest_snapshot, derived_signals, statements } = company;
  const annual = statements.profitAndLossAnnual;
  const q = statements.quarterly;
  const bs = statements.balanceSheet;
  const cf = statements.cashFlow;
  const annualPeriods = annual?.periods ?? [];
  const qPeriods = q?.periods ?? [];

  const revAnnual = findLineValues(annual, "revenue");
  const patAnnual = findLineValues(annual, "pat");
  const pbtAnnual = findLineValuesByLabel(annual, "profit before tax");
  const interestAnnual = findLineValuesByLabel(annual, "interest");
  const cfoAnnual = findLineValues(cf, "cfo");
  const cfiAnnual = findLineValuesByLabel(cf, "investing");

  const equity = findLineValuesByLabel(bs, "equity share capital");
  const reserves = findLineValuesByLabel(bs, "reserves");
  const borrowings = findLineValuesByLabel(bs, "borrowings");
  const receivables = findLineValues(bs, "receivables");
  const inventory = findLineValues(bs, "inventory");
  const cwip = findLineValues(bs, "cwip");
  const netBlock = findLineValues(bs, "net_block");

  const latestAnnual = annualPeriods.at(-1);
  const latestRev = latestAnnual && revAnnual ? revAnnual[latestAnnual] : null;
  const latestPat = latestAnnual && patAnnual ? patAnnual[latestAnnual] : null;
  const latestPbt = latestAnnual && pbtAnnual ? pbtAnnual[latestAnnual] : null;
  const latestInterest = latestAnnual && interestAnnual ? interestAnnual[latestAnnual] : null;
  const latestCfo = latestAnnual && cfoAnnual ? cfoAnnual[latestAnnual] : null;
  const latestCfi = latestAnnual && cfiAnnual ? cfiAnnual[latestAnnual] : null;

  const latestEquity =
    latestAnnual && equity && reserves
      ? (equity[latestAnnual] ?? 0) + (reserves[latestAnnual] ?? 0)
      : null;
  const latestBorrowings = latestAnnual && borrowings ? borrowings[latestAnnual] : null;
  const latestReceivables = latestAnnual && receivables ? receivables[latestAnnual] : null;
  const latestInventory = latestAnnual && inventory ? inventory[latestAnnual] : null;

  const cashQuality =
    derived_signals?.cash_quality_5y ?? computeCashQuality5y(statements);
  const cwipRatio =
    derived_signals?.cwip_to_net_block_ratio ?? computeCwipRatio(latest_snapshot);
  const qtrYoY = derived_signals?.qtr_yoy_growth ?? computeQtrYoY(statements);

  const receivablesToQtrRev = ratio(
    latest_snapshot.receivables,
    latest_snapshot.revenue_latest_qtr,
  );

  const features = {
    forensic: {
      cash_quality_5y: round(cashQuality, 4),
      cwip_to_net_block_ratio: round(cwipRatio, 4),
      qtr_yoy_growth_pct: round(qtrYoY),
      receivables_to_qtr_revenue: receivablesToQtrRev,
      passes_cash_quality: Number.isFinite(cashQuality) ? cashQuality >= 1 : null,
      inflection_candidate:
        Number.isFinite(cwipRatio) &&
        Number.isFinite(qtrYoY) &&
        cwipRatio >= 0.15 &&
        qtrYoY > 0,
      receivables_warning:
        Number.isFinite(receivablesToQtrRev) ? receivablesToQtrRev > 1 : null,
    },

    profitability: {
      pat_margin_latest_yr_pct: pct(latestPat, latestRev),
      pbt_margin_latest_yr_pct: pct(latestPbt, latestRev),
      interest_coverage_latest_yr: ratio(
        latestPbt != null && latestInterest != null ? latestPbt + latestInterest : null,
        latestInterest,
      ),
    },

    growth: {
      revenue_cagr_3y_pct: cagr(revAnnual, annualPeriods, 3),
      revenue_cagr_5y_pct: cagr(revAnnual, annualPeriods, 5),
      pat_cagr_3y_pct: cagr(patAnnual, annualPeriods, 3),
      revenue_yoy_latest_yr_pct: yoyPct(revAnnual, annualPeriods),
      pat_yoy_latest_yr_pct: yoyPct(patAnnual, annualPeriods),
    },

    cash_flow: {
      cfo_to_pat_by_year: cfoToPatByYear(statements),
      latest_yr_cfo: round(latestCfo),
      latest_yr_fcf: round(
        Number.isFinite(latestCfo) && Number.isFinite(latestCfi)
          ? latestCfo + latestCfi
          : null,
      ),
      capex_to_revenue_latest_yr: pct(
        Number.isFinite(latestCfi) ? Math.abs(latestCfi) : null,
        latestRev,
      ),
    },

    balance_sheet: {
      debt_to_equity: ratio(latestBorrowings, latestEquity),
      receivables_days: daysOutstanding(latestReceivables, latestRev),
      inventory_days: daysOutstanding(latestInventory, latestRev),
      net_block_yoy_pct: yoyPct(netBlock, annualPeriods),
      cwip_yoy_pct: yoyPct(cwip, annualPeriods),
    },

    price: priceReturns(statements),

    trends: {
      annual: {
        periods: annualPeriods.slice(-5),
        revenue: seriesFor(annual, "revenue", annualPeriods.slice(-5)),
        pat: seriesFor(annual, "pat", annualPeriods.slice(-5)),
        cfo: seriesFor(cf, "cfo", annualPeriods.slice(-5)),
        receivables: seriesFor(bs, "receivables", annualPeriods.slice(-5)),
        borrowings: seriesForLabel(bs, "borrowings", annualPeriods.slice(-5)),
        net_block: seriesFor(bs, "net_block", annualPeriods.slice(-5)),
        cwip: seriesFor(bs, "cwip", annualPeriods.slice(-5)),
      },
      quarterly: {
        periods: qPeriods.slice(-5),
        revenue: seriesFor(q, "revenue", qPeriods.slice(-5)),
        pat: seriesFor(q, "pat", qPeriods.slice(-5)),
        operating_profit: findLineValuesByLabel(q, "operating profit")
          ? qPeriods.slice(-5).map((p) => {
              const v = findLineValuesByLabel(q, "operating profit")[p];
              return Number.isFinite(v) ? round(v) : null;
            })
          : null,
      },
    },
  };

  return {
    source: company.source,
    company_info,
    latest_snapshot,
    features,
  };
}

export function buildFeaturesPayload(raw, sourceConsolidated) {
  const companies = (raw.companies ?? []).map(extractCompany);
  return {
    extractedAt: new Date().toISOString(),
    sourceConsolidated: sourceConsolidated ?? null,
    sourceDirectory: raw.sourceDirectory ?? null,
    count: companies.length,
    schema: {
      description:
        "LLM-friendly feature extract from consolidated.json. Full statements omitted; trends are last 5 periods.",
      sections: [
        "forensic — Phase 3 filters (cash quality, CWIP inflection, receivables)",
        "profitability / growth / cash_flow / balance_sheet — computed ratios",
        "price — latest and multi-year returns",
        "trends — compact arrays for annual & quarterly key lines",
      ],
    },
    companies,
  };
}

const isMain =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const inputArg = process.argv[2];
  if (!inputArg) usage();

  const inputPath = resolve(repoRoot, inputArg);
  const outPath = process.argv[3]
    ? resolve(repoRoot, process.argv[3])
    : join(dirname(inputPath), "features.json");

  const raw = JSON.parse(readFileSync(inputPath, "utf8"));
  const payload = buildFeaturesPayload(raw, basename(inputPath));

  writeFileSync(outPath, JSON.stringify(payload, null, 2), "utf8");

  const inBytes = readFileSync(inputPath).length;
  const outBytes = readFileSync(outPath).length;
  console.log(
    `Wrote ${outPath} (${payload.count} companies, ${(outBytes / 1024).toFixed(1)} KB, ${Math.round((1 - outBytes / inBytes) * 100)}% smaller than consolidated)`,
  );
}
