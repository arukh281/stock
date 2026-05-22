"""EOD OHLCV ingest via yfinance with split-adjusted prices."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

from kali.config import cache_dir, load_config


def _normalize_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if not s.endswith(".NS") and not s.endswith(".BO"):
        s = f"{s}.NS"
    return s


def _ticker_base(symbol: str) -> str:
    return _normalize_symbol(symbol).replace(".NS", "").replace(".BO", "")


def download_ohlcv(
    symbol: str,
    start: str | None = "2015-01-01",
    end: str | None = None,
    cfg: dict | None = None,
) -> pd.DataFrame:
    """Download and cache split-adjusted daily OHLCV."""
    cfg = cfg or load_config()
    sym = _normalize_symbol(symbol)
    ticker = yf.Ticker(sym)
    raw = ticker.history(start=start, end=end, auto_adjust=False)
    if raw.empty:
        raise ValueError(f"No data returned for {sym}")

    adj = ticker.history(start=start, end=end, auto_adjust=True)
    if adj.empty:
        raise ValueError(f"No adjusted data for {sym}")

    raw = raw.copy()
    raw.index = pd.to_datetime(raw.index, utc=True).tz_convert(None).normalize()
    adj = adj.copy()
    adj.index = pd.to_datetime(adj.index, utc=True).tz_convert(None).normalize()

    common = raw.index.intersection(adj.index)
    raw = raw.loc[common]
    adj = adj.loc[common]
    df = raw.copy()

    ratio = adj["Close"] / df["Close"].replace(0, float("nan"))
    ratio = ratio.ffill().bfill()

    for col in ["Open", "High", "Low", "Close"]:
        df[col] = df[col] * ratio

    df["Volume"] = raw["Volume"]
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df["symbol"] = _ticker_base(sym)
    df["is_doji"] = (df["high"] - df["low"]).abs() < 1e-9

    out_dir = cache_dir(cfg) / cfg["data"]["ohlcv_subdir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_ticker_base(sym)}.parquet"
    df.to_parquet(path)
    return df


def load_ohlcv(symbol: str, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    sym = _ticker_base(symbol)
    path = cache_dir(cfg) / cfg["data"]["ohlcv_subdir"] / f"{sym}.parquet"
    if not path.exists():
        return download_ohlcv(symbol, cfg=cfg)
    return pd.read_parquet(path)
