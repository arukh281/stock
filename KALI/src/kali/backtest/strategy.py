"""Backtrader strategy with T+1 execution and KALI signals."""

from __future__ import annotations

import backtrader as bt
import pandas as pd

from kali.risk.circuit_breaker import PortfolioCircuitBreaker
from kali.risk.kelly import KellyEngine
from kali.risk.sizing import atr_position_size, max_positions_for_regime


class KaliStrategy(bt.Strategy):
    params = (
        ("features", None),
        ("cfg", None),
        ("symbol", ""),
    )

    def __init__(self):
        self.features: pd.DataFrame = self.p.features
        self.cfg = self.p.cfg
        self.order = None
        self.entry_bar = None
        self.kelly = KellyEngine(
            bootstrap=self.cfg["risk"]["kelly_bootstrap"],
            min_trades=self.cfg["risk"]["kelly_min_trades"],
            winrate_ci_floor=self.cfg["risk"]["kelly_winrate_ci_floor"],
        )
        self.cb = PortfolioCircuitBreaker(
            halt_dd=self.cfg["risk"]["circuit_breaker_dd"],
            resume_dd=self.cfg["risk"]["circuit_resume_dd"],
        )

    def _row(self) -> pd.Series | None:
        dt = self.data.datetime.date(0)
        ts = pd.Timestamp(dt)
        if ts not in self.features.index:
            return None
        return self.features.loc[ts]

    def next(self):
        if self.order:
            return
        row = self._row()
        if row is None:
            return

        if row.get("unexecutable", False):
            return

        equity = self.broker.getvalue()
        if not self.cb.update(equity):
            if self.position:
                self._maybe_exit(row)
            return

        if self.position:
            self._maybe_exit(row)
            return

        regime = row.get("regime_active", "BEAR_TREND")
        if max_positions_for_regime(regime, self.cfg) == 0:
            return
        if row.get("regime_risk_off", False):
            return
        if not row.get("long_entry", False):
            return

        entry = float(self.data.open[0])
        stop = float(row.get("stop_loss", entry - 3 * row.get("atr_14", 1)))
        kelly_frac = self.kelly.kelly_fraction(regime)
        if kelly_frac <= 0 and regime in ("BEAR_TREND", "DISTRIBUTION"):
            return

        size = atr_position_size(
            equity,
            entry,
            stop,
            risk_pct=self.cfg["risk"]["risk_per_trade_pct"],
            kelly_frac=kelly_frac if kelly_frac > 0 else 0.01,
        )
        if size <= 0:
            return
        self.order = self.buy(size=size)
        self.entry_bar = len(self)

    def _maybe_exit(self, row: pd.Series) -> None:
        if row.get("exit_signal", False):
            self.order = self.close()

    def notify_order(self, order):
        if order.status in (order.Completed, order.Canceled, order.Margin, order.Rejected):
            self.order = None

    def notify_trade(self, trade):
        if trade.isclosed:
            row = self._row()
            regime = row.get("regime_active", "SIDEWAYS") if row is not None else "SIDEWAYS"
            self.kelly.record_trade(regime, trade.pnlcomm)
