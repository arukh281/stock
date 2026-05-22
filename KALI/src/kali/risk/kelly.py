"""Regime-conditional half-Kelly with bootstrap priors."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class RegimeStats:
    wins: int = 0
    losses: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0


@dataclass
class KellyEngine:
    bootstrap: dict[str, float]
    min_trades: int = 30
    winrate_ci_floor: float = 0.35
    regime_stats: dict[str, RegimeStats] = field(default_factory=dict)

    def record_trade(self, regime: str, pnl: float) -> None:
        if regime not in self.regime_stats:
            self.regime_stats[regime] = RegimeStats()
        s = self.regime_stats[regime]
        if pnl >= 0:
            s.wins += 1
            s.gross_win += pnl
        else:
            s.losses += 1
            s.gross_loss += abs(pnl)

    def kelly_fraction(self, regime: str) -> float:
        s = self.regime_stats.get(regime)
        n = (s.wins + s.losses) if s else 0
        if n < self.min_trades:
            return self.bootstrap.get(regime, 0.0) / 2.0

        p = s.wins / n
        if s.gross_loss <= 0:
            return 0.0
        b = (s.gross_win / max(s.wins, 1)) / (s.gross_loss / max(s.losses, 1))
        q = 1 - p
        f_star = (b * p - q) / b if b > 0 else 0.0

        # Wilson CI lower bound for win rate
        z = 1.96
        denom = 1 + z**2 / n
        center = p + z**2 / (2 * n)
        margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
        ci_low = (center - margin) / denom

        if f_star <= 0:
            return 0.0

        if ci_low < self.winrate_ci_floor:
            if regime == "BULL_TREND":
                return 0.10
            if regime == "SIDEWAYS":
                return 0.05
            return 0.0

        safe_kelly = f_star / 2.0
        if regime == "BULL_TREND":
            return min(safe_kelly, 0.35)
        if regime == "SIDEWAYS":
            return min(safe_kelly, 0.10)
        return 0.0
