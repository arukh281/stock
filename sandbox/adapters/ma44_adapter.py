from __future__ import annotations

from pathlib import Path
from typing import Any

from sandbox.adapters._paths import setup_paths
from sandbox.ma44_variants import config_path_for, is_ma44_algo
from sandbox.market_session import append_skip_journal_once, format_session_banner, plan_eod_session
from sandbox.store.supabase_store import SupabaseLedgerStore


def _load_settings(algo_id: str):
    setup_paths()
    from ma44.config import Settings

    path = config_path_for(algo_id)
    if not path.is_file():
        raise FileNotFoundError(f"44MA config not found for {algo_id}: {path}")
    return Settings.load(path)


def run_ma44_analyze(algo_id: str = "44ma", *, force: bool = False) -> dict[str, Any]:
    if not is_ma44_algo(algo_id):
        raise ValueError(f"Not a 44MA sandbox algo: {algo_id}")

    setup_paths()
    from ma44.paper import daily_step, paper_context

    settings = _load_settings(algo_id)

    store = SupabaseLedgerStore(algo_id)
    plan = plan_eod_session(store, force=force)
    if plan.should_skip:
        append_skip_journal_once(store, plan)
        store.close()
        return {
            "lines": [plan.skip_message or ""],
            "skipped": True,
            "session_date": str(plan.session_date.date()),
            "algo_id": algo_id,
        }

    try:
        lines = [format_session_banner(plan), *daily_step(store, settings, session_date=plan.session_date)]
        ctx = paper_context(store, settings)
        equity = float(ctx["data"]["approx_equity"])
        store.set_portfolio_equity(equity)
        store.insert_equity_snapshot(
            plan.session_date.date(),
            float(ctx["data"]["cash"]),
            equity,
        )
        store.commit()
    finally:
        store.close()

    return {
        "lines": lines,
        "context": ctx["data"],
        "equity": equity,
        "session_date": str(plan.session_date.date()),
        "skipped": False,
        "algo_id": algo_id,
    }
