"""OU half-life, CMS, RSI, StochRSI, MACD curvature, Fisher."""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from kali.features.indicators import macd as calc_macd
from kali.features.indicators import rsi as calc_rsi
from kali.features.indicators import stoch_rsi as calc_stoch_rsi


def compute_rolling_ou(price_series: pd.Series, window: int = 60) -> pd.Series:
    """Appendix A2 — rolling OU half-life."""
    log_p = np.log(price_series.replace(0, np.nan)).dropna()
    half_lives = pd.Series(index=price_series.index, dtype=float, name="ou_halflife")
    aligned_idx = price_series.index

    for t in range(window, len(log_p)):
        segment = log_p.iloc[t - window : t].values
        x_t = segment[1:]
        x_t_1 = segment[:-1]
        y = x_t - x_t_1
        x = sm.add_constant(x_t_1)
        model = sm.OLS(y, x).fit()
        b = model.params[1] if len(model.params) > 1 else 0
        t_b = model.tvalues[1] if len(model.tvalues) > 1 else 0
        orig_idx = aligned_idx[t]
        if b < 0 and abs(t_b) > 2.0:
            theta = -b
            half_lives.loc[orig_idx] = np.log(2) / theta
        else:
            half_lives.loc[orig_idx] = np.inf
    return half_lives


def ou_cms_weights(halflife: float, t_b: float = 3.0) -> list[float]:
    if halflife < 10:
        return [0.4, 0.3, 0.2, 0.1]
    if 10 <= halflife <= 30:
        return [0.25, 0.3, 0.25, 0.2]
    if halflife > 30 or abs(t_b) < 2.0:
        return [0.1, 0.2, 0.3, 0.4]
    return [0.25, 0.3, 0.25, 0.2]


def composite_momentum_score(
    close: pd.Series,
    ou_halflife: pd.Series,
    horizons: list[int] | None = None,
) -> pd.Series:
    horizons = horizons or [5, 10, 20, 60]
    log_close = np.log(close)
    z_cols = []
    for h in horizons:
        roc = log_close.diff(h)
        z = (roc - roc.rolling(60).mean()) / roc.rolling(60).std()
        z_cols.append(z)
    cms = pd.Series(0.0, index=close.index)
    for i, idx in enumerate(close.index):
        hl = ou_halflife.loc[idx] if idx in ou_halflife.index else np.inf
        if pd.isna(hl):
            hl = np.inf
        w = ou_cms_weights(float(hl))
        val = 0.0
        for w_i, z_s in zip(w, z_cols):
            zv = z_s.loc[idx] if idx in z_s.index else 0
            val += w_i * (0 if pd.isna(zv) else zv)
        cms.loc[idx] = val
    return cms.rename("cms")


def add_momentum_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    out["rsi_14"] = calc_rsi(close, 14)
    out["stoch_rsi"] = calc_stoch_rsi(close, 14, 14)

    macd_df = calc_macd(close, 12, 26, 9)
    h = macd_df["macd_hist"]
    out["macd_hist"] = h
    out["macd_curvature"] = h - 2 * h.shift(1) + h.shift(2)

    n = 10
    mn = close.rolling(n).min()
    mx = close.rolling(n).max()
    x = 2 * (close - mn) / (mx - mn + 1e-12) - 1
    x = x.clip(-0.999, 0.999)
    out["fisher_transform"] = 0.5 * np.log((1 + x) / (1 - x))

    ou_window = cfg["features"]["ou_window"]
    out["ou_halflife"] = compute_rolling_ou(close, window=ou_window)
    out["cms"] = composite_momentum_score(close, out["ou_halflife"])
    return out
