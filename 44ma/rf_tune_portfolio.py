#!/usr/bin/env python3
"""
Random Forest–guided search for ma44 baseline portfolio parameters.

Prefetches OHLC once, runs portfolio backtests, and scores configs with an
objective that can be restricted to train calendar years. After search,
reports held-out test-year PnL so you can spot overfitting before trading.

This is experimental research tooling, not financial advice.
"""

from __future__ import annotations

import argparse
import json
import pickle
import random
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

import ma44.backtest as _bt_mod
from ma44.backtest import TradeResult, run_portfolio_backtest
from ma44.config import Settings
from ma44.data import fetch_daily
from ma44.strategy import add_indicators, signal_mask as _base_signal_mask
from ma44.symbols import load_backtest_universe

# Strategy-flag variants use stacked_sma_mask / path_floor / close buffer in strategy.py.
STRATEGY_FLAG_VARIANTS = (
    "baseline",
    "path_floor",
    "path_floor_tol",
    "stacked_2ma",
    "stacked_3ma",
    "path_floor_stacked",
    "full_ladder",
)
# Legacy extra-mask patches (subset of ladder logic).
PATCH_VARIANTS = ("ma1_gt_ma2", "ma2_gte_ma3", "ma1_gt_ma3")
VARIANT_CHOICES = STRATEGY_FLAG_VARIANTS + PATCH_VARIANTS
DEFAULT_PREFETCH_CACHE = Path(".cache/rf_tune/ohlc_prefetch.pkl")


def _stacked_offset(settings: Settings) -> int:
    return int(getattr(settings, "sma_stacked_offset", 44) or 44)


def _mask_ma2_gte_ma3(work: pd.DataFrame, off: int) -> pd.Series:
    sma = work["sma"]
    return (sma.shift(off) >= sma.shift(2 * off)).fillna(False)


def _mask_ma1_gt_ma2(work: pd.DataFrame, off: int) -> pd.Series:
    sma = work["sma"]
    return (sma > sma.shift(off)).fillna(False)


def _mask_ma1_gt_ma3(work: pd.DataFrame, off: int) -> pd.Series:
    sma = work["sma"]
    return (sma > sma.shift(2 * off)).fillna(False)


def _variant_extra_mask(work: pd.DataFrame, variant: str, off: int) -> pd.Series | None:
    if variant == "ma1_gt_ma2":
        return _mask_ma1_gt_ma2(work, off)
    if variant == "ma2_gte_ma3":
        return _mask_ma2_gte_ma3(work, off)
    if variant == "ma1_gt_ma3":
        return _mask_ma1_gt_ma3(work, off)
    return None


def _variant_structural_overrides(variant: str, off: int) -> dict[str, Any]:
    """Anti-V structural flags per variant (RF only tunes entry/risk TUNE_FIELDS)."""
    cleared = {
        "sma_stacked_enabled": False,
        "sma_stacked_offset": off,
        "sma_stacked_require_third": True,
        "sma_stacked_relax_third": True,
        "sma_path_floor_days": 0,
        "sma_path_floor_tol_pct": 0.0,
        "sma_close_below_max_days": 0,
        "sma_close_below_lookback": off,
    }
    if variant == "baseline":
        return cleared
    if variant == "path_floor":
        return {**cleared, "sma_path_floor_days": off}
    if variant == "path_floor_tol":
        return {**cleared, "sma_path_floor_days": off, "sma_path_floor_tol_pct": 0.005}
    if variant == "stacked_2ma":
        return {
            **cleared,
            "sma_stacked_enabled": True,
            "sma_stacked_require_third": False,
        }
    if variant == "stacked_3ma":
        return {
            **cleared,
            "sma_stacked_enabled": True,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": True,
        }
    if variant == "path_floor_stacked":
        return {
            **cleared,
            "sma_stacked_enabled": True,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": True,
            "sma_path_floor_days": off,
        }
    if variant == "full_ladder":
        return {
            **cleared,
            "sma_stacked_enabled": True,
            "sma_stacked_require_third": True,
            "sma_stacked_relax_third": True,
            "sma_path_floor_days": off,
            "sma_close_below_max_days": 4,
            "sma_close_below_lookback": off,
        }
    return cleared


def _settings_for_variant(base: Settings, variant: str) -> Settings:
    off = _stacked_offset(base)
    if variant in STRATEGY_FLAG_VARIANTS:
        return replace(base, **_variant_structural_overrides(variant, off))
    if variant in PATCH_VARIANTS:
        return replace(base, **_variant_structural_overrides("baseline", off))
    return base


