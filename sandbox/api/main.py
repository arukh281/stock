from __future__ import annotations

import sandbox.bootstrap  # noqa: F401, E402 — .env + PYTHONPATH before adapters

import os
import traceback
from typing import Any, Callable, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from sandbox.adapters import (
    run_financially_free_analyze,
    run_kali_analyze,
    run_ma44_analyze,
)
from sandbox.gates.registry import gate_breakdown, list_algos, run_compare
from sandbox.store.supabase_store import SupabaseLedgerStore

app = FastAPI(title="Paper Trading Sandbox", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALGO_IDS = ("44ma", "44ma_stacked_2ma", "financially_free", "kali")


def verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("ANALYZE_API_KEY", "")
    if not expected:
        raise HTTPException(500, "ANALYZE_API_KEY not configured on server")
    if x_api_key != expected:
        raise HTTPException(401, "Invalid or missing X-API-Key")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/gates", dependencies=[Depends(verify_api_key)])
def list_gate_algos() -> Dict[str, Any]:
    return {"algos": list_algos()}


@app.get("/gates/{algo_id}", dependencies=[Depends(verify_api_key)])
def get_gate_breakdown(
    algo_id: str,
    symbol: str = Query(..., description="Yahoo symbol e.g. ETERNAL.NS"),
) -> Dict[str, Any]:
    if algo_id not in ALGO_IDS:
        raise HTTPException(404, f"Unknown algo_id: {algo_id}")
    result = gate_breakdown(algo_id, symbol)
    if "error" in result and "algos" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/gates/{algo_id}/compare", dependencies=[Depends(verify_api_key)])
def get_gate_compare(
    algo_id: str,
    start: str = Query("2018-01-01"),
    end: Optional[str] = Query(None),
) -> Dict[str, Any]:
    if algo_id not in ALGO_IDS:
        raise HTTPException(404, f"Unknown algo_id: {algo_id}")
    kwargs: Dict[str, Any] = {"start": start}
    if end:
        kwargs["end"] = end
    return run_compare(algo_id, **kwargs)


def _guard_running(algo_id: str) -> SupabaseLedgerStore:
    store = SupabaseLedgerStore(algo_id)
    if store.get_running_run():
        raise HTTPException(409, f"Analyze already running for {algo_id}")
    return store


def _run_job(algo_id: str, fn: Callable[[], dict], run_id: str) -> None:
    store = SupabaseLedgerStore(algo_id)
    try:
        result = fn()
        store.finish_run(
            run_id,
            "ok",
            summary={
                "lines": result.get("lines", [])[:200],
                "line_count": len(result.get("lines", [])),
                "equity": result.get("equity"),
                "skipped": result.get("skipped", False),
                "session_date": result.get("session_date"),
            },
        )
    except Exception as exc:
        store.finish_run(
            run_id,
            "error",
            error_message=str(exc),
            summary={"traceback": traceback.format_exc()[-4000:]},
        )


def _schedule_ma44_analyze(algo_id: str, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    store = _guard_running(algo_id)
    run_id = store.start_run()

    def job():
        return run_ma44_analyze(algo_id)

    background_tasks.add_task(_run_job, algo_id, job, run_id)
    return {"run_id": run_id, "algo_id": algo_id, "status": "running"}


@app.post("/analyze/44ma", dependencies=[Depends(verify_api_key)])
def analyze_44ma(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    return _schedule_ma44_analyze("44ma", background_tasks)


@app.post("/analyze/44ma-stacked-2ma", dependencies=[Depends(verify_api_key)])
def analyze_44ma_stacked_2ma(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    return _schedule_ma44_analyze("44ma_stacked_2ma", background_tasks)


@app.post("/analyze/financially-free", dependencies=[Depends(verify_api_key)])
def analyze_financially_free(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    store = _guard_running("financially_free")
    run_id = store.start_run()
    background_tasks.add_task(_run_job, "financially_free", run_financially_free_analyze, run_id)
    return {"run_id": run_id, "algo_id": "financially_free", "status": "running"}


@app.post("/analyze/kali", dependencies=[Depends(verify_api_key)])
def analyze_kali(
    background_tasks: BackgroundTasks,
    skip_fundamentals: bool = Query(False),
    force_screener: bool = Query(False),
) -> Dict[str, Any]:
    store = _guard_running("kali")
    run_id = store.start_run()

    def job():
        return run_kali_analyze(
            skip_fundamentals=skip_fundamentals,
            force_screener=force_screener,
        )

    background_tasks.add_task(_run_job, "kali", job, run_id)
    return {"run_id": run_id, "algo_id": "kali", "status": "running"}


@app.post(
    "/portfolio/{algo_id}/pending/{symbol}/cancel",
    dependencies=[Depends(verify_api_key)],
)
def cancel_pending_order(algo_id: str, symbol: str) -> Dict[str, Any]:
    if algo_id not in ALGO_IDS:
        raise HTTPException(404, f"Unknown algo_id: {algo_id}")
    sym = symbol.strip().upper()
    store = SupabaseLedgerStore(algo_id)
    try:
        ok = store.cancel_open_pending(sym)
        store.commit()
        if not ok:
            raise HTTPException(404, f"No open pending order for {sym}")
        return {"algo_id": algo_id, "symbol": sym, "status": "cancelled"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.get("/portfolio/{algo_id}", dependencies=[Depends(verify_api_key)])
def get_portfolio(algo_id: str) -> Dict[str, Any]:
    if algo_id not in ALGO_IDS:
        raise HTTPException(404, f"Unknown algo_id: {algo_id}")
    try:
        return SupabaseLedgerStore(algo_id).load_portfolio_summary()
    except Exception as exc:
        msg = str(exc)
        if "portfolio_meta" in msg and "PGRST205" in msg:
            raise HTTPException(
                503,
                "Database tables missing. Apply sandbox/supabase/migrations/20260520000000_initial_schema.sql in Supabase SQL Editor.",
            ) from exc
        if "YOUR_PROJECT" in os.environ.get("SUPABASE_URL", "") or "ConnectError" in type(exc).__name__:
            raise HTTPException(
                503,
                "Supabase not reachable. Put real SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in sandbox/.env (not .env.example placeholders).",
            ) from exc
        raise HTTPException(500, msg) from exc


@app.get("/runs/{algo_id}", dependencies=[Depends(verify_api_key)])
def list_runs(algo_id: str, limit: int = Query(10, ge=1, le=50)) -> List[dict]:
    if algo_id not in ALGO_IDS:
        raise HTTPException(404, f"Unknown algo_id: {algo_id}")
    return SupabaseLedgerStore(algo_id).list_runs(limit)


@app.get("/runs/id/{run_id}", dependencies=[Depends(verify_api_key)])
def get_run(run_id: str) -> Dict[str, Any]:
    for algo_id in ALGO_IDS:
        row = SupabaseLedgerStore(algo_id).get_run(run_id)
        if row:
            return row
    raise HTTPException(404, "Run not found")
