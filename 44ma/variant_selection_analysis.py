#!/usr/bin/env python3
"""
Variant selection analysis: trade metrics, combo grid, ranking, push delta.

Reuses rf_tune_portfolio prefetch cache and best-params JSON. Does not modify config.json.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ma44.backtest as _bt_mod
from ma44.backtest import TradeResult
from ma44.config import Settings
from ma44.strategy import add_indicators, signal_mask as _base_signal_mask

from rf_tune_portfolio import (
    TUNE_FIELDS,
    _install_variant_signal_mask,
    _mask_ma1_gt_ma2,
    _mask_ma1_gt_ma3,
    _mask_ma2_gte_ma3,
    _merge_settings,
    _pnl_by_year,
    _restore_signal_mask,
    _settings_for_variant,
    _stacked_offset,
    _year_metrics,
    load_prefetch_cache,
)

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "rf_tune"
PREFETCH = CACHE / "ohlc_prefetch.pkl"

TRAIN_YEARS = {2014, 2015, 2018, 2019, 2022, 2023}
TEST_YEARS = {2016, 2017, 2020, 2021}
ALL_YEARS = TRAIN_YEARS | TEST_YEARS

SINGLE_VARIANTS = ("baseline", "full_ladder", "ma1_gt_ma2", "ma2_gte_ma3", "ma1_gt_ma3")
COMBO_GRID = (
    "baseline",
    "full_ladder",
    "ma1_gt_ma2",
    "ma2_gte_ma3",
    "ma1_gt_ma3",
    "ma1_gt_ma2+ma2_gte_ma3",
    "ma1_gt_ma2+ma1_gt_ma3",
    "ma2_gte_ma3+ma1_gt_ma3",
    "ma1_gt_ma2+ma2_gte_ma3+ma1_gt_ma3",
)

RESULTS_FILES = {
    "full_ladder": CACHE / "full_ladder_results.json",
    "ma1_gt_ma2": CACHE / "ma1_gt_ma2_results.json",
    "ma2_gte_ma3": CACHE / "ma2_gte_ma3_results.json",
    "ma1_gt_ma3": CACHE / "ma1_gt_ma3_results.json",
}

MASK_BY_PART = {
    "ma1_gt_ma2": _mask_ma1_gt_ma2,
    "ma2_gte_ma3": _mask_ma2_gte_ma3,
    "ma1_gt_ma3": _mask_ma1_gt_ma3,
}

STARTING_CASH = 20_000.0
TRADE_BAND = (100, 220)
FALLBACK_TRADE_BAND = (90, 260)
MIN_WIN_RATE = 0.30


def _parse_combo_id(combo_id: str) -> tuple[str, tuple[str, ...] | None]:
    if "+" not in combo_id:
        return combo_id, None
    parts = tuple(combo_id.split("+"))
    return "combo", parts


def _install_combo_signal_mask(parts: tuple[str, ...], off: int) -> None:
    def patched(df: pd.DataFrame, settings: Settings) -> pd.Series:
        work = add_indicators(df, settings)
        m = _base_signal_mask(work, settings)
        for p in parts:
            m = m & MASK_BY_PART[p](work, off)
        return m

    _bt_mod.signal_mask = patched


def _baseline_params(base: Settings) -> dict[str, Any]:
    return {k: getattr(base, k) for k in TUNE_FIELDS if hasattr(base, k)}


def _load_best_params(variant: str, base: Settings) -> dict[str, Any]:
    if variant == "baseline":
        return _baseline_params(base)
    path = RESULTS_FILES.get(variant)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"missing results for {variant}: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data["best_params"])


def _params_for_candidate(candidate_id: str, base: Settings) -> tuple[dict[str, Any], str]:
    """Return (params, params_source_note). Combos use ma1_gt_ma2 best params."""
    kind, parts = _parse_combo_id(candidate_id)
    if kind == "combo":
        params = _load_best_params("ma1_gt_ma2", base)
        return params, "ma1_gt_ma2 best_params (combo base)"
    if candidate_id == "baseline":
        return _baseline_params(base), "config.json TUNE_FIELDS (baseline not RF-tuned)"
    return _load_best_params(candidate_id, base), f"{candidate_id}_results.json best_params"


def _filter_trades(trades: list[TradeResult], years: set[int] | None) -> list[TradeResult]:
    if years is None:
        return list(trades)
    return [t for t in trades if int(pd.Timestamp(t.exit_time).year) in years]


def _trade_level_metrics(
    trades: list[TradeResult],
    years: set[int] | None,
    start_cash: float,
) -> dict[str, Any]:
    ts = _filter_trades(trades, years)
    n = len(ts)
    wins = sum(1 for t in ts if t.pnl > 0)
    losses = n - wins
    stops = sum(1 for t in ts if t.exit_reason == "stop")
    targets = sum(1 for t in ts if t.exit_reason == "target")
    total_pnl = float(sum(t.pnl for t in ts))
    pnl_by_year = _pnl_by_year(ts, years)
    rets = [v / start_cash for v in pnl_by_year.values()] if start_cash else []
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / n, 2) if n else 0.0,
        "stops": stops,
        "targets": targets,
        "return_pct": round(100.0 * total_pnl / start_cash, 4) if start_cash else 0.0,
        "median_yearly_return_pct": round(float(np.median(rets) * 100.0), 4) if rets else None,
        "pnl_by_year": {str(y): round(v, 2) for y, v in sorted(pnl_by_year.items())},
    }


def _run_candidate(
    candidate_id: str,
    base: Settings,
    dfs: dict[str, pd.DataFrame],
    params: dict[str, Any],
) -> list[TradeResult]:
    from ma44.backtest import run_portfolio_backtest

    kind, parts = _parse_combo_id(candidate_id)
    s = _merge_settings(replace(base, starting_cash_inr=STARTING_CASH), params)
    off = _stacked_offset(s)
    if kind == "combo":
        assert parts is not None
        s = replace(s, sma_stacked_enabled=False)
        _install_combo_signal_mask(parts, off)
    else:
        s = _settings_for_variant(s, candidate_id)
        _install_variant_signal_mask(candidate_id, off)
    try:
        trades, _eq = run_portfolio_backtest(dfs, s)
    finally:
        _restore_signal_mask()
    return trades


def _scopes_metrics(trades: list[TradeResult], start_cash: float) -> dict[str, Any]:
    return {
        "ALL": _trade_level_metrics(trades, ALL_YEARS, start_cash),
        "TRAIN": _trade_level_metrics(trades, TRAIN_YEARS, start_cash),
        "TEST": _trade_level_metrics(trades, TEST_YEARS, start_cash),
    }


def compute_trade_metrics(base: Settings, dfs: dict[str, pd.DataFrame]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "starting_cash_inr": STARTING_CASH,
        "train_years": sorted(TRAIN_YEARS),
        "test_years": sorted(TEST_YEARS),
        "variants": {},
    }
    for v in SINGLE_VARIANTS:
        if v in RESULTS_FILES and not RESULTS_FILES[v].is_file():
            continue
        params, src = _params_for_candidate(v, base)
        trades = _run_candidate(v, base, dfs, params)
        out["variants"][v] = {
            "params_source": src,
            "best_params": params,
            "scopes": _scopes_metrics(trades, STARTING_CASH),
        }
    return out


def run_combo_grid(base: Settings, dfs: dict[str, pd.DataFrame]) -> dict[str, Any]:
    rows: dict[str, Any] = {
        "starting_cash_inr": STARTING_CASH,
        "train_years": sorted(TRAIN_YEARS),
        "test_years": sorted(TEST_YEARS),
        "combo_params_policy": "AND-mask combos use ma1_gt_ma2 RF best_params; singles use each variant's results JSON (baseline: config.json).",
        "candidates": {},
    }
    for cid in COMBO_GRID:
        params, src = _params_for_candidate(cid, base)
        trades = _run_candidate(cid, base, dfs, params)
        train_m = _year_metrics(trades, TRAIN_YEARS, STARTING_CASH)
        test_m = _year_metrics(trades, TEST_YEARS, STARTING_CASH)
        test_trade = _trade_level_metrics(trades, TEST_YEARS, STARTING_CASH)
        train_trade = _trade_level_metrics(trades, TRAIN_YEARS, STARTING_CASH)
        rows["candidates"][cid] = {
            "params_source": src,
            "train": {**train_m, **{f"trade_{k}": v for k, v in train_trade.items() if k != "pnl_by_year"}},
            "test": {**test_m, **{f"trade_{k}": v for k, v in test_trade.items() if k != "pnl_by_year"}},
            "test_trade": test_trade,
            "train_trade": train_trade,
        }
    return rows


def _constraint_pass(
    test_trade: dict[str, Any],
    band: tuple[int, int],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    wr = test_trade.get("win_rate") or 0.0
    n = test_trade.get("trades") or 0
    med = test_trade.get("median_yearly_return_pct")
    ok = True
    if wr < MIN_WIN_RATE:
        ok = False
        reasons.append(f"win_rate {wr:.2%} < {MIN_WIN_RATE:.0%}")
    if n < band[0] or n > band[1]:
        ok = False
        reasons.append(f"trades {n} not in [{band[0]}, {band[1]}]")
    if med is None or med <= 0:
        ok = False
        reasons.append(f"median_yearly_return_pct {med} <= 0")
    return ok, reasons


def rank_and_select(combo: dict[str, Any]) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    for cid, row in combo["candidates"].items():
        tt = row["test_trade"]
        pass_strict, reasons_strict = _constraint_pass(tt, TRADE_BAND)
        pass_fb, reasons_fb = _constraint_pass(tt, FALLBACK_TRADE_BAND)
        ranked.append(
            {
                "candidate_id": cid,
                "pass_strict": pass_strict,
                "pass_fallback": pass_fb and not pass_strict,
                "fail_reasons_strict": reasons_strict if not pass_strict else [],
                "test_total_return_pct": row["test"]["total_return_pct"],
                "test_win_rate": tt["win_rate"],
                "test_trades": tt["trades"],
                "test_median_yearly_return_pct": tt["median_yearly_return_pct"],
                "test_total_pnl": tt["total_pnl"],
            }
        )

    def sort_key(r: dict[str, Any]) -> tuple:
        return (
            -r["test_total_return_pct"],
            -r["test_win_rate"],
            r["test_trades"],
        )

    qualifiers = [r for r in ranked if r["pass_strict"]]
    band_used = "strict"
    if not qualifiers:
        qualifiers = [r for r in ranked if r["pass_fallback"]]
        band_used = "fallback"
    qualifiers.sort(key=sort_key)
    ranked.sort(key=lambda r: (not (r["pass_strict"] or r["pass_fallback"]),) + sort_key(r))

    winner = qualifiers[0]["candidate_id"] if qualifiers else None
    runner = qualifiers[1]["candidate_id"] if len(qualifiers) > 1 else None

    return {
        "selection_policy": {
            "scope": "TEST years only",
            "hard_constraints": {
                "win_rate_min": MIN_WIN_RATE,
                "trade_band": list(TRADE_BAND),
                "median_yearly_return_pct_min": 0,
            },
            "fallback": {
                "trade_band": list(FALLBACK_TRADE_BAND),
                "win_rate_min": MIN_WIN_RATE,
                "median_yearly_return_pct_min": 0,
            },
            "tie_break": ["max test total_return_pct", "higher test win_rate", "fewer test trades"],
        },
        "band_used": band_used,
        "winner": winner,
        "runner_up": runner,
        "ranking_table": ranked,
    }


def _md_trade_metrics(data: dict[str, Any]) -> str:
    lines = [
        "# Trade metrics by variant",
        "",
        f"Cash: ₹{data['starting_cash_inr']:,.0f} | Train: {data['train_years']} | Test: {data['test_years']}",
        "",
    ]
    for v, block in data["variants"].items():
        lines.append(f"## {v}")
        lines.append(f"Params: {block['params_source']}")
        for scope, m in block["scopes"].items():
            lines.append(
                f"- **{scope}**: trades={m['trades']} win_rate={m['win_rate']:.1%} "
                f"return={m['return_pct']:.2f}% stops={m['stops']} targets={m['targets']} "
                f"avg_pnl={m['avg_pnl_per_trade']:.2f}"
            )
        lines.append("")
    return "\n".join(lines)


def _md_combo_grid(data: dict[str, Any]) -> str:
    lines = [
        "# Combo grid results",
        "",
        data["combo_params_policy"],
        "",
        "| Candidate | Test return % | Test trades | Test win % | Test median yr % | Train return % |",
        "|-----------|---------------|-------------|------------|------------------|----------------|",
    ]
    for cid, row in data["candidates"].items():
        t, tr = row["test"], row["test_trade"]
        lines.append(
            f"| {cid} | {t['total_return_pct']:.2f} | {tr['trades']} | "
            f"{100*tr['win_rate']:.1f} | {tr.get('median_yearly_return_pct')} | "
            f"{row['train']['total_return_pct']:.2f} |"
        )
    return "\n".join(lines) + "\n"


def _md_ranking(data: dict[str, Any]) -> str:
    lines = [
        "# Variant selection ranking",
        "",
        f"**Winner:** {data['winner']}",
        f"**Runner-up:** {data['runner_up']}",
        f"**Band used:** {data['band_used']}",
        "",
        "| Candidate | Pass (strict) | Pass (fallback) | Test return % | Trades | Win % | Median yr % |",
        "|-----------|---------------|-----------------|---------------|--------|-------|-------------|",
    ]
    for r in data["ranking_table"]:
        lines.append(
            f"| {r['candidate_id']} | {r['pass_strict']} | {r['pass_fallback']} | "
            f"{r['test_total_return_pct']:.2f} | {r['test_trades']} | "
            f"{100*r['test_win_rate']:.1f} | {r['test_median_yearly_return_pct']} |"
        )
    return "\n".join(lines) + "\n"


def prepare_push_delta(
    winner: str | None,
    ranking: dict[str, Any],
    combo: dict[str, Any],
    base: Settings,
) -> tuple[str, dict[str, Any] | None]:
    if not winner:
        md = "# Push recommendation\n\nNo candidate passed selection constraints on TEST years.\n"
        return md, None

    params, _ = _params_for_candidate(winner, base)
    win_row = combo["candidates"][winner]
    tt = win_row["test_trade"]
    prod_path = ROOT / "config.json"
    prod = json.loads(prod_path.read_text(encoding="utf-8"))

    config_delta: dict[str, Any] = {}
    for k in TUNE_FIELDS:
        if k in params and prod.get(k) != params[k]:
            config_delta[k] = {"from": prod.get(k), "to": params[k]}

    variant_wiring = []
    kind, parts = _parse_combo_id(winner)
    if winner == "full_ladder":
        variant_wiring = [
            'Set sma_stacked_enabled=true, sma_stacked_relax_third=true in config.',
            "No signal_mask patch required (uses stacked_sma_mask in strategy).",
        ]
    elif kind == "combo":
        variant_wiring = [
            f"Add config field sma_trend_variant (or equivalent) = '{winner}'.",
            f"In strategy/backtest entry path, AND base signal_mask with: {', '.join(parts)}.",
            "Reuse ma1_gt_ma2_sma_mask / ma2_gte_ma3_sma_mask / ma1_gt_ma3_sma_mask from strategy.py.",
        ]
    elif winner in MASK_BY_PART:
        variant_wiring = [
            f"Add sma_trend_variant = '{winner}' and apply {winner} mask on top of base signal_mask.",
            f"Ensure sma_stacked_enabled=false (extra mask only).",
        ]
    else:
        variant_wiring = ["baseline: no extra SMA ladder mask; tuned params only."]

    md_lines = [
        "# Push recommendation",
        "",
        f"## Winner: `{winner}`",
        "",
        "### Why (TEST-only selection)",
        f"- Test total return: **{win_row['test']['total_return_pct']:.2f}%** (₹{tt['total_pnl']:,.0f} PnL)",
        f"- Test trades: {tt['trades']} | win rate: {tt['win_rate']:.1%} | median yearly return: {tt['median_yearly_return_pct']}%",
        f"- Selection band: {ranking['band_used']}",
        "",
        "### Runner-up",
        f"`{ranking['runner_up']}`" if ranking.get("runner_up") else "(none qualified)",
        "",
        "### config.json fields to change",
        "```json",
        json.dumps(config_delta, indent=2),
        "```",
        "",
        "Also set production book (unchanged in RF study):",
        f"- starting_cash_inr: {prod.get('starting_cash_inr')} (keep)",
        f"- risk_per_trade_inr: {prod.get('risk_per_trade_inr')} (keep)",
        "",
        "### strategy.py wiring",
        *[f"- {line}" for line in variant_wiring],
        "",
        "### Full tuned overrides for winner",
        "```json",
        json.dumps(params, indent=2),
        "```",
    ]
    recommended = dict(prod)
    for k, v in params.items():
        recommended[k] = v
    recommended["starting_cash_inr"] = 15000
    recommended["risk_per_trade_inr"] = 1000
    if winner == "full_ladder":
        recommended["sma_stacked_enabled"] = True
        recommended["sma_stacked_relax_third"] = True
    elif winner != "baseline":
        recommended["sma_stacked_enabled"] = False
    recommended["_selection_meta"] = {
        "winner": winner,
        "sma_trend_variant": winner if winner not in ("baseline", "full_ladder") else None,
        "note": "Review before deploy; RF tuned on 20k cash 2014-2023 holdout.",
    }
    return "\n".join(md_lines) + "\n", recommended


def main() -> int:
    base = Settings.load(ROOT / "config.json")
    print("Loading prefetch cache…", flush=True)
    dfs = load_prefetch_cache(PREFETCH)
    print(f"  {len(dfs)} symbols", flush=True)

    print("1/4 trade metrics…", flush=True)
    tm = compute_trade_metrics(base, dfs)
    (CACHE / "trade_metrics_by_variant.json").write_text(
        json.dumps(tm, indent=2) + "\n", encoding="utf-8"
    )
    (CACHE / "trade_metrics_by_variant.md").write_text(_md_trade_metrics(tm), encoding="utf-8")

    print("2/4 combo grid…", flush=True)
    cg = run_combo_grid(base, dfs)
    (CACHE / "combo_grid_results.json").write_text(json.dumps(cg, indent=2) + "\n", encoding="utf-8")
    (CACHE / "combo_grid_results.md").write_text(_md_combo_grid(cg), encoding="utf-8")

    print("3/4 rank & select…", flush=True)
    rk = rank_and_select(cg)
    (CACHE / "variant_selection_ranking.json").write_text(
        json.dumps(rk, indent=2) + "\n", encoding="utf-8"
    )
    (CACHE / "variant_selection_ranking.md").write_text(_md_ranking(rk), encoding="utf-8")

    print("4/4 push delta…", flush=True)
    push_md, rec_cfg = prepare_push_delta(rk["winner"], rk, cg, base)
    (CACHE / "push_recommendation.md").write_text(push_md, encoding="utf-8")
    if rec_cfg is not None:
        (CACHE / "config_recommended.json").write_text(
            json.dumps(rec_cfg, indent=2) + "\n", encoding="utf-8"
        )

    print(f"Winner: {rk['winner']} | Runner-up: {rk['runner_up']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
