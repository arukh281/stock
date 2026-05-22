#!/usr/bin/env python3
"""
Generate tomorrow's AMO action plan from today's EOD close.

Intended cron: daily ~15:35 IST (after NSE cash close).
Uses live EOD via yfinance (~250 trading days) — no T+1 shift on entry flags
(signals are for next-session AMO placement).

Universe: PIT seed list -> Screener.in fundamental filter (ROE, D/E, EPS CAGR,
Piotroski, FCF yield). Live Screener refresh runs in the first week of rebalance
months (Jan/Apr/Jul/Oct); otherwise cached snapshots are used (~1 req/s on miss).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kali.config import load_config, project_root
from kali.data.ohlcv import download_ohlcv
from kali.data.universe import (
    load_nifty150_symbols,
    resolve_fundamental_universe,
    should_refresh_screener,
)
from kali.features.pipeline import build_features, build_weekly_features
from kali.regime.classifier import classify_regime
from kali.risk.sizing import atr_position_size
from kali.signals.entries import attach_stop_target, long_entry_signal
from kali.signals.mtf_gate import attach_mtf_columns
from kali.validation.integrity import tag_unexecutable

DEFAULT_PORTFOLIO = 1_000_000
DEFAULT_LOOKBACK_CALENDAR_DAYS = 400  # ~250+ trading bars for HMM(252) + Hurst(60)


def load_universe_symbols(universe_path: Path | None, cfg: dict) -> list[str]:
    if universe_path and universe_path.exists():
        df = pd.read_csv(universe_path)
        col = "symbol" if "symbol" in df.columns else df.columns[0]
        return sorted(df[col].astype(str).str.upper().str.replace(".NS", "", regex=False).unique())
    return load_nifty150_symbols(cfg)


def _needs_refresh(ohlcv: pd.DataFrame, start: str, min_bars: int) -> bool:
    """Re-fetch when cache is too short, starts too late, or stale vs today."""
    if ohlcv.empty or len(ohlcv) < min_bars + 50:
        return True
    if ohlcv.index.min() > pd.Timestamp(start):
        return True
    last_bar = ohlcv.index.max()
    stale_days = (pd.Timestamp.today().normalize() - last_bar).days
    return stale_days > 5


def prepare_live_features(
    symbol: str,
    start: str,
    end: str | None,
    cfg: dict,
    force_download: bool,
) -> pd.DataFrame | None:
    """Build feature frame for one symbol; returns None if history is insufficient."""
    min_bars = cfg["features"]["min_history_bars"]
    try:
        from kali.data.ohlcv import load_ohlcv

        if force_download:
            ohlcv = download_ohlcv(symbol, start=start, end=end, cfg=cfg)
        else:
            ohlcv = load_ohlcv(symbol, cfg)
            if _needs_refresh(ohlcv, start, min_bars):
                ohlcv = download_ohlcv(symbol, start=start, end=end, cfg=cfg)
    except (ValueError, OSError) as exc:
        print(f"  SKIP {symbol}: {exc}")
        return None

    ohlcv = ohlcv.sort_index().loc[pd.Timestamp(start) :]
    if end:
        ohlcv = ohlcv.loc[: pd.Timestamp(end)]
    if len(ohlcv) < min_bars:
        print(f"  SKIP {symbol}: only {len(ohlcv)} bars (need {min_bars})")
        return None

    df = build_features(ohlcv, cfg)
    weekly = build_weekly_features(ohlcv, cfg)
    df = attach_mtf_columns(df, weekly, cfg)
    df = classify_regime(df, cfg)
    df["long_entry_signal"] = long_entry_signal(df, cfg)
    df = attach_stop_target(df, cfg)
    df["unexecutable"] = tag_unexecutable(df, cfg["backtest"]["circuit_limit_default"])
    return df.iloc[min_bars:]


def _cms_score(row: pd.Series) -> float:
    v = row.get("cms", np.nan)
    return float(v) if pd.notna(v) else -np.inf


def aggregate_market_regime(regimes: list[str]) -> str:
    if not regimes:
        return "UNKNOWN"
    counts = pd.Series(regimes).value_counts()
    return str(counts.index[0])


def check_open_positions(
    positions_path: Path | None,
    feature_map: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """
    Position-level trailing stop (matches portfolio sim):
      trailing = max(highest_high_since_entry - 3*ATR, initial_stop)
      breach if today's close < trailing
    """
    if positions_path is None or not positions_path.exists():
        return pd.DataFrame()

    pos_df = pd.read_csv(positions_path)
    required = {"symbol", "entry_price", "entry_atr", "initial_stop"}
    if not required.issubset(pos_df.columns):
        print(
            f"  WARNING: {positions_path} missing columns {required - set(pos_df.columns)}; "
            "skipping open-position checks"
        )
        return pd.DataFrame()

    rows = []
    for _, pos in pos_df.iterrows():
        sym = str(pos["symbol"]).upper().replace(".NS", "")
        if sym not in feature_map:
            rows.append({"symbol": sym, "status": "NO_DATA"})
            continue
        df = feature_map[sym]
        if as_of not in df.index:
            bar = df.iloc[-1]
            bar_date = df.index[-1]
        else:
            bar = df.loc[as_of]
            bar_date = as_of

        close = float(bar["close"])
        atr = float(bar.get("atr_14", pos["entry_atr"]))
        high = float(bar["high"])
        entry_high = float(pos.get("highest_high_since_entry", pos["entry_price"]))
        if high > entry_high:
            entry_high = high
        initial_stop = float(pos["initial_stop"])
        trailing = max(entry_high - 3.0 * atr, initial_stop)
        breached = close < trailing
        rows.append(
            {
                "symbol": sym,
                "as_of": bar_date.date(),
                "close": round(close, 2),
                "trailing_stop": round(trailing, 2),
                "initial_stop": round(initial_stop, 2),
                "highest_high": round(entry_high, 2),
                "atr_14": round(atr, 2),
                "breach": breached,
                "action": "EXIT_AMO" if breached else "HOLD",
            }
        )
    return pd.DataFrame(rows)


def build_action_plan(
    symbols: list[str],
    start: str,
    end: str | None,
    cfg: dict,
    portfolio_equity: float,
    force_download: bool,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.Timestamp]:
    feature_map: dict[str, pd.DataFrame] = {}
    signal_date: pd.Timestamp | None = None

    for sym in symbols:
        print(f"Processing {sym}...")
        df = prepare_live_features(sym, start, end, cfg, force_download)
        if df is None or df.empty:
            continue
        base = sym.replace(".NS", "").upper()
        feature_map[base] = df
        if signal_date is None or df.index[-1] > signal_date:
            signal_date = df.index[-1]

    if not feature_map or signal_date is None:
        raise RuntimeError("No symbols produced valid feature histories")

    buy_rows = []
    regimes = []
    for sym, df in feature_map.items():
        if signal_date not in df.index:
            row = df.iloc[-1]
            bar_date = df.index[-1]
        else:
            row = df.loc[signal_date]
            bar_date = signal_date

        regimes.append(str(row.get("regime_active", "UNKNOWN")))
        if not bool(row.get("long_entry_signal", False)):
            continue
        if bool(row.get("unexecutable", False)):
            continue
        if row.get("regime_active") in ("BEAR_TREND", "DISTRIBUTION"):
            continue
        if bool(row.get("regime_risk_off", False)):
            continue

        entry = float(row["close"])
        stop = float(row["stop_loss"])
        target = float(row["take_profit"])
        shares = atr_position_size(
            portfolio_equity,
            entry,
            stop,
            risk_pct=cfg["risk"]["risk_per_trade_pct"],
            kelly_frac=1.0,
        )
        buy_rows.append(
            {
                "signal_date": bar_date.date(),
                "ticker": sym,
                "entry_price": round(entry, 2),
                "stop_loss": round(stop, 2),
                "target": round(target, 2),
                "cms_score": round(_cms_score(row), 4),
                "regime": row.get("regime_active"),
                "daily_alignment": bool(row.get("daily_alignment", False)),
                "recommended_shares": shares,
                "notional": round(shares * entry, 0),
            }
        )

    plan = pd.DataFrame(buy_rows)
    if not plan.empty:
        plan = plan.sort_values("cms_score", ascending=False).reset_index(drop=True)
    return plan, feature_map, signal_date


def main() -> None:
    parser = argparse.ArgumentParser(description="KALI daily AMO signal generator")
    parser.add_argument(
        "--universe",
        type=str,
        default="",
        help="CSV with symbol column (default: config pit_nifty150.csv)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/cache/live/daily_action_plan.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_CALENDAR_DAYS,
        help="Calendar days of OHLCV to fetch (need ~252+ for HMM)",
    )
    parser.add_argument(
        "--portfolio",
        type=float,
        default=DEFAULT_PORTFOLIO,
        help="Theoretical portfolio equity for share sizing (INR)",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Refresh yfinance cache for all symbols",
    )
    parser.add_argument(
        "--positions",
        type=str,
        default="",
        help="Optional CSV of open positions for trailing-stop breach check",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="Skip Screener.in filter (PIT list only)",
    )
    parser.add_argument(
        "--force-screener",
        action="store_true",
        help="Force live Screener.in fetch for all symbols (ignores cache)",
    )
    args = parser.parse_args()
    cfg = load_config()
    as_of = date.today()

    universe_path = Path(args.universe) if args.universe else None
    candidate_symbols = load_universe_symbols(universe_path, cfg)
    end = as_of.isoformat()
    start = (as_of - timedelta(days=args.lookback_days)).isoformat()

    screen_path = project_root() / "data/cache/live/fundamental_screen_latest.csv"
    if args.skip_fundamentals:
        symbols = candidate_symbols
        print(
            f"Universe: {len(symbols)} symbols (PIT only, --skip-fundamentals) | "
            f"OHLCV: {start} -> {end}"
        )
    else:
        auto_refresh = should_refresh_screener(as_of, cfg)
        force_screener = args.force_screener or auto_refresh
        print(
            f"Screening {len(candidate_symbols)} candidates via Screener.in "
            f"(force={force_screener}, quarterly_refresh={auto_refresh})..."
        )
        symbols, _, screen = resolve_fundamental_universe(
            candidate_symbols,
            as_of=as_of,
            cfg=cfg,
            force_screener=force_screener,
        )
        screen_path.parent.mkdir(parents=True, exist_ok=True)
        dated_path = screen_path.parent / f"fundamental_screen_{as_of.isoformat()}.csv"
        screen.to_csv(dated_path, index=False)
        screen.to_csv(screen_path, index=False)
        n_pass = int(screen["in_universe"].sum())
        n_err = int((screen["status"] == "ERROR").sum())
        print(
            f"Fundamental screen: {n_pass}/{len(candidate_symbols)} active | "
            f"errors={n_err} | report: {dated_path}"
        )
        if screen["status"].eq("PASS").any():
            passed = screen.loc[screen["in_universe"], "symbol"].tolist()
            print(f"  PASS: {', '.join(passed[:20])}" + (" ..." if len(passed) > 20 else ""))
        if not symbols:
            raise RuntimeError(
                "No symbols passed PIT + Screener filters. "
                "Run scripts/fetch_universe.py to inspect failures, or use --skip-fundamentals."
            )
        print(f"Universe: {len(symbols)} symbols after screen | OHLCV: {start} -> {end}")
    plan, feature_map, signal_date = build_action_plan(
        symbols, start, end, cfg, args.portfolio, args.force_download
    )

    regimes = [
        str(feature_map[s].loc[signal_date if signal_date in feature_map[s].index else -1].get(
            "regime_active", "UNKNOWN"
        ))
        for s in feature_map
    ]
    market_regime = aggregate_market_regime(regimes)
    regime_counts = pd.Series(regimes).value_counts().to_dict()

    out_path = project_root() / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plan.to_csv(out_path, index=False)

    print("\n" + "=" * 60)
    print(f"KALI Daily Action Plan — signal date: {signal_date.date()}")
    print("=" * 60)
    print(f"ACTIVE REGIME (mode across {len(feature_map)} symbols): {market_regime}")
    print(f"Regime breakdown: {regime_counts}")
    print(f"Theoretical portfolio: ₹{args.portfolio:,.0f} | Risk/trade: {cfg['risk']['risk_per_trade_pct']*100:.1f}%")
    print(f"BUY signals (EOD, CMS-sorted): {len(plan)}")
    print("-" * 60)

    if plan.empty:
        print("No new BUY signals on today's close.")
    else:
        display_cols = [
            "ticker",
            "entry_price",
            "stop_loss",
            "target",
            "cms_score",
            "recommended_shares",
            "regime",
        ]
        print(plan[display_cols].to_string(index=False))

    print(f"\nSaved: {out_path}")

    # --- Open position tracker (wire to broker API or manual CSV) ---
    # Expected columns: symbol, entry_price, entry_atr, initial_stop,
    #   optional: highest_high_since_entry, shares, entry_date
    # Example: data/manual/open_positions.csv
    positions_path = Path(args.positions) if args.positions else project_root() / "data/manual/open_positions.csv"
    if positions_path.exists():
        breaches = check_open_positions(positions_path, feature_map, signal_date)
        if not breaches.empty:
            print("\n--- Open positions / trailing stop ---")
            print(breaches.to_string(index=False))
            breach_path = out_path.parent / "position_exit_alerts.csv"
            breaches.to_csv(breach_path, index=False)
            print(f"Position alerts: {breach_path}")
    else:
        print(
            f"\n[Open positions] No file at {positions_path}. "
            "Create CSV or pass --positions to check trailing Chandelier stops."
        )
        print(
            "  TODO: load positions from Zerodha/Upstox API "
            "(positions() / holdings()) and map to the same schema."
        )


if __name__ == "__main__":
    main()