def _install_variant_signal_mask(variant: str, off: int) -> None:
    if variant in STRATEGY_FLAG_VARIANTS:
        _bt_mod.signal_mask = _base_signal_mask
        return

    def patched(df: pd.DataFrame, settings: Settings) -> pd.Series:
        work = add_indicators(df, settings)
        m = _variant_extra_mask(work, variant, off)
        assert m is not None
        return _base_signal_mask(work, settings) & m

    _bt_mod.signal_mask = patched


def _restore_signal_mask() -> None:
    _bt_mod.signal_mask = _base_signal_mask


# Tuned fields (starting_cash_inr is fixed per run).
TUNE_FIELDS: tuple[str, ...] = (
    "sma_rising_lookback",
    "sma_monotone_days",
    "sma_slope_min_pct",
    "touch_above_pct",
    "touch_below_pct",
    "entry_buffer_pct",
    "stop_buffer_pct",
    "max_initial_risk_pct",
    "breakout_hold_days",
    "risk_reward",
    "risk_per_trade_inr",
)

FIELD_BOUNDS: dict[str, tuple[float, float]] = {
    "sma_rising_lookback": (3, 12),
    "sma_monotone_days": (2, 6),
    "sma_slope_min_pct": (0.0, 0.003),
    "touch_above_pct": (0.002, 0.012),
    "touch_below_pct": (0.006, 0.02),
    "entry_buffer_pct": (0.0001, 0.002),
    "stop_buffer_pct": (0.0001, 0.002),
    "max_initial_risk_pct": (0.02, 0.12),
    "breakout_hold_days": (2, 10),
    "risk_reward": (2.0, 5.0),
    "risk_per_trade_inr": (100.0, 5000.0),
}

OBJECTIVES = ("terminal_equity", "median_yearly_return", "min_yearly_return", "train_pnl")


def _parse_years(raw: str | None) -> set[int] | None:
    if not raw or not str(raw).strip():
        return None
    return {int(x.strip()) for x in str(raw).split(",") if x.strip()}


def _load_config_path(path: Path) -> Settings:
    return Settings.load(path)


def _risk_cap_inr(base: Settings, max_risk_arg: float | None) -> float:
    cash = float(base.starting_cash_inr)
    if max_risk_arg is not None and max_risk_arg > 0:
        return float(max_risk_arg)
    # Default: 10% of book, never above FIELD_BOUNDS high
    return min(float(FIELD_BOUNDS["risk_per_trade_inr"][1]), cash * 0.10)


