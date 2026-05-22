import numpy as np
import pandas as pd

from kali.features.volatility import rolling_hmm_vol_state


def test_hmm_state_invariant_to_future_data():
    idx = pd.date_range("2018-01-01", periods=400, freq="B")
    rng = np.random.default_rng(42)
    yz = pd.Series(rng.lognormal(0, 0.15, len(idx)).cumsum() * 0.01 + 0.2, index=idx, name="yz_vol")

    states_a = rolling_hmm_vol_state(yz, train_window=252, retrain_step=20)
    yz_perturbed = yz.copy()
    yz_perturbed.iloc[200:] = yz_perturbed.iloc[200:] * 3.0
    states_b = rolling_hmm_vol_state(yz_perturbed, train_window=252, retrain_step=20)

    check_idx = yz.index[120:200]
    pd.testing.assert_series_equal(
        states_a.loc[check_idx],
        states_b.loc[check_idx],
        check_names=False,
    )
