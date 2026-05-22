from pathlib import Path

import pytest

from kali.config import load_config
from kali.data.screener import (
    ScreenerClient,
    _derive_debt_to_equity,
    _derive_fcf_yield,
    _extract_compounded_5y,
    _extract_sector,
    compute_piotroski_from_ratios,
)
from bs4 import BeautifulSoup

FIXTURES = Path(__file__).parent / "fixtures"


def test_screener_fixture_passes_filters():
    cfg = load_config()
    html = (FIXTURES / "screener_itc.html").read_text()
    client = ScreenerClient(cfg)
    snap = client.fetch_from_fixture("ITC", html)
    snap.sector_median_roe = 15.0
    assert snap.roe_pct == 22.5
    assert snap.debt_to_equity == 0.05
    assert snap.eps_cagr_5y == 14.2
    assert snap.fcf_yield_pct == 5.1
    assert snap.promoter_holding_pct == 51.2
    assert snap.piotroski_f_score == 8
    assert snap.passes_filters(cfg)


def test_live_style_html_mapping():
    html = (FIXTURES / "screener_itc_live.html").read_text()
    soup = BeautifulSoup(html, "html.parser")
    sector, sector_path = _extract_sector(soup)
    assert sector == "Fast Moving Consumer Goods"
    assert sector_path == "/market/IN04/"
    assert _extract_compounded_5y(soup, "Compounded Profit Growth") == 5.0

    ratios = {
        "ROE": 27.9,
        "Borrowings+": 143.0,
        "Equity Capital": 1253.0,
        "Reserves": 67332.0,
        "Free Cash Flow": 15120.0,
        "Market Cap": 385344.0,
        "Cash from Operating Activity+": 16751.0,
        "Net Profit+": 34743.0,
        "OPM %": 34.0,
    }
    assert _derive_debt_to_equity(ratios) == pytest.approx(143 / (1253 + 67332), rel=1e-4)
    assert _derive_fcf_yield(ratios) == pytest.approx(15120 / 385344 * 100, rel=1e-3)
    assert compute_piotroski_from_ratios(ratios) >= 7

    cfg = load_config()
    snap = ScreenerClient(cfg).fetch_from_fixture("ITC", html)
    snap.sector_median_roe = 15.0
    assert snap.roe_pct == 27.9
    assert snap.eps_cagr_5y == 5.0
    assert snap.debt_to_equity is not None
    assert snap.fcf_yield_pct is not None


@pytest.mark.integration
def test_live_screener_fetch_reliance():
    cfg = load_config()
    snap = ScreenerClient(cfg).fetch("RELIANCE", force=True)
    assert snap.roe_pct is not None
    assert snap.sector is not None
    assert snap.sector_median_roe is not None
    assert snap.debt_to_equity is not None
    assert snap.eps_cagr_5y is not None
    assert snap.promoter_holding_pct is not None
    assert snap.piotroski_f_score is not None
