"""
Current Nifty Smallcap 250 membership for live scans and paper trading.

Constituents: data/smallcap250_constituents.csv (NSE Indices).
Refresh: curl -o data/smallcap250_constituents.csv \\
  https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv

Historical rebalance timelines are not encoded yet; get_yf_constituents() returns
today's index for paper-trading scans. Backtests should add semi-annual events
before relying on point-in-time membership.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd

from nifty50_history import NSE_TO_YF as _N50_MAP
from nifty_midcap_history import NSE_TO_YF as _MIDCAP_MAP

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CONSTITUENTS_CSV = _DATA_DIR / "smallcap250_constituents.csv"

# Yahoo has only 1d/5d for NIFTYSMLCAP250.NS; HDFC Nifty Smallcap 250 ETF is a usable proxy.
SMALLCAP_INDEX_TICKER = "HDFCSML250.NS"
SMALLCAP_INDEX_TICKER_ALT = "NIFTYSMLCAP250.NS"

_EXTRA_NSE_TO_YF = {
    "M&M": "M&M.NS",
    "BAJAJ-AUTO": "BAJAJ-AUTO.NS",
    "NAM-INDIA": "NAM-INDIA.NS",
    "3MINDIA": "3MINDIA.NS",
    "360ONE": "360ONE.NS",
}


def _load_current_nse_symbols() -> set[str]:
    if not _CONSTITUENTS_CSV.exists():
        raise FileNotFoundError(
            f"Missing {_CONSTITUENTS_CSV}. Run: curl -o data/smallcap250_constituents.csv "
            "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv"
        )
    symbols = set()
    with _CONSTITUENTS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            symbols.add(row["Symbol"].strip())
    if len(symbols) != 250:
        raise ValueError(f"Expected 250 smallcap symbols, got {len(symbols)}")
    return symbols


_CURRENT_NSE = _load_current_nse_symbols()

_SKIP_NSE: set[str] = set()


def _build_nse_to_yf() -> dict[str, str]:
    mapping = dict(_N50_MAP)
    mapping.update(_MIDCAP_MAP)
    mapping.update(_EXTRA_NSE_TO_YF)
    for sym in _CURRENT_NSE:
        if sym not in mapping:
            mapping[sym] = f"{sym}.NS"
    return mapping


NSE_TO_YF = _build_nse_to_yf()


def _nse_to_yf(nse_symbols) -> list[str]:
    out = []
    for s in nse_symbols:
        if s in _SKIP_NSE:
            continue
        yf = NSE_TO_YF.get(s)
        if yf:
            out.append(yf)
    return sorted(set(out))


def get_nse_constituents(as_of_date=None) -> frozenset[str]:
    """NSE symbols in Smallcap 250 (current list until rebalance history is added)."""
    _ = as_of_date
    return frozenset(_CURRENT_NSE)


def get_yf_constituents(as_of_date=None) -> list[str]:
    """Yahoo tickers for Smallcap 250 members."""
    return _nse_to_yf(get_nse_constituents(as_of_date))


def current_smallcap_tickers() -> list[str]:
    """Alias for today's Smallcap 250 Yahoo tickers (daily scanner entry point)."""
    return get_yf_constituents(date.today())


def download_constituents_csv(dest: Path | None = None) -> Path:
    """Fetch latest NSE constituent CSV into data/."""
    import urllib.request

    dest = dest or _CONSTITUENTS_CSV
    url = "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())
    global _CURRENT_NSE
    _CURRENT_NSE = _load_current_nse_symbols()
    return dest


if __name__ == "__main__":
    tickers = current_smallcap_tickers()
    print(f"Smallcap 250: {len(tickers)} Yahoo tickers")
    print(f"Index proxy for macro ROC: {SMALLCAP_INDEX_TICKER}")
    print("Sample:", ", ".join(tickers[:8]))
