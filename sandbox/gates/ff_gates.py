from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from sandbox.adapters._paths import setup_paths


def _production_params() -> dict[str, Any]:
    setup_paths()
    from daily_paper_step import PRODUCTION_PARAMS

    return dict(PRODUCTION_PARAMS)


def gate_breakdown(symbol: str) -> dict[str, Any]:
    setup_paths()
    from swing_trading_algo import SwingTradingAlgo, UNIVERSE_MIDCAP150, _flatten_yfinance_columns

    params = _production_params()
    idx_defaults = {
        UNIVERSE_MIDCAP150: ("NIFTYMIDCAP150.NS", 20),
    }
    index_ticker, lookback = idx_defaults.get(UNIVERSE_MIDCAP150, ("NIFTYMIDCAP150.NS", 20))
    algo = SwingTradingAlgo(index_ticker=index_ticker, lookback_roc=lookback)

    sym = symbol.strip().upper()
    end = datetime.today()
    start = end - timedelta(days=1200)
    raw = yf.download(sym, start=start, end=end, progress=False)
    raw = _flatten_yfinance_columns(raw)
    if raw.empty or len(raw) < 252:
        return {"algo_id": "financially_free", "symbol": sym, "error": "insufficient_history"}

    df = raw.rename(
        columns={
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Volume": "Volume",
        }
    )
    if "Close" not in df.columns:
        return {"algo_id": "financially_free", "symbol": sym, "error": "no_ohlc"}

    macro = algo.calculate_macro_roc(
        (pd.Timestamp(start) - pd.DateOffset(months=lookback + 3)).strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )
    df = df.join(macro, how="left").ffill()
    df = algo.calculate_vcp_and_emas(df)
    row = df.iloc[-1]
    asof = df.index[-1]

    max_roc = float(params.get("max_roc", 75))
    min_vol = float(params.get("min_volume_ratio", 1.0))
    require_trend = bool(params.get("require_index_trend", True))

    roc = float(row.get("ROC_18M", np.nan))
    macro_ok = pd.notna(roc) and roc < max_roc
    index_trend_ok = True
    if require_trend:
        index_trend_ok = bool(row.get("Index_Above_200DMA", True)) if pd.notna(
            row.get("Index_Above_200DMA", np.nan)
        ) else False

    vol_ratio = float(row.get("Volume_Ratio", np.nan))
    vol_ok = pd.notna(vol_ratio) and vol_ratio >= min_vol

    contracting = bool(row.get("Is_Contracting", False))
    contracting_prior = bool(df["Is_Contracting"].shift(1).iloc[-1])
    breakout = bool(pd.notna(row.get("Resistance_20D"))) and float(row["Close"]) > float(
        row["Resistance_20D"]
    )
    high_vol = bool(row.get("High_Volume", False))
    stage2_prior = bool(df["Stage_2_Uptrend"].shift(1).iloc[-1])
    vcp = bool(row.get("VCP_Breakout", False))

    entry_ok = macro_ok and index_trend_ok and vol_ok and vcp

    gates = [
        {
            "id": "macro_roc",
            "label": f"Index {lookback}M ROC < {max_roc}%",
            "pass": macro_ok,
            "detail": None if pd.isna(roc) else f"roc={roc:.2f}%",
        },
        {
            "id": "index_above_200dma",
            "label": "Index above 200 DMA (if required)",
            "pass": index_trend_ok if require_trend else True,
            "detail": f"required={require_trend}",
        },
        {
            "id": "volume_ratio",
            "label": f"Volume ratio ≥ {min_vol}",
            "pass": vol_ok,
            "detail": None if pd.isna(vol_ratio) else f"ratio={vol_ratio:.2f}",
        },
        {
            "id": "contracting_prior",
            "label": "Volatility contraction prior bar",
            "pass": contracting_prior,
        },
        {
            "id": "breakout_resistance",
            "label": "Close > 20D resistance",
            "pass": breakout,
        },
        {
            "id": "high_volume",
            "label": "Volume > 1.2× 50D avg",
            "pass": high_vol,
        },
        {
            "id": "stage2_prior",
            "label": "Stage 2 uptrend (prior bar)",
            "pass": stage2_prior,
        },
        {
            "id": "vcp_breakout",
            "label": "VCP breakout composite",
            "pass": vcp,
        },
        {
            "id": "entry_signal",
            "label": "Full entry (_entry_signal)",
            "pass": entry_ok,
        },
    ]
    failed = [g["id"] for g in gates if not g["pass"]]

    return {
        "algo_id": "financially_free",
        "symbol": sym,
        "asof": str(asof.date()),
        "close": float(row["Close"]),
        "signal": entry_ok,
        "gates": gates,
        "failed": failed,
        "note": "RS top-20% ranking applies at portfolio level, not per-symbol gate list",
    }


def run_compare(*, start: str = "2018-01-01", end: str = "2026-05-01") -> dict[str, Any]:
    setup_paths()
    from daily_paper_step import PRODUCTION_PARAMS
    from nifty_midcap_history import get_yf_constituents
    from swing_trading_algo import UNIVERSE_MIDCAP150, SwingTradingAlgo

    params = PRODUCTION_PARAMS
    algo = SwingTradingAlgo(index_ticker="NIFTYMIDCAP150.NS", lookback_roc=20)
    tickers = get_yf_constituents(pd.Timestamp(end).year)

    variants = [
        ("loose macro (max_roc=100)", {**params, "max_roc": 100}),
        ("production (max_roc=75)", params),
        ("strict macro (max_roc=45)", {**params, "max_roc": 45}),
    ]

    rows: list[dict[str, Any]] = []
    prepared = algo.prepare_universe(tickers[:80], start, end)

    for name, kw in variants:
        result = algo.backtest_portfolio(
            tickers=None,
            start_date=start,
            end_date=end,
            initial_capital=1_000_000.0,
            universe=UNIVERSE_MIDCAP150,
            prepared_universe=prepared,
            include_benchmark=False,
            **kw,
        )
        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        trades = int(summary.get("num_trades_closed", 0))
        sp = float(summary.get("final_equity", 1_000_000)) - 1_000_000.0
        wr = summary.get("win_rate_pct")
        rows.append(
            {
                "variant": name,
                "trades": trades,
                "win_pct": round(float(wr), 1) if wr is not None else 0.0,
                "sum_pnl": round(sp, 2),
                "total_return_pct": float(summary.get("total_return_pct", 0)),
            }
        )

    return {
        "algo_id": "financially_free",
        "start": start,
        "end": end,
        "symbols": len(prepared),
        "variants": rows,
        "note": "Subset of midcap universe (80 tickers) for faster compare",
    }
