/**
 * Shared parser for Screener.in multi-sheet .xlsx exports (Data Sheet layout).
 */

import { readFileSync, writeFileSync } from "node:fs";
import { basename } from "node:path";
import * as XLSX from "xlsx";

export const DATA_SHEET_NAME = "Data Sheet";

export const SKIP_SHEETS = new Set(["Customization"]);

/** Keep only the Screener "Data Sheet" tab (drops P&L, Quarters, Balance Sheet, Cash Flow, Customization). */
export function stripWorkbookToDataSheet(workbook) {
  const sheet = workbook.Sheets[DATA_SHEET_NAME];
  if (!sheet) {
    throw new Error(`Missing required sheet "${DATA_SHEET_NAME}"`);
  }
  return {
    SheetNames: [DATA_SHEET_NAME],
    Sheets: { [DATA_SHEET_NAME]: sheet },
  };
}

/** Overwrite an export .xlsx so it contains only "Data Sheet". */
export function stripXlsxFileToDataSheet(filePath) {
  const workbook = XLSX.read(readFileSync(filePath), { type: "buffer", cellDates: true });
  const stripped = stripWorkbookToDataSheet(workbook);
  writeFileSync(filePath, XLSX.write(stripped, { type: "buffer", bookType: "xlsx" }));
}

/** Raw Screener row label → strict key for snapshot / signals */
export const LABEL_MAP = {
  "Net profit": "pat",
  "Cash from Operating Activity": "cfo",
  "Capital Work in Progress": "cwip",
  "Net Block": "net_block",
  Sales: "revenue",
  Receivables: "receivables",
  Inventory: "inventory",
};

function normalizeLabelForLookup(s) {
  return String(s ?? "")
    .trim()
    .replace(/:+\s*$/u, "")
    .toLowerCase();
}

const LABEL_LOOKUP = new Map();
for (const [display, canon] of Object.entries(LABEL_MAP)) {
  LABEL_LOOKUP.set(normalizeLabelForLookup(display), canon);
}

/** Map a raw line label to a canonical key, or null if unmapped */
export function canonicalKeyForLabel(label) {
  const hit = LABEL_LOOKUP.get(normalizeLabelForLookup(label));
  if (hit) return hit;
  const k = keyify(label);
  for (const [display, canon] of Object.entries(LABEL_MAP)) {
    if (keyify(display) === k) return canon;
  }
  return null;
}

function trimRow(row) {
  return row.map((c) => String(c ?? "").trim());
}

function rowNonEmptyCount(tr) {
  return tr.filter(Boolean).length;
}

function isRowEmpty(tr) {
  return rowNonEmptyCount(tr) === 0;
}

function matchSection(firstCell) {
  const raw = String(firstCell ?? "").trim();
  if (!raw) return null;
  const head = raw.replace(/:+\s*$/u, "").trim();
  const u = head.toUpperCase();
  if (u === "META") return "meta";
  if (u === "PROFIT & LOSS") return "profitAndLoss";
  if (u === "QUARTERS") return "quarters";
  if (u === "BALANCE SHEET") return "balanceSheet";
  if (u.startsWith("CASH FLOW")) return "cashFlow";
  if (u.startsWith("PRICE")) return "price";
  if (u.startsWith("DERIVED")) return "derived";
  return null;
}

function sectionTitleKind(tr) {
  const kind = matchSection(tr[0]);
  if (!kind) return null;
  if (kind === "price" && rowNonEmptyCount(tr) > 1) return null;
  if (rowNonEmptyCount(tr) !== 1) return null;
  return kind;
}

