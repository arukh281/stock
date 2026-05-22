"""Tests for pruned exits, 6ATR take-profit, and pyramiding TP anchor."""

import pandas as pd

from kali.backtest.portfolio import (
    PortfolioState,
    Position,
    _cms_score,
    _sort_entry_candidates,
    _take_profit_target,
    apply_pyramiding,
)
from kali.config import load_config
from kali.signals.exits import exit_signal


def _minimal_exit_df(**overrides) -> pd.DataFrame:
    idx = pd.date_range("2024-07-01", periods=3, freq="B")
    base = {
        "close": 100.0,
        "regime_active": "BULL_TREND",
        "obv_divergence": True,
        "macd_curvature": -1.0,
        "hurst_regime": "MEAN_REVERTING",
    }
    base.update(overrides)
    return pd.DataFrame([base] * len(idx), index=idx)


def test_cms_score_missing_or_nan_sorts_last():
    assert _cms_score(pd.Series({"open": 100.0})) == float("-inf")
    assert _cms_score(pd.Series({"cms": float("nan"), "open": 100.0})) == float("-inf")
    assert _cms_score(pd.Series({"cms": 1.25, "open": 100.0})) == 1.25


def test_sort_entry_candidates_by_cms_descending():
    rows = {
        "AAA": pd.Series({"cms": 0.2, "open": 10.0}),
        "ZZZ": pd.Series({"cms": 0.9, "open": 20.0}),
        "MMM": pd.Series({"cms": 0.5, "open": 15.0}),
    }
    candidates = [
        {
            "symbol": sym,
            "row": rows[sym],
            "regime": "BULL_TREND",
            "kelly_frac": 1.0,
            "cms": _cms_score(rows[sym]),
        }
        for sym in ["AAA", "ZZZ", "MMM"]
    ]
    ranked = _sort_entry_candidates(candidates)
    assert [c["symbol"] for c in ranked] == ["ZZZ", "MMM", "AAA"]
    assert [c["cms"] for c in ranked] == [0.9, 0.5, 0.2]


def test_exit_signal_ignores_soft_and_hurst():
    cfg = load_config()
    df = _minimal_exit_df()
    ex = exit_signal(df, cfg=cfg)
    assert not ex.any()


def test_exit_signal_bear_regime_only():
    cfg = load_config()
    df = _minimal_exit_df(regime_active="BEAR_TREND")
    ex = exit_signal(df, cfg=cfg)
    assert ex.all()


def test_exit_signal_ignores_days_held_parameter():
    cfg = load_config()
    df = _minimal_exit_df()
    days = pd.Series([50, 91, 100], index=df.index)
    ex = exit_signal(df, days_held=days, cfg=cfg)
    assert not ex.any()


def test_take_profit_target_anchored_after_pyramiding():
    cfg = load_config()
    pos = Position(
        symbol="ITC",
        shares=150,
        initial_shares=100,
        entry_price=110.5,
        entry_atr=10.0,
        stop=70.0,
        entry_date=pd.Timestamp("2024-06-01"),
        regime="BULL_TREND",
        has_pyramided=True,
        tp_anchor_price=100.0,
        tp_anchor_atr=10.0,
    )
    target = _take_profit_target(pos, cfg)
    assert target == 100.0 + 6.0 * 10.0


def test_pyramiding_does_not_change_tp_anchor():
    cfg = load_config()
    cfg["risk"]["pyramid_size_frac"] = 0.5
    state = PortfolioState(cash=500_000.0)
    state.positions["ITC"] = Position(
        symbol="ITC",
        shares=100,
        initial_shares=100,
        entry_price=100.0,
        entry_atr=10.0,
        stop=70.0,
        entry_date=pd.Timestamp("2024-06-01"),
        regime="BULL_TREND",
        tp_anchor_price=100.0,
        tp_anchor_atr=10.0,
    )
    rows = {"ITC": pd.Series({"open": 131.0, "regime_active": "BULL_TREND"})}
    apply_pyramiding(state, rows, cfg)
    pos = state.positions["ITC"]
    assert pos.tp_anchor_price == 100.0
    assert pos.tp_anchor_atr == 10.0
    assert _take_profit_target(pos, cfg) == 160.0
