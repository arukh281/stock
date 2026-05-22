"""Quarterly fundamental universe filter with PIT membership."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from kali.config import load_config, project_root
from kali.data.screener import FundamentalsSnapshot, ScreenerClient

logger = logging.getLogger(__name__)


def load_nifty150_symbols(cfg: dict | None = None) -> list[str]:
    """Load symbol list from PIT file or default seed list."""
    cfg = cfg or load_config()
    pit_path = project_root() / cfg["data"]["pit_membership_file"]
    if pit_path.exists():
        df = pd.read_csv(pit_path, parse_dates=["effective_from", "effective_to"])
        today = pd.Timestamp(date.today())
        eff_to = df["effective_to"]
        active = df[
            (df["effective_from"] <= today)
            & (eff_to.isna() | (eff_to >= today))
        ]
        return sorted(active["symbol"].str.upper().unique().tolist())
    return ["ITC", "RELIANCE", "HDFCBANK", "INFY", "TCS"]


def load_pit_membership(cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    path = project_root() / cfg["data"]["pit_membership_file"]
    if not path.exists():
        import warnings

        warnings.warn(f"PIT membership file missing: {path}", stacklevel=2)
        return pd.DataFrame(columns=["symbol", "effective_from", "effective_to"])
    return pd.read_csv(path, parse_dates=["effective_from", "effective_to"])


def rebalance_dates(
    start: date, end: date, months: list[int] | None = None
) -> list[date]:
    months = months or [1, 4, 7, 10]
    dates = []
    y = start.year
    while date(y, 1, 1) <= end:
        for m in months:
            d = date(y, m, 1)
            if start <= d <= end:
                dates.append(d)
        y += 1
    return dates


def filter_universe_at_date(
    snapshots: dict[str, FundamentalsSnapshot],
    as_of: date,
    cfg: dict | None = None,
) -> list[str]:
    cfg = cfg or load_config()
    pit = load_pit_membership(cfg)
    passed = []
    for sym, snap in snapshots.items():
        if snap.as_of > as_of:
            continue
        if not pit.empty:
            sym_pit = pit[pit["symbol"].str.upper() == sym.upper()]
            if not sym_pit.empty:
                row = sym_pit.iloc[0]
                eff_from = pd.Timestamp(row["effective_from"]).date()
                eff_to = row["effective_to"]
                if pd.notna(eff_to):
                    eff_to = pd.Timestamp(eff_to).date()
                    if as_of < eff_from or (eff_to and as_of > eff_to):
                        continue
                elif as_of < eff_from:
                    continue
        if snap.passes_filters(cfg):
            passed.append(sym.upper())
    return passed


def build_universe_mask(
    symbols: list[str],
    trading_index: pd.DatetimeIndex,
    snapshots: dict[str, FundamentalsSnapshot] | None = None,
    client: ScreenerClient | None = None,
    cfg: dict | None = None,
) -> pd.DataFrame:
    """Boolean mask: rows=dates, cols=symbols."""
    cfg = cfg or load_config()
    client = client or ScreenerClient(cfg)
    snapshots = snapshots or {s: client.fetch(s) for s in symbols}

    rebal = rebalance_dates(
        trading_index.min().date(),
        trading_index.max().date(),
        cfg["universe"]["rebalance_months"],
    )

    mask = pd.DataFrame(False, index=trading_index, columns=[s.upper() for s in symbols])
    for i, rd in enumerate(rebal):
        end = rebal[i + 1] if i + 1 < len(rebal) else trading_index.max().date()
        active = filter_universe_at_date(snapshots, rd, cfg)
        period_idx = trading_index[
            (trading_index >= pd.Timestamp(rd)) & (trading_index < pd.Timestamp(end))
        ]
        for sym in active:
            if sym in mask.columns:
                mask.loc[period_idx, sym] = True
    return mask


def is_fundamentally_approved(
    symbol: str, as_of: pd.Timestamp, cfg: dict | None = None
) -> bool:
    """
    Mock fundamental gate until historical fundamentals DB exists.

    TODO: Wire to historical fundamentals DB (ROE, D/E, Piotroski, FCF yield).
    """
    cfg = cfg or load_config()
    pit = load_pit_membership(cfg)
    if pit.empty:
        return True
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    rows = pit[pit["symbol"].str.upper() == sym]
    if rows.empty:
        return False
    row = rows.iloc[0]
    if pd.Timestamp(row["effective_from"]) > as_of:
        return False
    eff_to = row["effective_to"]
    if pd.notna(eff_to) and pd.Timestamp(eff_to) < as_of:
        return False
    return True


def apply_fundamental_mask(
    df: pd.DataFrame, symbol: str, cfg: dict | None = None
) -> pd.DataFrame:
    """Attach per-bar fundamental approval flag (PIT membership proxy)."""
    out = df.copy()
    out["is_fundamentally_approved"] = [
        is_fundamentally_approved(symbol, dt, cfg) for dt in out.index
    ]
    return out


def should_refresh_screener(as_of: date, cfg: dict | None = None) -> bool:
    """Force live Screener fetch in the first week of rebalance months (Jan/Apr/Jul/Oct)."""
    cfg = cfg or load_config()
    months = cfg["universe"]["rebalance_months"]
    return as_of.month in months and as_of.day <= 5


def fetch_fundamental_snapshots(
    symbols: list[str],
    client: ScreenerClient | None = None,
    cfg: dict | None = None,
    force: bool = False,
) -> tuple[dict[str, FundamentalsSnapshot], dict[str, str]]:
    """Fetch Screener.in snapshots; on forced refresh failure, fall back to cache."""
    cfg = cfg or load_config()
    client = client or ScreenerClient(cfg)
    snapshots: dict[str, FundamentalsSnapshot] = {}
    errors: dict[str, str] = {}

    for sym in symbols:
        base = sym.upper().replace(".NS", "").replace(".BO", "")
        try:
            snapshots[base] = client.fetch(base, force=force)
        except Exception as exc:
            if force:
                try:
                    snapshots[base] = client.fetch(base, force=False)
                    logger.warning(
                        "Screener live fetch failed for %s; using cache (%s)",
                        base,
                        exc,
                    )
                    continue
                except Exception as fallback_exc:
                    errors[base] = str(fallback_exc)
                    continue
            errors[base] = str(exc)
    return snapshots, errors


def fundamental_screen_report(
    symbols: list[str],
    snapshots: dict[str, FundamentalsSnapshot],
    errors: dict[str, str],
    as_of: date,
    cfg: dict | None = None,
) -> pd.DataFrame:
    """Per-symbol screening status for logging and audit CSV."""
    cfg = cfg or load_config()
    active = set(filter_universe_at_date(snapshots, as_of, cfg))
    rows = []
    for sym in symbols:
        base = sym.upper().replace(".NS", "").replace(".BO", "")
        if base in errors:
            rows.append(
                {
                    "symbol": base,
                    "status": "ERROR",
                    "passes_filters": False,
                    "in_universe": False,
                    "roe_pct": None,
                    "piotroski_f_score": None,
                    "error": errors[base],
                }
            )
            continue
        snap = snapshots.get(base)
        if snap is None:
            rows.append(
                {
                    "symbol": base,
                    "status": "MISSING",
                    "passes_filters": False,
                    "in_universe": False,
                    "roe_pct": None,
                    "piotroski_f_score": None,
                    "error": "no snapshot",
                }
            )
            continue
        passes = snap.passes_filters(cfg)
        in_pit = is_fundamentally_approved(base, pd.Timestamp(as_of), cfg)
        in_universe = base in active
        status = "PASS" if in_universe else ("FAIL_FILTERS" if in_pit and not passes else "FAIL_PIT")
        rows.append(
            {
                "symbol": base,
                "status": status,
                "passes_filters": passes,
                "in_universe": in_universe,
                "roe_pct": snap.roe_pct,
                "piotroski_f_score": snap.piotroski_f_score,
                "error": "",
            }
        )
    return pd.DataFrame(rows)


def resolve_fundamental_universe(
    symbols: list[str],
    as_of: date | None = None,
    cfg: dict | None = None,
    client: ScreenerClient | None = None,
    force_screener: bool | None = None,
) -> tuple[list[str], dict[str, FundamentalsSnapshot], pd.DataFrame]:
    """
    Apply PIT membership + Screener.in fundamental filters for live/daily use.

    Returns (active_symbols, snapshots, audit_report).
    """
    cfg = cfg or load_config()
    as_of = as_of or date.today()
    if force_screener is None:
        force_screener = should_refresh_screener(as_of, cfg)

    normalized = sorted(
        {s.upper().replace(".NS", "").replace(".BO", "") for s in symbols}
    )
    snapshots, errors = fetch_fundamental_snapshots(
        normalized, client=client, cfg=cfg, force=force_screener
    )
    active = filter_universe_at_date(snapshots, as_of, cfg)
    report = fundamental_screen_report(normalized, snapshots, errors, as_of, cfg)
    return active, snapshots, report
