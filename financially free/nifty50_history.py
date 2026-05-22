"""
Point-in-time Nifty 50 membership from NSE semi-annual rebalances (2017–2025).

Built by rolling back documented inclusion/exclusion events from the current
constituent set. Not a substitute for official NSE files, but avoids survivorship
bias from using today's index for historical backtests.
"""

from __future__ import annotations

import pandas as pd

# NSE symbol -> yfinance ticker
NSE_TO_YF = {
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "INFY": "INFY.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
    "ITC": "ITC.NS",
    "SBIN": "SBIN.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "LT": "LT.NS",
    "AXISBANK": "AXISBANK.NS",
    "ASIANPAINT": "ASIANPAINT.NS",
    "MARUTI": "MARUTI.NS",
    "TITAN": "TITAN.NS",
    "SUNPHARMA": "SUNPHARMA.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "WIPRO": "WIPRO.NS",
    "ULTRACEMCO": "ULTRACEMCO.NS",
    "NESTLEIND": "NESTLEIND.NS",
    "TATASTEEL": "TATASTEEL.NS",
    "POWERGRID": "POWERGRID.NS",
    "NTPC": "NTPC.NS",
    "ONGC": "ONGC.NS",
    "M&M": "M&M.NS",
    "TECHM": "TECHM.NS",
    "HCLTECH": "HCLTECH.NS",
    "ADANIPORTS": "ADANIPORTS.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "TMPV": "TMPV.NS",
    "COALINDIA": "COALINDIA.NS",
    "BPCL": "BPCL.NS",
    "GRASIM": "GRASIM.NS",
    "EICHERMOT": "EICHERMOT.NS",
    "DRREDDY": "DRREDDY.NS",
    "CIPLA": "CIPLA.NS",
    "HEROMOTOCO": "HEROMOTOCO.NS",
    "INDUSINDBK": "INDUSINDBK.NS",
    "HINDALCO": "HINDALCO.NS",
    "DIVISLAB": "DIVISLAB.NS",
    "BRITANNIA": "BRITANNIA.NS",
    "APOLLOHOSP": "APOLLOHOSP.NS",
    "ADANIENT": "ADANIENT.NS",
    "BAJAJFINSV": "BAJAJFINSV.NS",
    "JSWSTEEL": "JSWSTEEL.NS",
    "HDFCLIFE": "HDFCLIFE.NS",
    "SBILIFE": "SBILIFE.NS",
    "TATACONSUM": "TATACONSUM.NS",
    "SHRIRAMFIN": "SHRIRAMFIN.NS",
    "BEL": "BEL.NS",
    "TRENT": "TRENT.NS",
    "JIOFIN": "JIOFIN.NS",
    "ETERNAL": "ETERNAL.NS",
    "INDIGO": "INDIGO.NS",
    "MAXHEALTH": "MAXHEALTH.NS",
    "BAJAJ-AUTO": "BAJAJ-AUTO.NS",
    # Removed / historical members (still needed for backtests)
    "ACC": "ACC.NS",
    "AMBUJACEM": "AMBUJACEM.NS",
    "AUROPHARMA": "AUROPHARMA.NS",
    "BHEL": "BHEL.NS",
    "BOSCHLTD": "BOSCHLTD.NS",
    "GAIL": "GAIL.NS",
    "HINDPETRO": "HINDPETRO.NS",
    "IDEA": "IDEA.NS",
    "INDIABULLS": "IBULHSGFIN.NS",
    "INFRATEL": "INFRATEL.NS",
    "IOC": "IOC.NS",
    "LUPIN": "LUPIN.NS",
    "SHREECEM": "SHREECEM.NS",
    "UPL": "UPL.NS",
    "VEDL": "VEDL.NS",
    "YESBANK": "YESBANK.NS",
    "ZEEL": "ZEEL.NS",
    "BANKBARODA": "BANKBARODA.NS",
    "TATAPOWER": "TATAPOWER.NS",
    "LTIM": "LTIM.NS",
    "HDFC": "HDFC.NS",
}

# Current Nifty 50 (late 2025) in NSE symbols — rollback start point
_CURRENT_NSE = {
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BEL",
    "BHARTIARTL",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "ETERNAL",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "INDIGO",
    "INFY",
    "ITC",
    "JIOFIN",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "MAXHEALTH",
    "NESTLEIND",
    "NTPC",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SHRIRAMFIN",
    "SBIN",
    "SUNPHARMA",
    "TCS",
    "TATACONSUM",
    "TMPV",
    "TATASTEEL",
    "TECHM",
    "TITAN",
    "TRENT",
    "ULTRACEMCO",
    "WIPRO",
}

