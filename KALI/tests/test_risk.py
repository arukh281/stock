import pandas as pd

from kali.risk.circuit_breaker import PortfolioCircuitBreaker
from kali.risk.kelly import KellyEngine
from kali.risk.sizing import atr_position_size


def test_atr_position_size_full_risk_not_multiplied_by_kelly():
    # 1% risk on ₹10k at risk/share → 416 shares; Kelly 1.0 does not shrink risk budget
    shares = atr_position_size(1_000_000, 400, 376, risk_pct=0.01, kelly_frac=1.0)
    assert shares == int(10_000 / 24)


def test_atr_position_size_kelly_caps_position_cost():
    # Same ATR risk, Kelly 0.10 caps deployable capital to 10% of equity (not 0.1% risk)
    shares = atr_position_size(1_000_000, 400, 376, risk_pct=0.01, kelly_frac=0.10)
    assert shares == int(100_000 / 400)
    assert shares < int(10_000 / 24)


def test_kelly_bootstrap():
    k = KellyEngine(bootstrap={"BULL_TREND": 0.12, "SIDEWAYS": 0.06})
    assert k.kelly_fraction("BULL_TREND") == 0.06


def _kelly_engine() -> KellyEngine:
    return KellyEngine(
        bootstrap={"BULL_TREND": 0.06, "SIDEWAYS": 0.03},
        min_trades=30,
        winrate_ci_floor=0.35,
    )


def _kelly_with_losing_streak(regime: str, n: int = 35) -> KellyEngine:
    k = _kelly_engine()
    for _ in range(n):
        k.record_trade(regime, -100.0)
    return k


def _kelly_trend_following_profile(regime: str, n: int = 40) -> KellyEngine:
    """~40% win rate, 3:1 payoff — positive f_star, Wilson CI below floor."""
    k = _kelly_engine()
    wins = int(n * 0.4)
    for i in range(n):
        k.record_trade(regime, 300.0 if i < wins else -100.0)
    return k


def test_kelly_bull_zero_when_negative_expectancy():
    k = _kelly_with_losing_streak("BULL_TREND")
    assert k.kelly_fraction("BULL_TREND") == 0.0


def test_kelly_bull_fallback_when_ci_blocked():
    k = _kelly_trend_following_profile("BULL_TREND")
    assert k.kelly_fraction("BULL_TREND") == 0.10


def test_kelly_sideways_fallback_when_ci_blocked():
    k = _kelly_trend_following_profile("SIDEWAYS")
    assert k.kelly_fraction("SIDEWAYS") == 0.05


def test_kelly_sideways_zero_when_negative_expectancy():
    k = _kelly_with_losing_streak("SIDEWAYS")
    assert k.kelly_fraction("SIDEWAYS") == 0.0


def test_kelly_bear_always_zero():
    k = _kelly_with_losing_streak("BEAR_TREND")
    assert k.kelly_fraction("BEAR_TREND") == 0.0


def test_kelly_high_confidence_bull_cap():
    k = _kelly_engine()
    for _ in range(50):
        k.record_trade("BULL_TREND", 500.0)
    for _ in range(20):
        k.record_trade("BULL_TREND", -100.0)
    frac = k.kelly_fraction("BULL_TREND")
    assert 0 < frac <= 0.35


def test_circuit_breaker_always_allows_entries():
    cb = PortfolioCircuitBreaker(halt_dd=0.08, resume_dd=0.04)
    cb.peak = 100
    assert cb.update(100)
    assert cb.update(90)
    assert cb.update(50)
    allowed = cb.rolling_check(pd.Series([100, 90, 50], index=pd.date_range("2020-01-01", periods=3)))
    assert allowed.all()
