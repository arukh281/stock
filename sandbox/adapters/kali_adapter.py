from __future__ import annotations

from typing import Any

from sandbox.adapters._paths import setup_paths
from sandbox.store.supabase_store import SupabaseLedgerStore


def run_kali_analyze(
    *, skip_fundamentals: bool = False, force_screener: bool = False
) -> dict[str, Any]:
    setup_paths()
    from kali.live.daily_reconcile import run_daily_reconcile

    store = SupabaseLedgerStore("kali")
    lines = run_daily_reconcile(
        store,
        skip_fundamentals=skip_fundamentals,
        force_screener=force_screener,
    )
    skipped = len(lines) == 1 and lines[0] and (
        "already completed" in lines[0] or "Let the market close" in lines[0]
    )
    return {"lines": lines, "portfolio": store.load_portfolio_summary(), "skipped": skipped}
