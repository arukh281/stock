"""4-state regime classifier with persistence (Section 4)."""

from __future__ import annotations

import pandas as pd


def _score_component(value: float, pos_thresh: float, neg_thresh: float) -> int:
    if pd.isna(value):
        return 0
    if value >= pos_thresh:
        return 2
    if value <= neg_thresh:
        return -2
    return 0


def raw_regime_row(row: pd.Series, cfg: dict) -> str:
    hurst = row.get("hurst_regime", "INDETERMINATE")
    vr = row.get("vol_ratio", 1.0)
    cms = row.get("cms", 0.0)
    obv_z = row.get("obv_slope_z", 0.0)
    ofi = row.get("ofi_10", 0.0)
    kv = row.get("kalman_velocity_z", 0.0)
    ent_pct = row.get("entropy_pct", 0.5)
    vol_z = row.get("volume_z", 0.0)
    close_up = row.get("close", 0) >= row.get("open", 0)
    obv_div = row.get("obv_divergence", False)

    w = cfg["regime"]["weights"]
    s = 0.0
    if hurst == "TRENDING":
        s += 2 * w["hurst"]
    elif hurst == "MEAN_REVERTING":
        s -= 2 * w["hurst"]

    if vr < 0.8:
        s += 2 * w["vol_ratio"]
    elif vr > 1.3:
        s -= 2 * w["vol_ratio"]

    if cms > 0.8:
        s += 2 * w["cms"]
    elif cms < -0.8:
        s -= 2 * w["cms"]

    if obv_z > 1.5:
        s += 2 * w["obv_slope"]
    elif obv_z < -1.5:
        s -= 2 * w["obv_slope"]

    if ofi > 0.3:
        s += 2 * w["ofi"]
    elif ofi < -0.3:
        s -= 2 * w["ofi"]

    if kv > 0.5:
        s += 2 * w["kalman_velocity"]
    elif kv < -0.5:
        s -= 2 * w["kalman_velocity"]

    if ent_pct < 0.25:
        s += 2 * w["entropy"]
    elif ent_pct > 0.75:
        s -= 2 * w["entropy"]

    if vol_z > 2:
        if close_up:
            s += 2 * w["volume_z"]
        else:
            s -= 2 * w["volume_z"]

    kv_raw = row.get("kalman_velocity", 0.0)
    bull_th = cfg["regime"]["bull_threshold"]
    bear_th = cfg["regime"]["bear_threshold"]
    band = cfg["regime"]["sideways_band"]

    if s >= bull_th and kv_raw > 0:
        return "BULL_TREND"
    if s <= bear_th and kv_raw < 0:
        return "BEAR_TREND"
    if -band < s < band and vr < 0.9 and hurst == "INDETERMINATE":
        return "SIDEWAYS"
    if -band < s < band and obv_div and ofi < 0:
        return "DISTRIBUTION"
    if s > 0:
        return "SIDEWAYS"
    if s < 0:
        return "BEAR_TREND"
    return "SIDEWAYS"


def apply_regime_persistence(raw: pd.Series, n: int = 2) -> pd.Series:
    active = pd.Series(index=raw.index, dtype=object, name="regime_active")
    prev_active = None
    streak = 0
    last_raw = None
    for idx, state in raw.items():
        if state == last_raw:
            streak += 1
        else:
            streak = 1
            last_raw = state
        if streak >= n:
            prev_active = state
        active.loc[idx] = prev_active if prev_active else state
    return active


def detect_anomaly_transition(regime: pd.Series) -> pd.Series:
    risk_off = pd.Series(False, index=regime.index, name="regime_risk_off")
    prev = None
    for idx, state in regime.items():
        if prev == "BULL_TREND" and state == "BEAR_TREND":
            risk_off.loc[idx] = True
        prev = state
    return risk_off


def classify_regime(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    from kali.config import load_config

    cfg = cfg or load_config()
    out = df.copy()
    raw = out.apply(lambda r: raw_regime_row(r, cfg), axis=1)
    out["regime_raw"] = raw
    n = cfg["regime"]["persistence_bars"]
    out["regime_active"] = apply_regime_persistence(raw, n=n)
    out["regime_risk_off"] = detect_anomaly_transition(out["regime_active"])
    out["regime_score"] = out.apply(
        lambda r: _compute_score_scalar(r, cfg), axis=1
    )
    return out


def _compute_score_scalar(row: pd.Series, cfg: dict) -> float:
    """Approximate weighted score for diagnostics."""
    hurst = row.get("hurst_regime", "INDETERMINATE")
    vr = row.get("vol_ratio", 1.0)
    cms = row.get("cms", 0.0)
    w = cfg["regime"]["weights"]
    s = 0.0
    if hurst == "TRENDING":
        s += 2 * w["hurst"]
    elif hurst == "MEAN_REVERTING":
        s -= 2 * w["hurst"]
    if vr < 0.8:
        s += 2 * w["vol_ratio"]
    elif vr > 1.3:
        s -= 2 * w["vol_ratio"]
    if cms > 0.8:
        s += 2 * w["cms"]
    elif cms < -0.8:
        s -= 2 * w["cms"]
    return s
