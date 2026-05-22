from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class PositionRow:
    id: int | None
    symbol: str
    qty: float
    entry_px: float
    stop_px: float | None
    target_px: float | None
    opened_at: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingRow:
    id: int | None
    symbol: str
    signal_ts: str
    trigger_px: float | None
    stop_px: float | None
    target_px: float | None
    deadline_ts: str | None
    status: str
    qty: float | None = None
    fill_model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LedgerStore(Protocol):
    """Paper ledger interface shared by SQLite (44ma CLI) and Supabase (sandbox)."""

    def get_meta(self, key: str, default: str | None = None) -> str | None: ...
    def set_meta(self, key: str, value: str) -> None: ...
    def get_cash(self) -> float: ...
    def set_cash(self, cash: float) -> None: ...

    def count_positions(self) -> int: ...
    def list_position_symbols(self) -> list[str]: ...
    def get_position(self, symbol: str) -> PositionRow | None: ...
    def list_positions(self) -> list[PositionRow]: ...
    def insert_position(
        self,
        symbol: str,
        qty: float,
        entry_px: float,
        stop_px: float,
        target_px: float,
        opened_ts: str,
        extra: dict[str, Any] | None = None,
    ) -> None: ...
    def delete_position(self, symbol: str) -> None: ...
    def update_position_extra(self, symbol: str, extra: dict[str, Any]) -> None: ...

    def list_open_pending_symbols(self) -> list[str]: ...
    def get_open_pending(self, symbol: str) -> PendingRow | None: ...
    def list_pending_recent(self, limit: int = 20) -> list[PendingRow]: ...
    def list_open_pending(self) -> list[PendingRow]: ...
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
    ) -> int: ...
    def update_pending_status(self, pending_id: int, status: str) -> None: ...
    def fill_pending_at_open(
        self, pending_id: int, fill_px: float, fill_date: str, fill_model: str
    ) -> None: ...

    def append_journal(
        self, ts: str, symbol: str | None, kind: str, message: str
    ) -> None: ...
    def list_journal(self, limit: int = 50) -> list[dict[str, Any]]: ...

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
    ) -> None: ...
    def list_closed_trades(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def insert_equity_snapshot(
        self, as_of: date, cash: float, equity: float, benchmark: dict | None = None
    ) -> None: ...
    def set_portfolio_equity(self, equity: float) -> None: ...

    def commit(self) -> None: ...
    def close(self) -> None: ...
