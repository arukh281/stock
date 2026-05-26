"""
NSE cash EOD session helpers (IST).

Before EOD_ANALYZE_AFTER_IST the latest *completed* session is the prior trading day.
After that time (aligned with GitHub cron ~17:30 IST), target today's bar.
Skip only when an equity snapshot already exists for that target session date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
# NSE cash close ~15:30 IST.
NSE_CASH_CLOSE_IST = time(15, 30)
# When cron / manual EOD should target today's bar (after Yahoo has settled).
EOD_ANALYZE_AFTER_IST = time(17, 30)


@dataclass(frozen=True)
class EodSessionPlan:
    """Resolved EOD bar date and whether analyze should run."""

    session_date: pd.Timestamp  # naive, normalized calendar day
    calendar_today: date
    before_market_close: bool
    skip_message: str | None = None
    already_done: bool = False

    @property
    def should_skip(self) -> bool:
        return self.skip_message is not None


def now_ist() -> datetime:
    return datetime.now(IST)


def normalize_timestamp(ts: Any) -> pd.Timestamp:
    """Strip tz for comparisons with naive OHLC indices."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.normalize()


def normalize_df_index(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    return out.sort_index()


def target_eod_session_date(now: datetime | None = None) -> pd.Timestamp:
    """
    Calendar date of the latest *completed* NSE daily bar we may analyze now.
    Weekends/holidays: last business day before today in IST.
    """
    now = now or now_ist()
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    cal = now.date()
    if cal.weekday() >= 5:
        return (pd.Timestamp(cal) - pd.offsets.BDay(1)).normalize()

    if now.time() < EOD_ANALYZE_AFTER_IST:
        return (pd.Timestamp(cal) - pd.offsets.BDay(1)).normalize()
    return pd.Timestamp(cal).normalize()


def resolve_session_from_universe(
    planned: pd.Timestamp,
    universe: dict[str, pd.DataFrame],
) -> pd.Timestamp:
    """Use planned session, or latest available bar on/before that date."""
    planned = normalize_timestamp(planned)
    available: list[pd.Timestamp] = []
    for df in universe.values():
        if df.empty:
            continue
        idx = normalize_df_index(df).index
        available.extend([normalize_timestamp(d) for d in idx if normalize_timestamp(d) <= planned])
    if not available:
        raise RuntimeError(f"No market data on or before {planned.date()}")
    return max(available)


def clip_universe(
    universe: dict[str, pd.DataFrame], session_date: pd.Timestamp
) -> dict[str, pd.DataFrame]:
    session_date = normalize_timestamp(session_date)
    clipped: dict[str, pd.DataFrame] = {}
    for sym, df in universe.items():
        if df.empty:
            continue
        norm = normalize_df_index(df)
        part = norm.loc[:session_date]
        if not part.empty:
            clipped[sym] = part
    return clipped


def _last_snapshot_date(store: Any) -> date | None:
    if hasattr(store, "get_last_equity_snapshot_date"):
        return store.get_last_equity_snapshot_date()
    return None


def append_journal_once(
    store: Any,
    ts: str,
    symbol: str | None,
    kind: str,
    message: str,
    *,
    recent_limit: int = 50,
) -> bool:
    """Append a journal row only when the same entry is not already present."""
    if hasattr(store, "list_journal"):
        target_symbol = symbol or None
        for row in store.list_journal(recent_limit):
            row_symbol = row.get("symbol") or None
            if row.get("kind") != kind:
                continue
            if row_symbol != target_symbol:
                continue
            if str(row.get("message", "")) == message:
                return False
    store.append_journal(ts, symbol, kind, message)
    return True


def append_skip_journal_once(store: Any, plan: "EodSessionPlan") -> None:
    """Avoid duplicate skip rows when cron/UI retries the same session."""
    if not plan.skip_message:
        return
    if append_journal_once(
        store,
        str(plan.session_date.date()),
        None,
        "skip",
        plan.skip_message,
        recent_limit=10,
    ):
        store.commit()


def append_analysis_journal_once(store: Any, plan: "EodSessionPlan", message: str) -> bool:
    """Record informative analyze-session lines in the activity log once."""
    return append_journal_once(
        store,
        str(plan.session_date.date()),
        None,
        "analysis",
        message,
    )


def plan_eod_session(
    store: Any, now: datetime | None = None, *, force: bool = False
) -> EodSessionPlan:
    """
    Decide which EOD bar to process and whether to skip (idempotent runs).
    force=True bypasses the equity-snapshot skip (manual recovery).
    """
    now = now or now_ist()
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    session = target_eod_session_date(now)
    cal_today = now.date()
    before_close = cal_today.weekday() < 5 and now.time() < EOD_ANALYZE_AFTER_IST
    last = _last_snapshot_date(store)
    ist_clock = now.strftime("%H:%M IST")

    if (
        not force
        and last is not None
        and last == session.date()
    ):
        msg = (
            f"EOD analysis already completed for {session.date()} "
            f"(equity snapshot on file). Nothing to do. "
            f"(now {ist_clock}; target session {session.date()})"
        )
        return EodSessionPlan(
            session_date=session,
            calendar_today=cal_today,
            before_market_close=before_close,
            skip_message=msg,
            already_done=True,
        )

    if before_close and last is not None and last >= session.date():
        msg = (
            f"Let the market close (after {EOD_ANALYZE_AFTER_IST.strftime('%H:%M')} IST). "
            f"Today's candle is not available yet. "
            f"Last EOD analysis: {last}. (now {ist_clock})"
        )
        return EodSessionPlan(
            session_date=session,
            calendar_today=cal_today,
            before_market_close=True,
            skip_message=msg,
        )

    return EodSessionPlan(
        session_date=session,
        calendar_today=cal_today,
        before_market_close=before_close,
    )


def format_session_banner(plan: EodSessionPlan) -> str:
    when = "before" if plan.before_market_close else "after"
    return (
        f"EOD session {plan.session_date.date()} (IST {when} "
        f"{EOD_ANALYZE_AFTER_IST.strftime('%H:%M')} EOD window; calendar {plan.calendar_today})"
    )


# Back-compat alias used in comments elsewhere
MARKET_CLOSE_IST = EOD_ANALYZE_AFTER_IST
