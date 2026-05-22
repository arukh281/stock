"""Multi-scale Hurst (Appendix A1), DFA, Lyapunov."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import gammaln
import scipy.stats as stats


def anis_lloyd_expected(s: int) -> float:
    if s < 2:
        return 0.0
    term1 = (s - 0.5) / s
    term2 = np.exp(gammaln((s - 1) / 2.0) - gammaln(s / 2.0)) / np.sqrt(np.pi)
    i = np.arange(1, s)
    term3 = np.sum(np.sqrt((s - i) / i))
    return term1 * term2 * term3


def multi_scale_hurst(
    returns: np.ndarray,
    window: int = 60,
    scales: list[int] | None = None,
) -> tuple[float, float, float, str]:
    """Appendix A1 — returns H, SE_slope, R_squared, regime label."""
    scales = scales or [10, 14, 20, 30, 40, 60]
    if len(returns) < window:
        return 0.5, 0.0, 0.0, "INDETERMINATE"

    segment_all = returns[-window:]
    log_s, log_rs = [], []

    for s in scales:
        m = window // s
        if m == 0 or s < 3:
            continue
        rs_values = []
        for j in range(m):
            segment = segment_all[j * s : (j + 1) * s]
            mean_adj = segment - np.mean(segment)
            cum_dev = np.cumsum(mean_adj)
            r_val = np.max(cum_dev) - np.min(cum_dev)
            s_scale = np.std(segment, ddof=1)
            if s_scale > 0:
                rs_values.append(r_val / s_scale)
        if rs_values:
            obs_rs = np.mean(rs_values)
            al_expected = anis_lloyd_expected(s)
            asymptotic_rw = np.sqrt(s * np.pi / 2.0)
            adj_rs = obs_rs - al_expected + asymptotic_rw
            if adj_rs > 0:
                log_s.append(np.log(s))
                log_rs.append(np.log(adj_rs))

    k = len(log_s)
    if k < 3:
        return 0.5, 0.0, 0.0, "INDETERMINATE"

    x, y = np.array(log_s), np.array(log_rs)
    x_mean = np.mean(x)
    ss_xx = np.sum((x - x_mean) ** 2)
    ss_xy = np.sum((x - x_mean) * (y - np.mean(y)))
    h = ss_xy / ss_xx if ss_xx > 0 else 0.5

    c = np.mean(y) - h * x_mean
    y_pred = h * x + c
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    se_slope = np.sqrt(ss_res / ((k - 2) * ss_xx)) if ss_xx > 0 else 0.0645
    t_crit = stats.t.ppf(0.975, df=k - 2)
    upper_bound = 0.5 + (t_crit * se_slope)
    lower_bound = 0.5 - (t_crit * se_slope)

    if h > upper_bound:
        regime = "TRENDING"
    elif h < lower_bound:
        regime = "MEAN_REVERTING"
    else:
        regime = "INDETERMINATE"

    return float(h), float(se_slope), float(r_squared), regime


def rolling_hurst(
    close: pd.Series,
    window: int = 60,
    scales: list[int] | None = None,
) -> pd.DataFrame:
    log_ret = np.log(close / close.shift(1))
    idx = close.index
    h_vals, se_vals, r2_vals, regimes = [], [], [], []
    for i in range(len(close)):
        if i < window:
            h_vals.append(np.nan)
            se_vals.append(np.nan)
            r2_vals.append(np.nan)
            regimes.append("INDETERMINATE")
            continue
        ret_win = log_ret.iloc[i - window + 1 : i + 1].dropna().values
        if len(ret_win) < window // 2:
            h, se, r2, reg = 0.5, 0.0, 0.0, "INDETERMINATE"
        else:
            h, se, r2, reg = multi_scale_hurst(
                ret_win, window=min(len(ret_win), window), scales=scales
            )
        h_vals.append(h)
        se_vals.append(se)
        r2_vals.append(r2)
        regimes.append(reg)
    return pd.DataFrame(
        {
            "hurst_H": h_vals,
            "hurst_SE": se_vals,
            "hurst_R2": r2_vals,
            "hurst_regime": regimes,
        },
        index=idx,
    )


def detrended_fluctuation_alpha(
    close: pd.Series,
    window: int = 60,
    scales: list[int] | None = None,
) -> pd.Series:
    scales = scales or [10, 14, 20, 30, 40, 60]
    alphas = []
    log_p = np.log(close.replace(0, np.nan)).ffill()
    for i in range(len(close)):
        if i < window:
            alphas.append(np.nan)
            continue
        y = log_p.iloc[i - window : i].values
        x = np.arange(len(y))
        coef = np.polyfit(x, y, 1)
        trend = np.polyval(coef, x)
        profile = np.cumsum(y - trend)
        fluctuations = []
        for n in scales:
            if n < 4 or len(profile) < n:
                continue
            segs = len(profile) // n
            if segs == 0:
                continue
            rms = []
            for k in range(segs):
                seg = profile[k * n : (k + 1) * n]
                rms.append(np.sqrt(np.mean(seg ** 2)))
            if rms:
                fluctuations.append((n, np.mean(rms)))
        if len(fluctuations) < 2:
            alphas.append(np.nan)
            continue
        ns = np.log([f[0] for f in fluctuations])
        fs = np.log([f[1] for f in fluctuations])
        alphas.append(np.polyfit(ns, fs, 1)[0])
    return pd.Series(alphas, index=close.index, name="dfa_alpha")


def largest_lyapunov_proxy(
    close: pd.Series,
    window: int = 60,
    embed: int = 3,
    delay: int = 2,
) -> pd.Series:
    """Simplified Rosenstein-style Lyapunov proxy for daily bars."""
    vals = []
    log_ret = np.log(close / close.shift(1)).fillna(0).values
    for i in range(len(close)):
        if i < window:
            vals.append(np.nan)
            continue
        series = log_ret[i - window : i]
        n = len(series) - (embed - 1) * delay
        if n < 10:
            vals.append(np.nan)
            continue
        vectors = np.array(
            [series[j : j + embed * delay : delay] for j in range(n)]
        )
        divergences = []
        for j in range(min(n - 1, 50)):
            dists = np.linalg.norm(vectors - vectors[j], axis=1)
            dists[j] = np.inf
            nn = np.argmin(dists)
            if j + 1 < n and nn + 1 < n:
                d0 = max(dists[nn], 1e-12)
                d1 = max(np.linalg.norm(vectors[j + 1] - vectors[nn + 1]), 1e-12)
                divergences.append(np.log(d1 / d0))
        vals.append(np.mean(divergences) if divergences else np.nan)
    return pd.Series(vals, index=close.index, name="lyapunov_lambda")
