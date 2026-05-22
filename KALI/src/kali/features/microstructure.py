"""Amihud illiquidity and order flow imbalance."""

from __future__ import annotations

import numpy as np
import pandas as pd


def amihud_illiquidity(df: pd.DataFrame) -> pd.Series:
    ret = df["close"].pct_change().abs()
    dollar_vol = df["volume"] * df["close"]
    illiq = ret / dollar_vol.replace(0, np.nan)
    return illiq.replace([np.inf, -np.inf], np.nan).rename("amihud")


def order_flow_imbalance(df: pd.DataFrame, n: int = 10) -> pd.Series:
    hl = (df["high"] - df["low"]).replace(0, np.nan)
    bv = df["volume"] * (df["close"] - df["low"]) / hl
    ofi = (2 * bv - df["volume"]) / df["volume"].replace(0, np.nan)
    return ofi.rolling(n).mean().rename("ofi_10")


def add_microstructure_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["amihud"] = amihud_illiquidity(out)
    out["ofi_10"] = order_flow_imbalance(out)
    return out
