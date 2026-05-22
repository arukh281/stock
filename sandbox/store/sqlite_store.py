from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from sandbox.store.base import PendingRow, PositionRow


class SqliteLedgerStore:
    """SQLite ledger matching ma44 paper.db schema (algo_id ignored — single file per algo)."""

    def __init__(self, db_path: Path, algo_id: str = "44ma") -> None:
        self.db_path = db_path
        self.algo_id = algo_id
        self._con: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._con is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._con = sqlite3.connect(self.db_path)
            self._con.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                  k TEXT PRIMARY KEY, v TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT NOT NULL, signal_ts TEXT NOT NULL,
                  trigger_px REAL NOT NULL, stop_px REAL NOT NULL,
                  target_px REAL NOT NULL, deadline_ts TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'open',
                  fill_model TEXT, qty REAL,
                  extra TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS positions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT NOT NULL UNIQUE,
                  qty REAL NOT NULL, entry_px REAL NOT NULL,
                  stop_px REAL NOT NULL, target_px REAL NOT NULL,
                  opened_ts TEXT NOT NULL, extra TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS journal (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts TEXT NOT NULL, symbol TEXT, kind TEXT NOT NULL, message TEXT NOT NULL
                );
                """
            )
            if self._con.execute("SELECT v FROM meta WHERE k='cash'").fetchone() is None:
                self._con.execute("INSERT INTO meta(k,v) VALUES('cash','20000')")
        return self._con

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn().execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self._conn().execute(
            "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )

    def get_cash(self) -> float:
        return float(self.get_meta("cash", "20000") or "20000")

    def set_cash(self, cash: float) -> None:
        self.set_meta("cash", str(round(cash, 2)))

    def count_positions(self) -> int:
        row = self._conn().execute("SELECT COUNT(*) FROM positions").fetchone()
        return int(row[0] or 0)

    def list_position_symbols(self) -> list[str]:
        return [r[0] for r in self._conn().execute("SELECT symbol FROM positions")]

    def get_position(self, symbol: str) -> PositionRow | None:
        row = self._conn().execute(
            "SELECT id, qty, entry_px, stop_px, target_px, opened_ts FROM positions WHERE symbol=?",
            (symbol,),
        ).fetchone()
        if not row:
            return None
        return PositionRow(
            id=row[0],
            symbol=symbol,
            qty=float(row[1]),
            entry_px=float(row[2]),
            stop_px=float(row[3]),
            target_px=float(row[4]),
            opened_at=str(row[5]),
        )

    def list_positions(self) -> list[PositionRow]:
        rows = self._conn().execute(
            "SELECT id, symbol, qty, entry_px, stop_px, target_px, opened_ts FROM positions"
        ).fetchall()
        return [
            PositionRow(
                id=r[0],
                symbol=r[1],
                qty=float(r[2]),
                entry_px=float(r[3]),
                stop_px=float(r[4]),
                target_px=float(r[5]),
                opened_at=str(r[6]),
            )
            for r in rows
        ]

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
        self._conn().execute(
            "INSERT INTO positions(symbol,qty,entry_px,stop_px,target_px,opened_ts) VALUES(?,?,?,?,?,?)",
            (symbol, qty, entry_px, stop_px, target_px, opened_ts),
        )

    def delete_position(self, symbol: str) -> None:
        self._conn().execute("DELETE FROM positions WHERE symbol=?", (symbol,))

    def update_position_extra(self, symbol: str, extra: dict[str, Any]) -> None:
        pass  # optional for sqlite v1

    def list_open_pending(self) -> list[PendingRow]:
        rows = self._conn().execute(
            """SELECT id, symbol, signal_ts, trigger_px, stop_px, target_px, deadline_ts, status
               FROM pending WHERE status='open'"""
        ).fetchall()
        return [
            PendingRow(
                id=r[0],
                symbol=r[1],
                signal_ts=str(r[2]),
                trigger_px=float(r[3]),
                stop_px=float(r[4]),
                target_px=float(r[5]),
                deadline_ts=str(r[6]),
                status=str(r[7]),
            )
            for r in rows
        ]

    def list_open_pending_symbols(self) -> list[str]:
        return [
            r[0]
            for r in self._conn().execute(
                "SELECT DISTINCT symbol FROM pending WHERE status='open'"
            )
        ]

    def get_open_pending(self, symbol: str) -> PendingRow | None:
        row = self._conn().execute(
            """SELECT id, signal_ts, trigger_px, stop_px, target_px, deadline_ts, status
               FROM pending WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if not row:
            return None
        return PendingRow(
            id=row[0],
            symbol=symbol,
            signal_ts=str(row[1]),
            trigger_px=float(row[2]),
            stop_px=float(row[3]),
            target_px=float(row[4]),
            deadline_ts=str(row[5]),
            status=str(row[6]),
        )

    def list_pending_recent(self, limit: int = 20) -> list[PendingRow]:
        rows = self._conn().execute(
            """SELECT id, symbol, signal_ts, trigger_px, stop_px, target_px, deadline_ts, status
               FROM pending ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            PendingRow(
                id=r[0],
                symbol=r[1],
                signal_ts=str(r[2]),
                trigger_px=float(r[3]),
                stop_px=float(r[4]),
                target_px=float(r[5]),
                deadline_ts=str(r[6]),
                status=str(r[7]),
            )
            for r in rows
        ]

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
        cur = self._conn().execute(
            """INSERT INTO pending(symbol,signal_ts,trigger_px,stop_px,target_px,deadline_ts,status,fill_model,qty)
               VALUES(?,?,?,?,?,?, 'open', ?, ?)""",
            (symbol, signal_ts, trigger_px, stop_px, target_px, deadline_ts, fill_model, qty),
        )
        return int(cur.lastrowid or 0)

    def update_pending_status(self, pending_id: int, status: str) -> None:
        self._conn().execute("UPDATE pending SET status=? WHERE id=?", (status, pending_id))

    def fill_pending_at_open(
        self, pending_id: int, fill_px: float, fill_date: str, fill_model: str
    ) -> None:
        self.update_pending_status(pending_id, "filled")

    def append_journal(self, ts: str, symbol: str | None, kind: str, message: str) -> None:
        self._conn().execute(
            "INSERT INTO journal(ts,symbol,kind,message) VALUES(?,?,?,?)",
            (ts, symbol, kind, message),
        )

    def list_journal(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT ts, symbol, kind, message FROM journal ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"ts": r[0], "symbol": r[1], "kind": r[2], "message": r[3]} for r in rows
        ]

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
        pass  # sqlite ma44 doesn't use closed_trades table in v1 CLI

    def list_closed_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def insert_equity_snapshot(
        self, as_of: date, cash: float, equity: float, benchmark: dict | None = None
    ) -> None:
        pass

    def set_portfolio_equity(self, equity: float) -> None:
        self.set_meta("equity", str(round(equity, 2)))

    def commit(self) -> None:
        if self._con:
            self._con.commit()

    def close(self) -> None:
        if self._con:
            self._con.close()
            self._con = None
