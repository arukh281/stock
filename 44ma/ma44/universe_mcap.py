from __future__ import annotations

import csv
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from ma44.config import Settings
from ma44.ticker import normalize_yahoo_symbol

try:
    from sandbox.yahoo_fetch import call_with_rate_limit_retry, is_rate_limit_error
except ImportError:

    _RATE_HINTS = (
        "too many requests",
        "rate limit",
        "429",
        "yfratelimit",
        "unexpectedly terminated",
    )

    def is_rate_limit_error(exc=None, text=""):  # type: ignore[misc]
        blob = f"{exc or ''} {text}".lower()
        return any(h in blob for h in _RATE_HINTS)

    def call_with_rate_limit_retry(  # type: ignore[misc]
        fn,
        *,
        max_attempts: int = 5,
        base_sleep_sec: float = 4.0,
        label: str = "yahoo",
    ):
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if not is_rate_limit_error(exc) or attempt >= max_attempts:
                    raise
                wait = min(90.0, base_sleep_sec * (2 ** (attempt - 1)))
                print(
                    f"[{label}] rate limited (attempt {attempt}/{max_attempts}); "
                    f"sleep {wait:.0f}s ...",
                    flush=True,
                )
                time.sleep(wait)
        raise last_exc


_NSE_TOTAL_MARKET_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"
_REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _cache_dir(settings: Settings) -> Path:
    raw = str(getattr(settings, "universe_cache_dir", ".cache/universe") or ".cache/universe")
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def _http_session():
    import requests

    s = requests.Session()
    s.headers.update(_REQ_HEADERS)
    return s


def _candidate_yahoo_symbols(symbol: str) -> list[str]:
    s = symbol.strip().upper()
    if not s:
        return []
    out = [s]
    if s.endswith(".NS"):
        out.append(s[:-3] + ".BO")
    elif s.endswith(".BO"):
        out.append(s[:-3] + ".NS")
    return list(dict.fromkeys(out))


def _constituents_cache_path(settings: Settings, as_of: date) -> Path:
    return _cache_dir(settings) / f"nifty_total_market_constituents_{as_of.isoformat()}.json"


def _close_cache_path(settings: Settings, as_of: date) -> Path:
    return _cache_dir(settings) / f"nifty_total_market_close_{as_of.isoformat()}.json"


def _marketcap_cache_path(settings: Settings, as_of: date) -> Path:
    return _cache_dir(settings) / f"nifty_total_market_market_cap_{as_of.isoformat()}.json"


def _ranking_cache_path(settings: Settings, as_of: date) -> Path:
    return _cache_dir(settings) / f"nifty_total_market_rank_{as_of.isoformat()}.json"


def _load_json_list(path: Path) -> list[str]:
    return [str(x) for x in json.loads(path.read_text(encoding="utf-8"))]


def _load_json_float_map(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in data.items()}


def _latest_cache_path(settings: Settings, pattern: str) -> Path | None:
    matches = sorted(_cache_dir(settings).glob(pattern))
    return matches[-1] if matches else None


def _last_numeric_value(obj: pd.Series) -> float:
    vals = pd.to_numeric(obj, errors="coerce").dropna()
    return float(vals.iloc[-1]) if not vals.empty else 0.0


def _clean_constituent_symbols(symbols: list[str]) -> list[str]:
    return [sym for sym in symbols if sym and not sym.startswith("DUMMY")]


