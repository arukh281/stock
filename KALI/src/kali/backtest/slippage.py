"""Amihud-scaled slippage via backtrader CommissionInfo."""

from __future__ import annotations

import backtrader as bt


class AmihudCommission(bt.CommInfoBase):
    params = (
        ("stocklike", True),
        ("commtype", bt.CommInfoBase.COMM_PERC),
        ("percabs", True),
        ("commission", 0.001),
        ("amihud_scale", 1.0),
    )

    def getcommission(self, size, price, pseudoexec=False):
        base = abs(size) * price * self.p.commission
        amihud = getattr(self.p, "_amihud", 0.0) or 0.0
        impact = base * (1 + self.p.amihud_scale * amihud * 1e6)
        return impact

    def set_amihud(self, value: float) -> None:
        self.p._amihud = value
