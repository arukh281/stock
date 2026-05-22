"""Backtest integrity: circuit limits, T+1, long-only."""

from __future__ import annotations

import pandas as pd


def is_circuit_day(
    pct_change: float,
    limit: float = 0.19,
) -> bool:
    return abs(pct_change) >= limit


def tag_unexecutable(df: pd.DataFrame, default_limit: float = 0.19) -> pd.Series:
    pct = df["close"].pct_change()
    return (pct.abs() >= default_limit).rename("unexecutable")


def apply_t1_execution(signals: pd.Series) -> pd.Series:
    """Shift signals so execution happens next bar."""
    return signals.shift(1).astype("boolean").fillna(False)