def _parse_download_closes(df: pd.DataFrame, requested_symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    if df.empty:
        return out

    if isinstance(df.columns, pd.MultiIndex):
        level0 = list(df.columns.get_level_values(0))
        close_key = next((x for x in level0 if str(x).lower() == "close"), None)
        if close_key is not None:
            close_df = df[close_key]
            if isinstance(close_df, pd.Series):
                if requested_symbols:
                    val = _last_numeric_value(close_df)
                    if val > 0:
                        out[requested_symbols[0]] = val
                return out
            for sym in close_df.columns:
                val = _last_numeric_value(close_df[sym])
                if val > 0:
                    out[str(sym)] = val
            return out

        requested = set(requested_symbols)
        for sym in requested:
            if sym not in df.columns.get_level_values(0):
                continue
            sub = df[sym]
            close_col = next((c for c in sub.columns if str(c).lower() == "close"), None)
            if close_col is None:
                continue
            val = _last_numeric_value(sub[close_col])
            if val > 0:
                out[sym] = val
        return out

    close_col = next((c for c in df.columns if str(c).lower() == "close"), None)
    if close_col is None or not requested_symbols:
        return out
    val = _last_numeric_value(df[close_col])
    if val > 0:
        out[requested_symbols[0]] = val
    return out


def _fetch_download_closes(symbols: list[str], *, sleep_s: float) -> dict[str, float]:
    out: dict[str, float] = {}
    batch_size = 80
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        if not batch:
            continue
        if sleep_s > 0 and i > 0:
            time.sleep(sleep_s)

        def _pull() -> pd.DataFrame:
            return yf.download(
                tickers=batch,
                period="10d",
                interval="1d",
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
                group_by="column",
            )

        raw = call_with_rate_limit_retry(_pull, label=f"yf.download:close:{len(batch)}")
        out.update(_parse_download_closes(raw, batch))
    return out


def fetch_total_market_constituents(settings: Settings, as_of: date | None = None) -> list[str]:
    """
    Official NIFTY Total Market membership from the NSE archive CSV.
    Cached per calendar day; if the archive is temporarily unavailable, reuse the
    most recent successful official member list instead of failing the run.
    """
    as_of = as_of or date.today()
    cache_path = _constituents_cache_path(settings, as_of)
    if cache_path.exists():
        return _clean_constituent_symbols(_load_json_list(cache_path))

    try:
        sess = _http_session()
        r = sess.get(_NSE_TOTAL_MARKET_CSV_URL, timeout=25)
        r.raise_for_status()
        rows = csv.DictReader(io.StringIO(r.text))
        symbols: list[str] = []
        for row in rows:
            series = str(row.get("Series") or "").strip().upper()
            if series and series != "EQ":
                continue
            sym = normalize_yahoo_symbol(str(row.get("Symbol") or ""))
            if sym:
                symbols.append(sym)
        symbols = _clean_constituent_symbols(list(dict.fromkeys(symbols)))
        if len(symbols) < 500:
            raise RuntimeError(
                f"NSE Total Market CSV returned too few symbols ({len(symbols)})"
            )
        cache_path.write_text(json.dumps(symbols), encoding="utf-8")
        return symbols
    except Exception:
        latest = _latest_cache_path(settings, "nifty_total_market_constituents_*.json")
        if latest is not None:
            return _load_json_list(latest)
        raise


def fetch_last_close_snapshot(
    settings: Settings,
    symbols: list[str],
    as_of: date | None = None,
) -> dict[str, float]:
    """
    Latest daily close snapshot for the requested Yahoo symbols.
    Uses batched downloads and retries missing NSE listings via the corresponding
    BSE symbol, then caches the day-level close map.
    """
    as_of = as_of or date.today()
    cache_path = _close_cache_path(settings, as_of)
    if cache_path.exists():
        return _load_json_float_map(cache_path)

    sleep_s = float(getattr(settings, "universe_fetch_sleep_sec", 0.2) or 0)
    closes = _fetch_download_closes(symbols, sleep_s=sleep_s)

    missing = [sym for sym in symbols if sym not in closes]
    alt_map = {sym: sym[:-3] + ".BO" for sym in missing if sym.upper().endswith(".NS")}
    if alt_map:
        alt_closes = _fetch_download_closes(list(alt_map.values()), sleep_s=sleep_s)
        for sym, alt in alt_map.items():
            val = float(alt_closes.get(alt, 0.0) or 0.0)
            if val > 0:
                closes[sym] = val

    if closes:
        cache_path.write_text(json.dumps(closes, sort_keys=True), encoding="utf-8")
    return closes


def _fetch_market_cap_one(sym: str) -> float:
    def _pull_market_cap(cand: str) -> float:
        fast = yf.Ticker(cand).fast_info
        try:
            val = float(fast["marketCap"] or 0.0)
        except Exception:
            val = 0.0
        if val > 0:
            return val

        info = yf.Ticker(cand).info or {}
        return float(info.get("marketCap") or 0.0)

    for cand in _candidate_yahoo_symbols(sym):
        try:
            info = call_with_rate_limit_retry(
                lambda cand=cand: _pull_market_cap(cand),
                label=f"yahoo:marketcap:{cand}",
            )
        except Exception as exc:
            if is_rate_limit_error(exc):
                raise
            info = 0.0
        val = float(info or 0.0)
        if val > 0:
            return val
    return 0.0


def fetch_market_cap_snapshot(
    settings: Settings,
    symbols: list[str],
    as_of: date | None = None,
) -> dict[str, float]:
    """
    Daily market-cap snapshot for the official NIFTY Total Market constituents.
    Uses parallel Yahoo lookups and caches the resulting map per calendar day.
    """
    as_of = as_of or date.today()
    cache_path = _marketcap_cache_path(settings, as_of)
    if cache_path.exists():
        return _load_json_float_map(cache_path)

    workers = min(4, max(1, len(symbols)))
    out: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_market_cap_one, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                val = float(fut.result() or 0.0)
            except Exception:
                val = 0.0
            if val > 0:
                out[sym] = val

    if out:
        cache_path.write_text(json.dumps(out, sort_keys=True), encoding="utf-8")
    return out


def fetch_nse_ffmc_snapshot(settings: Settings, as_of: date | None = None) -> dict[str, float]:
    """
    Daily NIFTY Total Market ranking snapshot.

    Membership comes from the official NSE archive CSV. Ranking is then derived
    from live Yahoo market-cap snapshots for those current members, so the
    top-100 universe is still rebuilt each day even though the old NSE JSON
    endpoint stopped working.
    """
    as_of = as_of or date.today()
    cache_path = _ranking_cache_path(settings, as_of)
    if cache_path.exists():
        return _load_json_float_map(cache_path)

    top_n = int(getattr(settings, "universe_top_n", 100) or 100)
    try:
        symbols = fetch_total_market_constituents(settings, as_of=as_of)
        ranked = fetch_market_cap_snapshot(settings, symbols, as_of=as_of)
        if len(ranked) < top_n:
            raise RuntimeError(
                f"Could only rank {len(ranked)} NIFTY Total Market symbols for top-{top_n}"
            )
        cache_path.write_text(json.dumps(ranked, sort_keys=True), encoding="utf-8")
        return ranked
    except Exception:
        latest = _latest_cache_path(settings, "nifty_total_market_rank_*.json")
        if latest is not None:
            ranked = _load_json_float_map(latest)
            if len(ranked) >= top_n:
                return ranked
        raise


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
        cached = _load_json_float_map(path)

    sleep_s = float(getattr(settings, "universe_fetch_sleep_sec", 0.2) or 0)
    updated = False
    for i, sym in enumerate(symbols):
        if sym in cached and cached[sym] > 0:
            continue
        if sleep_s > 0 and i > 0:
            time.sleep(sleep_s)
        sh = 0.0
        for cand in _candidate_yahoo_symbols(sym):
            try:
                info = call_with_rate_limit_retry(
                    lambda cand=cand: yf.Ticker(cand).info or {},
                    label=f"yahoo:info:{cand}",
                )
            except Exception as exc:
                if is_rate_limit_error(exc):
                    raise
                info = {}
            sh = float(
                info.get("floatShares")
                or info.get("sharesOutstanding")
                or info.get("impliedSharesOutstanding")
                or 0
            )
            if sh > 0:
                break
        if sh > 0:
            cached[sym] = sh
            updated = True
    if updated:
        path.write_text(json.dumps(cached), encoding="utf-8")
    return cached


def master_pool_symbols(settings: Settings) -> list[str]:
    """
    Symbols to prefetch OHLC for backtests: top `universe_master_pool` names by
    the latest live market-cap proxy snapshot (defaults to 2× universe_top_n).
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
