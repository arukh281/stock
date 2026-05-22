from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from ma44.config import Settings
from ma44.ticker import normalize_yahoo_symbol

_NSE_HOME = "https://www.nseindia.com"
_NSE_INDEX_API = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20TOTAL%20MARKET"


def _cache_dir(settings: Settings) -> Path:
    raw = str(getattr(settings, "universe_cache_dir", ".cache/universe") or ".cache/universe")
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def _nse_session():
    import requests

    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    s.get(_NSE_HOME, timeout=20)
    return s


def fetch_nse_ffmc_snapshot(settings: Settings, as_of: date | None = None) -> dict[str, float]:
    """
    Live NSE free-float market cap (ffmc) for equities in NIFTY Total Market.
    Cached per calendar day under universe_cache_dir.
    """
    as_of = as_of or date.today()
    cache_path = _cache_dir(settings) / f"nse_ffmc_{as_of.isoformat()}.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()}

    import requests

    sess = _nse_session()
    r = sess.get(_NSE_INDEX_API, timeout=25)
    r.raise_for_status()
    rows = r.json().get("data") or []
    out: dict[str, float] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym or " " in sym:
            continue
        ffmc = row.get("ffmc")
        if ffmc is None:
            continue
        ysym = normalize_yahoo_symbol(sym)
        if ysym:
            out[ysym] = float(ffmc)
    cache_path.write_text(json.dumps(out, indent=0), encoding="utf-8")
    return out


def top_symbols_by_live_ffmc(settings: Settings, n: int | None = None) -> list[str]:
    n = int(n or getattr(settings, "universe_top_n", 100) or 100)
    snap = fetch_nse_ffmc_snapshot(settings)
    ranked = sorted(snap.items(), key=lambda kv: kv[1], reverse=True)
    return [sym for sym, _ in ranked[:n]]


def _shares_cache_path(settings: Settings) -> Path:
    return _cache_dir(settings) / "shares_outstanding.json"


def load_shares_outstanding(settings: Settings, symbols: list[str]) -> dict[str, float]:
    path = _shares_cache_path(settings)
    cached: dict[str, float] = {}
    if path.exists():
        cached = {str(k): float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}

    sleep_s = float(getattr(settings, "universe_fetch_sleep_sec", 0.2) or 0)
    updated = False
    for i, sym in enumerate(symbols):
        if sym in cached and cached[sym] > 0:
            continue
        if sleep_s > 0 and i > 0:
            time.sleep(sleep_s)
        sh = 0.0
        try:
            info = yf.Ticker(sym).info or {}
            sh = float(
                info.get("sharesOutstanding")
                or info.get("impliedSharesOutstanding")
                or info.get("floatShares")
                or 0
            )
        except Exception:
            sh = 0.0
        if sh > 0:
            cached[sym] = sh
            updated = True
    if updated:
        path.write_text(json.dumps(cached), encoding="utf-8")
    return cached


def master_pool_symbols(settings: Settings) -> list[str]:
    """
    Symbols to prefetch OHLC for backtests: top `universe_master_pool` names by
  latest NSE ffmc (defaults to 2× universe_top_n).
    """
    top_n = int(getattr(settings, "universe_top_n", 100) or 100)
    pool_n = int(getattr(settings, "universe_master_pool", 0) or 0)
    if pool_n <= 0:
        pool_n = max(top_n * 2, top_n)
    snap = fetch_nse_ffmc_snapshot(settings)
    ranked = sorted(snap.items(), key=lambda kv: kv[1], reverse=True)
    return [sym for sym, _ in ranked[:pool_n]]


def build_daily_mcap_membership(
    works: dict[str, pd.DataFrame],
    all_ts_sorted: list[pd.Timestamp],
    warmup: int,
    settings: Settings,
    shares: dict[str, float],
) -> dict[pd.Timestamp, set[str]] | None:
    """
    Per session, allow new entries only in top-N by prior close × shares outstanding
    (point-in-time proxy using latest reported shares).
    """
    top_n = int(getattr(settings, "universe_top_n", 0) or 0)
    if top_n <= 0:
        return None

    def _bar_index(work: pd.DataFrame, ts: pd.Timestamp) -> int | None:
        if ts not in work.index:
            return None
        loc = work.index.get_loc(ts)
        if isinstance(loc, slice):
            return int(loc.stop) - 1
        if isinstance(loc, (int, np.integer)):
            return int(loc)
        return int(loc[-1])

    members: dict[pd.Timestamp, set[str]] = {}
    for ts in all_ts_sorted:
        scored: list[tuple[float, str]] = []
        for sym, w in works.items():
            sh = float(shares.get(sym, 0.0) or 0.0)
            if sh <= 0:
                continue
            t = _bar_index(w, ts)
            if t is None or t < warmup:
                continue
            c = float(w.iloc[t - 1]["close"])
            if c <= 0:
                continue
            scored.append((c * sh, sym))
        scored.sort(reverse=True)
        members[ts] = {sym for _, sym in scored[:top_n]}
    return members
