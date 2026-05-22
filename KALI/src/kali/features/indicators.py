"""Manual Wilder/standard indicators (pandas-ta replacement)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    return atr.rename("atr")


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum().rename("obv")


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).rename("rsi")


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "macd_signal": signal_line, "macd_hist": hist}
    )


def stoch_rsi(close: pd.Series, rsi_len: int = 14, stoch_len: int = 14) -> pd.Series:
    r = rsi(close, rsi_len)
    lo = r.rolling(stoch_len).min()
    hi = r.rolling(stoch_len).max()
    stoch = (r - lo) / (hi - lo).replace(0, np.nan)
    return (stoch * 100).rename("stoch_rsi")
