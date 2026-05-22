from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from ma44.backtest import (
    _buy_cost,
    _entry_fill_long,
    _exit_fill_long,
    _exit_long_bar,
    _mark_to_market_long,
    _qty_for_risk,
    _sell_proceeds,
)
from ma44.config import Settings
from ma44.data import fetch_daily
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sandbox.store.base import LedgerStore
else:
    LedgerStore = object  # runtime: duck-typed store protocol
from ma44.strategy import (
    add_indicators,
    last_bar_positive_slope,
    latest_signal_info,
    signal_bar_confidence,
    signal_mask,
)
from ma44.symbols import resolve_daily_processing_order, resolve_trade_symbols

FILL_BREAKOUT = "breakout_trigger_after_signal"


def _normalize_day(ts: Any) -> pd.Timestamp:
    """Calendar day for comparisons; strip tz so Supabase UTC matches naive OHLC bars."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.normalize()
FILL_EXIT_INTRABAR = "intrabar_stop_target"


def init_db(path: Path) -> None:
    from sandbox.store.sqlite_store import SqliteLedgerStore

    SqliteLedgerStore(path).close()


def _legacy_conn(db_path: Path) -> sqlite3.Connection:
    init_db(db_path)
    return sqlite3.connect(db_path)


def _get_meta(con: sqlite3.Connection, k: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def _set_meta(con: sqlite3.Connection, k: str, v: str) -> None:
    con.execute(
        "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (k, v),
    )


def _jlog_store(store: LedgerStore, ts: str, symbol: str | None, kind: str, message: str) -> None:
    store.append_journal(ts, symbol, kind, message)


def daily_step(
    store: LedgerStore,
    settings: Settings,
    *,
    session_date: pd.Timestamp | None = None,
) -> list[str]:
    """
    Assumes one run per session after the daily bar is available.
    """
    lines: list[str] = []
    cash = store.get_cash()

    extra: set[str] = set(store.list_position_symbols())
    extra.update(store.list_open_pending_symbols())

    hist_start = str(getattr(settings, "universe_history_start", "2018-01-01"))

    position_symbols: set[str] = set(store.list_position_symbols())

    pending_conf: dict[str, float] = {}
    for pend in store.list_open_pending():
        sym_p = pend.symbol
        signal_ts_s = pend.signal_ts
        pdf = fetch_daily(str(sym_p), start=hist_start)
        if not pdf.empty:
            pwork = add_indicators(pdf, settings)
            sig_day = _normalize_day(signal_ts_s)
            if sig_day in pwork.index:
                i = int(pwork.index.get_loc(sig_day))
                pending_conf[str(sym_p)] = signal_bar_confidence(pwork, i, settings)

    universe_syms = resolve_trade_symbols(settings, db_symbols=extra)
    ordered = resolve_daily_processing_order(
        settings,
        universe_syms,
        position_symbols=position_symbols,
        pending_confidence=pending_conf,
    )

    for sym in ordered:
        df = fetch_daily(sym, start=hist_start)
        if df.empty or len(df) < settings.sma_period + 5:
            msg = f"{sym}: insufficient data"
            lines.append(msg)
            _jlog_store(store, pd.Timestamp.utcnow().isoformat(), sym, "skip", msg)
            continue

        if session_date is not None:
            sess = _normalize_day(session_date)
            if sess not in df.index:
                prior = df.index[df.index <= sess]
                if prior.empty:
                    msg = f"{sym}: no bar on or before {sess.date()}"
                    lines.append(msg)
                    _jlog_store(store, str(sess), sym, "skip", msg)
                    continue
                bar_ts = prior[-1]
            else:
                bar_ts = sess
            work = df.loc[:bar_ts]
            if len(work) < settings.sma_period + 5:
                msg = f"{sym}: insufficient data through {bar_ts.date()}"
                lines.append(msg)
                _jlog_store(store, str(bar_ts), sym, "skip", msg)
                continue
            df = work
        else:
            bar_ts = df.index[-1]
        bar_date = _normalize_day(bar_ts)
        o = float(df.iloc[-1]["open"])
        h = float(df.iloc[-1]["high"])
        l = float(df.iloc[-1]["low"])

        pos = store.get_position(sym)
        if pos is not None:
            qty = float(pos.qty)
            ep = float(pos.entry_px)
            stp = float(pos.stop_px or 0)
            tgt = float(pos.target_px or 0)
            ex_raw, reason = _exit_long_bar(o, h, l, stp, tgt)
            if ex_raw is not None:
                ex = _exit_fill_long(float(ex_raw), settings)
                proceeds = _sell_proceeds(ex, int(qty), settings)
                pnl = proceeds - _buy_cost(ep, int(qty), settings)
                cash += proceeds
                store.set_cash(cash)
                store.delete_position(sym)
                msg = f"{sym}: CLOSED {reason} @ {ex:.2f} (qty {int(qty)}) pnl {pnl:.2f}"
                lines.append(msg)
                _jlog_store(store, str(bar_ts), sym, "exit", msg)

        pos = store.get_position(sym)
        pend = store.get_open_pending(sym)

        if pos is None and pend is not None:
            signal_ts = _normalize_day(pend.signal_ts)
            deadline = _normalize_day(pend.deadline_ts or pend.signal_ts)
            if bar_date > signal_ts and bar_date <= deadline:
                if h >= float(pend.trigger_px or 0):
                    cap = int(getattr(settings, "max_open_positions", 0) or 0)
                    open_count = store.count_positions()
                    can_open_new = cap <= 0 or open_count < cap
                    if can_open_new and pend.id is not None:
                        entry = _entry_fill_long(max(float(pend.trigger_px or 0), o), settings)
                        stp = float(pend.stop_px or 0)
                        q = _qty_for_risk(entry, stp, cash, settings)
                        cost = _buy_cost(entry, q, settings)
                        if q > 0 and entry > stp and cash >= cost:
                            cash -= cost
                            store.set_cash(cash)
                            store.update_pending_status(int(pend.id), "filled")
                            store.insert_position(
                                sym,
                                q,
                                entry,
                                stp,
                                float(pend.target_px or 0),
                                str(bar_ts),
                                extra={"fill_model": FILL_BREAKOUT},
                            )
                            msg = (
                                f"{sym}: ENTRY @ {entry:.2f} qty {q} stop {stp:.2f} "
                                f"tgt {float(pend.target_px or 0):.2f}"
                            )
                            lines.append(msg)
                            _jlog_store(store, str(bar_ts), sym, "entry", msg)
            elif bar_date > deadline and pend.id is not None:
                store.update_pending_status(int(pend.id), "expired")
                msg = f"{sym}: pending EXPIRED (signal {pend.signal_ts})"
                lines.append(msg)
                _jlog_store(store, str(bar_ts), sym, "expire", msg)

        pos = store.get_position(sym)
        pend_open = store.get_open_pending(sym)
        if pos is None and pend_open is None:
            info = latest_signal_info(df, settings)
            if info is not None:
                sig_day = str(pd.Timestamp(info["date"]).date())
                if store.get_open_pending(sym) is None:
                    sh, sl = float(info["high"]), float(info["low"])
                    trig = sh * (1.0 + settings.entry_buffer_pct)
                    stp = sl * (1.0 - settings.stop_buffer_pct)
                    risk = max(trig - stp, 1e-9)
                    tgt = trig + settings.risk_reward * risk
                    deadline = pd.Timestamp(info["date"]) + pd.offsets.BDay(
                        settings.breakout_hold_days
                    )
                    store.insert_pending(
                        sym,
                        sig_day,
                        trig,
                        stp,
                        tgt,
                        str(deadline.date()),
                        fill_model=FILL_BREAKOUT,
                    )
                    conf = float(info.get("confidence", 0.0))
                    msg = (
                        f"{sym}: NEW SIGNAL @ {info['date']} close={info['close']:.2f} "
                        f"sma={info['sma']:.2f} conf={conf:.2f} trig={trig:.2f}"
                    )
                    lines.append(msg)
                    _jlog_store(store, str(bar_ts), sym, "signal", msg)

    store.commit()
    return lines


def daily_step_db(db_path: Path, settings: Settings) -> list[str]:
    """CLI backward-compatible entry using SQLite file."""
    from sandbox.store.sqlite_store import SqliteLedgerStore

    store = SqliteLedgerStore(db_path)
    try:
        return daily_step(store, settings)
    finally:
        store.close()


def paper_status(store_or_path: LedgerStore | Path) -> dict[str, Any]:
    if isinstance(store_or_path, Path):
        from sandbox.store.sqlite_store import SqliteLedgerStore

        store = SqliteLedgerStore(store_or_path)
        close_after = True
    else:
        store = store_or_path
        close_after = False
    try:
        cash = store.get_cash()
        pos = [
            (p.symbol, p.qty, p.entry_px, p.stop_px, p.target_px, p.opened_at)
            for p in store.list_positions()
        ]
        pend = [
            (
                p.symbol,
                p.signal_ts,
                p.trigger_px,
                p.stop_px,
                p.target_px,
                p.deadline_ts,
                p.status,
            )
            for p in store.list_pending_recent(20)
        ]
        return {"cash": cash, "positions": pos, "pending_recent": pend}
    finally:
        if close_after:
            store.close()


def paper_context(store_or_path: LedgerStore | Path, settings: Settings) -> dict[str, Any]:
    st = paper_status(store_or_path)
    cash = float(st["cash"])
    hist_start = str(getattr(settings, "universe_history_start", "2018-01-01"))

    mtm_value = 0.0
    cost_basis = 0.0
    pos_details: list[dict[str, Any]] = []

    for row in st.get("positions") or []:
        sym, qty_s, ep, stp, tgt, opened_ts = row
        qty = int(qty_s)
        ep = float(ep)
        stp = float(stp)
        tgt = float(tgt)
        cost_basis += qty * ep
        last_close = ep
        df = fetch_daily(sym, start=hist_start)
        if not df.empty:
            last_close = float(df.iloc[-1]["close"])
        mtm = _mark_to_market_long(last_close, qty, settings)
        mtm_value += mtm
        unreal = mtm - _buy_cost(ep, qty, settings)
        r_per_share = max(ep - stp, 1e-9)
        r_trade = r_per_share * qty
        pos_details.append(
            {
                "symbol": sym,
                "qty": qty,
                "entry": ep,
                "stop": stp,
                "target": tgt,
                "last_close": last_close,
                "unrealized_pnl": round(unreal, 2),
                "approx_risk_inr": round(r_trade, 2),
                "opened_ts": opened_ts,
            }
        )

    equity = cash + mtm_value
    top_n = int(getattr(settings, "universe_top_n", 100) or 100)
    lines: list[str] = [
        "=== Paper context (EOD-style) ===",
        f"Universe: top {top_n} NSE names by market cap",
        f"Free cash: ₹{cash:,.2f}",
        f"Open positions: {len(pos_details)}",
    ]
    if pos_details:
        lines.append(f"  Mark-to-market value: ₹{mtm_value:,.2f}")
        for p in pos_details:
            lines.append(
                f"  · {p['symbol']}: qty {p['qty']} last={p['last_close']:.2f} "
                f"unreal ₹{p['unrealized_pnl']:,.2f}"
            )
    lines.append(f"Approx total equity (cash + MTM): ₹{equity:,.2f}")

    pend = st.get("pending_recent") or []
    open_pend = [r for r in pend if len(r) > 6 and r[6] == "open"]
    lines.append(f"Open pending breakouts: {len(open_pend)}")

    data: dict[str, Any] = {
        "cash": cash,
        "cost_basis_open": round(cost_basis, 2),
        "mtm_open": round(mtm_value, 2),
        "approx_equity": round(equity, 2),
        "positions": pos_details,
        "open_pending_count": len(open_pend),
    }
    return {"text": "\n".join(lines), "lines": lines, "data": data}


def dump_scan(settings: Settings, db_symbols: set[str] | None = None) -> list[str]:
    out: list[str] = []
    hist_start = str(getattr(settings, "universe_history_start", "2018-01-01"))
    for sym in resolve_trade_symbols(settings, db_symbols=db_symbols or set()):
        df = fetch_daily(sym, start=hist_start)
        if df.empty:
            out.append(f"{sym}: no data")
            continue
        work = add_indicators(df, settings)
        sig = signal_mask(work, settings)
        last_sig = bool(sig.iloc[-1])
        slope_ok = last_bar_positive_slope(work, settings)
        row = work.iloc[-1]
        out.append(
            f"{sym}: last={work.index[-1].date()} close={float(row['close']):.2f} "
            f"slope44={slope_ok} signal={last_sig}"
        )
    return out
