"""ATR risk sizing with Kelly cap and correlation penalty."""

from __future__ import annotations

import pandas as pd


def atr_position_size(
    portfolio_equity: float,
    entry: float,
    stop: float,
    risk_pct: float = 0.01,
    kelly_frac: float = 1.0,
    available_cash: float | None = None,
    friction: float = 0.0,
) -> int:
    """Size from 1% equity risk at stop; Kelly caps max position cost (not risk)."""
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0 or portfolio_equity <= 0 or kelly_frac <= 0:
        return 0

    target_risk_capital = portfolio_equity * risk_pct
    theoretical_shares = int(target_risk_capital / risk_per_share)

    max_position_cost = portfolio_equity * kelly_frac
    theoretical_cost = theoretical_shares * entry

    if theoretical_cost > max_position_cost:
        shares = int(max_position_cost / entry)
    else:
        shares = theoretical_shares

    if available_cash is not None:
        max_affordable = int(available_cash / (entry * (1 + friction)))
        shares = min(shares, max_affordable)

    return max(0, shares)


def correlation_matrix(returns: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    return returns.tail(window).corr()


def apply_correlation_penalty(
    sizes: dict[str, int],
    returns: pd.DataFrame,
    threshold: float = 0.70,
    penalty: float = 0.30,
) -> dict[str, int]:
    if len(sizes) < 2:
        return sizes
    corr = correlation_matrix(returns)
    adjusted = sizes.copy()
    symbols = list(sizes.keys())
    for i, a in enumerate(symbols):
        for b in symbols[i + 1 :]:
            if a not in corr.columns or b not in corr.columns:
                continue
            rho = corr.loc[a, b]
            if pd.notna(rho) and abs(rho) > threshold:
                adjusted[a] = int(adjusted[a] * (1 - penalty))
                adjusted[b] = int(adjusted[b] * (1 - penalty))
    return adjusted


def max_positions_for_regime(regime: str, cfg: dict) -> int:
    if regime == "BULL_TREND":
        return cfg["risk"]["max_positions_bull"]
    if regime == "SIDEWAYS":
        return cfg["risk"]["max_positions_sideways"]
    return 0
