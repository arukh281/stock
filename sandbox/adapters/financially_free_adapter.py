from __future__ import annotations

from typing import Any

from sandbox.adapters._paths import setup_paths
from sandbox.store.supabase_store import SupabaseLedgerStore


def run_financially_free_analyze() -> dict[str, Any]:
    setup_paths()
    from daily_paper_step import run_daily_paper

    store = SupabaseLedgerStore("financially_free")
    lines = run_daily_paper(store)
    summary = store.load_portfolio_summary()
    skipped = len(lines) == 1 and lines[0] and (
        "already completed" in lines[0] or "Let the market close" in lines[0]
    )
    return {"lines": lines, "portfolio": summary, "skipped": skipped}
