"""Multi-timeframe weekly gate (Section 5A) — zero look-ahead."""

from __future__ import annotations

import pandas as pd

_DEFAULT_WEEKLY_HURST_ALLOW = ["TRENDING", "INDETERMINATE"]


def _weekly_hurst_allow(cfg: dict | None) -> list[str]:
    if cfg is None:
        return _DEFAULT_WEEKLY_HURST_ALLOW
    return cfg.get("signals", {}).get("weekly_hurst_allow", _DEFAULT_WEEKLY_HURST_ALLOW)


def weekly_uptrend(weekly_df: pd.DataFrame, cfg: dict | None = None) -> pd.Series:
    """W_UPTREND: weekly Hurst allowed regimes, positive kalman velocity and CMS."""
    h = weekly_df.get("w_hurst_regime", weekly_df.get("hurst_regime"))
    kv = weekly_df.get("w_kalman_velocity", weekly_df.get("kalman_velocity"))
    cms = weekly_df.get("w_cms", weekly_df.get("cms"))
    hurst_ok = h.isin(_weekly_hurst_allow(cfg))
    return hurst_ok & (kv > 0) & (cms > 0)


def _lagged_weekly_to_daily(
    weekly: pd.DataFrame, daily_index: pd.DatetimeIndex, col: str
) -> pd.Series:
    lagged = weekly[col].shift(1)
    return lagged.reindex(daily_index, method="ffill")


def multi_timeframe_gate(
    daily_df: pd.DataFrame, weekly_df: pd.DataFrame, cfg: dict | None = None
) -> pd.Series:
    weekly = weekly_df.copy()
    weekly["W_UPTREND"] = weekly_uptrend(weekly, cfg)
    weekly["W_UPTREND_lagged"] = weekly["W_UPTREND"].shift(1)
    daily_alignment = weekly["W_UPTREND_lagged"].reindex(daily_df.index, method="ffill")
    return daily_alignment.astype("boolean").fillna(False).rename("daily_alignment")


def attach_mtf_columns(
    daily_df: pd.DataFrame, weekly_df: pd.DataFrame, cfg: dict | None = None
) -> pd.DataFrame:
    """Attach PIT-safe daily_alignment and lagged weekly Hurst regime."""
    weekly = weekly_df.copy()
    weekly["W_UPTREND"] = weekly_uptrend(weekly, cfg)
    weekly["W_UPTREND_lagged"] = weekly["W_UPTREND"].shift(1)
    h_col = "w_hurst_regime" if "w_hurst_regime" in weekly.columns else "hurst_regime"
    if h_col in weekly.columns:
        weekly["w_hurst_regime_lagged"] = weekly[h_col].shift(1)
    else:
        weekly["w_hurst_regime_lagged"] = pd.NA

    out = daily_df.copy()
    align = _lagged_weekly_to_daily(weekly, out.index, "W_UPTREND_lagged")
    out["daily_alignment"] = align.astype("boolean").fillna(False).astype(bool)
    out["weekly_hurst_regime"] = _lagged_weekly_to_daily(
        weekly, out.index, "w_hurst_regime_lagged"
    )
    return out
