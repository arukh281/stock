import pandas as pd

from kali.signals.mtf_gate import attach_mtf_columns, weekly_uptrend
from kali.validation.lookahead import assert_no_lookahead, count_lookahead_violations


def test_weekly_gate_no_lookahead():
    daily_idx = pd.date_range("2024-01-01", periods=20, freq="B")
    weekly_idx = pd.date_range("2024-01-05", periods=4, freq="W-FRI")
    daily = pd.DataFrame(index=daily_idx)
    weekly = pd.DataFrame(
        {
            "w_hurst_regime": ["TRENDING"] * 4,
            "w_kalman_velocity": [1.0, 1.0, -1.0, 1.0],
            "w_cms": [0.5, 0.6, 0.7, 0.8],
        },
        index=weekly_idx,
    )
    weekly["W_UPTREND"] = weekly_uptrend(weekly)
    weekly["W_UPTREND_lagged"] = weekly["W_UPTREND"].shift(1)
    daily = attach_mtf_columns(daily, weekly)
    failures = count_lookahead_violations(daily, weekly)
    assert failures == 0
    assert_no_lookahead(daily, weekly)


def test_weekly_uptrend_allows_indeterminate():
    weekly = pd.DataFrame(
        {
            "w_hurst_regime": ["INDETERMINATE"],
            "w_kalman_velocity": [0.5],
            "w_cms": [0.3],
        },
        index=pd.date_range("2024-01-05", periods=1, freq="W-FRI"),
    )
    assert weekly_uptrend(weekly).iloc[0]
