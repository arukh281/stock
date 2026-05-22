import numpy as np
import pandas as pd

from kali.features.volatility import yang_zhang_vol


def test_yang_zhang_positive():
    n = 30
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    high = close * 1.01
    low = close * 0.99
    open_ = close.shift(1).fillna(close.iloc[0])
    yz = yang_zhang_vol(open_, high, low, close, n=20)
    valid = yz.dropna()
    assert len(valid) > 0
    assert (valid >= 0).all()