def _sample_params(
    rng: random.Random,
    base: Settings,
    *,
    risk_cap_inr: float,
    tune_risk: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in TUNE_FIELDS:
        if name == "risk_per_trade_inr" and not tune_risk:
            out[name] = float(base.risk_per_trade_inr)
            continue
        lo, hi = FIELD_BOUNDS[name]
        if name == "risk_per_trade_inr":
            hi = min(hi, risk_cap_inr)
            lo = min(lo, max(50.0, hi * 0.25))
        v = lo + (hi - lo) * rng.random()
        if name in ("sma_rising_lookback", "sma_monotone_days", "breakout_hold_days"):
            out[name] = int(round(v))
            out[name] = max(int(FIELD_BOUNDS[name][0]), min(int(FIELD_BOUNDS[name][1]), out[name]))
        elif name == "risk_per_trade_inr":
            out[name] = max(50.0, min(risk_cap_inr, round(v, 2)))
        else:
            out[name] = float(v)
    return out


def _clamp_params(params: dict[str, Any], base: Settings, risk_cap_inr: float) -> dict[str, Any]:
    p = dict(params)
    if "risk_per_trade_inr" in p:
        p["risk_per_trade_inr"] = max(50.0, min(risk_cap_inr, float(p["risk_per_trade_inr"])))
    return p


def _params_to_row(p: dict[str, Any]) -> list[float]:
    return [float(p[k]) for k in TUNE_FIELDS]


def _merge_settings(base: Settings, overrides: dict[str, Any]) -> Settings:
    return replace(base, **overrides)


def _pnl_by_year(trades: list[TradeResult], years: set[int] | None) -> dict[int, float]:
    out: dict[int, float] = {}
    for t in trades:
        y = int(pd.Timestamp(t.exit_time).year)
        if years is not None and y not in years:
            continue
        out[y] = out.get(y, 0.0) + float(t.pnl)
    return out


def _yearly_returns(pnl_by_year: dict[int, float], start_cash: float) -> list[float]:
    if start_cash <= 0:
        return []
    return [pnl / start_cash for pnl in pnl_by_year.values()]


def _score_from_trades(
    trades: list[TradeResult],
    *,
    train_years: set[int] | None,
    start_cash: float,
    objective: str,
    last_equity: float,
) -> float:
    if objective == "terminal_equity" or train_years is None:
        return last_equity

    train_pnl = _pnl_by_year(trades, train_years)
    if not train_pnl:
        return -1e12

    rets = _yearly_returns(train_pnl, start_cash)
    if objective == "train_pnl":
        return float(sum(train_pnl.values()))
    if objective == "min_yearly_return":
        return float(min(rets))
    if objective == "median_yearly_return":
        return float(np.median(rets))
    return last_equity


def _filter_trades(trades: list[TradeResult], years: set[int] | None) -> list[TradeResult]:
    if years is None:
        return list(trades)
    return [t for t in trades if int(pd.Timestamp(t.exit_time).year) in years]


def _trade_metrics(trades: list[TradeResult], years: set[int] | None, start_cash: float) -> dict[str, Any]:
    ts = _filter_trades(trades, years)
    n = len(ts)
    wins = sum(1 for t in ts if t.pnl > 0)
    total = float(sum(t.pnl for t in ts))
    gross_win = float(sum(t.pnl for t in ts if t.pnl > 0))
    gross_loss = float(sum(t.pnl for t in ts if t.pnl <= 0))
    pf = (gross_win / abs(gross_loss)) if gross_loss < 0 else None
    return {
        "trades": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "total_pnl": round(total, 2),
        "avg_pnl_per_trade": round(total / n, 2) if n else 0.0,
        "profit_factor": round(pf, 3) if pf is not None else None,
        "total_return_pct": round(100.0 * total / start_cash, 4) if start_cash else 0.0,
    }


def _year_metrics(
    trades: list[TradeResult],
    years: set[int] | None,
    start_cash: float,
) -> dict[str, Any]:
    pnl_by_year = {str(y): v for y, v in sorted(_pnl_by_year(trades, years).items())}
    rets = _yearly_returns({int(k): v for k, v in pnl_by_year.items()}, start_cash)
    total = float(sum(pnl_by_year.values())) if pnl_by_year else 0.0
    out = {
        "pnl_by_year": pnl_by_year,
        "total_pnl": total,
        "total_return_pct": 100.0 * total / start_cash if start_cash else 0.0,
        "median_yearly_return_pct": float(np.median(rets) * 100.0) if rets else None,
        "min_yearly_return_pct": float(min(rets) * 100.0) if rets else None,
    }
    out.update(_trade_metrics(trades, years, start_cash))
    return out


def _evaluate(
    base: Settings,
    dfs: dict[str, pd.DataFrame],
    overrides: dict[str, Any],
    *,
    train_years: set[int] | None,
    objective: str,
    variant: str = "baseline",
) -> tuple[float, int, list[TradeResult]]:
    s = _merge_settings(_settings_for_variant(base, variant), overrides)
    _install_variant_signal_mask(variant, _stacked_offset(s))
    try:
        trades, eq = run_portfolio_backtest(dfs, s)
    finally:
        _restore_signal_mask()
    last_eq = float(eq.iloc[-1]) if not eq.empty else float(s.starting_cash_inr)
    score = _score_from_trades(
        trades,
        train_years=train_years,
        start_cash=float(s.starting_cash_inr),
        objective=objective,
        last_equity=last_eq,
    )
    return score, len(eq), trades


def _format_year_table(
    label: str,
    pnl_by_year: dict[int, float],
    start_cash: float,
) -> str:
    if not pnl_by_year:
        return f"{label}: (no trades)"
    lines = [f"{label}:"]
    total = 0.0
    for y in sorted(pnl_by_year):
        pnl = pnl_by_year[y]
        total += pnl
        ret = 100.0 * pnl / start_cash if start_cash else 0.0
        lines.append(f"  {y}: pnl={pnl:,.2f}  return={ret:.2f}%")
    lines.append(f"  total: pnl={total:,.2f}  return={100.0 * total / start_cash:.2f}%")
    return "\n".join(lines)


def _prefetch(
    symbols: list[str],
    start: str,
    end: str | None = None,
    *,
    log_progress: bool = True,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    n = len(symbols)
    for i, sym in enumerate(symbols):
        if log_progress and (i == 0 or (i + 1) % 10 == 0 or i + 1 == n):
            print(f"  fetch {i + 1}/{n}: {sym}", flush=True)
        df = fetch_daily(sym, start=start, end=end)
        if not df.empty:
            out[sym] = df
    return out


def save_prefetch_cache(
    path: Path,
    dfs: dict[str, pd.DataFrame],
    *,
    symbols: list[str],
    start: str,
    end: str | None,
) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "symbols_requested": list(symbols),
        "start": start,
        "end": end,
        "symbol_count": len(dfs),
        "dfs": dfs,
    }
    with path.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"wrote OHLC prefetch cache -> {path} ({len(dfs)} symbols)", flush=True)