function keyify(label) {
  return String(label ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function kvText(v) {
  const s = String(v ?? "").trim();
  return s === "" ? null : s;
}

function parseFinancialNumber(v) {
  const s = String(v ?? "").trim();
  if (!s) return null;
  if (/^[\s\-–—]+$/u.test(s)) return null;
  const cleaned = s.replace(/,/g, "");
  const n = Number(cleaned);
  if (!Number.isFinite(n)) return null;
  return n;
}

function valuesFromPeriodSpec(tr, periodSpec) {
  const values = {};
  for (const p of periodSpec) {
    const n = parseFinancialNumber(tr[p.col]);
    if (n !== null) values[p.label] = n;
  }
  return values;
}

function parseReportDateTable(rows, startIndex, periodSpecRef) {
  let i = startIndex;
  while (i < rows.length && isRowEmpty(trimRow(rows[i]))) i++;
  if (i >= rows.length) return { table: null, nextIndex: i };

  const tr0 = trimRow(rows[i]);
  if (!/^report date$/iu.test(tr0[0])) return { table: null, nextIndex: i };

  const periodSpec = [];
  for (let c = 1; c < tr0.length; c++) {
    const lab = tr0[c];
    if (lab) periodSpec.push({ col: c, label: lab });
  }
  periodSpecRef.current = periodSpec;
  i++;

  const lines = [];
  while (i < rows.length) {
    const tr = trimRow(rows[i]);
    if (isRowEmpty(tr)) {
      i++;
      continue;
    }
    if (sectionTitleKind(tr)) break;
    if (matchSection(tr[0]) === "price" && rowNonEmptyCount(tr) > 1) break;

    const label = tr[0];
    if (!label || /^report date$/iu.test(label)) {
      i++;
      continue;
    }

    const values = valuesFromPeriodSpec(tr, periodSpec);
    lines.push({
      label,
      ...(Object.keys(values).length ? { values } : {}),
    });
    i++;
  }

  return {
    table: {
      periods: periodSpec.map((p) => p.label),
      lines,
    },
    nextIndex: i,
  };
}

function parseContinuationLines(rows, startIndex, periodSpec) {
  let i = startIndex;
  while (i < rows.length && isRowEmpty(trimRow(rows[i]))) i++;

  const lines = [];
  while (i < rows.length) {
    const tr = trimRow(rows[i]);
    if (isRowEmpty(tr)) {
      i++;
      continue;
    }
    if (sectionTitleKind(tr)) break;

    const label = tr[0];
    if (!label) {
      i++;
      continue;
    }

    const values = valuesFromPeriodSpec(tr, periodSpec);
    lines.push({
      label,
      ...(Object.keys(values).length ? { values } : {}),
    });
    i++;
  }

  return { lines, nextIndex: i };
}

function parseDataSheet(rows2d) {
  const rows = rows2d.map(trimRow);
  const n = rows.length;

  const company = {};
  const meta = {};
  const periodSpecRef = { current: [] };

  let i = 0;
  while (i < n) {
    const tr = rows[i];
    if (isRowEmpty(tr)) {
      i++;
      continue;
    }
    if (matchSection(tr[0]) === "meta" && rowNonEmptyCount(tr) === 1) break;

    const key = keyify(tr[0]) || `row_${i}`;
    company[key] = kvText(tr[1]);
    for (let c = 2; c < tr.length; c++) {
      const extra = tr[c];
      if (extra && extra.length > 8) {
        if (!company._notes) company._notes = [];
        company._notes.push(extra);
      }
    }
    i++;
  }

  if (i < n && matchSection(rows[i][0]) === "meta" && rowNonEmptyCount(rows[i]) === 1)
    i++;

  while (i < n) {
    const tr = rows[i];
    if (isRowEmpty(tr)) {
      i++;
      continue;
    }
    if (matchSection(tr[0]) === "profitAndLoss" && rowNonEmptyCount(tr) === 1) break;

    const key = keyify(tr[0]) || `meta_${i}`;
    meta[key] = kvText(tr[1]);
    i++;
  }

  const tables = {};

  while (i < n) {
    const tr = rows[i];
    if (isRowEmpty(tr)) {
      i++;
      continue;
    }

    const kind = sectionTitleKind(tr);
    const secFromA = matchSection(tr[0]);

    if (
      secFromA === "price" &&
      rowNonEmptyCount(tr) > 1 &&
      periodSpecRef.current.length
    ) {
      const values = valuesFromPeriodSpec(tr, periodSpecRef.current);
      tables.price = {
        periods: periodSpecRef.current.map((p) => p.label),
        lines: [
          {
            label: tr[0].replace(/:+\s*$/u, "").trim(),
            values,
          },
        ],
      };
      i++;
      continue;
    }

    if (!kind) {
      i++;
      continue;
    }

    if (
      kind === "profitAndLoss" ||
      kind === "quarters" ||
      kind === "balanceSheet" ||
      kind === "cashFlow"
    ) {
      const { table, nextIndex } = parseReportDateTable(rows, i + 1, periodSpecRef);
      tables[kind] = table;
      i = nextIndex;
      continue;
    }

    if (kind === "derived") {
      const { lines, nextIndex } = parseContinuationLines(
        rows,
        i + 1,
        periodSpecRef.current,
      );
      tables.derived = {
        periods: periodSpecRef.current.map((p) => p.label),
        lines,
      };
      i = nextIndex;
      continue;
    }

    i++;
  }

  return { company, meta, tables };
}

function buildStatementsFromTables(tables) {
  return {
    profitAndLossAnnual: tables.profitAndLoss ?? null,
    quarterly: tables.quarters ?? null,
    balanceSheet: tables.balanceSheet ?? null,
    cashFlow: tables.cashFlow ?? null,
    priceByPeriod: tables.price ?? null,
    derived: tables.derived ?? null,
  };
}

function findLineValuesForCanonical(table, canonicalKey) {
  if (!table?.lines || !canonicalKey) return null;
  for (const line of table.lines) {
    if (canonicalKeyForLabel(line.label) === canonicalKey) {
      return line.values && typeof line.values === "object" ? line.values : {};
    }
  }
  return null;
}

/** Latest finite value for `canonicalKey` walking periods from newest to oldest */
function valueAtLastPeriod(table, canonicalKey) {
  const periods = table?.periods;
  if (!periods?.length) return null;
  const values = findLineValuesForCanonical(table, canonicalKey);
  if (!values) return null;
  for (let i = periods.length - 1; i >= 0; i--) {
    const v = values[periods[i]];
    if (Number.isFinite(v)) return v;
  }
  return null;
}

function buildCompanyInfo(company, meta) {
  const rawName =
    company?.company_name ??
    company?.name ??
    company?.company ??
    null;
  const name =
    typeof rawName === "string" && rawName.trim() ? rawName.trim() : null;
  const market_cap = parseFinancialNumber(meta?.market_capitalization);
  return { name, market_cap };
}

function buildLatestSnapshot(statements) {
  const annual = statements.profitAndLossAnnual;
  const q = statements.quarterly;
  const bs = statements.balanceSheet;

  const latest_annual_period = annual?.periods?.length
    ? annual.periods[annual.periods.length - 1]
    : null;
  const latest_quarter_period = q?.periods?.length
    ? q.periods[q.periods.length - 1]
    : null;

  return {
    cwip: valueAtLastPeriod(bs, "cwip"),
    net_block: valueAtLastPeriod(bs, "net_block"),
    receivables: valueAtLastPeriod(bs, "receivables"),
    revenue_latest_qtr: valueAtLastPeriod(q, "revenue"),
    pat_latest_qtr: valueAtLastPeriod(q, "pat"),
    latest_annual_period,
    latest_quarter_period,
  };
}

/**
 * Sum of last up to `maxYears` annual period labels (aligned to annual.periods order)
 * for a canonical line on `table`. Missing periods contribute 0; if no finite values, null.
 */
function sumForCanonicalOverLabels(table, canonicalKey, labels) {
  const values = findLineValuesForCanonical(table, canonicalKey);
  if (!values) return { sum: null, hadAny: false };
  let sum = 0;
  let hadAny = false;
  for (const lab of labels) {
    const v = values[lab];
    if (Number.isFinite(v)) {
      sum += v;
      hadAny = true;
    }
  }
  return { sum: hadAny ? sum : null, hadAny };
}

function computeCashQuality5y(statements) {
  const annual = statements.profitAndLossAnnual;
  const cf = statements.cashFlow;
  if (!annual?.periods?.length) return null;
  const n = Math.min(5, annual.periods.length);
  const labels = annual.periods.slice(-n);
  const pat = sumForCanonicalOverLabels(annual, "pat", labels);
  const cfo = sumForCanonicalOverLabels(cf, "cfo", labels);
  if (!pat.hadAny || pat.sum === null || pat.sum === 0) return null;
  if (!cfo.hadAny || cfo.sum === null) return null;
  return cfo.sum / pat.sum;
}

function computeCwipToNetBlockRatio(snapshot) {
  const { cwip, net_block } = snapshot;
  if (!Number.isFinite(cwip) || !Number.isFinite(net_block) || net_block === 0) {
    return null;
  }
  return cwip / net_block;
}

function computeQtrYoYGrowth(statements) {
  const q = statements.quarterly;
  if (!q?.periods || q.periods.length < 5) return null;
  const revVals = findLineValuesForCanonical(q, "revenue");
  if (!revVals) return null;
  const periods = q.periods;
  const latestLab = periods[periods.length - 1];
  const yoyLab = periods[periods.length - 5];
  const latest = revVals[latestLab];
  const yoy = revVals[yoyLab];
  if (!Number.isFinite(latest) || !Number.isFinite(yoy) || yoy === 0) return null;
  return ((latest - yoy) / yoy) * 100;
}

function buildDerivedSignals(statements, latest_snapshot) {
  return {
    cash_quality_5y: computeCashQuality5y(statements),
    cwip_to_net_block_ratio: computeCwipToNetBlockRatio(latest_snapshot),
    qtr_yoy_growth: computeQtrYoYGrowth(statements),
  };
}

/**
 * @param {import("xlsx").WorkBook} workbook
 * @param {string} sourcePathOrName — used for `source` basename in output
 */
export function buildPayloadFromWorkbook(workbook, sourcePathOrName) {
  if (!workbook.Sheets[DATA_SHEET_NAME]) {
    throw new Error(`Missing required sheet "${DATA_SHEET_NAME}"`);
  }

  const rows = XLSX.utils.sheet_to_json(workbook.Sheets[DATA_SHEET_NAME], {
    header: 1,
    defval: "",
    raw: false,
  });

  const { company, meta, tables } = parseDataSheet(rows);
  const statements = buildStatementsFromTables(tables);
  const company_info = buildCompanyInfo(company, meta);
  const latest_snapshot = buildLatestSnapshot(statements);
  const derived_signals = buildDerivedSignals(statements, latest_snapshot);

  return {
    source: basename(sourcePathOrName),
    company_info,
    latest_snapshot,
    derived_signals,
    statements,
  };
}
