"""Portfolio circuit breaker (Section 6) — disabled.

Equity-curve halt/resume is counterproductive for trend-following: once halted
at high drawdown, a cash-only book cannot recover to the resume threshold.
Entry gating uses regime_active / daily_alignment only.
"""

from __future__ import annotations

import pandas as pd


class PortfolioCircuitBreaker:
    """No-op: always allows new entries."""

    def __init__(self, halt_dd: float = 0.08, resume_dd: float = 0.04):
        self.halt_dd = halt_dd
        self.resume_dd = resume_dd
        self.halted = False
        self.peak = 0.0

    def update(self, equity: float) -> bool:
        """Return True if new entries allowed (always True)."""
        return True

    def rolling_check(self, equity_curve: pd.Series) -> pd.Series:
        return pd.Series(True, index=equity_curve.index, name="entries_allowed")
