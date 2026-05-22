import pandas as pd

from kali.config import load_config
from kali.signals.entries import hurst_confluence_mask, long_entry_signal
from kali.signals.exits import exit_signal
from kali.signals.mtf_gate import weekly_uptrend


def _synthetic_row(**overrides):
    row = {
        "regime_active": "BULL_TREND",
        "daily_alignment": True,
        "weekly_hurst_regime": "TRENDING",
        "cms": 0.65,
        "hurst_regime": "TRENDING",
        "kalman_velocity": 0.8,
        "volume_z": 2.1,
        "obv_divergence": False,
        "macd_curvature": 0.1,
        "close": 400,
        "open": 395,
        "atr_14": 8,
        "chandelier_exit": 370,
        "entropy_pct": 0.5,
    }
    row.update(overrides)
    return row


def test_entry_conditions_itc_style():
    cfg = load_config()
    idx = pd.date_range("2024-06-01", periods=5, freq="B")
    df = pd.DataFrame([_synthetic_row()] * 5, index=idx)
    entry = long_entry_signal(df, cfg)
    assert entry.iloc[-1]


def test_entry_confluence_two_of_four():
    cfg = load_config()
    idx = pd.date_range("2024-06-01", periods=1, freq="B")
    df = pd.DataFrame(
        [
            _synthetic_row(
                cms=0.65,
                volume_z=0.5,
                macd_curvature=-0.1,
                hurst_regime="TRENDING",
            )
        ],
        index=idx,
    )
    entry = long_entry_signal(df, cfg)
    assert entry.iloc[0]


def test_entry_confluence_one_of_four_fails():
    cfg = load_config()
    idx = pd.date_range("2024-06-01", periods=1, freq="B")
    df = pd.DataFrame(
        [
            _synthetic_row(
                cms=0.1,
                volume_z=0.5,
                macd_curvature=-0.1,
                hurst_regime="MEAN_REVERTING",
            )
        ],
        index=idx,
    )
    entry = long_entry_signal(df, cfg)
    assert not entry.iloc[0]


def test_entry_weekly_indeterminate_requires_daily_trending():
    cfg = load_config()
    idx = pd.date_range("2024-06-01", periods=1, freq="B")
    df = pd.DataFrame(
        [
            _synthetic_row(
                weekly_hurst_regime="INDETERMINATE",
                hurst_regime="INDETERMINATE",
                cms=0.1,
                volume_z=0.5,
                macd_curvature=-0.1,
            )
        ],
        index=idx,
    )
    entry = long_entry_signal(df, cfg)
    assert not entry.iloc[0]

    df2 = pd.DataFrame(
        [
            _synthetic_row(
                weekly_hurst_regime="INDETERMINATE",
                hurst_regime="TRENDING",
                volume_z=2.0,
                macd_curvature=0.1,
            )
        ],
        index=idx,
    )
    entry2 = long_entry_signal(df2, cfg)
    assert entry2.iloc[0]


def test_weekly_uptrend_indeterminate_from_config():
    cfg = load_config()
    weekly = pd.DataFrame(
        {
            "w_hurst_regime": ["INDETERMINATE"],
            "w_kalman_velocity": [0.5],
            "w_cms": [0.3],
        },
        index=pd.date_range("2024-01-05", periods=1, freq="W-FRI"),
    )
    assert weekly_uptrend(weekly, cfg).iloc[0]
    weekly.loc[weekly.index[0], "w_kalman_velocity"] = -0.1
    assert not weekly_uptrend(weekly, cfg).iloc[0]


def test_hurst_weekly_trending_daily_indeterminate():
    idx = pd.date_range("2024-06-01", periods=1, freq="B")
    df = pd.DataFrame(
        [_synthetic_row(weekly_hurst_regime="TRENDING", hurst_regime="INDETERMINATE")],
        index=idx,
    )
    assert hurst_confluence_mask(df).iloc[0]


def test_exit_soft_stop_removed():
    """OBV+MACD soft combo no longer triggers exit after pruning."""
    cfg = load_config()
    idx = pd.date_range("2024-07-01", periods=5, freq="B")
    rows = []
    for i in range(5):
        r = _synthetic_row()
        r["obv_divergence"] = i >= 3
        r["macd_curvature"] = -0.1 if i >= 2 else 0.1
        r["close"] = 200.0
        r["chandelier_exit"] = 150.0
        r["regime_active"] = "BULL_TREND"
        r["entropy_pct"] = 0.1
        r["hurst_regime"] = "MEAN_REVERTING"
        rows.append(r)
    df = pd.DataFrame(rows, index=idx)
    ex = exit_signal(df, cfg=cfg)
    assert not ex.any()
