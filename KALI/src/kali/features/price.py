"""ATR, NATR, Heikin-Ashi, Beta body-wick."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from kali.features.indicators import wilder_atr


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=["ha_open", "ha_high", "ha_low", "ha_close"], index=df.index
        )
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    ha_close = (o + h + l + c) / 4
    ha_open = ((o + c) / 2).copy()
    for i in range(1, len(ha_open)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
    ha_high = pd.concat([h, ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([l, ha_open, ha_close], axis=1).min(axis=1)
    return pd.DataFrame(
        {"ha_open": ha_open, "ha_high": ha_high, "ha_low": ha_low, "ha_close": ha_close},
        index=df.index,
    )


def beta_body_wick_ratio(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    hl = (df["high"] - df["low"]).replace(0, np.nan)
    r = (df["close"] - df["open"]).abs() / hl
    r = r.clip(0.001, 0.999).fillna(0.5)
    alpha = pd.Series(np.nan, index=df.index, name="beta_alpha")
    beta = pd.Series(np.nan, index=df.index, name="beta_beta")
    ratio = pd.Series(np.nan, index=df.index, name="beta_alpha_beta")
    for i in range(n, len(df)):
        seg = r.iloc[i - n : i].dropna()
        if len(seg) < 5:
            continue
        a, b, _, _ = stats.beta.fit(seg, floc=0, fscale=1)
        alpha.iloc[i] = a
        beta.iloc[i] = b
        ratio.iloc[i] = a / b if b > 0 else np.nan
    return pd.DataFrame({"beta_alpha": alpha, "beta_beta": beta, "beta_ratio": ratio})


def add_price_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    atr_n = cfg["features"]["atr_period"]
    atr = wilder_atr(out["high"], out["low"], out["close"], n=atr_n)
    out["atr_14"] = atr
    out["natr"] = (atr / out["close"]) * 100
    out = out.join(heikin_ashi(out))
    out = out.join(beta_body_wick_ratio(out))
    return out
