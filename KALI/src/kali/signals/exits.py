"""Exit signals (Section 5C)."""

from __future__ import annotations

import pandas as pd


def exit_signal(
    df: pd.DataFrame,
    days_held: pd.Series | None = None,
    trade_type: str = "swing",
    cfg: dict | None = None,
) -> pd.Series:
    """Regime-collapse exit only; trailing/time exits handled in portfolio simulator."""
    from kali.config import load_config

    # Preserve config loading behavior for compatibility with existing call sites.
    _ = cfg or load_config()
    exit_signal_raw = df["regime_active"] == "BEAR_TREND"
    return exit_signal_raw.astype("boolean").fillna(False).rename("exit_signal")
