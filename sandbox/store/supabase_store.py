from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sandbox.store.base import PendingRow, PositionRow


def _client():
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


class SupabaseLedgerStore:
    def __init__(self, algo_id: str) -> None:
        self.algo_id = algo_id
        self._sb = _client()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = (
            self._sb.table("portfolio_meta")
            .select("config,cash")
            .eq("algo_id", self.algo_id)
            .single()
            .execute()
        )
        data = row.data or {}
        if key == "cash":
            return str(data.get("cash", default or "0"))
        cfg = data.get("config") or {}
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        if key in cfg:
            return str(cfg[key])
        return default

    def set_meta(self, key: str, value: str) -> None:
        if key == "cash":
            self._sb.table("portfolio_meta").update(
                {"cash": float(value), "updated_at": datetime.now(timezone.utc).isoformat()}
            ).eq("algo_id", self.algo_id).execute()
            return
        row = (
            self._sb.table("portfolio_meta")
            .select("config")
            .eq("algo_id", self.algo_id)
            .single()
            .execute()
        )
        cfg = row.data.get("config") or {}
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        cfg[key] = value
        self._sb.table("portfolio_meta").update(
            {"config": cfg, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("algo_id", self.algo_id).execute()

    def get_cash(self) -> float:
        row = (
            self._sb.table("portfolio_meta")
            .select("cash")
            .eq("algo_id", self.algo_id)
            .single()
            .execute()
        )
        return float(row.data.get("cash", 0))

    def set_cash(self, cash: float) -> None:
        self.set_meta("cash", str(round(cash, 2)))

    def get_starting_capital(self) -> float:
        row = (
            self._sb.table("portfolio_meta")
            .select("starting_capital")
            .eq("algo_id", self.algo_id)
            .single()
            .execute()
        )
        return float(row.data.get("starting_capital", 1_000_000))

    def count_positions(self) -> int:
        r = (
            self._sb.table("positions")
            .select("id", count="exact")
            .eq("algo_id", self.algo_id)
            .execute()
        )
        return r.count or 0

    def list_position_symbols(self) -> list[str]:
        r = (
            self._sb.table("positions")
            .select("symbol")
            .eq("algo_id", self.algo_id)
            .execute()
        )
        return [x["symbol"] for x in (r.data or [])]

    def _pos_from_row(self, row: dict) -> PositionRow:
        return PositionRow(
            id=row.get("id"),
            symbol=row["symbol"],
            qty=float(row["qty"]),
            entry_px=float(row["entry_px"]),
            stop_px=float(row["stop_px"]) if row.get("stop_px") is not None else None,
            target_px=float(row["target_px"]) if row.get("target_px") is not None else None,
            opened_at=str(row["opened_at"]),
            extra=row.get("extra") or {},
        )

    def get_position(self, symbol: str) -> PositionRow | None:
        r = (
            self._sb.table("positions")
            .select("*")
            .eq("algo_id", self.algo_id)
            .eq("symbol", symbol)
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return self._pos_from_row(r.data[0])

    def list_positions(self) -> list[PositionRow]:
        r = (
            self._sb.table("positions")
            .select("*")
            .eq("algo_id", self.algo_id)
            .execute()
        )
        return [self._pos_from_row(x) for x in (r.data or [])]

    def insert_position(
        self,
        symbol: str,
        qty: float,
        entry_px: float,
        stop_px: float,
        target_px: float,
        opened_ts: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._sb.table("positions").upsert(
            {
                "algo_id": self.algo_id,
                "symbol": symbol,
                "qty": qty,
                "entry_px": entry_px,
                "stop_px": stop_px,
                "target_px": target_px,
                "opened_at": opened_ts,
                "extra": extra or {},
            },
            on_conflict="algo_id,symbol",
        ).execute()

    def delete_position(self, symbol: str) -> None:
        self._sb.table("positions").delete().eq("algo_id", self.algo_id).eq(
            "symbol", symbol
        ).execute()

    def update_position_extra(self, symbol: str, extra: dict[str, Any]) -> None:
        pos = self.get_position(symbol)
        if not pos:
            return
        merged = {**(pos.extra or {}), **extra}
        self._sb.table("positions").update({"extra": merged}).eq(
            "algo_id", self.algo_id
        ).eq("symbol", symbol).execute()

    def list_open_pending_symbols(self) -> list[str]:
        r = (
            self._sb.table("pending_orders")
            .select("symbol")
            .eq("algo_id", self.algo_id)
            .eq("status", "open")
            .execute()
        )
        return [x["symbol"] for x in (r.data or [])]

    def get_open_pending(self, symbol: str) -> PendingRow | None:
        r = (
            self._sb.table("pending_orders")
            .select("*")
            .eq("algo_id", self.algo_id)
            .eq("symbol", symbol)
            .eq("status", "open")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return self._pending_from_row(r.data[0])

    def _pending_from_row(self, row: dict) -> PendingRow:
        sig = row.get("signal_date")
        return PendingRow(
            id=row.get("id"),
            symbol=row["symbol"],
            signal_ts=str(sig) if sig else "",
            trigger_px=float(row["trigger_px"]) if row.get("trigger_px") is not None else None,
            stop_px=float(row["stop_px"]) if row.get("stop_px") is not None else None,
            target_px=float(row["target_px"]) if row.get("target_px") is not None else None,
            deadline_ts=str(row["deadline_ts"]) if row.get("deadline_ts") else None,
            status=row["status"],
            qty=float(row["qty"]) if row.get("qty") is not None else None,
            fill_model=row.get("fill_model"),
            extra=row.get("extra") or {},
        )

    def list_pending_recent(self, limit: int = 20) -> list[PendingRow]:
        r = (
            self._sb.table("pending_orders")
            .select("*")
            .eq("algo_id", self.algo_id)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return [self._pending_from_row(x) for x in (r.data or [])]

    def list_open_pending(self) -> list[PendingRow]:
        r = (
            self._sb.table("pending_orders")
            .select("*")
            .eq("algo_id", self.algo_id)
            .eq("status", "open")
            .execute()
        )
        return [self._pending_from_row(x) for x in (r.data or [])]

    def insert_pending(
        self,
        symbol: str,
        signal_ts: str,
        trigger_px: float,
        stop_px: float,
        target_px: float,
        deadline_ts: str,
        fill_model: str | None = None,
        qty: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        row = {
            "algo_id": self.algo_id,
            "symbol": symbol,
            "signal_date": signal_ts[:10] if signal_ts else None,
            "trigger_px": trigger_px,
            "stop_px": stop_px,
            "target_px": target_px,
            "deadline_ts": deadline_ts if deadline_ts else None,
            "status": "open",
            "fill_model": fill_model,
            "qty": qty,
            "extra": extra or {},
        }
        r = self._sb.table("pending_orders").insert(row).execute()
        return int(r.data[0]["id"])

    def update_pending_status(self, pending_id: int, status: str) -> None:
        self._sb.table("pending_orders").update({"status": status}).eq(
            "id", pending_id
        ).execute()

    def cancel_open_pending(self, symbol: str, *, reason: str = "cancelled manually") -> bool:
        """Mark open pending for symbol as cancelled; returns False if none open."""
        pend = self.get_open_pending(symbol)
        if pend is None or pend.id is None:
            return False
        self.update_pending_status(int(pend.id), "cancelled")
        self.append_journal(
            datetime.now(timezone.utc).isoformat(),
            symbol,
            "cancel",
            f"{symbol}: pending CANCELLED ({reason})",
        )
        return True

    def fill_pending_at_open(
        self, pending_id: int, fill_px: float, fill_date: str, fill_model: str
    ) -> None:
        self._sb.table("pending_orders").update(
            {"status": "filled", "fill_model": fill_model, "extra": {"fill_px": fill_px, "fill_date": fill_date}}
        ).eq("id", pending_id).execute()

    def append_journal(self, ts: str, symbol: str | None, kind: str, message: str) -> None:
        self._sb.table("journal").insert(
            {
                "algo_id": self.algo_id,
                "ts": ts,
                "symbol": symbol,
                "kind": kind,
                "message": message,
            }
        ).execute()

    def list_journal(self, limit: int = 50) -> list[dict[str, Any]]:
        r = (
            self._sb.table("journal")
            .select("ts,symbol,kind,message")
            .eq("algo_id", self.algo_id)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return list(r.data or [])

    def insert_closed_trade(
        self,
        symbol: str,
        qty: float,
        entry_date: date,
        exit_date: date,
        entry_px: float,
        exit_px: float,
        return_pct: float | None,
        exit_reason: str | None,
        fill_model: str | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._sb.table("closed_trades").insert(
            {
                "algo_id": self.algo_id,
                "symbol": symbol,
                "qty": qty,
                "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "entry_px": entry_px,
                "exit_px": exit_px,
                "return_pct": return_pct,
                "exit_reason": exit_reason,
                "fill_model": fill_model,
                "extra": extra or {},
            }
        ).execute()

    def list_closed_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        r = (
            self._sb.table("closed_trades")
            .select("*")
            .eq("algo_id", self.algo_id)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return list(r.data or [])

    def sum_realized_pnl(self) -> float:
        r = (
            self._sb.table("closed_trades")
            .select("qty, entry_px, exit_px")
            .eq("algo_id", self.algo_id)
            .execute()
        )
        total = 0.0
        for row in r.data or []:
            qty = float(row["qty"])
            entry = float(row["entry_px"])
            exit_px = float(row["exit_px"])
            total += qty * (exit_px - entry)
        return round(total, 2)

    def get_last_equity_snapshot_date(self) -> date | None:
        r = (
            self._sb.table("equity_snapshots")
            .select("as_of_date")
            .eq("algo_id", self.algo_id)
            .order("as_of_date", desc=True)
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        raw = r.data[0].get("as_of_date")
        if raw is None:
            return None
        return date.fromisoformat(str(raw)[:10])

    def has_equity_snapshot(self, as_of: date) -> bool:
        r = (
            self._sb.table("equity_snapshots")
            .select("id")
            .eq("algo_id", self.algo_id)
            .eq("as_of_date", as_of.isoformat())
            .limit(1)
            .execute()
        )
        return bool(r.data)

    def insert_equity_snapshot(
        self, as_of: date, cash: float, equity: float, benchmark: dict | None = None
    ) -> None:
        self._sb.table("equity_snapshots").upsert(
            {
                "algo_id": self.algo_id,
                "as_of_date": as_of.isoformat(),
                "cash": cash,
                "equity": equity,
                "benchmark": benchmark,
            },
            on_conflict="algo_id,as_of_date",
        ).execute()

    def set_portfolio_equity(self, equity: float) -> None:
        self._sb.table("portfolio_meta").update(
            {"equity": equity, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("algo_id", self.algo_id).execute()

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass

    # --- run logs ---

    def start_run(self) -> str:
        r = (
            self._sb.table("run_logs")
            .insert({"algo_id": self.algo_id, "status": "running"})
            .execute()
        )
        return str(r.data[0]["id"])

    def finish_run(
        self,
        run_id: str,
        status: str,
        summary: dict | None = None,
        error_message: str | None = None,
        raw_stdout: str | None = None,
    ) -> None:
        self._sb.table("run_logs").update(
            {
                "status": status,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "error_message": error_message,
                "raw_stdout": raw_stdout,
            }
        ).eq("id", run_id).execute()

    def get_running_run(self) -> dict | None:
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        r = (
            self._sb.table("run_logs")
            .select("*")
            .eq("algo_id", self.algo_id)
            .eq("status", "running")
            .gte("started_at", cutoff)
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None

    def get_run(self, run_id: str) -> dict | None:
        r = self._sb.table("run_logs").select("*").eq("id", run_id).limit(1).execute()
        return r.data[0] if r.data else None

    def list_runs(self, limit: int = 10) -> list[dict]:
        r = (
            self._sb.table("run_logs")
            .select("*")
            .eq("algo_id", self.algo_id)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(r.data or [])

    def load_portfolio_summary(self) -> dict[str, Any]:
        meta = (
            self._sb.table("portfolio_meta")
            .select("*")
            .eq("algo_id", self.algo_id)
            .single()
            .execute()
        ).data
        cash = float(meta.get("cash", 0))
        stored_equity = float(meta.get("equity", 0))
        positions = self.list_positions()
        # Cost basis of open holdings (MTM updated on each EOD analyze)
        in_market = sum(float(p.qty) * float(p.entry_px) for p in positions)
        if positions and stored_equity > cash:
            in_market = round(stored_equity - cash, 2)
        elif not positions:
            in_market = 0.0
        total_value = round(cash + in_market, 2)
        starting_capital = float(meta.get("starting_capital", 0))
        realized_pnl = self.sum_realized_pnl()
        unrealized_from_extra = 0.0
        has_extra_unreal = False
        for p in positions:
            u = (p.extra or {}).get("unrealized_pnl")
            if u is not None:
                unrealized_from_extra += float(u)
                has_extra_unreal = True
        total_pnl = round(total_value - starting_capital, 2)
        if has_extra_unreal and positions:
            unrealized_pnl = round(unrealized_from_extra, 2)
        else:
            unrealized_pnl = round(total_pnl - realized_pnl, 2)
        total_pnl_pct = (
            round(100.0 * total_pnl / starting_capital, 2)
            if starting_capital > 0
            else 0.0
        )
        return {
            "algo_id": self.algo_id,
            "meta": meta,
            "cash": cash,
            "equity": total_value,
            "in_market": in_market,
            "total_value": total_value,
            "starting_capital": starting_capital,
            "pnl": {
                "total": total_pnl,
                "total_pct": total_pnl_pct,
                "realized": realized_pnl,
                "unrealized": unrealized_pnl,
            },
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "entry_px": p.entry_px,
                    "stop_px": p.stop_px,
                    "target_px": p.target_px,
                    "opened_at": p.opened_at,
                    "extra": p.extra,
                }
                for p in positions
            ],
            "pending_recent": [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "signal_ts": p.signal_ts,
                    "trigger_px": p.trigger_px,
                    "stop_px": p.stop_px,
                    "target_px": p.target_px,
                    "deadline_ts": p.deadline_ts,
                    "status": p.status,
                    "qty": p.qty,
                    "fill_model": p.fill_model,
                }
                for p in self.list_pending_recent(20)
            ],
            "journal_recent": self.list_journal(10),
            "closed_trades_recent": self.list_closed_trades(10),
            "runs_recent": self.list_runs(5),
        }
