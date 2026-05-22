#!/usr/bin/env python3
"""
Compare baseline 44MA vs stacked-SMA anti-V variants on cached OHLC.

Does not modify config.json. Writes .cache/rf_tune/stacked_ma_comparison.md
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ma44.backtest as _bt_mod
from ma44.backtest import TradeResult, run_portfolio_backtest
from ma44.config import Settings
from ma44.strategy import signal_mask as _base_signal_mask

from rf_tune_portfolio import (
    TUNE_FIELDS,
    _install_variant_signal_mask,
    _restore_signal_mask,
    _stacked_offset,
    load_prefetch_cache,
)

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "rf_tune"
PREFETCH = CACHE / "ohlc_prefetch.pkl"
OUT_MD = CACHE / "stacked_ma_comparison.md"
OUT_JSON = CACHE / "stacked_ma_results.json"

STARTING_CASH = 20_000.0
TRAIN_YEARS = {2014, 2015, 2018, 2019, 2022, 2023}
TEST_YEARS = {2016, 2017, 2020, 2021}
ALL_YEARS = TRAIN_YEARS | TEST_YEARS

# Baseline params from config.json (production-style, not RF-tuned).
BASELINE_PARAMS: dict[str, Any] = {
    "sma_rising_lookback": 5,
    "sma_monotone_days": 0,
    "sma_slope_min_pct": 0.008,
    "touch_above_pct": 0.005,
    "touch_below_pct": 0.012,
    "entry_buffer_pct": 0.0005,
    "stop_buffer_pct": 0.0005,
    "max_initial_risk_pct": 0.06,
    "breakout_hold_days": 4,
    "risk_reward": 3.5,
    "risk_per_trade_inr": 1000.0,
}

MA1_GT_MA2_PARAMS: dict[str, Any] = {
    "sma_rising_lookback": 4,
    "sma_monotone_days": 3,
    "sma_slope_min_pct": 0.0023713322106486646,
    "touch_above_pct": 0.007986967474668507,
    "touch_below_pct": 0.008765859141251633,
    "entry_buffer_pct": 0.00012107748778156666,
    "stop_buffer_pct": 0.000668792262462175,
    "max_initial_risk_pct": 0.09792476367961853,
    "breakout_hold_days": 7,
    "risk_reward": 4.994900044169886,
    "risk_per_trade_inr": 1000.0,
}


def _baseline_settings() -> Settings:
    base = Settings.load(ROOT / "config.json")
    return replace(base, starting_cash_inr=STARTING_CASH, **BASELINE_PARAMS)


def _filter_trades(trades: list[TradeResult], years: set[int] | None) -> list[TradeResult]:
    if years is None:
        return list(trades)
    return [t for t in trades if int(pd.Timestamp(t.exit_time).year) in years]


def _metrics(trades: list[TradeResult], years: set[int] | None, start_cash: float) -> dict[str, Any]:
    ts = _filter_trades(trades, years)
    n = len(ts)
    wins = sum(1 for t in ts if t.pnl > 0)
    total = float(sum(t.pnl for t in ts))
    by_year: dict[int, float] = {}
    for t in ts:
        y = int(pd.Timestamp(t.exit_time).year)
        by_year[y] = by_year.get(y, 0.0) + float(t.pnl)
    rets = [v / start_cash for v in by_year.values()] if start_cash else []
    return {
        "trades": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "total_pnl": round(total, 2),
        "return_pct": round(100.0 * total / start_cash, 4) if start_cash else 0.0,
        "median_yearly_return_pct": round(float(np.median(rets) * 100.0), 4) if rets else None,
        "pnl_by_year": {str(y): round(v, 2) for y, v in sorted(by_year.items())},
    }


# (id, label, params_key, settings_overrides, use_signal_patch)
CANDIDATES: list[tuple[str, str, str, dict[str, Any], str | None]] = [
    ("baseline", "Baseline (current config params)", "baseline", {}, None),
    (
        "path_floor",
        "Path floor: min(SMA 44d) > SMA[44d ago]",
        "baseline",
        {"sma_path_floor_days": 44},
        None,
    ),
    (
        "path_floor_tol",
        "Path floor + 0.5% tolerance",
        "baseline",
        {"sma_path_floor_days": 44, "sma_path_floor_tol_pct": 0.005},
        None,
    ),
    (
        "close_buf_4",
        "≤4 closes below SMA in last 44d",
        "baseline",
        {"sma_close_below_max_days": 4, "sma_close_below_lookback": 44},
        None,
    ),
    (
        "stacked_2ma",
        "2-segment ladder: MA1 > MA2 only (+44d slope)",
        "baseline",
        {
            "sma_stacked_enabled": True,
            "sma_stacked_offset": 44,
            "sma_stacked_require_third": False,
        },
        None,
    ),
    (
        "stacked_3ma_relaxed",
        "3-segment ladder: MA1>MA2, MA2≥MA3 (relaxed)",
        "baseline",
        {
            "sma_stacked_enabled": True,
            "sma_stacked_offset": 44,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": True,
        },
        None,
    ),
    (
        "stacked_3ma_strict",
        "3-segment ladder: MA1>MA2>MA3 (strict)",
        "baseline",
        {
            "sma_stacked_enabled": True,
            "sma_stacked_offset": 44,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": False,
        },
        None,
    ),
    (
        "full_ladder",
        "Full anti-V: path floor + 3MA ladder + ≤4 closes below SMA",
        "baseline",
        {
            "sma_stacked_enabled": True,
            "sma_stacked_offset": 44,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": True,
            "sma_path_floor_days": 44,
            "sma_close_below_max_days": 4,
            "sma_close_below_lookback": 44,
        },
        None,
    ),
    (
        "path_floor_stacked_3",
        "Path floor + 3MA relaxed (no close buffer)",
        "baseline",
        {
            "sma_stacked_enabled": True,
            "sma_stacked_offset": 44,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": True,
            "sma_path_floor_days": 44,
        },
        None,
    ),
    (
        "ma1_gt_ma2_baseline_params",
        "Legacy mask: SMA > SMA[44] (baseline params)",
        "baseline",
        {},
        "ma1_gt_ma2",
    ),
    (
        "ma1_gt_ma2_tuned",
        "Legacy mask: SMA > SMA[44] (RF-tuned params)",
        "tuned",
        {},
        "ma1_gt_ma2",
    ),
    (
        "full_ladder_tuned",
        "Full anti-V + RF-tuned entry params",
        "tuned",
        {
            "sma_stacked_enabled": True,
            "sma_stacked_offset": 44,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": True,
            "sma_path_floor_days": 44,
            "sma_close_below_max_days": 4,
            "sma_close_below_lookback": 44,
        },
        None,
    ),
]


def _run_one(
    dfs: dict[str, pd.DataFrame],
    *,
    params: dict[str, Any],
    overrides: dict[str, Any],
    patch: str | None,
) -> list[TradeResult]:
    base = _baseline_settings()
    params_full = {**params, **overrides}
    s = replace(base, **{k: v for k, v in params_full.items() if hasattr(base, k)})
    off = _stacked_offset(s)
    if patch:
        _install_variant_signal_mask(patch, off)
    else:
        _bt_mod.signal_mask = _base_signal_mask
    try:
        trades, _ = run_portfolio_backtest(dfs, s)
    finally:
        _restore_signal_mask()
    return trades


def main() -> int:
    if not PREFETCH.is_file():
        print(f"Missing prefetch cache: {PREFETCH}")
        print("Run: python rf_tune_portfolio.py --prefetch-only --write-prefetch-cache")
        return 1

    dfs = load_prefetch_cache(PREFETCH)
    rows: list[dict[str, Any]] = []

    for cid, label, params_key, overrides, patch in CANDIDATES:
        params = BASELINE_PARAMS if params_key == "baseline" else MA1_GT_MA2_PARAMS
        print(f"running {cid}…", flush=True)
        trades = _run_one(dfs, params=params, overrides=overrides, patch=patch)
        test_m = _metrics(trades, TEST_YEARS, STARTING_CASH)
        all_m = _metrics(trades, ALL_YEARS, STARTING_CASH)
        train_m = _metrics(trades, TRAIN_YEARS, STARTING_CASH)
        rows.append(
            {
                "id": cid,
                "label": label,
                "params_key": params_key,
                "overrides": overrides,
                "patch": patch,
                "test": test_m,
                "train": train_m,
                "all": all_m,
            }
        )

    baseline_test = next(r["test"] for r in rows if r["id"] == "baseline")
    lines = [
        "# Stacked 44MA comparison vs baseline",
        "",
        "Anti-V rules tested:",
        "- **Path floor**: `min(SMA last 44d) > SMA[44d ago]` — no V-dip inside latest SMA window",
        "- **Stacked ladder**: MA1 (now) > MA2 (SMA@−44d); optional MA2 ≥ MA3 (SMA@−88d)",
        "- **Close buffer**: at most 4 sessions closed below SMA in last 44d",
        "",
        f"Cash: ₹{STARTING_CASH:,.0f} | TEST years: {sorted(TEST_YEARS)} | symbols: {len(dfs)}",
        "",
        "## TEST period (held-out)",
        "",
        "| Candidate | Return % | Δ vs baseline | Trades | Win % | Median yr % |",
        "|-----------|----------|---------------|--------|-------|-------------|",
    ]

    for r in rows:
        t = r["test"]
        delta = t["return_pct"] - baseline_test["return_pct"]
        lines.append(
            f"| {r['id']} | {t['return_pct']:.2f} | {delta:+.2f} | {t['trades']} | "
            f"{100*t['win_rate']:.1f} | {t['median_yearly_return_pct']} |"
        )

    def sort_key(r: dict[str, Any]) -> tuple:
        t = r["test"]
        return (-t["return_pct"], -t["win_rate"], t["trades"])

    ranked = sorted(rows, key=sort_key)
    best_ret = ranked[0]
    best_wr = max(rows, key=lambda r: r["test"]["win_rate"])

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"- **Best TEST return**: `{best_ret['id']}` ({best_ret['test']['return_pct']:.2f}%)",
            f"- **Best TEST win rate**: `{best_wr['id']}` ({100*best_wr['test']['win_rate']:.1f}%)",
            f"- **Baseline TEST**: {baseline_test['return_pct']:.2f}% return, "
            f"{baseline_test['trades']} trades, {100*baseline_test['win_rate']:.1f}% wins",
            "",
            "### Recommendation",
            "",
        ]
    )

    full = next((r for r in rows if r["id"] == "full_ladder"), None)
    if full and full["test"]["return_pct"] >= baseline_test["return_pct"] * 0.85:
        if full["test"]["win_rate"] > baseline_test["win_rate"]:
            lines.append(
                "Full ladder improves win rate with acceptable return trade-off — "
                "consider enabling stacked filters in config after you review dropped trades."
            )
        else:
            lines.append(
                "Full ladder filters V-shaped setups but does not beat baseline on TEST return "
                "or win rate — keep baseline for production until further tuning."
            )
    else:
        lines.append(
            "Strict stacked/full_ladder filters reduce return vs baseline on TEST years. "
            "Do **not** push to production yet; baseline still wins on held-out PnL."
        )

    lines.append("")
    lines.append("## ALL years (reference)")
    lines.append("")
    lines.append("| Candidate | Return % | Trades | Win % |")
    lines.append("|-----------|----------|--------|-------|")
    for r in rows:
        a = r["all"]
        lines.append(
            f"| {r['id']} | {a['return_pct']:.2f} | {a['trades']} | {100*a['win_rate']:.1f} |"
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"candidates": rows}, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
