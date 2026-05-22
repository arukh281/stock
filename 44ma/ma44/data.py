from __future__ import annotations

import contextlib
import io
import logging
from datetime import datetime

import pandas as pd
import yfinance as yf


def _candidate_yahoo_symbols(symbol: str) -> list[str]:
    """
    Yahoo sometimes returns 404 / empty for NSE (.NS) but has BSE (.BO), or vice versa.
    Try the requested symbol first, then the other Indian listing when applicable.
    """
    s = symbol.strip()
    if not s:
        return []
    out: list[str] = [s]
    if s.upper().endswith(".NS"):
        out.append(s[:-3] + ".BO")
    elif s.upper().endswith(".BO"):
        out.append(s[:-3] + ".NS")
    return list(dict.fromkeys(out))


def _normalize_history_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1)
    df.columns = [str(c).lower() for c in df.columns]
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            return pd.DataFrame()
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.sort_index()


def fetch_daily(symbol: str, start: str | datetime, end: str | datetime | None = None) -> pd.DataFrame:
    """
    Daily raw (unadjusted) OHLCV from Yahoo via Ticker.history
    so prices match standard chart values.
    Tries alternate NSE/BSE suffix if the first listing is empty.
    """
    loggers = (
        logging.getLogger("yfinance"),
        logging.getLogger("urllib3"),
        logging.getLogger("peewee"),
    )
    saved_levels = [(lg, lg.level) for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.CRITICAL)

    buf_out, buf_err = io.StringIO(), io.StringIO()
    last_empty = pd.DataFrame()
    try:
        for sym in _candidate_yahoo_symbols(symbol):
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                t = yf.Ticker(sym)
                df = t.history(start=start, end=end, interval="1d", auto_adjust=False, actions=False)
            norm = _normalize_history_df(df)
            if not norm.empty:
                return norm
            last_empty = norm
        return last_empty
    finally:
        for lg, lvl in saved_levels:
            lg.setLevel(lvl)
