"""Kalman filter, Shannon entropy, skew, kurtosis."""

from __future__ import annotations

import numpy as np
import pandas as pd
from filterpy.kalman import KalmanFilter


def kalman_price_velocity(
    close: pd.Series, process_var: float = 1e-5, measurement_var: float = 1e-2
) -> pd.DataFrame:
    kf = KalmanFilter(dim_x=2, dim_z=1)
    kf.F = np.array([[1.0, 1.0], [0.0, 1.0]])
    kf.H = np.array([[1.0, 0.0]])
    kf.P *= 1000
    kf.R = measurement_var
    kf.Q = np.array([[process_var, 0], [0, process_var]])

    prices, velocities = [], []
    for z in close.values:
        if np.isnan(z):
            prices.append(np.nan)
            velocities.append(np.nan)
            continue
        kf.predict()
        kf.update(z)
        prices.append(float(np.asarray(kf.x[0]).flat[0]))
        velocities.append(float(np.asarray(kf.x[1]).flat[0]))
    return pd.DataFrame(
        {"kalman_price": prices, "kalman_velocity": velocities},
        index=close.index,
    )


def shannon_entropy_normalized(returns: pd.Series, window: int = 60) -> pd.Series:
    ent = []
    for i in range(len(returns)):
        if i < window:
            ent.append(np.nan)
            continue
        r = returns.iloc[i - window : i].dropna().values
        if len(r) < 10:
            ent.append(np.nan)
            continue
        q75, q25 = np.percentile(r, [75, 25])
        iqr = q75 - q25
        if iqr <= 0:
            ent.append(0.0)
            continue
        h = 2 * iqr / (len(r) ** (1 / 3))
        n_bins = max(int(np.ceil((r.max() - r.min()) / h)), 1)
        counts, _ = np.histogram(r, bins=n_bins)
        p = counts / counts.sum()
        p = p[p > 0]
        h_raw = -np.sum(p * np.log2(p))
        ent.append(h_raw / np.log2(n_bins) if n_bins > 1 else 0.0)
    return pd.Series(ent, index=returns.index, name="entropy_norm")


def entropy_percentile_rank(entropy: pd.Series, lookback: int = 252) -> pd.Series:
    return entropy.rolling(lookback, min_periods=60).apply(
        lambda x: (x.iloc[-1] <= x).mean() if len(x) > 1 else 0.5,
        raw=False,
    ).rename("entropy_pct")


def add_stats_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    ret = out["close"].pct_change()
    out = out.join(kalman_price_velocity(out["close"]))
    win = cfg["features"]["entropy_window"]
    out["entropy_norm"] = shannon_entropy_normalized(ret, window=win)
    out["entropy_pct"] = entropy_percentile_rank(out["entropy_norm"])
    out["skew_20"] = ret.rolling(20).skew()
    out["kurt_20"] = ret.rolling(20).kurt()
    vel = out["kalman_velocity"]
    out["kalman_velocity_z"] = (vel - vel.rolling(20).mean()) / vel.rolling(20).std()
    return out
