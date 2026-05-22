from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from ma44.config import Settings
from ma44.strategy import add_indicators, signal_bar_confidence, signal_mask, _trend_warmup_bars
from ma44.universe_mcap import build_daily_mcap_membership, load_shares_outstanding


def _bar_index(work: pd.DataFrame, ts: pd.Timestamp) -> int | None:
    if ts not in work.index:
        return None
    loc = work.index.get_loc(ts)
    if isinstance(loc, slice):
        return int(loc.stop) - 1
    if isinstance(loc, (int, np.integer)):
        return int(loc)
    return int(loc[-1])


def _close_on_or_before(work: pd.DataFrame, ts: pd.Timestamp) -> float:
    sub = work.loc[:ts]
    if sub.empty:
        return float(work.iloc[-1]["close"])
    return float(sub.iloc[-1]["close"])


@dataclass
class TradeResult:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    qty: int
    stop: float
    target: float
    pnl: float
    exit_reason: Literal["stop", "target"]


def _exit_long_bar(
    open_: float, high: float, low: float, stop: float, target: float
) -> tuple[float, Literal["stop", "target"]] | tuple[None, None]:
    hit_s = low <= stop
    hit_t = high >= target
    if hit_s and hit_t:
        return (open_ if open_ < stop else stop), "stop"
    if hit_s:
        return (open_ if open_ < stop else stop), "stop"
    if hit_t:
        return (open_ if open_ > target else target), "target"
    return None, None


def _warmup(settings: Settings) -> int:
    return int(settings.sma_period + _trend_warmup_bars(settings) + 5)


def _portfolio_symbol_order(
    works: dict[str, pd.DataFrame],
    sigs: dict[str, pd.Series],
    ts: pd.Timestamp,
    positions: dict[str, dict],
    pendings: dict[str, dict | None],
    settings: Settings,
    warmup: int,
) -> list[str]:
    """
    Per session: open positions first, then pending breakouts, then new signals —
    each group sorted by descending signal confidence (not alphabetical ticker).
    """
    scored: list[tuple[int, float, str]] = []
    for sym, w in works.items():
        t = _bar_index(w, ts)
        if t is None or t < warmup:
            continue
        has_pos = sym in positions
        pending = pendings.get(sym)
        conf = 0.0
        if pending is not None:
            conf = float(pending.get("confidence", 0.0))
        elif t - 1 >= 0 and bool(sigs[sym].iloc[t - 1]):
            conf = signal_bar_confidence(w, t - 1, settings)
        if has_pos:
            tier = 0
        elif pending is not None:
            tier = 1
        else:
            tier = 2
        scored.append((tier, -conf, sym))
    scored.sort()
    return [sym for _, _, sym in scored]


def _qty_for_risk(entry: float, stop: float, cash: float, settings: Settings) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0
    by_risk = int(settings.risk_per_trade_inr / risk_per_share)
    fee = settings.commission_pct
    max_afford = int(cash / (entry * (1 + fee)))
    return max(0, min(by_risk, max_afford))


def _slippage(settings: Settings) -> float:
    return max(float(getattr(settings, "slippage_pct", 0.0) or 0.0), 0.0)


def _entry_fill_long(raw_entry: float, settings: Settings) -> float:
    return float(raw_entry) * (1.0 + _slippage(settings))


def _exit_fill_long(raw_exit: float, settings: Settings) -> float:
    return float(raw_exit) * (1.0 - _slippage(settings))


def _buy_cost(px: float, qty: int, settings: Settings) -> float:
    return float(px) * int(qty) * (1.0 + float(settings.commission_pct))


def _sell_proceeds(px: float, qty: int, settings: Settings) -> float:
    return float(px) * int(qty) * (1.0 - float(settings.commission_pct))


def _mark_to_market_long(close_px: float, qty: int, settings: Settings) -> float:
    """
    Conservative MTM: assume we liquidate at close with slippage + commission.
    """
    ex = _exit_fill_long(close_px, settings)
    return _sell_proceeds(ex, qty, settings)


