"""Parkinson, Garman-Klass, Yang-Zhang, HMM volatility regime."""

from __future__ import annotations

import logging
from contextlib import contextmanager

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

_HMMLEARN_LOG = logging.getLogger("hmmlearn.base")


@contextmanager
def _quiet_hmmlearn_fit():
    """Suppress hmmlearn EM 'Model is not converging' warnings during fit."""
    prev = _HMMLEARN_LOG.level
    _HMMLEARN_LOG.setLevel(logging.ERROR)
    try:
        yield
    finally:
        _HMMLEARN_LOG.setLevel(prev)


def yang_zhang_vol(
    o: pd.Series,
    h: pd.Series,
    l: pd.Series,
    c: pd.Series,
    n: int = 20,
) -> pd.Series:
    """Appendix A3."""
    ln_o_cprev = np.log(o / c.shift(1))
    ln_c_o = np.log(c / o)
    var_o = ln_o_cprev.rolling(window=n).var(ddof=1)
    var_c = ln_c_o.rolling(window=n).var(ddof=1)
    rs_term = (np.log(h / c) * np.log(h / o)) + (np.log(l / c) * np.log(l / o))
    var_rs = rs_term.rolling(window=n).mean()
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    yz_var = var_o + (k * var_c) + ((1 - k) * var_rs)
    return np.sqrt(yz_var.clip(lower=0) * 252).rename("yz_vol")


def parkinson_vol(h: pd.Series, l: pd.Series, n: int = 20) -> pd.Series:
    rs = (np.log(h / l)) ** 2
    var = rs.rolling(n).mean() / (4 * n * np.log(2))
    return np.sqrt(var.clip(lower=0) * 252).rename("parkinson_vol")


def garman_klass_vol(
    o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series, n: int = 20
) -> pd.Series:
    term1 = 0.5 * (np.log(h / l)) ** 2
    term2 = (2 * np.log(2) - 1) * (np.log(c / o)) ** 2
    var = (term1 - term2).rolling(n).mean().clip(lower=0)
    return np.sqrt(var * 252).rename("gk_vol")


def _fit_hmm_labels(train_vals: np.ndarray, n_states: int = 2) -> tuple[GaussianHMM | None, dict[int, str]]:
    if len(train_vals) < 60:
        return None, {0: "LOW_VOL", 1: "HIGH_VOL"}
    model = GaussianHMM(n_components=n_states, covariance_type="full", n_iter=100)
    try:
        with _quiet_hmmlearn_fit():
            model.fit(train_vals.reshape(-1, 1))
        labels = {
            i: "LOW_VOL" if i == np.argmin(model.means_.flatten()) else "HIGH_VOL"
            for i in range(n_states)
        }
        return model, labels
    except Exception:
        return None, {0: "LOW_VOL", 1: "HIGH_VOL"}


def rolling_hmm_vol_state(
    yz_vol: pd.Series,
    n_states: int = 2,
    train_window: int = 252,
    retrain_step: int = 20,
    min_train: int = 60,
) -> pd.Series:
    """Point-in-time HMM: fit on trailing window (excluding current bar), predict current bar."""
    states = pd.Series("LOW_VOL", index=yz_vol.index, dtype="object", name="hmm_state")
    valid = yz_vol.dropna()
    if len(valid) < min_train:
        return states

    model: GaussianHMM | None = None
    labels: dict[int, str] = {0: "LOW_VOL", 1: "HIGH_VOL"}
    last_state = "LOW_VOL"

    for i in range(len(valid)):
        if i < min_train:
            states.loc[valid.index[i]] = "LOW_VOL"
            continue

        if model is None or i % retrain_step == 0:
            start = max(0, i - train_window)
            train_slice = valid.iloc[start:i].values
            model, labels = _fit_hmm_labels(train_slice, n_states)

        if model is None:
            states.loc[valid.index[i]] = last_state
            continue

        try:
            pred = model.predict(valid.iloc[i : i + 1].values.reshape(-1, 1))
            last_state = labels.get(int(pred[0]), "LOW_VOL")
        except Exception:
            pass
        states.loc[valid.index[i]] = last_state

    return states.ffill().fillna("LOW_VOL")


def vol_ratio(close: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
    ret = close.pct_change()
    short_vol = ret.rolling(short).std()
    long_vol = ret.rolling(long).std()
    return (short_vol / long_vol.replace(0, np.nan)).rename("vol_ratio")
