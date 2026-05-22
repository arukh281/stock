#!/usr/bin/env python3
"""
Fair anti-V comparison: every candidate RF-tuned on TRAIN years only, scored on TEST holdout.

Requires {variant}_results.json from rf_tune_portfolio.py with the standard scenario:
  train 2014,2015,2018,2019,2022,2023 | test 2016,2017,2020,2021

Writes .cache/rf_tune/fair_antiv_comparison.md
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "rf_tune"
OUT_MD = CACHE / "fair_antiv_comparison.md"
OUT_JSON = CACHE / "fair_antiv_comparison.json"

# All candidates on identical RF protocol (orchestrator BASELINE_SCENARIO).
FAIR_VARIANTS = (
    "baseline",
    "path_floor",  # strict min(SMA 44d) > SMA[44d ago]
    "path_floor_tol",
    "stacked_2ma",
    "stacked_3ma",
    "path_floor_stacked",
    "full_ladder",
    "ma1_gt_ma2",
)

TRAIN_YEARS = [2014, 2015, 2018, 2019, 2022, 2023]
TEST_YEARS = [2016, 2017, 2020, 2021]


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _results_path(variant: str) -> Path:
    if variant == "baseline":
        return CACHE / "baseline_results.json"
    return CACHE / f"{variant}_results.json"


def _ensure_tuned(variants: tuple[str, ...], *, run_missing: bool) -> list[str]:
    missing = [v for v in variants if not _results_path(v).is_file()]
    if missing and run_missing:
        cmd = [
            sys.executable,
            str(ROOT / "rf_tune_orchestrator.py"),
            "--only",
            "tune",
            "--variants",
            ",".join(missing),
            "--skip-existing",
        ]
        print(f"Running RF tune for missing: {missing}", flush=True)
        rc = subprocess.call(cmd, cwd=ROOT)
        if rc != 0:
            raise SystemExit(rc)
        still = [v for v in missing if not _results_path(v).is_file()]
        if still:
            raise FileNotFoundError(f"still missing results after tune: {still}")
    return missing


def _row(data: dict) -> dict:
    train = data.get("train_metrics") or {}
    test = data.get("test_metrics") or {}
    return {
        "variant": data.get("variant"),
        "best_train_score": data.get("best_score"),
        "train_return_pct": train.get("total_return_pct"),
        "train_trades": train.get("trades"),
        "train_win_rate": train.get("win_rate"),
        "train_avg_pnl": train.get("avg_pnl_per_trade"),
        "train_profit_factor": train.get("profit_factor"),
        "test_return_pct": test.get("total_return_pct"),
        "test_trades": test.get("trades"),
        "test_win_rate": test.get("win_rate"),
        "test_avg_pnl": test.get("avg_pnl_per_trade"),
        "test_profit_factor": test.get("profit_factor"),
        "test_median_yr_pct": test.get("median_yearly_return_pct"),
        "test_total_pnl": test.get("total_pnl"),
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Fair RF-tuned anti-V comparison (train/test split)")
    ap.add_argument(
        "--run-missing-tunes",
        action="store_true",
        help="Invoke orchestrator for variants without results JSON",
    )
    ap.add_argument("--variants", default=",".join(FAIR_VARIANTS))
    args = ap.parse_args()

    variants = tuple(v.strip() for v in args.variants.split(",") if v.strip())
    missing = _ensure_tuned(variants, run_missing=args.run_missing_tunes)

    rows: list[dict] = []
    for v in variants:
        path = _results_path(v)
        data = _load(path)
        if not data:
            rows.append({"variant": v, "status": "missing"})
            continue
        rows.append(_row(data))

    present = [r for r in rows if r.get("status") != "missing"]
    if not present:
        print("No results found. Run: python fair_antiv_compare.py --run-missing-tunes")
        return 1

    baseline_test = next((r for r in present if r["variant"] == "baseline"), None)
    b_ret = baseline_test["test_return_pct"] if baseline_test else 0.0
    b_avg = baseline_test["test_avg_pnl"] if baseline_test else 0.0

    def sort_key(r: dict) -> tuple:
        return (
            -(r.get("test_return_pct") or -1e9),
            -(r.get("test_avg_pnl") or -1e9),
            -(r.get("test_win_rate") or 0),
        )

    ranked = sorted(present, key=sort_key)

    lines = [
        "# Fair anti-V comparison (RF-tuned, same ground)",
        "",
        "Every row: **64 trials** (40 random + 24 RF), objective **median_yearly_return** on "
        f"**TRAIN** years only `{TRAIN_YEARS}`. Params chosen without peeking at TEST.",
        "",
        f"**TEST** holdout: `{TEST_YEARS}` | ₹20,000 book | risk cap ₹1,500 | no tune risk",
        "",
        "## TEST holdout (what matters for production)",
        "",
        "| Variant | Return % | Δ vs baseline | Trades | Win % | Avg ₹/trade | Profit factor | Median yr % |",
        "|---------|----------|---------------|--------|-------|-------------|---------------|-------------|",
    ]

    for r in ranked:
        delta = (r.get("test_return_pct") or 0) - b_ret
        wr = r.get("test_win_rate")
        wr_s = f"{100 * wr:.1f}" if wr is not None else "—"
        pf = r.get("test_profit_factor")
        pf_s = f"{pf:.2f}" if pf is not None else "—"
        avg = r.get("test_avg_pnl")
        avg_s = f"{avg:.2f}" if avg is not None else "—"
        lines.append(
            f"| {r['variant']} | {r.get('test_return_pct', 0):.2f} | {delta:+.2f} | "
            f"{r.get('test_trades', 0)} | {wr_s} | {avg_s} | "
            f"{pf_s} | {r.get('test_median_yr_pct')} |"
        )

    lines.extend(
        [
            "",
            "## TRAIN (fit years — informational)",
            "",
            "| Variant | Return % | Trades | Win % | Avg ₹/trade | Train score |",
            "|---------|----------|--------|-------|-------------|-------------|",
        ]
    )
    for r in ranked:
        wr = r.get("train_win_rate")
        wr_s = f"{100 * wr:.1f}" if wr is not None else "—"
        sc = r.get("best_train_score")
        sc_s = f"{sc:.4f}" if sc is not None else "—"
        avg = r.get("train_avg_pnl")
        avg_s = f"{avg:.2f}" if avg is not None else "—"
        lines.append(
            f"| {r['variant']} | {r.get('train_return_pct', 0):.2f} | {r.get('train_trades', 0)} | "
            f"{wr_s} | {avg_s} | {sc_s} |"
        )

    best = ranked[0]
    best_wr = max(present, key=lambda r: r.get("test_win_rate") or 0)
    best_avg = max(present, key=lambda r: r.get("test_avg_pnl") or 0)

    lines.extend(
        [
            "",
            "## Trader read",
            "",
            f"- **Best TEST return (RF-tuned)**: `{best['variant']}` ({best.get('test_return_pct', 0):.2f}%)",
            f"- **Best TEST win rate**: `{best_wr['variant']}` ({100 * (best_wr.get('test_win_rate') or 0):.1f}%)",
            f"- **Best TEST avg PnL/trade** (quality per cost): `{best_avg['variant']}` "
            f"(₹{best_avg.get('test_avg_pnl', 0):.2f})",
            "",
            "Anti-V variants filter **dip-then-rise V** setups (Paytm-style): stacked ladder, "
            "SMA path floor, optional close-below-SMA cap.",
            "",
            "### Recommendation",
            "",
        ]
    )

    if baseline_test and best["variant"] != "baseline" and (best.get("test_return_pct") or 0) > b_ret:
        avg_best = best.get("test_avg_pnl") or 0
        lines.append(
            f"On fair ground, **`{best['variant']}`** leads TEST holdout: "
            f"{best.get('test_return_pct', 0):.1f}% return vs baseline {b_ret:.1f}%, "
            f"{best.get('test_trades', 0)} trades vs {baseline_test.get('test_trades', 0)}, "
            f"₹{avg_best:.0f}/trade vs ₹{b_avg:.0f}/trade. "
            "Anti-V filters remove dip-and-rise V entries (Paytm-style) while improving "
            "expectancy per trade — paper-trade this config before live; do not merge to "
            "production until you sign off."
        )
    elif baseline_test:
        lines.append(
            "RF-tuned **baseline** still leads TEST holdout. Keep production config; "
            "use anti-V flags only if you accept lower TEST return for cleaner trend structure."
        )
    else:
        lines.append("Run baseline RF tune: see commands below.")

    if missing:
        lines.append(f"\nMissing results (not in table): `{missing}`")

    lines.extend(
        [
            "",
            "## Commands",
            "",
            "```bash",
            "cd 44ma",
            "python rf_tune_orchestrator.py --variants baseline,path_floor,stacked_2ma,stacked_3ma,path_floor_stacked,full_ladder",
            "python fair_antiv_compare.py",
            "```",
        ]
    )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"variants": rows, "ranked_test": ranked}, indent=2), encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