def _step_long_backtest_bar(
    symbol: str,
    t: int,
    ts: pd.Timestamp,
    work: pd.DataFrame,
    sig: pd.Series,
    cash: float,
    pending: dict | None,
    pos: dict | None,
    settings: Settings,
    trades: list[TradeResult],
    allow_new_entries: bool = True,
) -> tuple[float, dict | None, dict | None]:
    """One daily bar: expiry → exit → new pending from prior signal → breakout fill."""
    cur_pending = pending
    cur_pos = pos

    row = work.iloc[t]
    o, h, l = float(row["open"]), float(row["high"]), float(row["low"])

    if cur_pending is not None and t > int(cur_pending["last_try_index"]):
        cur_pending = None

    if cur_pos is not None:
        ep = float(cur_pos["entry"])
        st = float(cur_pos["stop"])
        tg = float(cur_pos["target"])
        q = int(cur_pos["qty"])
        ex_raw, reason = _exit_long_bar(o, h, l, st, tg)
        if ex_raw is not None:
            ex = _exit_fill_long(ex_raw, settings)
            proceeds = _sell_proceeds(ex, q, settings)
            pnl = proceeds - _buy_cost(ep, q, settings)
            cash += proceeds
            trades.append(
                TradeResult(
                    symbol=symbol,
                    entry_time=cur_pos["entry_time"],
                    exit_time=ts,
                    entry=ep,
                    exit=ex,
                    qty=q,
                    stop=st,
                    target=tg,
                    pnl=float(pnl),
                    exit_reason=reason,
                )
            )
            cur_pos = None

    if cur_pos is None:
        if not allow_new_entries:
            return cash, cur_pending, cur_pos
        if cur_pending is None and t - 1 >= 0 and bool(sig.iloc[t - 1]):
            prev = work.iloc[t - 1]
            s = t - 1
            sh, sl = float(prev["high"]), float(prev["low"])
            trig = sh * (1.0 + settings.entry_buffer_pct)
            stp = sl * (1.0 - settings.stop_buffer_pct)
            risk = max(trig - stp, 1e-9)
            max_risk_pct = float(getattr(settings, "max_initial_risk_pct", 0.0) or 0.0)
            risk_pct = risk / max(trig, 1e-9)
            if not (max_risk_pct > 0 and risk_pct > max_risk_pct):
                tgt = trig + settings.risk_reward * risk
                last_try = s + settings.breakout_hold_days
                cur_pending = {
                    "trigger": trig,
                    "stop": stp,
                    "target": tgt,
                    "last_try_index": last_try,
                    "signal_index": s,
                    "confidence": signal_bar_confidence(work, s, settings),
                }

        if cur_pending is not None:
            trig = float(cur_pending["trigger"])
            if h >= trig:
                entry = _entry_fill_long(max(trig, o), settings)
                stp = float(cur_pending["stop"])
                tgt = float(cur_pending["target"])
                q = _qty_for_risk(entry, stp, cash, settings)
                if q > 0 and entry > stp:
                    cost = _buy_cost(entry, q, settings)
                    if cost <= cash:
                        cash -= cost
                        cur_pos = {
                            "entry": entry,
                            "stop": stp,
                            "target": tgt,
                            "qty": q,
                            "entry_time": ts,
                        }
                cur_pending = None

    return cash, cur_pending, cur_pos


def run_symbol_backtest(df: pd.DataFrame, symbol: str, settings: Settings) -> tuple[list[TradeResult], pd.Series]:
    work = add_indicators(df, settings)
    sig = signal_mask(work, settings)
    cash = float(settings.starting_cash_inr)

    trades: list[TradeResult] = []
    equity: list[tuple[pd.Timestamp, float]] = []

    idx = work.index
    n = len(work)
    warmup = _warmup(settings)

    pending: dict | None = None
    pos: dict | None = None

    for t in range(warmup, n):
        ts = idx[t]
        row = work.iloc[t]
        c = float(row["close"])
        cash, pending, pos = _step_long_backtest_bar(
            symbol, t, ts, work, sig, cash, pending, pos, settings, trades
        )
        nav = cash
        if pos is not None:
            nav += _mark_to_market_long(c, int(pos["qty"]), settings)
        equity.append((ts, nav))

    eq_ser = pd.Series(dict(equity)).sort_index()
    if eq_ser.index.duplicated().any():
        eq_ser = eq_ser.groupby(level=0).last()
    return trades, eq_ser


def run_portfolio_backtest(dfs: dict[str, pd.DataFrame], settings: Settings) -> tuple[list[TradeResult], pd.Series]:
    """
    One shared cash pool. When several names compete on the same bar, higher-confidence
    setups fill first. New entries are limited to the daily top-N by market cap when
    `universe_top_n` is set (point-in-time via prior close × shares outstanding).
    """
    works: dict[str, pd.DataFrame] = {}
    sigs: dict[str, pd.Series] = {}
    for sym, df in sorted(dfs.items()):
        if df.empty:
            continue
        w = add_indicators(df, settings)
        works[sym] = w
        sigs[sym] = signal_mask(w, settings)
    if not works:
        return [], pd.Series(dtype=float)

    warmup = _warmup(settings)
    all_ts: set[pd.Timestamp] = set()
    for w in works.values():
        if len(w) > warmup:
            all_ts.update(w.index[warmup:])
    all_ts_sorted = sorted(all_ts)

    shares = load_shares_outstanding(settings, list(works.keys()))
    top_members = build_daily_mcap_membership(works, all_ts_sorted, warmup, settings, shares)

    cash = float(settings.starting_cash_inr)
    positions: dict[str, dict] = {}
    pendings: dict[str, dict | None] = {s: None for s in works}
    trades: list[TradeResult] = []
    equity: list[tuple[pd.Timestamp, float]] = []

    for ts in all_ts_sorted:
        for sym in _portfolio_symbol_order(works, sigs, ts, positions, pendings, settings, warmup):
            w, sig = works[sym], sigs[sym]
            t = _bar_index(w, ts)
            if t is None or t < warmup:
                continue
            top_ok = top_members is None or sym in top_members.get(ts, set())
            cap = int(getattr(settings, "max_open_positions", 0) or 0)
            cap_ok = cap <= 0 or sym in positions or len(positions) < cap
            allow_new_entries = top_ok and cap_ok
            cash, pendings[sym], sym_pos = _step_long_backtest_bar(
                sym,
                t,
                ts,
                w,
                sig,
                cash,
                pendings[sym],
                positions.get(sym),
                settings,
                trades,
                allow_new_entries=allow_new_entries,
            )
            if sym_pos is not None:
                positions[sym] = sym_pos
            elif sym in positions:
                del positions[sym]

        nav = cash
        for sym, pos in positions.items():
            nav += _mark_to_market_long(_close_on_or_before(works[sym], ts), int(pos["qty"]), settings)
        equity.append((ts, nav))

    eq_ser = pd.Series(dict(equity)).sort_index()
    if eq_ser.index.duplicated().any():
        eq_ser = eq_ser.groupby(level=0).last()
    return trades, eq_ser
