"""Unified feature pipeline per symbol."""

from __future__ import annotations

import pandas as pd

from kali.config import load_config
from kali.features.fractal import (
    detrended_fluctuation_alpha,
    largest_lyapunov_proxy,
    rolling_hurst,
)
from kali.features.microstructure import add_microstructure_features
from kali.features.momentum import add_momentum_features
from kali.features.price import add_price_features
from kali.features.stats import add_stats_features
from kali.features.volatility import (
    garman_klass_vol,
    parkinson_vol,
    rolling_hmm_vol_state,
    vol_ratio,
    yang_zhang_vol,
)
from kali.features.volume import add_volume_features


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    if "symbol" in df.columns:
        agg["symbol"] = "last"
    w = df.resample("W-FRI").agg(agg).dropna(subset=["close"])
    return w


def build_features(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)

    out = add_price_features(out, cfg)
    out = add_volume_features(out)
    out = add_momentum_features(out, cfg)
    out = add_stats_features(out, cfg)
    out = add_microstructure_features(out)

    n_yz = cfg["features"]["yz_window"]
    out["yz_vol"] = yang_zhang_vol(out["open"], out["high"], out["low"], out["close"], n=n_yz)
    out["parkinson_vol"] = parkinson_vol(out["high"], out["low"], n=n_yz)
    out["gk_vol"] = garman_klass_vol(out["open"], out["high"], out["low"], out["close"], n=n_yz)
    feat = cfg["features"]
    out["hmm_state"] = rolling_hmm_vol_state(
        out["yz_vol"],
        train_window=feat.get("hmm_train_window", 252),
        retrain_step=feat.get("hmm_retrain_step", 20),
        min_train=60,
    )
    out["vol_ratio"] = vol_ratio(out["close"])

    h_win = cfg["features"]["hurst_window"]
    scales = cfg["features"]["hurst_scales"]
    out = out.join(rolling_hurst(out["close"], window=h_win, scales=scales))

    if cfg["features"].get("chaos_enabled", True):
        out["dfa_alpha"] = detrended_fluctuation_alpha(out["close"], window=h_win)
        out["lyapunov_lambda"] = largest_lyapunov_proxy(
            out["close"], window=cfg["features"]["lyapunov_window"]
        )

    obv = out["obv"]
    out["obv_slope_z"] = (
        (obv.diff(20) / 20) - (obv.diff(20) / 20).rolling(20).mean()
    ) / (obv.diff(20) / 20).rolling(20).std()

    return out


def build_weekly_features(daily: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    weekly = resample_weekly(daily)
    wf = build_features(weekly, cfg)
    wf = wf.rename(
        columns={
            c: f"w_{c}"
            for c in wf.columns
            if c not in ("open", "high", "low", "close", "volume", "symbol")
        }
    )
    return wf
