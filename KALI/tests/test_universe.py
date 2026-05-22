from datetime import date
from pathlib import Path

import pandas as pd

from kali.config import load_config
from kali.data.screener import ScreenerClient
from kali.data.universe import (
    apply_fundamental_mask,
    is_fundamentally_approved,
    resolve_fundamental_universe,
    should_refresh_screener,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_fundamental_approval_pit_symbol():
    cfg = load_config()
    assert is_fundamentally_approved("ITC", pd.Timestamp("2020-01-15"), cfg)


def test_fundamental_rejects_unknown_symbol():
    cfg = load_config()
    assert not is_fundamentally_approved("FAKECO", pd.Timestamp("2020-01-15"), cfg)


def test_apply_fundamental_mask_column():
    cfg = load_config()
    idx = pd.date_range("2020-01-01", periods=3, freq="B")
    df = pd.DataFrame({"close": [1, 2, 3]}, index=idx)
    out = apply_fundamental_mask(df, "ITC", cfg)
    assert out["is_fundamentally_approved"].all()


def test_should_refresh_screener_first_week_of_quarter():
    cfg = load_config()
    assert should_refresh_screener(date(2024, 1, 3), cfg)
    assert should_refresh_screener(date(2024, 4, 5), cfg)
    assert not should_refresh_screener(date(2024, 1, 15), cfg)
    assert not should_refresh_screener(date(2024, 3, 1), cfg)


def test_resolve_fundamental_universe_fixture_pass():
    cfg = load_config()
    html = (FIXTURES / "screener_itc.html").read_text()
    client = ScreenerClient(cfg)
    snap = client.fetch_from_fixture("ITC", html)
    snap.sector_median_roe = 15.0

    class _StubClient:
        def fetch(self, symbol: str, force: bool = False):
            return snap

    active, snapshots, report = resolve_fundamental_universe(
        ["ITC"],
        as_of=snap.as_of,
        cfg=cfg,
        client=_StubClient(),
        force_screener=False,
    )
    assert active == ["ITC"]
    assert "ITC" in snapshots
    assert report.loc[report["symbol"] == "ITC", "in_universe"].iloc[0]
