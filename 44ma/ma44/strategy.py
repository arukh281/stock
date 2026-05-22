from __future__ import annotations

import pandas as pd

from ma44.config import Settings


def _hammer(row: pd.Series) -> bool:
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return False
    upper = h - max(o, c)
    lower = min(o, c) - l
    if body < rng * 0.25 and lower >= 2 * max(body, 1e-9) and upper <= max(body * 1.5, rng * 0.15):
        return True
    return False


def add_indicators(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    out = df.copy()
    p = settings.sma_period
    out["sma"] = out["close"].rolling(p, min_periods=p).mean()
    return out


def _sma_slope_pct_series(sma: pd.Series, lookback: int) -> pd.Series:
    prev = sma.shift(lookback)
    return ((sma - prev) / sma).where(sma > 0)


def monotone_sma_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """
    True when SMA44 rose on each of the last N sessions (day-over-day).
    Filters weak V-shaped recoveries where only the far lookback comparison passes.
    """
    n = int(getattr(settings, "sma_monotone_days", 0) or 0)
    if n <= 0:
        return pd.Series(True, index=df.index)
    sma = df["sma"]
    ok = pd.Series(True, index=df.index)
    for k in range(1, n + 1):
        ok &= sma > sma.shift(k)
    return ok.fillna(False)


def rising_sma_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """44 SMA must be higher than lookback bars ago."""
    sma = df["sma"]
    rising = sma > sma.shift(int(settings.sma_rising_lookback))
    return rising.fillna(False)


def sma_slope_min_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """Enforce minimum SMA rise (%) over sma_rising_lookback when sma_slope_min_pct > 0."""
    min_pct = float(getattr(settings, "sma_slope_min_pct", 0.0) or 0.0)
    if min_pct <= 0:
        return pd.Series(True, index=df.index)
    L = int(settings.sma_rising_lookback)
    slope = _sma_slope_pct_series(df["sma"], L)
    return (slope >= min_pct).fillna(False)


def _stacked_offset(settings: Settings) -> int:
    return int(getattr(settings, "sma_stacked_offset", 44) or 44)


def sma_path_floor_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """
    Reject V-shaped SMA recoveries: the lowest SMA over the last N sessions must stay
    above the SMA level N sessions ago (optional tolerance).
    """
    n = int(getattr(settings, "sma_path_floor_days", 0) or 0)
    if n <= 0:
        return pd.Series(True, index=df.index)
    sma = df["sma"]
    floor = sma.rolling(n, min_periods=n).min()
    ref = sma.shift(n)
    tol = float(getattr(settings, "sma_path_floor_tol_pct", 0.0) or 0.0)
    if tol > 0:
        return (floor > ref * (1.0 - tol)).fillna(False)
    return (floor > ref).fillna(False)


def close_above_sma_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """Allow at most N closes below the 44 SMA within the lookback window."""
    max_below = int(getattr(settings, "sma_close_below_max_days", 0) or 0)
    if max_below <= 0:
        return pd.Series(True, index=df.index)
    lookback = int(getattr(settings, "sma_close_below_lookback", 44) or 44)
    below = (df["close"] < df["sma"]).astype(float)
    count = below.rolling(lookback, min_periods=lookback).sum()
    return (count <= max_below).fillna(False)


def stacked_sma_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """
    Three-segment 44 SMA ladder using the same rolling SMA at staggered offsets:
      MA1 (0..−43): current SMA must exceed MA2 endpoint
      MA2 (−44..−87): SMA[offset] must exceed MA3 endpoint (positive segment slope)
      MA3 (−88..−131): optional relaxed ordering vs MA2
    """
    if not bool(getattr(settings, "sma_stacked_enabled", False)):
        return pd.Series(True, index=df.index)
    off = _stacked_offset(settings)
    sma = df["sma"]
    ma1 = sma
    ma2 = sma.shift(off)
    ma3 = sma.shift(2 * off)
    ok = (ma1 > ma2).fillna(False)
    if bool(getattr(settings, "sma_stacked_require_third", True)):
        relax = bool(getattr(settings, "sma_stacked_relax_third", True))
        if relax:
            ok &= (ma2 >= ma3).fillna(False)
        else:
            ok &= (ma2 > ma3).fillna(False)
    return ok


def close_above_prev_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    if not bool(getattr(settings, "require_close_above_prev", False)):
        return pd.Series(True, index=df.index)
    return (df["close"] > df["close"].shift(1)).fillna(False)


def trend_sma_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """All SMA trend gates used for signals and universe slope scans."""
    return (
        rising_sma_mask(df, settings)
        & monotone_sma_mask(df, settings)
        & sma_slope_min_mask(df, settings)
        & stacked_sma_mask(df, settings)
        & sma_path_floor_mask(df, settings)
        & close_above_sma_mask(df, settings)
    )


def _trend_warmup_bars(settings: Settings) -> int:
    off = _stacked_offset(settings) if bool(getattr(settings, "sma_stacked_enabled", False)) else 0
    stacked = 2 * off if off else 0
    path_n = int(getattr(settings, "sma_path_floor_days", 0) or 0)
    close_lb = (
        int(getattr(settings, "sma_close_below_lookback", 44) or 44)
        if int(getattr(settings, "sma_close_below_max_days", 0) or 0) > 0
        else 0
    )
    return max(
        int(settings.sma_rising_lookback),
        int(getattr(settings, "sma_monotone_days", 0) or 0),
        stacked,
        path_n,
        close_lb,
    )


def last_bar_positive_slope(work: pd.DataFrame, settings: Settings) -> bool:
    need = settings.sma_period + _trend_warmup_bars(settings) + 2
    if len(work) < need or "sma" not in work.columns:
        return False
    return bool(trend_sma_mask(work, settings).iloc[-1])


def signal_mask(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """
    True on bars where:
    - 44 SMA trend passes (rising + optional monotone + min slope)
    - price touches/comes near the 44 SMA support zone
    - the same bar is bullish (green) or a confirmed hammer
      (hammer close in the upper half of its range)
    - optional: close above prior session close
    """
    sma = df["sma"]
    trend = trend_sma_mask(df, settings)

    touch_hi = sma * (1 + settings.touch_above_pct)
    touch_lo = sma * (1 - settings.touch_below_pct)
    touched = (df["low"] <= touch_hi) & (df["low"] >= touch_lo)

    close = df["close"]
    open_ = df["open"]
    bull = close > open_
    half_level = (df["high"] + df["low"]) / 2.0
    hammer_confirmed = df.apply(_hammer, axis=1) & (close >= half_level)
    candle_ok = bull | hammer_confirmed
    return trend & touched & candle_ok & close_above_prev_mask(df, settings)


def signal_bar_confidence(work: pd.DataFrame, i: int, settings: Settings) -> float:
    """
    Higher = stronger pullback setup. Uses SMA slope, touch proximity to the MA,
    candle quality (hammer / bullish body), and optional volume vs recent average.
    """
    if i < 0 or i >= len(work):
        return 0.0
    row = work.iloc[i]
    sma = float(row["sma"]) if pd.notna(row.get("sma")) else float("nan")
    if pd.isna(sma) or sma <= 0:
        return 0.0

    L = int(settings.sma_rising_lookback)
    slope_pct = 0.0
    if i >= L:
        sma_prev = float(work["sma"].iloc[i - L])
        if pd.notna(sma_prev) and sma_prev > 0:
            slope_pct = max((sma - sma_prev) / sma, 0.0)

    touch_hi = sma * (1 + settings.touch_above_pct)
    touch_lo = sma * (1 - settings.touch_below_pct)
    low = float(row["low"])
    zone_half = max((touch_hi - touch_lo) * 0.5, 1e-9)
    touch_score = 1.0 - min(abs(low - sma) / zone_half, 1.0)

    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    if _hammer(row) and c >= (h + l) / 2.0:
        candle_score = 1.0
    elif c > o:
        body = c - o
        rng = max(h - l, 1e-9)
        candle_score = 0.5 + 0.5 * min(body / rng, 1.0)
    else:
        candle_score = 0.0

    vol_bonus = 0.0
    if "volume" in work.columns and i >= 20:
        vol = float(row["volume"])
        avg_vol = float(work["volume"].iloc[i - 20 : i].mean())
        if avg_vol > 0 and vol > 0:
            vol_bonus = min(vol / avg_vol, 2.0) / 2.0 * 0.12

    slope_min = float(getattr(settings, "sma_slope_min_pct", 0.0) or 0.0)
    slope_score = max(slope_pct - slope_min, 0.0) * 100.0
    return float(0.42 * slope_score + 0.33 * touch_score + 0.25 * candle_score + vol_bonus)


def signal_gate_breakdown(
    df: pd.DataFrame, settings: Settings, i: int | None = None
) -> dict | None:
    """
    Per-gate pass/fail for one bar (default: last bar). Used by sandbox diagnostics.
    """
    need = settings.sma_period + _trend_warmup_bars(settings) + 2
    if len(df) < need:
        return None
    work = add_indicators(df, settings)
    if i is None:
        i = len(work) - 1
    if i < 0 or i >= len(work):
        return None

    row = work.iloc[i]
    sma = float(row["sma"]) if pd.notna(row.get("sma")) else float("nan")
    L = int(settings.sma_rising_lookback)
    slope_pct = None
    if pd.notna(sma) and sma > 0 and i >= L:
        sma_prev = float(work["sma"].iloc[i - L])
        if pd.notna(sma_prev) and sma_prev > 0:
            slope_pct = (sma - sma_prev) / sma

    touch_hi = sma * (1 + settings.touch_above_pct) if pd.notna(sma) else float("nan")
    touch_lo = sma * (1 - settings.touch_below_pct) if pd.notna(sma) else float("nan")
    low = float(row["low"])
    touched = (
        pd.notna(touch_hi)
        and pd.notna(touch_lo)
        and (low <= touch_hi)
        and (low >= touch_lo)
    )

    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    bull = c > o
    hammer_ok = bool(_hammer(row) and c >= (h + l) / 2.0)
    candle_ok = bull or hammer_ok

    gates: list[dict] = [
        {
            "id": "rising_sma",
            "label": f"44 SMA > SMA[{L} bars ago]",
            "pass": bool(rising_sma_mask(work, settings).iloc[i]),
        },
        {
            "id": "monotone_sma",
            "label": f"SMA up {int(getattr(settings, 'sma_monotone_days', 0) or 0)} sessions (0=off)",
            "pass": bool(monotone_sma_mask(work, settings).iloc[i]),
        },
        {
            "id": "sma_slope_min",
            "label": f"SMA slope ≥ {float(getattr(settings, 'sma_slope_min_pct', 0) or 0):.4f}",
            "pass": bool(sma_slope_min_mask(work, settings).iloc[i]),
            "detail": None if slope_pct is None else f"slope={slope_pct:.4f}",
        },
        {
            "id": "stacked_sma",
            "label": (
                f"Stacked 44 ladder (offset {_stacked_offset(settings)})"
                if bool(getattr(settings, "sma_stacked_enabled", False))
                else "Stacked 44 ladder (off)"
            ),
            "pass": bool(stacked_sma_mask(work, settings).iloc[i]),
        },
        {
            "id": "sma_path_floor",
            "label": (
                f"SMA path floor {int(getattr(settings, 'sma_path_floor_days', 0) or 0)}d"
                if int(getattr(settings, "sma_path_floor_days", 0) or 0) > 0
                else "SMA path floor (off)"
            ),
            "pass": bool(sma_path_floor_mask(work, settings).iloc[i]),
        },
        {
            "id": "close_above_sma",
            "label": (
                f"≤{int(getattr(settings, 'sma_close_below_max_days', 0) or 0)} closes below SMA "
                f"in {int(getattr(settings, 'sma_close_below_lookback', 44) or 44)}d"
                if int(getattr(settings, "sma_close_below_max_days", 0) or 0) > 0
                else "Close above SMA buffer (off)"
            ),
            "pass": bool(close_above_sma_mask(work, settings).iloc[i]),
        },
        {
            "id": "touch_zone",
            "label": "Low touched 44 SMA support band",
            "pass": bool(touched),
            "detail": (
                None
                if not pd.notna(touch_lo)
                else f"low={low:.2f} band=[{touch_lo:.2f},{touch_hi:.2f}]"
            ),
        },
        {
            "id": "bull_or_hammer",
            "label": "Green candle or confirmed hammer",
            "pass": bool(candle_ok),
            "detail": f"green={bull} hammer={hammer_ok}",
        },
        {
            "id": "close_above_prev",
            "label": (
                "Close > prior close"
                if bool(getattr(settings, "require_close_above_prev", False))
                else "Close > prior close (off)"
            ),
            "pass": bool(close_above_prev_mask(work, settings).iloc[i]),
            "detail": (
                None
                if i < 1
                else f"close={c:.2f} prev={float(work['close'].iloc[i-1]):.2f}"
            ),
        },
    ]
    signal = bool(signal_mask(work, settings).iloc[i])
    failed = [g["id"] for g in gates if not g["pass"]]
    return {
        "date": work.index[i],
        "close": c,
        "sma": sma if pd.notna(sma) else None,
        "signal": signal,
        "gates": gates,
        "failed": failed,
        "confidence": signal_bar_confidence(work, i, settings) if signal else 0.0,
    }


def latest_signal_info(df: pd.DataFrame, settings: Settings) -> dict | None:
    """
    Inspect the most recent *completed* bar. Returns metadata if signal fired on that bar.
    """
    need = settings.sma_period + _trend_warmup_bars(settings) + 2
    if len(df) < need:
        return None
    work = add_indicators(df, settings)
    sig = signal_mask(work, settings)
    if not bool(sig.iloc[-1]):
        return None
    row = work.iloc[-1]
    i = len(work) - 1
    return {
        "date": work.index[-1],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "sma": float(row["sma"]),
        "hammer": bool(_hammer(row) and row["close"] >= (row["high"] + row["low"]) / 2.0),
        "green": bool(row["close"] > row["open"]),
        "confidence": signal_bar_confidence(work, i, settings),
    }
