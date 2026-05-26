"""
Midcap 150 production daily paper step — one session advance (not daily_scanner).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from nifty_midcap_history import get_yf_constituents
from swing_trading_algo import UNIVERSE_MIDCAP150, SwingTradingAlgo, _universe_helpers

try:
    from sandbox.market_session import (
        clip_universe,
        format_session_banner,
        normalize_timestamp,
        plan_eod_session,
        resolve_session_from_universe,
    )
except ImportError:
    import sys
    from pathlib import Path

    _repo = Path(__file__).resolve().parents[1].parent
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from sandbox.market_session import (  # type: ignore[no-redef]
        clip_universe,
        format_session_banner,
        normalize_timestamp,
        plan_eod_session,
        resolve_session_from_universe,
    )

FILL_MODEL = "signal_prev_bar_fill_session_open"  # backtest only (advance_one_session entries)
FILL_MODEL_BREAKOUT = "breakout_trigger_after_signal"  # paper: fill when bar high >= trigger_px

PRODUCTION_PARAMS = {
    "max_positions": 5,
    "max_roc": 75,
    "exit_confirm_days": 2,
    "cooldown_days": 0,
    "min_volume_ratio": 1.0,
    "require_index_trend": True,
    "rank_by_rs": True,
    "rs_top_pct": 0.20,
    "use_stop_loss": True,
    "stop_lookback": 20,
    "commission_pct": 0.001,
    "pending_hold_days": 5,
}


def _slot_size_inr(starting_capital: float, max_positions: int) -> float:
    """Per-slot budget; max_positions clamped to avoid division by zero."""
    return starting_capital / max(1, int(max_positions))


def _slot_qty_for_trigger(
    cash: float,
    starting_capital: float,
    max_positions: int,
    trigger_px: float,
    commission_pct: float,
    *,
    min_invest: float = 1000.0,
) -> tuple[int, float]:
    """
    Whole shares within one slot (starting_capital / max_positions).
    Returns 0 when trigger is above what the slot can buy (e.g. ₹35k stock, ₹3k slot).
    """
    slot_size = _slot_size_inr(starting_capital, max_positions)
    invest = min(cash, slot_size)
    if trigger_px <= 0 or invest < min_invest:
        return 0, slot_size
    qty = int(invest * (1 - commission_pct) / trigger_px)
    return qty, slot_size


# Keys passed to advance_one_session only (pending_hold_days is paper-only).
_ADVANCE_SESSION_PARAM_KEYS = frozenset(
    {
        "max_positions",
        "commission_pct",
        "use_stop_loss",
        "rank_by_rs",
        "rank_by_volume",
        "rs_top_pct",
        "max_roc",
        "min_volume_ratio",
        "exit_confirm_days",
        "stop_lookback",
        "cooldown_days",
        "require_index_trend",
    }
)


def _breakout_trigger_px(df: pd.DataFrame, signal_date: pd.Timestamp) -> float:
    """20D resistance at signal bar (same pivot as daily_scanner Breakout_Pivot)."""
    if "Resistance_20D" in df.columns:
        px = df.loc[signal_date, "Resistance_20D"]
        if not pd.isna(px) and float(px) > 0:
            return float(px)
    return float(df.loc[signal_date, "High"])


def _entry_candidates(
    algo: SwingTradingAlgo,
    state: PortfolioState,
    session_date: pd.Timestamp,
    universe: dict[str, pd.DataFrame],
    *,
    max_positions: int,
    rank_by_rs: bool,
    rank_by_volume: bool,
    rs_top_pct: float,
    max_roc: float,
    min_volume_ratio: float,
    require_index_trend: bool,
    cooldown_days: int,
    use_historical_universe: bool,
    get_constituents_fn: Callable,
    signal_on: str = "prev",
) -> list[tuple[str, float, pd.Timestamp]]:
    """Ranked entry candidates. signal_on='prev' uses prior bar; 'today' uses session bar."""
    date = normalize_timestamp(session_date)
    index_members_prev = set(get_constituents_fn(date))
    rs_pool: list[float] = []
    if rank_by_rs and rs_top_pct > 0:
        for sym, df in universe.items():
            if use_historical_universe and sym not in index_members_prev:
                continue
            prev_rs = algo._prev_trading_day(df, date)
            if prev_rs is None:
                continue
            rs_val = df.loc[prev_rs, "RS_Blend"]
            if not pd.isna(rs_val):
                rs_pool.append(float(rs_val))
    rs_cutoff = (
        float(np.quantile(rs_pool, 1 - rs_top_pct)) if rs_pool and rs_top_pct > 0 else None
    )

    candidates: list[tuple[str, float, pd.Timestamp]] = []
    for sym, df in universe.items():
        if sym in state.positions:
            continue
        if (
            cooldown_days > 0
            and sym in state.last_exit_date
            and (date - state.last_exit_date[sym]).days <= cooldown_days
        ):
            continue
        if use_historical_universe and sym not in index_members_prev:
            continue
        if signal_on == "today":
            sig_day = date
        else:
            sig_day = algo._prev_trading_day(df, date)
        if sig_day is None or date not in df.index:
            continue
        row = df.loc[sig_day]
        if not algo._entry_signal(
            row,
            max_roc=max_roc,
            min_volume_ratio=min_volume_ratio,
            require_index_trend=require_index_trend,
        ):
            continue
        rs_score = row.get("RS_Blend", np.nan)
        if rank_by_rs:
            if pd.isna(rs_score):
                continue
            if rs_cutoff is not None and float(rs_score) < rs_cutoff:
                continue
            rank_key = float(rs_score)
        else:
            vol_ratio = row["Volume_Ratio"]
            rank_key = float(vol_ratio) if not pd.isna(vol_ratio) else 0.0
        candidates.append((sym, rank_key, sig_day))

    if rank_by_rs or rank_by_volume:
        candidates.sort(key=lambda x: x[1], reverse=True)
    else:
        candidates.sort(key=lambda x: x[0])
    slots = max_positions - len(state.positions)
    return candidates[:slots]


@dataclass
class PositionState:
    shares: float
    entry_price: float
    entry_date: pd.Timestamp
    stop_price: float | None = None


@dataclass
class PortfolioState:
    cash: float
    starting_capital: float
    positions: dict[str, PositionState] = field(default_factory=dict)
    last_exit_date: dict[str, pd.Timestamp] = field(default_factory=dict)


@dataclass
class ActionEvent:
    kind: str
    symbol: str
    message: str
    entry_date: date | None = None
    exit_date: date | None = None
    entry_px: float | None = None
    exit_px: float | None = None
    qty: float | None = None
    return_pct: float | None = None
    exit_reason: str | None = None
    fill_model: str = FILL_MODEL
    pending_signal_date: date | None = None


def advance_one_session(
    algo: SwingTradingAlgo,
    state: PortfolioState,
    session_date: pd.Timestamp,
    universe: dict[str, pd.DataFrame],
    *,
    max_positions: int = 5,
    commission_pct: float = 0.001,
    use_stop_loss: bool = True,
    rank_by_rs: bool = True,
    rank_by_volume: bool = False,
    rs_top_pct: float = 0.20,
    max_roc: float = 75,
    min_volume_ratio: float = 1.0,
    exit_confirm_days: int = 2,
    stop_lookback: int = 20,
    cooldown_days: int = 0,
    require_index_trend: bool = True,
    get_constituents_fn: Callable | None = None,
    use_historical_universe: bool = True,
    allow_entries: bool = True,
) -> tuple[PortfolioState, list[ActionEvent]]:
    """Single backtest day: exits first, then optional session-open entries."""
    events: list[ActionEvent] = []
    _, get_constituents_fn, _ = _universe_helpers(UNIVERSE_MIDCAP150)
    if get_constituents_fn is None:
        get_constituents_fn = get_yf_constituents

    date = normalize_timestamp(session_date)
    slot_size = _slot_size_inr(state.starting_capital, max_positions)
    exit_col = f"Exit_{int(exit_confirm_days)}"
    if universe and exit_col not in next(iter(universe.values())).columns:
        exit_col = "Exit_2"
    stop_col = f"Stop_Level_{int(stop_lookback)}"
    if universe and stop_col not in next(iter(universe.values())).columns:
        stop_col = "Stop_Level_20"

    index_members_today = set(get_constituents_fn(date))
    prev_calendar = None
    for sym, df in universe.items():
        prev = algo._prev_trading_day(df, date)
        if prev is not None:
            prev_calendar = prev
            break
    index_members_prev = set(get_constituents_fn(prev_calendar)) if prev_calendar is not None else index_members_today

    for sym in list(state.positions.keys()):
        if sym not in universe:
            continue
        df = universe[sym]
        prev = algo._prev_trading_day(df, date)
        if prev is None or date not in df.index:
            continue
        pos = state.positions[sym]
        exit_signal = bool(df.loc[prev, exit_col])
        dropped = sym not in index_members_today
        stop_price = pos.stop_price
        stop_hit = (
            use_stop_loss
            and stop_price is not None
            and not pd.isna(stop_price)
            and df.loc[prev, "Close"] < stop_price
        )
        if not exit_signal and not dropped and not stop_hit:
            continue
        pos = state.positions.pop(sym)
        exec_price = float(df.loc[date, "Open"])
        proceeds = pos.shares * exec_price * (1 - commission_pct)
        state.cash += proceeds
        ret_pct = (
            (exec_price / pos.entry_price - 1) * 100
            if pos.entry_price and pos.entry_price > 0
            else 0.0
        )
        if stop_hit:
            reason = "stop_loss"
        elif dropped and not exit_signal:
            reason = "index_removal"
        else:
            reason = "signal"
        state.last_exit_date[sym] = date
        events.append(
            ActionEvent(
                kind="exit",
                symbol=sym,
                message=f"{sym}: EXIT {reason} @ {exec_price:.2f}",
                entry_date=pos.entry_date.date(),
                exit_date=date.date(),
                entry_px=pos.entry_price,
                exit_px=exec_price,
                qty=pos.shares,
                return_pct=round(ret_pct, 2),
                exit_reason=reason,
                fill_model=FILL_MODEL,
            )
        )

    slots = max(0, max_positions - len(state.positions)) if allow_entries else 0
    if slots > 0 and state.cash > slot_size * 0.1:
        rs_pool = []
        if rank_by_rs and rs_top_pct > 0:
            for sym, df in universe.items():
                if use_historical_universe and sym not in index_members_prev:
                    continue
                prev_rs = algo._prev_trading_day(df, date)
                if prev_rs is None:
                    continue
                rs_val = df.loc[prev_rs, "RS_Blend"]
                if not pd.isna(rs_val):
                    rs_pool.append(float(rs_val))
        rs_cutoff = (
            float(np.quantile(rs_pool, 1 - rs_top_pct)) if rs_pool and rs_top_pct > 0 else None
        )

        candidates = []
        for sym, df in universe.items():
            if sym in state.positions:
                continue
            if (
                cooldown_days > 0
                and sym in state.last_exit_date
                and (date - state.last_exit_date[sym]).days <= cooldown_days
            ):
                continue
            if use_historical_universe and sym not in index_members_prev:
                continue
            prev = algo._prev_trading_day(df, date)
            if prev is None or date not in df.index:
                continue
            row = df.loc[prev]
            if algo._entry_signal(
                row,
                max_roc=max_roc,
                min_volume_ratio=min_volume_ratio,
                require_index_trend=require_index_trend,
            ):
                rs_score = row.get("RS_Blend", np.nan)
                if rank_by_rs:
                    if pd.isna(rs_score):
                        continue
                    if rs_cutoff is not None and float(rs_score) < rs_cutoff:
                        continue
                    rank_key = float(rs_score)
                else:
                    vol_ratio = row["Volume_Ratio"]
                    rank_key = float(vol_ratio) if not pd.isna(vol_ratio) else 0.0
                candidates.append((sym, rank_key, prev))

        if rank_by_rs or rank_by_volume:
            candidates.sort(key=lambda x: x[1], reverse=True)
        else:
            candidates.sort(key=lambda x: x[0])

        for sym, _rk, prev_for_sym in candidates[:slots]:
            df = universe[sym]
            invest = min(state.cash, slot_size)
            if invest < 1000:
                break
            exec_price = float(df.loc[date, "Open"])
            if pd.isna(exec_price) or exec_price <= 0:
                continue
            shares = invest * (1 - commission_pct) / exec_price
            if shares < 1:
                continue
            state.cash -= invest
            stop = (
                float(df.loc[prev_for_sym, stop_col])
                if use_stop_loss and not pd.isna(df.loc[prev_for_sym, stop_col])
                else None
            )
            state.positions[sym] = PositionState(
                shares=shares,
                entry_price=exec_price,
                entry_date=date,
                stop_price=stop,
            )
            events.append(
                ActionEvent(
                    kind="entry",
                    symbol=sym,
                    message=f"{sym}: ENTRY @ {exec_price:.2f} x {shares:.0f}",
                    entry_date=date.date(),
                    entry_px=exec_price,
                    qty=shares,
                    fill_model=FILL_MODEL,
                )
            )

    return state, events


def _try_fill_pendings_on_bar(
    store: Any,
    state: PortfolioState,
    session_date: pd.Timestamp,
    universe: dict[str, pd.DataFrame],
    params: dict,
) -> list[str]:
    """Fill open pendings when session bar trades at/through trigger (44ma-style)."""
    lines: list[str] = []
    bar_date = normalize_timestamp(session_date)
    for pend in store.list_open_pending():
        sym = pend.symbol
        if sym in state.positions or sym not in universe or pend.qty is None or not pend.id:
            continue
        df = universe[sym]
        if bar_date not in df.index:
            continue
        signal_day = normalize_timestamp(pend.signal_ts)
        deadline = normalize_timestamp(pend.deadline_ts) if pend.deadline_ts else None
        if bar_date <= signal_day:
            continue
        if deadline is not None and bar_date > deadline:
            store.update_pending_status(int(pend.id), "expired")
            msg = f"{sym}: pending EXPIRED (signal {pend.signal_ts})"
            lines.append(msg)
            store.append_journal(str(session_date), sym, "expire", msg)
            continue
        o = float(df.loc[bar_date, "Open"])
        h = float(df.loc[bar_date, "High"])
        trigger = float(pend.trigger_px or 0)
        if h < trigger:
            continue
        qty = int(float(pend.qty))
        if qty < 1:
            store.update_pending_status(int(pend.id), "cancelled")
            msg = (
                f"{sym}: pending CANCELLED — cannot buy 1 share "
                f"@ ₹{trigger:,.0f} within slot ₹{_slot_size_inr(state.starting_capital, params['max_positions']):,.0f}"
            )
            lines.append(msg)
            store.append_journal(str(session_date), sym, "cancel", msg)
            continue
        entry_px = max(trigger, o)
        cost = qty * entry_px * (1 + params["commission_pct"])
        if state.cash < cost:
            continue
        state.cash -= cost
        stop = pend.stop_px
        state.positions[sym] = PositionState(
            shares=float(qty),
            entry_price=entry_px,
            entry_date=bar_date,
            stop_price=float(stop) if stop else None,
        )
        store.update_pending_status(int(pend.id), "filled")
        store.fill_pending_at_open(
            int(pend.id), entry_px, str(bar_date.date()), FILL_MODEL_BREAKOUT
        )
        store.insert_position(
            sym,
            float(qty),
            entry_px,
            float(stop or 0),
            0,
            str(session_date),
            extra={
                "entry_date": str(bar_date.date()),
                "stop_price": state.positions[sym].stop_price,
                "fill_model": FILL_MODEL_BREAKOUT,
            },
        )
        msg = f"{sym}: filled pending @ {entry_px:.2f} (trigger {trigger:.2f}, bar high {h:.2f})"
        lines.append(msg)
        store.append_journal(str(session_date), sym, "entry", msg)
    return lines


def _last_eod_snapshot_date(store: Any) -> date | None:
    if hasattr(store, "get_last_equity_snapshot_date"):
        return store.get_last_equity_snapshot_date()
    return None


def _signal_allowed_for_paper(
    store: Any, sig_day: pd.Timestamp, session_date: pd.Timestamp
) -> bool:
    """
    Fresh ledger (no EOD snapshots): only signals on the session bar itself.
    Continuing ledger: skip signals on or before the last snapshotted session.
    """
    sig = normalize_timestamp(sig_day)
    sess = normalize_timestamp(session_date)
    last = _last_eod_snapshot_date(store)
    if last is None:
        return sig == sess
    return sig.date() > last


def _insert_entry_pendings(
    store: Any,
    state: PortfolioState,
    session_date: pd.Timestamp,
    universe: dict[str, pd.DataFrame],
    candidates: list[tuple[str, float, pd.Timestamp]],
    params: dict,
) -> list[str]:
    """Create open pending orders for entry signals (no position until trigger fills)."""
    lines: list[str] = []
    starting = state.starting_capital
    hold_days = int(params.get("pending_hold_days", 5))
    stop_col = f"Stop_Level_{int(params['stop_lookback'])}"
    open_count = len(state.positions) + len(store.list_open_pending_symbols())

    for sym, _rk, sig_day in candidates:
        if not _signal_allowed_for_paper(store, sig_day, session_date):
            continue
        if open_count >= params["max_positions"]:
            break
        if store.get_open_pending(sym):
            continue
        df = universe.get(sym)
        if df is None or sig_day not in df.index:
            continue
        trigger = _breakout_trigger_px(df, sig_day)
        if trigger <= 0:
            continue
        shares, slot_size = _slot_qty_for_trigger(
            state.cash,
            starting,
            params["max_positions"],
            trigger,
            params["commission_pct"],
        )
        if shares < 1:
            msg = (
                f"{sym}: skip — need 1 share @ ₹{trigger:,.0f} "
                f"but slot is ₹{slot_size:,.0f} ({int(params['max_positions'])} max positions)"
            )
            lines.append(msg)
            store.append_journal(str(session_date), sym, "skip", msg)
            continue
        stop = (
            float(df.loc[sig_day, stop_col])
            if params.get("use_stop_loss") and stop_col in df.columns and not pd.isna(df.loc[sig_day, stop_col])
            else trigger * 0.92
        )
        deadline = sig_day + pd.offsets.BDay(hold_days)
        store.insert_pending(
            sym,
            str(sig_day.date()),
            trigger,
            stop,
            0,
            str(deadline.date()),
            fill_model=FILL_MODEL_BREAKOUT,
            qty=shares,
        )
        notional = shares * trigger
        msg = (
            f"{sym}: signal {sig_day.date()} → pending {shares} sh @ {trigger:.2f} "
            f"(~₹{notional:,.0f} / ₹{slot_size:,.0f} slot; fill when high >= trigger)"
        )
        lines.append(msg)
        store.append_journal(str(session_date), sym, "pending", msg)
        open_count += 1
    return lines


def portfolio_mtm(
    state: PortfolioState, universe: dict[str, pd.DataFrame], on_date: pd.Timestamp
) -> float:
    total = state.cash
    for sym, pos in state.positions.items():
        df = universe.get(sym)
        if df is None or on_date not in df.index:
            continue
        total += pos.shares * float(df.loc[on_date, "Close"])
    return total


def run_daily_paper(
    store: Any,
    algo: SwingTradingAlgo | None = None,
    *,
    session_date: pd.Timestamp | None = None,
    skip_eod_gate: bool = False,
) -> list[str]:
    """Advance Midcap 150 paper portfolio one EOD session; persist via LedgerStore."""
    params = PRODUCTION_PARAMS.copy()
    algo = algo or SwingTradingAlgo(index_ticker="NIFTYMIDCAP150.NS", lookback_roc=20)

    from sandbox.market_session import append_analysis_journal_once, append_skip_journal_once

    plan = plan_eod_session(store, force=skip_eod_gate)
    if not skip_eod_gate and session_date is None and plan.should_skip:
        append_skip_journal_once(store, plan)
        return [plan.skip_message or ""]

    planned_session = (
        normalize_timestamp(session_date) if session_date is not None else plan.session_date
    )

    cash = store.get_cash()
    starting = store.get_starting_capital()
    state = PortfolioState(cash=cash, starting_capital=starting)

    for row in store.list_positions():
        ed = row.extra.get("entry_date") or row.opened_at[:10]
        state.positions[row.symbol] = PositionState(
            shares=float(row.qty),
            entry_price=float(row.entry_px),
            entry_date=normalize_timestamp(ed),
            stop_price=row.extra.get("stop_price"),
        )

    held = list(state.positions.keys())
    tickers = list(set(get_yf_constituents(planned_session)) | set(held))
    start = (planned_session - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    end = (planned_session + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    universe = algo.prepare_universe(tickers, start, end)
    if not universe:
        raise RuntimeError("Failed to prepare universe")

    session_date = resolve_session_from_universe(planned_session, universe)
    universe = clip_universe(universe, session_date)

    banner = format_session_banner(plan)
    lines: list[str] = [banner]
    analysis_messages: list[str] = [banner]
    if normalize_timestamp(planned_session) != session_date:
        fallback_msg = (
            f"Using last available bar {session_date.date()} "
            f"(requested {planned_session.date()})."
        )
        lines.append(fallback_msg)
        analysis_messages.append(fallback_msg)
    _, get_constituents_fn, _ = _universe_helpers(UNIVERSE_MIDCAP150)

    # Drop pendings sized below 1 share (legacy fractional qty on high-priced names).
    for pend in store.list_open_pending():
        if pend.id is None or pend.qty is None:
            continue
        if int(float(pend.qty)) >= 1:
            continue
        trig = float(pend.trigger_px or 0)
        slot = _slot_size_inr(state.starting_capital, params["max_positions"])
        store.update_pending_status(int(pend.id), "cancelled")
        msg = (
            f"{pend.symbol}: pending CANCELLED — cannot buy 1 share "
            f"@ ₹{trig:,.0f} within slot ₹{slot:,.0f}"
        )
        lines.append(msg)
        store.append_journal(str(session_date), pend.symbol, "cancel", msg)

    fresh_ledger = _last_eod_snapshot_date(store) is None
    if fresh_ledger:
        fresh_msg = (
            "Fresh ledger: only scanning signals on the current EOD session bar "
            "(no historical catch-up)."
        )
        lines.append(fresh_msg)
        analysis_messages.append(fresh_msg)

    # 1) Prior-bar signals only when continuing an existing EOD history.
    prev_candidates: list[tuple[str, float, pd.Timestamp]] = []
    if not fresh_ledger:
        prev_candidates = _entry_candidates(
            algo,
            state,
            session_date,
            universe,
            max_positions=params["max_positions"],
            rank_by_rs=params["rank_by_rs"],
            rank_by_volume=False,
            rs_top_pct=params["rs_top_pct"],
            max_roc=params["max_roc"],
            min_volume_ratio=params["min_volume_ratio"],
            require_index_trend=params["require_index_trend"],
            cooldown_days=params["cooldown_days"],
            use_historical_universe=True,
            get_constituents_fn=get_constituents_fn,
            signal_on="prev",
        )
        lines.extend(
            _insert_entry_pendings(store, state, session_date, universe, prev_candidates, params)
        )

    # 2) Fill pendings only when the session bar's high reaches trigger_px.
    lines.extend(_try_fill_pendings_on_bar(store, state, session_date, universe, params))

    # 3) Exits only on this path — new entries use pending breakout fills.
    advance_params = {k: params[k] for k in _ADVANCE_SESSION_PARAM_KEYS if k in params}
    state, events = advance_one_session(
        algo,
        state,
        session_date,
        universe,
        use_historical_universe=True,
        allow_entries=False,
        **advance_params,
    )

    for ev in events:
        lines.append(ev.message)
        store.append_journal(str(session_date), ev.symbol, ev.kind, ev.message)
        if ev.kind == "exit" and ev.exit_date and ev.entry_date:
            store.delete_position(ev.symbol)
            store.insert_closed_trade(
                ev.symbol,
                ev.qty or 0,
                ev.entry_date,
                ev.exit_date,
                ev.entry_px or 0,
                ev.exit_px or 0,
                ev.return_pct,
                ev.exit_reason,
                ev.fill_model,
            )

    # 4) Pendings for signals on the session bar itself (fill on a later bar).
    today_candidates = _entry_candidates(
        algo,
        state,
        session_date,
        universe,
        max_positions=params["max_positions"],
        rank_by_rs=params["rank_by_rs"],
        rank_by_volume=False,
        rs_top_pct=params["rs_top_pct"],
        max_roc=params["max_roc"],
        min_volume_ratio=params["min_volume_ratio"],
        require_index_trend=params["require_index_trend"],
        cooldown_days=params["cooldown_days"],
        use_historical_universe=True,
        get_constituents_fn=get_constituents_fn,
        signal_on="today",
    )
    lines.extend(
        _insert_entry_pendings(store, state, session_date, universe, today_candidates, params)
    )

    equity = portfolio_mtm(state, universe, session_date)
    store.set_cash(state.cash)
    store.set_portfolio_equity(equity)
    store.insert_equity_snapshot(session_date.date(), state.cash, equity)
    for message in analysis_messages:
        append_analysis_journal_once(store, plan, message)
    store.commit()
    return lines
