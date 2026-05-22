from __future__ import annotations

from dataclasses import replace
from typing import Any

from sandbox.adapters._paths import setup_paths
from sandbox.ma44_variants import VARIANTS, config_path_for, is_ma44_algo


def _settings(algo_id: str = "44ma"):
    setup_paths()
    from ma44.config import Settings

    return Settings.load(config_path_for(algo_id))


def gate_breakdown(symbol: str, algo_id: str = "44ma") -> dict[str, Any]:
    if not is_ma44_algo(algo_id):
        return {"algo_id": algo_id, "symbol": symbol, "error": f"Unknown 44MA algo: {algo_id}"}

    setup_paths()
    from ma44.data import fetch_daily
    from ma44.strategy import signal_gate_breakdown

    settings = _settings(algo_id)
    hist_start = str(getattr(settings, "universe_history_start", "2018-01-01"))
    df = fetch_daily(symbol.strip().upper(), start=hist_start)
    if df.empty:
        return {"algo_id": algo_id, "symbol": symbol, "error": "no_data"}

    info = signal_gate_breakdown(df, settings)
    if info is None:
        return {"algo_id": algo_id, "symbol": symbol, "error": "insufficient_history"}

    return {
        "algo_id": algo_id,
        "variant": VARIANTS[algo_id]["variant"],
        "symbol": symbol,
        "asof": str(info["date"].date()) if hasattr(info["date"], "date") else str(info["date"]),
        "close": info["close"],
        "sma44": info["sma"],
        "signal": info["signal"],
        "confidence": round(float(info["confidence"]), 4),
        "gates": info["gates"],
        "failed": info["failed"],
    }


def run_compare(*, start: str = "2018-01-01", algo_id: str = "44ma") -> dict[str, Any]:
    setup_paths()
    from ma44.backtest import run_portfolio_backtest
    from ma44.data import fetch_daily
    from ma44.symbols import load_backtest_universe

    base = _settings(algo_id)
    variants = [
        ("loose (old rules)", replace(base, sma_monotone_days=0, sma_slope_min_pct=0.0, require_close_above_prev=False)),
        ("+ monotone 3d", replace(base, sma_monotone_days=3, sma_slope_min_pct=0.0, require_close_above_prev=False)),
        ("+ slope_min 0.8%", replace(base, sma_monotone_days=0, sma_slope_min_pct=0.008, require_close_above_prev=False)),
        ("+ close>prev", replace(base, sma_monotone_days=0, sma_slope_min_pct=0.0, require_close_above_prev=True)),
        ("current config", base),
    ]

    syms = load_backtest_universe(base)
    dfs = {s: fetch_daily(s, start=start) for s in syms}
    dfs = {k: v for k, v in dfs.items() if not v.empty}

    rows: list[dict[str, Any]] = []
    for name, cfg in variants:
        trades, _ = run_portfolio_backtest(dfs, cfg)
        n = len(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        sp = float(sum(t.pnl for t in trades))
        rows.append(
            {
                "variant": name,
                "trades": n,
                "win_pct": round(100.0 * wins / n, 1) if n else 0.0,
                "sum_pnl": round(sp, 2),
                "avg_pnl": round(sp / n, 2) if n else 0.0,
            }
        )

    return {
        "algo_id": algo_id,
        "variant": VARIANTS[algo_id]["variant"],
        "start": start,
        "symbols": len(dfs),
        "variants": rows,
    }