# (effective_date, added NSE symbols, removed NSE symbols) — forward application
_REBALANCES = [
    ("2017-03-31", {"INDIABULLS", "IOC"}, {"BHEL", "IDEA"}),
    ("2017-05-26", {"VEDL"}, {"GRASIM"}),
    ("2017-09-29", {"BAJFINANCE", "HINDPETRO", "UPL"}, {"ACC", "BANKBARODA", "TATAPOWER"}),
    ("2018-04-02", {"BAJAJFINSV", "GRASIM", "TITAN"}, {"AMBUJACEM", "AUROPHARMA", "BOSCHLTD"}),
    ("2018-09-28", {"JSWSTEEL"}, {"LUPIN"}),
    ("2019-03-29", {"BRITANNIA"}, {"HINDPETRO"}),
    ("2019-09-27", {"NESTLEIND"}, {"INDIABULLS"}),
    ("2020-03-19", {"SHREECEM"}, {"YESBANK"}),
    ("2020-07-31", {"HDFCLIFE"}, {"VEDL"}),
    ("2020-09-25", {"SBILIFE", "DIVISLAB"}, {"ZEEL", "INFRATEL"}),
    ("2021-03-31", {"TATACONSUM"}, {"GAIL"}),
    ("2022-03-31", {"APOLLOHOSP"}, {"IOC"}),
    ("2022-09-30", {"ADANIENT"}, {"SHREECEM"}),
    ("2023-03-31", {"HDFC"}, {"LTIM"}),
    ("2023-09-30", {"TRENT", "BEL"}, {"HDFC", "DIVISLAB"}),
    ("2024-03-28", {"SHRIRAMFIN"}, {"UPL"}),
    ("2025-03-28", {"JIOFIN", "ETERNAL"}, {"BPCL", "BRITANNIA"}),
    ("2025-09-30", {"INDIGO", "MAXHEALTH"}, {"HEROMOTOCO", "INDUSINDBK"}),
]


def _build_membership_timeline():
    """Forward-build {effective_date: frozenset NSE symbols} from 2017."""
    members = set(_CURRENT_NSE)
    # Roll back all events to get pre-2017 state, then replay forward
    for _date, added, removed in reversed(_REBALANCES):
        members -= added
        members |= removed

    timeline = {pd.Timestamp("2016-01-01"): frozenset(members)}
    current = set(members)
    for date_str, added, removed in _REBALANCES:
        current -= removed
        current |= added
        timeline[pd.Timestamp(date_str)] = frozenset(current)
    return timeline


_MEMBERSHIP_TIMELINE = _build_membership_timeline()


def _nse_to_yf(nse_symbols):
    out = []
    for s in nse_symbols:
        yf = NSE_TO_YF.get(s)
        if yf:
            out.append(yf)
    return sorted(set(out))


def get_nse_constituents(as_of_date) -> frozenset:
    """Nifty 50 NSE symbols valid on as_of_date (last rebalance on or before)."""
    dt = pd.Timestamp(as_of_date)
    effective = pd.Timestamp("2016-01-01")
    for rebalance_date in sorted(_MEMBERSHIP_TIMELINE.keys()):
        if rebalance_date <= dt:
            effective = rebalance_date
    return _MEMBERSHIP_TIMELINE[effective]


def get_yf_constituents(as_of_date) -> list:
    return _nse_to_yf(get_nse_constituents(as_of_date))


def get_yf_constituents_for_year(year: int) -> list:
    """Membership as of 1 Jan that year (after prior year's Sep rebalance)."""
    return get_yf_constituents(f"{year}-01-01")


def all_yf_tickers_between(start_date, end_date) -> list:
    """Union of every stock that was in Nifty 50 at any point in the range."""
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    symbols = set()
    for rebalance_date, nse_set in _MEMBERSHIP_TIMELINE.items():
        if rebalance_date <= end:
            symbols |= nse_set
    # Also include state at end
    symbols |= get_nse_constituents(end)
    return _nse_to_yf(symbols)


def yearly_universe_report(start_year: int, end_year: int) -> pd.DataFrame:
    rows = []
    for y in range(start_year, end_year + 1):
        nse = get_nse_constituents(f"{y}-01-01")
        rows.append({"year": y, "count": len(nse), "sample": ", ".join(sorted(nse)[:5])})
    return pd.DataFrame(rows)
