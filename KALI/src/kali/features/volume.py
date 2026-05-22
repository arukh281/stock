"""OBV, VPT, VWAP, volume Z, RVOL, OBV divergence."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d

from kali.features.indicators import obv as calc_obv


def volume_z_score(volume: pd.Series, n: int = 20) -> pd.Series:
    mu = volume.rolling(n).mean()
    sigma = volume.rolling(n).std()
    return ((volume - mu) / sigma.replace(0, np.nan)).rename("volume_z")


def relative_volume(volume: pd.Series) -> pd.Series:
    df = pd.DataFrame({"volume": volume})
    df["weekday"] = df.index.weekday
    medians = []
    for idx in df.index:
        same = df[(df.index < idx) & (df["weekday"] == idx.weekday())].tail(4)
        if len(same) < 2:
            medians.append(np.nan)
        else:
            medians.append(same["volume"].median())
    med = pd.Series(medians, index=volume.index)
    return (volume / med.replace(0, np.nan)).rename("rvol")


def anchored_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    swing_low_idx = df["low"].expanding().apply(lambda _: 0)
    anchor = df["low"].idxmin() if len(df) else df.index[0]
    sub = df.loc[anchor:]
    tp = (sub["high"] + sub["low"] + sub["close"]) / 3
    vwap = (tp * sub["volume"]).cumsum() / sub["volume"].cumsum()
    full = pd.Series(np.nan, index=df.index)
    full.loc[vwap.index] = vwap.values
    return full.ffill().rename("anchored_vwap")


def _find_peaks(series: np.ndarray) -> list[int]:
    peaks = []
    for i in range(1, len(series) - 1):
        if series[i - 1] < series[i] > series[i + 1]:
            peaks.append(i)
    return peaks


def obv_divergence(close: pd.Series, obv: pd.Series, sigma: float = 3) -> pd.Series:
    flag = pd.Series(False, index=close.index, name="obv_divergence")
    if len(close) < 30:
        return flag
    c_smooth = gaussian_filter1d(close.ffill().bfill().values, sigma=sigma)
    o_smooth = gaussian_filter1d(obv.ffill().bfill().values, sigma=sigma)
    c_peaks = _find_peaks(c_smooth)
    if len(c_peaks) >= 2:
        p1, p2 = c_peaks[-2], c_peaks[-1]
        if c_smooth[p2] > c_smooth[p1] and o_smooth[p2] < o_smooth[p1]:
            flag.iloc[p2:] = True
    return flag


def add_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["obv"] = calc_obv(out["close"], out["volume"])
    out["vpt"] = (out["volume"] * out["close"].pct_change()).fillna(0).cumsum()
    out["volume_z"] = volume_z_score(out["volume"])
    out["rvol"] = relative_volume(out["volume"])
    out["anchored_vwap"] = anchored_vwap(out)
    out["obv_divergence"] = obv_divergence(out["close"], out["obv"])
    return out
