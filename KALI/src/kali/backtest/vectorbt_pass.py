"""Vectorized validation pass (Section 8C step 3)."""

from __future__ import annotations

import pandas as pd


def run_vectorbt_sanity(df: pd.DataFrame, cfg: dict | None = None) -> dict:
    """Lightweight vectorbt signal sanity check."""
    import vectorbt as vbt

    close = df["close"]
    entries = df["long_entry"].fillna(False)
    exits = df["exit_signal"].fillna(False)

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        init_cash=cfg["backtest"]["initial_capital"] if cfg else 1_000_000,
        fees=0.001,
        freq="1D",
    )
    stats = pf.stats()
    return {
        "total_return": float(stats.get("Total Return [%]", 0)),
        "sharpe": float(stats.get("Sharpe Ratio", 0)),
        "max_drawdown": float(stats.get("Max Drawdown [%]", 0)),
        "trades": int(stats.get("Total Trades", 0)),
    }
