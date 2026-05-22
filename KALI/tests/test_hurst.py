import numpy as np

from kali.features.fractal import anis_lloyd_expected, multi_scale_hurst


def test_anis_lloyd_positive():
    assert anis_lloyd_expected(10) > 0


def test_multi_scale_hurst_random_walk():
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.01, 120)
    h, se, r2, regime = multi_scale_hurst(returns, window=60)
    assert 0.3 <= h <= 0.7
    assert regime in ("TRENDING", "MEAN_REVERTING", "INDETERMINATE")
    assert se >= 0


def test_multi_scale_hurst_trending():
    returns = np.linspace(0.001, 0.002, 80)
    h, _, _, regime = multi_scale_hurst(returns, window=60, scales=[10, 20, 30])
    assert h >= 0.4
