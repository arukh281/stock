"""Long entry signals (Section 5B) — core requirements + confluence scoring."""

from __future__ import annotations

import pandas as pd


def hurst_confluence_mask(df: pd.DataFrame) -> pd.Series:
    """Conditional Hurst: weekly INDETERMINATE requires daily TRENDING; weekly TRENDING allows daily TRENDING/INDETERMINATE."""
    w_h = df.get("weekly_hurst_regime", pd.Series("INDETERMINATE", index=df.index))
    d_h = df["hurst_regime"]
    return (
        ((w_h == "INDETERMINATE") & (d_h == "TRENDING"))
        | ((w_h == "TRENDING") & d_h.isin(["TRENDING", "INDETERMINATE"]))
    ).astype("boolean").fillna(False)


def core_conditions_mask(df: pd.DataFrame) -> pd.Series:
    regime_ok = df["regime_active"].isin(["BULL_TREND", "SIDEWAYS"])
    aligned = df.get("daily_alignment", False)
    return (
        regime_ok
        & aligned
        & (df["kalman_velocity"] > 0)
        & (~df["obv_divergence"].astype("boolean").fillna(False))
    ).astype("boolean").fillna(False)


def confluence_score_series(df: pd.DataFrame, cfg: dict) -> pd.Series:
    sig = cfg["signals"]
    conf_cms = df["cms"] > sig["cms_entry_min"]
    conf_hurst = hurst_confluence_mask(df)
    conf_vol = df["volume_z"] > sig["volume_z_entry_min"]
    conf_macd = df["macd_curvature"] > 0
    return (
        conf_cms.astype(int)
        + conf_hurst.astype(int)
        + conf_vol.astype(int)
        + conf_macd.astype(int)
    )


def long_entry_signal(df: pd.DataFrame, cfg: dict | None = None) -> pd.Series:
    from kali.config import load_config

    cfg = cfg or load_config()
    confluence_min = cfg["signals"].get("confluence_min", 2)

    core = core_conditions_mask(df)
    score = confluence_score_series(df, cfg)
    return (core & (score >= confluence_min)).astype("boolean").fillna(False).rename("long_entry")


def attach_stop_target(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    from kali.config import load_config

    cfg = cfg or load_config()
    sig = cfg["signals"]
    out = df.copy()
    out["stop_loss"] = out["close"] - sig["atr_stop_mult"] * out["atr_14"]
    out["take_profit"] = out["close"] + sig["atr_target_mult"] * out["atr_14"]
    return out
