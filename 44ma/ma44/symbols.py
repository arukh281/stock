from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from ma44.config import Settings
from ma44.data import fetch_daily
from ma44.strategy import add_indicators, last_bar_positive_slope, signal_bar_confidence, signal_mask
from ma44.universe_mcap import master_pool_symbols, top_symbols_by_live_ffmc


def normalize_yahoo_symbol(raw: str) -> str:
    s = raw.strip().upper()
    if not s or s.startswith("#"):
        return ""
    if "." in s:
        return s
    if s.startswith("^"):
        return s
    return f"{s}.NS"


def load_universe_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        sym = normalize_yahoo_symbol(line)
        if sym:
            out.append(sym)
    return list(dict.fromkeys(out))


def _universe_file_path(settings: Settings) -> Path | None:
    uf = getattr(settings, "universe_file", None)
    if not uf:
        return None
    path = Path(uf)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def load_backtest_universe(settings: Settings) -> list[str]:
    """
    Symbols to prefetch OHLC for portfolio backtests.

    Default: master pool (~2× top-N) by latest NSE ffmc so historical top-100
    membership can rotate. Per-day top-N is enforced inside the backtest via
  `build_daily_mcap_membership`. Optional `universe_file` overrides entirely.
    """
    path = _universe_file_path(settings)
    if path:
        return load_universe_lines(path)
    return master_pool_symbols(settings)


def _fetch_work(sym: str, settings: Settings) -> pd.DataFrame | None:
    start = str(getattr(settings, "universe_history_start", "2018-01-01"))
    df = fetch_daily(sym, start=start)
    need = settings.sma_period + max(
        int(settings.sma_rising_lookback),
        int(getattr(settings, "sma_monotone_days", 0) or 0),
    ) + 2
    if df.empty or len(df) < need:
        return None
    return add_indicators(df, settings)


def live_top_universe(settings: Settings) -> list[str]:
    """Today's top-N by NSE free-float market cap (cached per calendar day)."""
    path = _universe_file_path(settings)
    if path:
        return load_universe_lines(path)
    return top_symbols_by_live_ffmc(settings)


def resolve_trade_symbols(settings: Settings, db_symbols: set[str] | None = None) -> list[str]:
    """
    Live daily / scan universe: top-N by market cap with positive SMA slope on the
    latest bar. Always includes `db_symbols` (open positions / pendings).
    """
    extra = set(db_symbols or ())
    base = live_top_universe(settings)
    sleep_s = float(getattr(settings, "universe_fetch_sleep_sec", 0.2) or 0)
    picked: list[str] = []
    for i, sym in enumerate(base):
        if sleep_s > 0 and i > 0:
            time.sleep(sleep_s)
        work = _fetch_work(sym, settings)
        if work is not None and last_bar_positive_slope(work, settings):
            picked.append(sym)
    return sorted(dict.fromkeys(extra | set(picked)))


def resolve_daily_processing_order(
    settings: Settings,
    symbols: list[str],
    *,
    position_symbols: set[str],
    pending_confidence: dict[str, float],
) -> list[str]:
    """
    Order symbols for one EOD pass: manage open positions first, then pending
    breakouts, then the rest — each tier by descending signal confidence.
    """
    sleep_s = float(getattr(settings, "universe_fetch_sleep_sec", 0.2) or 0)
    hist_start = str(getattr(settings, "universe_history_start", "2018-01-01"))
    scored: list[tuple[int, float, str]] = []

    for i, sym in enumerate(symbols):
        if sleep_s > 0 and i > 0:
            time.sleep(sleep_s)
        if sym in position_symbols:
            tier, conf = 0, 0.0
        elif sym in pending_confidence:
            tier, conf = 1, float(pending_confidence[sym])
        else:
            tier = 2
            conf = 0.0
            work = _fetch_work(sym, settings)
            if work is not None:
                sig = signal_mask(work, settings)
                if bool(sig.iloc[-1]):
                    conf = signal_bar_confidence(work, len(work) - 1, settings)
        scored.append((tier, -conf, sym))

    scored.sort()
    return [sym for _, _, sym in scored]


def scan_universe_positive_slope(settings: Settings) -> list[dict]:
    """One row per ticker in today's top-N universe (pass / fail + last bar stats)."""
    base = live_top_universe(settings)
    sleep_s = float(getattr(settings, "universe_fetch_sleep_sec", 0.2) or 0)
    rows: list[dict] = []
    for i, sym in enumerate(base):
        if sleep_s > 0 and i > 0:
            time.sleep(sleep_s)
        work = _fetch_work(sym, settings)
        if work is None:
            rows.append({"symbol": sym, "ok": False, "reason": "no_data"})
            continue
        ok = last_bar_positive_slope(work, settings)
        row = work.iloc[-1]
        L = int(settings.sma_rising_lookback)
        sma_now = float(row["sma"]) if pd.notna(row["sma"]) else float("nan")
        sma_prev = (
            float(work["sma"].iloc[-1 - L])
            if len(work) > L and pd.notna(work["sma"].iloc[-1 - L])
            else float("nan")
        )
        slope_l = sma_now - sma_prev if pd.notna(sma_now) and pd.notna(sma_prev) else float("nan")
        rec: dict = {
            "symbol": sym,
            "ok": ok,
            "close": float(row["close"]),
            "sma44": sma_now,
            "sma_slope_vs_L": slope_l,
            "asof": str(work.index[-1].date()),
        }
        if ok and bool(signal_mask(work, settings).iloc[-1]):
                rec["confidence"] = signal_bar_confidence(work, len(work) - 1, settings)
        if not ok:
            rec["reason"] = "sma_slope_rules"
        rows.append(rec)
    return rows
