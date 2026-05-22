import pandas as pd

from kali.backtest.portfolio import PortfolioState, Position, apply_pyramiding
from kali.config import load_config


def test_apply_pyramiding_adds_shares_without_breakeven_stop():
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
    )
    rows = {
        "ITC": pd.Series(
            {
                "open": 131.0,
                "regime_active": "BULL_TREND",
            }
        )
    }
    apply_pyramiding(state, rows, cfg)

    pos = state.positions["ITC"]
    assert pos.has_pyramided
    assert pos.shares == 150
    assert pos.stop == 70.0
    assert pos.entry_price == (100 * 100 + 50 * 131) / 150
    assert state.cash < 500_000.0


def test_apply_pyramiding_skips_when_insufficient_cash_without_flag():
    cfg = load_config()
    cfg["risk"]["pyramid_size_frac"] = 0.5

    state = PortfolioState(cash=50.0)
    state.positions["ITC"] = Position(
        symbol="ITC",
        shares=100,
        initial_shares=100,
        entry_price=100.0,
        entry_atr=10.0,
        stop=70.0,
        entry_date=pd.Timestamp("2024-06-01"),
        regime="BULL_TREND",
    )
    rows = {"ITC": pd.Series({"open": 131.0, "regime_active": "BULL_TREND"})}
    apply_pyramiding(state, rows, cfg)

    pos = state.positions["ITC"]
    assert not pos.has_pyramided
    assert pos.shares == 100


def test_apply_pyramiding_skips_if_already_pyramided():
    cfg = load_config()
    state = PortfolioState(cash=100_000.0)
    state.positions["ITC"] = Position(
        symbol="ITC",
        shares=150,
        initial_shares=100,
        entry_price=105.0,
        entry_atr=10.0,
        stop=105.0,
        entry_date=pd.Timestamp("2024-06-01"),
        regime="BULL_TREND",
        has_pyramided=True,
    )
    rows = {"ITC": pd.Series({"open": 200.0, "regime_active": "BULL_TREND"})}
    apply_pyramiding(state, rows, cfg)
    assert state.positions["ITC"].shares == 150
