"""KALI daily EOD reconcile against Supabase ledger."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from kali.config import load_config, project_root
from kali.data.universe import resolve_fundamental_universe, should_refresh_screener

try:
    from sandbox.market_session import (
        format_session_banner,
        normalize_timestamp,
        plan_eod_session,
    )
except ImportError:
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parents[4]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from sandbox.market_session import (  # type: ignore[no-redef]
        format_session_banner,
        normalize_timestamp,
        plan_eod_session,
    )

FILL_BUY = "eod_amo_pending_next_open"
FILL_EXIT = "exit_signal_close_fill_next_open"


def _import_signal_helpers():
    root = project_root()
    scripts = root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from generate_daily_signals import (  # type: ignore
        aggregate_market_regime,
        build_action_plan,
        check_open_positions,
        load_universe_symbols,
    )

    return build_action_plan, check_open_positions, load_universe_symbols, aggregate_market_regime


def _positions_df(store: Any) -> pd.DataFrame:
    rows = []
    for p in store.list_positions():
        sym = str(p.symbol).upper().replace(".NS", "")
        extra = p.extra or {}
        rows.append(
            {
                "symbol": sym,
                "entry_price": float(p.entry_px),
                "entry_atr": float(extra.get("entry_atr", 1.0)),
                "initial_stop": float(p.stop_px or extra.get("initial_stop", 0)),
                "shares": float(p.qty),
                "highest_high_since_entry": float(
                    extra.get("highest_high_since_entry", p.entry_px)
                ),
            }
        )
    return pd.DataFrame(rows)


def run_daily_reconcile(
    store: Any,
    *,
    skip_fundamentals: bool = False,
    force_screener: bool = False,
    force: bool = False,
    lookback_days: int = 400,
) -> list[str]:
    build_action_plan, check_open_positions, load_universe_symbols, aggregate_market_regime = (
        _import_signal_helpers()
    )

    cfg = load_config()
    from sandbox.market_session import append_skip_journal_once

    plan = plan_eod_session(store, force=force)
    if plan.should_skip:
        append_skip_journal_once(store, plan)
        return [plan.skip_message or ""]

    session = plan.session_date
    as_of = session.date()
    end = (session + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
    start = (session - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    portfolio_equity = float(
        getattr(store, "get_cash", lambda: 0)()
        if not hasattr(store, "load_portfolio_summary")
        else store.load_portfolio_summary().get("equity", store.get_cash())
    )
    if hasattr(store, "load_portfolio_summary"):
        portfolio_equity = float(store.load_portfolio_summary().get("equity", store.get_cash()))

    candidate_symbols = load_universe_symbols(None, cfg)
    lines: list[str] = [format_session_banner(plan)]

    if skip_fundamentals:
        symbols = candidate_symbols
    else:
        auto_refresh = should_refresh_screener(as_of, cfg)
        symbols, _, screen = resolve_fundamental_universe(
            candidate_symbols,
            as_of=as_of,
            cfg=cfg,
            force_screener=force_screener or auto_refresh,
        )
        if not symbols:
            n_pass = int(screen["in_universe"].sum()) if screen is not None and not screen.empty else 0
            msg = (
                f"Fundamental screen: 0/{len(candidate_symbols)} passed "
                f"(in_universe={n_pass}); using PIT universe for paper reconcile."
            )
            lines.append(msg)
            store.append_journal(str(session.date()), None, "warn", msg)
            symbols = candidate_symbols

    try:
        action_plan, feature_map, signal_date = build_action_plan(
            symbols, start, end, cfg, portfolio_equity, False
        )
    except RuntimeError as exc:
        if "No symbols produced valid feature histories" not in str(exc):
            raise
        msg = (
            f"KALI: no symbols with sufficient OHLCV/features "
            f"({len(symbols)} candidates). Run with cached data or check KALI/data/cache/ohlcv."
        )
        lines.append(msg)
        store.append_journal(str(session.date()), None, "skip", msg)
        store.commit()
        return lines

    # Paper EOD always evaluates the planned session bar, not the latest cache row.
    data_last = normalize_timestamp(signal_date)
    signal_date = session
    if data_last != session:
        lines.append(
            f"Using EOD session {session.date()} for signals "
            f"(feature cache last bar {data_last.date()})."
        )

    pos_df = _positions_df(store)
    if not pos_df.empty:
        import tempfile

        tmp = Path(tempfile.gettempdir()) / "kali_open_positions.csv"
        pos_df.to_csv(tmp, index=False)
        breaches = check_open_positions(tmp, feature_map, signal_date)
        for _, row in breaches.iterrows():
            sym = str(row["symbol"]).upper()
            if row.get("action") != "EXIT_AMO" or not bool(row.get("breach")):
                for p in store.list_positions():
                    if p.symbol.replace(".NS", "").upper() == sym:
                        df = feature_map.get(sym)
                        if df is not None:
                            bar = (
                                df.loc[signal_date]
                                if signal_date in df.index
                                else df.iloc[-1]
                            )
                            hh = max(
                                float(p.extra.get("highest_high_since_entry", p.entry_px)),
                                float(bar["high"]),
                            )
                            store.update_position_extra(p.symbol, {"highest_high_since_entry": hh})
                continue
            pos = None
            for p in store.list_positions():
                if p.symbol.replace(".NS", "").upper() == sym:
                    pos = p
                    break
            if pos is None:
                continue
            store.insert_pending(
                sym,
                str(signal_date.date()),
                float(row["close"]),
                float(row["trailing_stop"]),
                0,
                str((signal_date + pd.offsets.BDay(1)).date()),
                fill_model=FILL_EXIT,
                qty=float(pos.qty),
            )
            msg = f"{sym}: EXIT_AMO breach → pending next open"
            lines.append(msg)
            store.append_journal(str(signal_date), sym, "exit_pending", msg)

    held = {p.symbol.replace(".NS", "").upper() for p in store.list_positions()}
    pending_syms = {p.symbol.replace(".NS", "").upper() for p in store.list_open_pending()}
    max_slots = int(cfg.get("backtest", {}).get("max_positions", 5))

    if action_plan is not None and not action_plan.empty:
        for _, row in action_plan.iterrows():
            ticker = str(row["ticker"]).upper()
            if ticker in held or ticker in pending_syms:
                continue
            if store.count_positions() + len(store.list_open_pending()) >= max_slots:
                break
            shares = int(row.get("recommended_shares", 0) or 0)
            if shares <= 0:
                continue
            store.insert_pending(
                ticker,
                str(row.get("signal_date", signal_date.date())),
                float(row["entry_price"]),
                float(row["stop_loss"]),
                float(row.get("target", 0) or 0),
                str((signal_date + pd.offsets.BDay(1)).date()),
                fill_model=FILL_BUY,
                qty=float(shares),
            )
            msg = f"{ticker}: BUY pending {shares} sh"
            lines.append(msg)
            store.append_journal(str(signal_date), ticker, "pending", msg)

    regimes = []
    for s in feature_map:
        df = feature_map[s]
        if signal_date in df.index:
            row = df.loc[signal_date]
        else:
            row = df.iloc[-1]
        regimes.append(str(row.get("regime_active", "UNKNOWN")))
    lines.append(
        f"KALI {signal_date.date()} | {aggregate_market_regime(regimes)} | plan rows {len(action_plan) if action_plan is not None else 0}",
    )

    store.insert_equity_snapshot(session.date(), store.get_cash(), portfolio_equity)
    store.set_portfolio_equity(portfolio_equity)
    store.commit()
    return lines
