from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from kali.config import load_config
from kali.live.daily_reconcile import (
    FILL_BUY,
    FILL_EXIT,
    PAPER_FRICTION,
    _process_open_pendings,
)


class FakeStore:
    def __init__(self, cash: float, *, positions=None, pendings=None):
        self.cash = cash
        self.positions = {p.symbol: p for p in (positions or [])}
        self.pendings = {p.id: p for p in (pendings or [])}
        self.closed_trades: list[dict] = []
        self.journal: list[tuple[str, str | None, str, str]] = []

    def get_cash(self) -> float:
        return self.cash

    def set_cash(self, cash: float) -> None:
        self.cash = cash

    def list_positions(self) -> list[SimpleNamespace]:
        return list(self.positions.values())

    def get_position(self, symbol: str):
        return self.positions.get(symbol)

    def insert_position(
        self,
        symbol: str,
        qty: float,
        entry_px: float,
        stop_px: float,
        target_px: float,
        opened_ts: str,
        extra: dict | None = None,
    ) -> None:
        self.positions[symbol] = SimpleNamespace(
            symbol=symbol,
            qty=qty,
            entry_px=entry_px,
            stop_px=stop_px,
            target_px=target_px,
            opened_at=opened_ts,
            extra=extra or {},
        )

    def delete_position(self, symbol: str) -> None:
        self.positions.pop(symbol, None)

    def list_open_pending(self) -> list[SimpleNamespace]:
        return [p for p in self.pendings.values() if p.status == "open"]

    def update_pending_status(self, pending_id: int, status: str) -> None:
        self.pendings[pending_id].status = status

    def fill_pending_at_open(
        self, pending_id: int, fill_px: float, fill_date: str, fill_model: str
    ) -> None:
        pending = self.pendings[pending_id]
        pending.status = "filled"
        pending.fill_model = fill_model
        pending.extra = {"fill_px": fill_px, "fill_date": fill_date}

    def insert_closed_trade(
        self,
        symbol: str,
        qty: float,
        entry_date,
        exit_date,
        entry_px: float,
        exit_px: float,
        return_pct: float | None,
        exit_reason: str | None,
        fill_model: str | None,
        extra: dict | None = None,
    ) -> None:
        self.closed_trades.append(
            {
                "symbol": symbol,
                "qty": qty,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry_px": entry_px,
                "exit_px": exit_px,
                "return_pct": return_pct,
                "exit_reason": exit_reason,
                "fill_model": fill_model,
                "extra": extra or {},
            }
        )

    def append_journal(self, ts: str, symbol: str | None, kind: str, message: str) -> None:
        self.journal.append((ts, symbol, kind, message))


def _feature_map(session: str, *, open_px: float, close_px: float = 100.0, high_px: float = 100.0):
    idx = pd.DatetimeIndex([pd.Timestamp(session)])
    return {
        "TCS": pd.DataFrame(
            {"open": [open_px], "close": [close_px], "high": [high_px]},
            index=idx,
        )
    }


def test_process_open_pendings_fills_exit_next_open():
    cfg = load_config()
    session = pd.Timestamp("2024-07-03")
    position = SimpleNamespace(
        symbol="TCS",
        qty=10.0,
        entry_px=100.0,
        stop_px=92.0,
        target_px=0.0,
        opened_at="2024-07-01T00:00:00",
        extra={"entry_date": "2024-07-01"},
    )
    pending = SimpleNamespace(
        id=1,
        symbol="TCS",
        signal_ts="2024-07-02",
        trigger_px=99.0,
        stop_px=95.0,
        target_px=0.0,
        deadline_ts="2024-07-03",
        status="open",
        qty=10.0,
        fill_model=FILL_EXIT,
        extra={},
    )
    store = FakeStore(1_000.0, positions=[position], pendings=[pending])

    lines = _process_open_pendings(
        store,
        _feature_map("2024-07-03", open_px=110.0, close_px=108.0, high_px=112.0),
        session,
        cfg,
    )

    assert lines == ["TCS: EXIT filled @ 110.00 qty 10"]
    assert store.positions == {}
    assert store.pendings[1].status == "filled"
    assert store.closed_trades[0]["exit_reason"] == "trailing_stop"
    assert store.cash == pytest.approx(1_000.0 + 10.0 * 110.0 * (1 - PAPER_FRICTION))


def test_process_open_pendings_fills_buy_next_open_with_position_metadata():
    cfg = load_config()
    session = pd.Timestamp("2024-07-03")
    pending = SimpleNamespace(
        id=2,
        symbol="TCS",
        signal_ts="2024-07-02",
        trigger_px=101.0,
        stop_px=93.0,
        target_px=129.0,
        deadline_ts="2024-07-03",
        status="open",
        qty=5.0,
        fill_model=FILL_BUY,
        extra={},
    )
    store = FakeStore(1_000.0, pendings=[pending])

    lines = _process_open_pendings(
        store,
        _feature_map("2024-07-03", open_px=105.0, close_px=107.0, high_px=111.0),
        session,
        cfg,
    )

    assert lines == ["TCS: BUY filled @ 105.00 qty 5"]
    assert store.pendings[2].status == "filled"
    assert "TCS" in store.positions
    assert store.positions["TCS"].extra["entry_date"] == "2024-07-03"
    assert store.positions["TCS"].extra["initial_stop"] == 93.0
    assert store.positions["TCS"].extra["entry_atr"] == pytest.approx(4.0)
    assert store.cash == pytest.approx(1_000.0 - 5.0 * 105.0 * (1 + PAPER_FRICTION))


def test_process_open_pendings_cancels_buy_when_gap_exceeds_cash():
    cfg = load_config()
    session = pd.Timestamp("2024-07-03")
    pending = SimpleNamespace(
        id=3,
        symbol="TCS",
        signal_ts="2024-07-02",
        trigger_px=101.0,
        stop_px=93.0,
        target_px=129.0,
        deadline_ts="2024-07-03",
        status="open",
        qty=5.0,
        fill_model=FILL_BUY,
        extra={},
    )
    store = FakeStore(300.0, pendings=[pending])

    lines = _process_open_pendings(
        store,
        _feature_map("2024-07-03", open_px=105.0, close_px=107.0, high_px=111.0),
        session,
        cfg,
    )

    assert lines == ["TCS: BUY pending CANCELLED (insufficient cash at open 105.00)"]
    assert store.pendings[3].status == "cancelled"
    assert store.positions == {}
    assert store.cash == 300.0
