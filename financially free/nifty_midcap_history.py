"""
Point-in-time Nifty Midcap 150 membership from NSE semi-annual rebalances (2017–2025).

Current constituents: data/midcap150_constituents.csv (NSE Indices CSV).
Rebalance in/out lists: NSE Indices press releases (nsearchives.nseindia.com), except
Mar 2023 which uses the Feb 17, 2023 equity notification (debt-only PDF on archive).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from nifty50_history import NSE_TO_YF as _N50_MAP

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CONSTITUENTS_CSV = _DATA_DIR / "midcap150_constituents.csv"

# Extra NSE symbol -> Yahoo overrides (beyond Nifty 50 map)
_EXTRA_NSE_TO_YF = {
    "M&M": "M&M.NS",
    "L&TFH": "LTFH.NS",
    # GVT&D — no reliable Yahoo ticker; excluded from downloads
    "NAM-INDIA": "NAM-INDIA.NS",
    "BAJAJ-AUTO": "BAJAJ-AUTO.NS",
    "MOTHERSON": "MOTHERSON.NS",
    "TMPV": "TMPV.NS",
    "MANYAVAR": "MANYAVAR.NS",
    "POONAWALLA": "POONAWALLA.NS",
    "JUBLFOOD": "JUBLFOOD.NS",
    "3MINDIA": "3MINDIA.NS",
    "360ONE": "360ONE.NS",
}


def _load_current_nse_symbols() -> set[str]:
    if not _CONSTITUENTS_CSV.exists():
        raise FileNotFoundError(
            f"Missing {_CONSTITUENTS_CSV}. Run: curl -o data/midcap150_constituents.csv "
            "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv"
        )
    symbols = set()
    with _CONSTITUENTS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            symbols.add(row["Symbol"].strip())
    if len(symbols) != 150:
        raise ValueError(f"Expected 150 midcap symbols, got {len(symbols)}")
    return symbols


_CURRENT_NSE = _load_current_nse_symbols()

# (effective_date, added NSE symbols, removed NSE symbols)
_REBALANCES = [
    # ind_prs01092022.pdf
    (
        "2022-09-30",
        {
            "AWL",
            "DEVYANI",
            "JUBLFOOD",
            "KPRMILL",
            "LUPIN",
            "MSUMI",
            "PATANJALI",
            "POONAWALLA",
            "PNB",
            "SAIL",
            "MANYAVAR",
            "ZYDUSLIFE",
        },
        {
            "ATGL",
            "APOLLOTYRE",
            "BEL",
            "EXIDEIND",
            "GLENMARK",
            "HAL",
            "IDBI",
            "IRCTC",
            "MANAPPURAM",
            "METROPOLIS",
            "MPHASIS",
            "NUVOCO",
        },
    ),
    # Feb 17, 2023 notification — effective Mar 31, 2023 (NSE broad index release / ET)
    (
        "2023-03-31",
        {"ADANIPOWER", "BANDHANBNK", "BIOCON", "MPHASIS", "NMDC"},
        {"ABB", "CANBK", "GLAND", "PAGEIND", "TORNTPOWER"},
    ),
    # ind_prs17082023.pdf
    (
        "2023-09-30",
        {
            "ACC",
            "BDL",
            "CARBORUNIV",
            "FACT",
            "HDFCAMC",
            "INDUSTOWER",
            "JSL",
            "KPITTECH",
            "MAHABANK",
            "MAZDOCK",
            "NYKAA",
            "PAGEIND",
            "RVNL",
        },
        {
            "AAVAS",
            "AFFLE",
            "ALKYLAMINE",
            "CLEAN",
            "FINEORG",
            "HAPPSTMNDS",
            "NAM-INDIA",
            "PNB",
            "SHRIRAMFIN",
            "TRENT",
            "TTML",
            "TVSMOTOR",
            "ZYDUSLIFE",
        },
    ),
    # ind_prs28022024.pdf
    (
        "2024-03-28",
        {
            "AWL",
            "IDBI",
            "IREDA",
            "JSWINFRA",
            "KALYANKJIL",
            "KEI",
            "LLOYDSME",
            "MUTHOOTFIN",
            "PGHH",
            "PIIND",
            "SJVN",
            "SUZLON",
            "TATATECH",
            "UPL",
        },
        {
            "AARTIIND",
            "ADANIPOWER",
            "BLUEDART",
            "CROMPTON",
            "IRFC",
            "NAVINFLUOR",
            "PFC",
            "PFIZER",
            "RAJESHEXPO",
            "RECLTD",
            "RELAXO",
            "TRIDENT",
            "VINATIORGA",
            "WHIRLPOOL",
        },
    ),
    # ind_prs23082024.pdf
    (
        "2024-09-30",
        {
            "BERGEPAINT",
            "BHARTIHEXA",
            "CENTRALBK",
            "COCHINSHIP",
            "COLPAL",
            "EXIDEIND",
            "HUDCO",
            "IOB",
            "IRB",
            "IREDA",
            "MARICO",
            "MEDANTA",
            "MRPL",
            "NAM-INDIA",
            "NLCINDIA",
            "POWERINDIA",
            "SBICARD",
            "SRF",
            "TATAINVEST",
        },
        {
            "ATUL",
            "BATAINDIA",
            "BHEL",
            "DEVYANI",
            "IDEA",
            "ISEC",
            "JSWENERGY",
            "KAJARIACER",
            "KANSAINER",
            "LALPATHLAB",
            "LAURUSLABS",
            "LODHA",
            "MANYAVAR",
            "NHPC",
            "PEL",
            "RAMCOCEM",
            "SUMICHEM",
            "UNIONBANK",
            "ZEEL",
        },
    ),
    # ind_prs21022025.pdf — effective Mar 28, 2025
    (
        "2025-03-28",
        {
            "APARINDS",
            "ATGL",
            "BHEL",
            "BLUESTARCO",
            "GLENMARK",
            "GVT&D",
            "IRCTC",
            "MOTILALOFS",
            "NATIONALUM",
            "NHPC",
            "NTPCGREEN",
            "OLAELEC",
            "PREMIERENE",
            "UNIONBANK",
            "VMM",
            "WAAREEENER",
        },
        {
            "BAYERCROP",
            "CARBORUNIV",
            "CGPOWER",
            "DELHIVERY",
            "FACT",
            "GRINDWELL",
            "IDBI",
            "INDHOTEL",
            "IOB",
            "METROBRAND",
            "PGHH",
            "POONAWALLA",
            "SKFINDIA",
            "SUNDRMFAST",
            "TATACHEM",
            "TIMKEN",
            "ZFCVINDIA",
        },
    ),
    # ind_prs22082025.pdf — effective Sep 30, 2025
    (
        "2025-09-30",
        {
            "DABUR",
            "FACT",
            "GODFRYPHLP",
            "HEROMOTOCO",
            "HEXT",
            "ICICIPRULI",
            "IDBI",
            "INDUSINDBK",
            "IOB",
            "ITCHOTELS",
            "PGHH",
            "SWIGGY",
            "UCOBANK",
        },
        {
            "ABFRL",
            "BANDHANBNK",
            "EMAMILTD",
            "GLAND",
            "HINDZINC",
            "MAXHEALTH",
            "MAZDOCK",
            "MRPL",
            "MSUMI",
            "OLAELEC",
            "SOLARINDS",
            "STARHEALTH",
            "SUNTV",
        },
    ),
]


def _build_nse_to_yf() -> dict[str, str]:
    mapping = dict(_N50_MAP)
    mapping.update(_EXTRA_NSE_TO_YF)
    for sym in _CURRENT_NSE:
        if sym not in mapping:
            mapping[sym] = f"{sym}.NS"
    # Ensure all rebalance symbols map
    for _date, added, removed in _REBALANCES:
        for sym in added | removed:
            if sym not in mapping:
                mapping[sym] = f"{sym}.NS"
    return mapping


NSE_TO_YF = _build_nse_to_yf()


def _build_membership_timeline():
    members = set(_CURRENT_NSE)
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


# Symbols with no working Yahoo mapping (skip to avoid hung/failed bulk downloads)
_SKIP_NSE = {"GVT&D"}


def _nse_to_yf(nse_symbols):
    out = []
    for s in nse_symbols:
        if s in _SKIP_NSE:
            continue
        yf = NSE_TO_YF.get(s)
        if yf:
            out.append(yf)
    return sorted(set(out))


def get_nse_constituents(as_of_date) -> frozenset:
    """Midcap 150 NSE symbols valid on as_of_date (last rebalance on or before)."""
    dt = pd.Timestamp(as_of_date)
    effective = pd.Timestamp("2016-01-01")
    for rebalance_date in sorted(_MEMBERSHIP_TIMELINE.keys()):
        if rebalance_date <= dt:
            effective = rebalance_date
    return _MEMBERSHIP_TIMELINE[effective]


def get_yf_constituents(as_of_date) -> list:
    return _nse_to_yf(get_nse_constituents(as_of_date))


def get_yf_constituents_for_year(year: int) -> list:
    return get_yf_constituents(f"{year}-01-01")


def all_yf_tickers_between(start_date, end_date) -> list:
    """Union of every stock that was in Midcap 150 at any point in the range."""
    end = pd.Timestamp(end_date)
    symbols = set()
    for rebalance_date, nse_set in _MEMBERSHIP_TIMELINE.items():
        if rebalance_date <= end:
            symbols |= nse_set
    symbols |= get_nse_constituents(end)
    return _nse_to_yf(symbols)


def yearly_universe_report(start_year: int, end_year: int) -> pd.DataFrame:
    rows = []
    for y in range(start_year, end_year + 1):
        nse = get_nse_constituents(f"{y}-01-01")
        rows.append({"year": y, "count": len(nse), "sample": ", ".join(sorted(nse)[:5])})
    return pd.DataFrame(rows)


def rebalance_summary() -> pd.DataFrame:
    rows = []
    for date_str, added, removed in _REBALANCES:
        rows.append(
            {
                "effective_date": date_str,
                "added": len(added),
                "removed": len(removed),
            }
        )
    return pd.DataFrame(rows)
