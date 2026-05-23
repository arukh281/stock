from __future__ import annotations

from typing import Any

from sandbox.adapters._paths import setup_paths
from sandbox.store.supabase_store import SupabaseLedgerStore


def run_financially_free_analyze(*, force: bool = False) -> dict[str, Any]:
    setup_paths()
    from daily_paper_step import run_daily_paper
    from sandbox.market_session import plan_eod_session

    store = SupabaseLedgerStore("financially_free")
    plan = plan_eod_session(store, force=force)
    lines = run_daily_paper(store, skip_eod_gate=force)
    summary = store.load_portfolio_summary()
    skipped = plan.should_skip or (
        len(lines) == 1
        and lines[0]
        and ("already completed" in lines[0] or "Let the market close" in lines[0])
    )
    session_date = str(plan.session_date.date())
    if not skipped:
        last = store.get_last_equity_snapshot_date()
        if last is not None:
            session_date = str(last)
    return {
        "lines": lines,
        "portfolio": summary,
        "skipped": skipped,
        "session_date": session_date,
    }