def load_prefetch_cache(path: Path) -> dict[str, pd.DataFrame]:
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"prefetch cache not found: {path}")
    with path.open("rb") as f:
        payload = pickle.load(f)
    if isinstance(payload, dict) and "dfs" in payload:
        meta = {k: payload[k] for k in ("start", "end", "symbol_count") if k in payload}
        if meta:
            print(f"loaded prefetch cache {path} meta={meta}", flush=True)
        return payload["dfs"]
    if isinstance(payload, dict):
        return payload
    raise ValueError(f"unexpected prefetch cache format: {path}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="RF-guided portfolio parameter search with optional year holdout."
    )
    ap.add_argument("--config", default="config.json", help="Path to JSON config")
    ap.add_argument(
        "--starting-cash-inr",
        type=float,
        default=20000.0,
        metavar="AMOUNT",
        help="Starting cash (not tuned; written to --json-out)",
    )
    ap.add_argument("--start", default=None, help="OHLC prefetch start (default: from config)")
    ap.add_argument("--end", default=None, help="OHLC prefetch end (YYYY-MM-DD)")
    ap.add_argument("--max-symbols", type=int, default=0, help="Cap master pool for smoke tests")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--random-trials", type=int, default=48)
    ap.add_argument("--rf-refine-trials", type=int, default=32)
    ap.add_argument("--proposal-pool", type=int, default=4000)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--n-estimators", type=int, default=256)
    ap.add_argument(
        "--objective",
        choices=OBJECTIVES,
        default=None,
        help="Score for RF (default: median_yearly_return if --train-years set, else terminal_equity)",
    )
    ap.add_argument(
        "--train-years",
        default=None,
        metavar="Y1,Y2,...",
        help="Only these exit years count toward the optimization score (e.g. 2014,2015,2018,2019,2022,2023)",
    )
    ap.add_argument(
        "--test-years",
        default=None,
        metavar="Y1,Y2,...",
        help="Report PnL on these years after search (must not be used for picking best)",
    )
    ap.add_argument(
        "--max-risk-per-trade-inr",
        type=float,
        default=None,
        metavar="INR",
        help="Hard cap on risk_per_trade_inr samples (default: 10%% of starting cash)",
    )
    ap.add_argument(
        "--no-tune-risk",
        action="store_true",
        help="Keep risk_per_trade_inr from config (recommended)",
    )
    ap.add_argument("--json-out", default=None, help="Write best merged config here")
    ap.add_argument(
        "--variant",
        choices=VARIANT_CHOICES,
        default="baseline",
        help="SMA ladder filter variant (default: baseline)",
    )
    ap.add_argument(
        "--results-out",
        default=None,
        help="Write structured tune results JSON (params + train/test metrics)",
    )
    ap.add_argument(
        "--prefetch-cache",
        default=None,
        metavar="PATH",
        help=f"Pickle cache for OHLC (default when writing: {DEFAULT_PREFETCH_CACHE})",
    )
    ap.add_argument(
        "--no-fetch",
        action="store_true",
        help="Load OHLC from --prefetch-cache only (no Yahoo fetch)",
    )
    ap.add_argument(
        "--write-prefetch-cache",
        action="store_true",
        help="After fetching, save OHLC to --prefetch-cache (or default path)",
    )
    ap.add_argument(
        "--prefetch-only",
        action="store_true",
        help="Fetch universe OHLC, write cache, and exit (no tuning)",
    )
    args = ap.parse_args()

    train_years = _parse_years(args.train_years)
    test_years = _parse_years(args.test_years)
    objective = args.objective or (
        "median_yearly_return" if train_years else "terminal_equity"
    )

    cfg_path = Path(args.config).expanduser()
    base = _load_config_path(cfg_path)
    locked_cash = float(args.starting_cash_inr)
    base = replace(base, starting_cash_inr=locked_cash)
    risk_cap = _risk_cap_inr(base, args.max_risk_per_trade_inr)
    tune_risk = not args.no_tune_risk

    start = args.start or str(getattr(base, "universe_history_start", "2018-01-01"))
    if args.max_symbols and args.max_symbols > 0:
        base = replace(base, universe_master_pool=int(args.max_symbols))
    symbols = load_backtest_universe(base)
    if not symbols:
        print("No symbols resolved from config.")
        return 1

    variant = str(args.variant)
    print(
        f"mode: {variant} portfolio (top-N mcap + confidence-ranked entries)"
        + (
            f" [flags: {_variant_structural_overrides(variant, _stacked_offset(base))}]"
            if variant in STRATEGY_FLAG_VARIANTS and variant != "baseline"
            else (f" [extra mask: {variant}]" if variant in PATCH_VARIANTS else "")
        )
    )
    print(f"objective: {objective}" + (f" on train years {sorted(train_years)}" if train_years else ""))
    if test_years:
        print(f"test-years (report only): {sorted(test_years)}")
    print(
        f"risk_per_trade_inr: cap ₹{risk_cap:,.2f} | "
        f"{'fixed from config' if not tune_risk else 'tuned within cap'}"
    )
    cache_path = Path(
        args.prefetch_cache or DEFAULT_PREFETCH_CACHE
    ).expanduser()

    if args.no_fetch:
        print(f"symbols: loading cached OHLC from {cache_path}…")
        print(f"fetch window (cache): {start} .. {args.end or 'latest'} | starting_cash_inr: ₹{locked_cash:,.2f}")
        try:
            dfs = load_prefetch_cache(cache_path)
        except (FileNotFoundError, ValueError) as e:
            print(str(e))
            return 1
    else:
        print(f"symbols: prefetching {len(symbols)} names…")
        print(f"fetch window: {start} .. {args.end or 'latest'} | starting_cash_inr: ₹{locked_cash:,.2f}")
        dfs = _prefetch(symbols, start, end=args.end)
        if not dfs:
            _restore_signal_mask()
            print("No OHLC data fetched.")
            return 1
        print(f"loaded {len(dfs)} symbols with OHLC")
        if args.write_prefetch_cache or args.prefetch_only:
            save_prefetch_cache(
                cache_path,
                dfs,
                symbols=symbols,
                start=start,
                end=args.end,
            )

    if args.prefetch_only:
        return 0

    rng = random.Random(args.seed)
    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    rows_meta: list[dict[str, Any]] = []

    def run_batch(label: str, trials: int, proposals: list[dict[str, Any]] | None = None) -> None:
        for i in range(trials):
            p = proposals[i] if proposals is not None else _sample_params(
                rng, base, risk_cap_inr=risk_cap, tune_risk=tune_risk
            )
            p = _clamp_params(p, base, risk_cap)
            score, n_bars, trades = _evaluate(
                base, dfs, p, train_years=train_years, objective=objective, variant=variant
            )
            rows_x.append(_params_to_row(p))
            rows_y.append(score)
            rows_meta.append(dict(p))
            extra = ""
            if train_years:
                tp = _pnl_by_year(trades, train_years)
                extra = f" train_pnl={sum(tp.values()):.0f}"
            print(f"  [{label} {i + 1}/{trials}] score={score:.4f} bars={n_bars}{extra} params={p}")

    print(f"\nphase A: {args.random_trials} random trials")
    run_batch("random", args.random_trials, None)

    X = np.asarray(rows_x, dtype=np.float64)
    y = np.asarray(rows_y, dtype=np.float64)

    print(
        f"\nphase B: RF-guided evaluations "
        f"({args.rf_refine_trials} extra, up to {args.top_k} per RF fit)"
    )
    refine_done = 0
    r = 0
    while refine_done < args.rf_refine_trials:
        rf = RandomForestRegressor(
            n_estimators=args.n_estimators,
            random_state=args.seed + r,
            n_jobs=-1,
        )
        rf.fit(X, y)
        imp = dict(zip(TUNE_FIELDS, rf.feature_importances_.tolist()))
        print(
            f"  RF importances (fit {r + 1}): "
            f"{json.dumps({k: round(v, 4) for k, v in sorted(imp.items(), key=lambda kv: -kv[1])})}"
        )

        pool: list[tuple[float, dict[str, Any]]] = []
        for _ in range(args.proposal_pool):
            cand = _clamp_params(
                _sample_params(rng, base, risk_cap_inr=risk_cap, tune_risk=tune_risk),
                base,
                risk_cap,
            )
            pred = float(rf.predict(np.asarray([_params_to_row(cand)], dtype=np.float64))[0])
            pool.append((pred, cand))
        pool.sort(key=lambda t: t[0], reverse=True)
        k_need = min(args.top_k, args.rf_refine_trials - refine_done)
        chosen = [c for _, c in pool[:k_need]]

        print(f"  evaluating top-{len(chosen)} RF-scored proposals (batch {r + 1})")
        for cand in chosen:
            score, n_bars, trades = _evaluate(
                base, dfs, cand, train_years=train_years, objective=objective, variant=variant
            )
            rows_x.append(_params_to_row(cand))
            rows_y.append(score)
            rows_meta.append(dict(cand))
            refine_done += 1
            extra = ""
            if train_years:
                tp = _pnl_by_year(trades, train_years)
                extra = f" train_pnl={sum(tp.values()):.0f}"
            print(
                f"    [rf {refine_done}/{args.rf_refine_trials}] score={score:.4f} "
                f"bars={n_bars}{extra} params={cand}"
            )
        X = np.asarray(rows_x, dtype=np.float64)
        y = np.asarray(rows_y, dtype=np.float64)
        r += 1

    best_i = int(np.argmax(y))
    best_score = float(y[best_i])
    best_params = _clamp_params(rows_meta[best_i], base, risk_cap)

    print("\n" + "=" * 72)
    print(f"BEST {objective}: {best_score:.4f}")
    print(f"BEST param overrides:\n{json.dumps(best_params, indent=2)}")

    _, _, best_trades = _evaluate(
        base,
        dfs,
        best_params,
        train_years=None,
        objective="terminal_equity",
        variant=variant,
    )
    all_pnl = _pnl_by_year(best_trades, None)
    if train_years:
        print("\n" + _format_year_table("Train years", _pnl_by_year(best_trades, train_years), locked_cash))
    if test_years:
        print(_format_year_table("Test years (holdout)", _pnl_by_year(best_trades, test_years), locked_cash))
    if train_years or test_years:
        print(_format_year_table("All years (informational)", all_pnl, locked_cash))

    # Baseline comparison on test years when holdout is configured
    if test_years:
        base_overrides = {k: getattr(base, k) for k in TUNE_FIELDS if hasattr(base, k)}
        _, _, base_trades = _evaluate(
            base,
            dfs,
            base_overrides,
            train_years=None,
            objective="terminal_equity",
            variant=variant,
        )
        print(
            "\n"
            + _format_year_table(
                "Test years — original config (no tune)",
                _pnl_by_year(base_trades, test_years),
                locked_cash,
            )
        )

    if args.json_out:
        out_path = Path(args.json_out).expanduser()
        merged = json.loads(cfg_path.read_text(encoding="utf-8"))
        for k, v in best_params.items():
            merged[k] = v
        merged["starting_cash_inr"] = locked_cash
        if variant in STRATEGY_FLAG_VARIANTS:
            merged.update(_variant_structural_overrides(variant, _stacked_offset(base)))
        out_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote suggested config -> {out_path}")

    results_path = Path(
        args.results_out or f".cache/rf_tune/{variant}_results.json"
    ).expanduser()
    results_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "variant": variant,
        "objective": objective,
        "best_score": best_score,
        "best_params": best_params,
        "starting_cash_inr": locked_cash,
        "train_years": sorted(train_years) if train_years else None,
        "test_years": sorted(test_years) if test_years else None,
        "scenario": {
            "start": start,
            "end": args.end,
            "random_trials": args.random_trials,
            "rf_refine_trials": args.rf_refine_trials,
            "seed": args.seed,
            "no_tune_risk": args.no_tune_risk,
            "max_risk_per_trade_inr": args.max_risk_per_trade_inr,
        },
        "train_metrics": _year_metrics(best_trades, train_years, locked_cash)
        if train_years
        else None,
        "test_metrics": _year_metrics(best_trades, test_years, locked_cash)
        if test_years
        else None,
        "all_years_metrics": _year_metrics(best_trades, None, locked_cash),
    }
    results_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote results -> {results_path}")

    print(
        "\nNext: backtest --json-out on test years only before paper trading. "
        "Prefer --no-tune-risk and --max-risk-per-trade-inr to limit overfitting."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
