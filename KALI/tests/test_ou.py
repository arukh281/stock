import numpy as np
import pandas as pd

from kali.features.momentum import compute_rolling_ou, ou_cms_weights


def test_ou_weights_short_halflife():
    assert ou_cms_weights(5) == [0.4, 0.3, 0.2, 0.1]


def test_ou_weights_long_halflife():
    assert ou_cms_weights(40) == [0.1, 0.2, 0.3, 0.4]


def test_rolling_ou_runs():
    idx = pd.date_range("2020-01-01", periods=100, freq="B")
    price = pd.Series(100 + np.cumsum(np.random.randn(100) * 0.5), index=idx)
    hl = compute_rolling_ou(price, window=60)
    valid = hl.dropna()
    assert len(valid) >= 1
