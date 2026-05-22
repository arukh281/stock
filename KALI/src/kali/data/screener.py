"""Screener.in fundamental scraper with caching."""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from kali.config import cache_dir, load_config

_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")
_SECTOR_LINK_RE = re.compile(r"^/market/(IN\d+)/?$")
_COMPANY_LINK_RE = re.compile(r"^/company/([^/]+)/?$")


@dataclass
class FundamentalsSnapshot:
    symbol: str
    as_of: date
    roe_pct: float | None = None
    sector: str | None = None
    sector_median_roe: float | None = None
    debt_to_equity: float | None = None
    eps_cagr_5y: float | None = None
    fcf_yield_pct: float | None = None
    promoter_holding_pct: float | None = None
    promoter_pledged: bool = False
    piotroski_f_score: int | None = None
    market_cap_cr: float | None = None
    raw_ratios: dict[str, float] = field(default_factory=dict)

    def passes_filters(self, cfg: dict[str, Any]) -> bool:
        u = cfg["universe"]
        if self.roe_pct is None or self.sector_median_roe is None:
            roe_ok = False
        else:
            roe_ok = self.roe_pct > self.sector_median_roe
        if not roe_ok:
            return False
        if self.debt_to_equity is None or self.debt_to_equity >= u["debt_to_equity_max"]:
            return False
        if self.eps_cagr_5y is None or self.eps_cagr_5y < u["eps_cagr_5y_min"]:
            return False
        if self.fcf_yield_pct is None or self.fcf_yield_pct < u["fcf_yield_min"]:
            return False
        if self.promoter_holding_pct is None or self.promoter_holding_pct < u["promoter_holding_min"]:
            return False
        if self.promoter_pledged:
            return False
        f_min = u["piotroski_min"]
        if self.piotroski_f_score is None or self.piotroski_f_score < f_min:
            return False
        return True


def _parse_number(text: str) -> float | None:
    if not text:
        return None
    text = text.replace(",", "").replace("%", "").strip()
    m = _NUM_RE.search(text)
    return float(m.group()) if m else None


def _ratio_get(ratios: dict[str, float], *keys: str) -> float | None:
    for key in keys:
        if key in ratios:
            return ratios[key]
    return None


def _extract_sector(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Return (sector name, sector path) from top-level /market/INxx/ link."""
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if _SECTOR_LINK_RE.match(href):
            return anchor.get_text(strip=True) or None, href
    return None, None


def _extract_compounded_5y(soup: BeautifulSoup, section: str) -> float | None:
    for el in soup.find_all(string=lambda t: t and section in t):
        table = el.parent.find_parent("table") if el.parent else None
        if not table:
            continue
        for row in table.select("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if cells and cells[0].startswith("5 Years"):
                return _parse_number(cells[1])
    return None


def _derive_debt_to_equity(ratios: dict[str, float]) -> float | None:
    direct = _ratio_get(
        ratios,
        "Debt / Equity",
        "Debt to equity",
        "Debt to Equity",
        "Debt/Equity",
    )
    if direct is not None:
        return direct
    borrowings = _ratio_get(ratios, "Borrowings+", "Borrowings", "Total debt")
    equity = _ratio_get(ratios, "Equity Capital")
    reserves = _ratio_get(ratios, "Reserves")
    if borrowings is None or equity is None:
        return None
    denom = equity + (reserves or 0)
    return borrowings / denom if denom > 0 else None


def _derive_fcf_yield(ratios: dict[str, float]) -> float | None:
    direct = _ratio_get(
        ratios,
        "Free cash flow / Market cap",
        "FCF yield",
        "FCF / Market Cap",
    )
    if direct is not None:
        return direct
    fcf = _ratio_get(ratios, "Free Cash Flow", "Free cash flow")
    mcap = _ratio_get(ratios, "Market Cap")
    if fcf is None or not mcap:
        return None
    return (fcf / mcap) * 100


def _detect_promoter_pledged(soup: BeautifulSoup) -> bool:
    for row in soup.select("table tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if not cells:
            continue
        label = cells[0].lower()
        if "pledged" in label:
            pct = _parse_number(cells[-1])
            return pct is not None and pct > 0
    return False


def _roe_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    for li in soup.select("li.flex.flex-space-between"):
        spans = li.find_all("span")
        if len(spans) >= 2 and spans[0].get_text(strip=True) == "ROE":
            return _parse_number(spans[1].get_text(strip=True))
    return None


def compute_piotroski_from_ratios(ratios: dict[str, float]) -> int | None:
    """Approximate F-Score from available ratio deltas when direct score absent."""
    score = 0
    roe = _ratio_get(ratios, "ROE %", "ROE", "Return on equity")
    if roe is not None and roe > 0:
        score += 1
    roce = _ratio_get(ratios, "ROCE %", "ROCE")
    if roce is not None and roce > 10:
        score += 1
    cfo = _ratio_get(
        ratios,
        "Cash from Operating Activity+",
        "Cash from operations",
        "Cash from Operating Activity",
    )
    if cfo is not None and cfo > 0:
        score += 1
    profit = _ratio_get(ratios, "Net Profit+", "Net Profit")
    if cfo is not None and profit is not None and cfo > profit:
        score += 1
    de = _derive_debt_to_equity(ratios)
    if de is not None and de < 1:
        score += 1
    fcf = _ratio_get(ratios, "Free Cash Flow", "Free cash flow")
    if fcf is not None and fcf > 0:
        score += 1
    opm = _ratio_get(ratios, "OPM %")
    if opm is not None and opm > 10:
        score += 1
    if len(ratios) < 3:
        return None
    return min(score + 2, 9) if score >= 4 else None


class ScreenerClient:
    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or load_config()
        self.base_url = self.cfg["screener"]["base_url"].rstrip("/")
        self.rate_limit = self.cfg["screener"]["rate_limit_seconds"]
        self.max_retries = self.cfg["screener"]["max_retries"]
        self.sector_peer_limit = int(self.cfg["screener"].get("sector_peer_limit", 12))
        self.sector_median_default = float(
            self.cfg["screener"].get("sector_median_roe_default", 12.0)
        )
        self._cache = cache_dir(self.cfg) / self.cfg["data"]["screener_subdir"]
        self._cache.mkdir(parents=True, exist_ok=True)
        self._sector_cache = self._cache / "sectors"
        self._sector_cache.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def _cache_path(self, symbol: str) -> Path:
        return self._cache / f"{symbol.upper()}.json"

    def _sector_cache_path(self, sector_path: str) -> Path:
        slug = sector_path.strip("/").replace("/", "_")
        return self._sector_cache / f"{slug}.json"

    def _fetch_html(self, url: str) -> str:
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = httpx.get(url, timeout=30, follow_redirects=True)
                if resp.status_code == 200:
                    return resp.text
            except httpx.HTTPError:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Failed to fetch {url}")

    def _fetch_company_html(self, symbol: str) -> str:
        return self._fetch_html(f"{self.base_url}/company/{symbol.upper()}/")

    def _sector_median_roe(self, sector_path: str, exclude_symbol: str) -> float:
        cache_path = self._sector_cache_path(sector_path)
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            return float(cached["median_roe"])

        market_html = self._fetch_html(f"{self.base_url}{sector_path}")
        soup = BeautifulSoup(market_html, "html.parser")
        symbols: list[str] = []
        for anchor in soup.find_all("a", href=True):
            match = _COMPANY_LINK_RE.match(anchor["href"])
            if match:
                sym = match.group(1).upper()
                if sym != exclude_symbol.upper() and sym not in symbols:
                    symbols.append(sym)
            if len(symbols) >= self.sector_peer_limit:
                break

        roes: list[float] = []
        for sym in symbols:
            try:
                html = self._fetch_company_html(sym)
            except RuntimeError:
                continue
            roe = _roe_from_html(html)
            if roe is not None:
                roes.append(roe)

        median = (
            float(statistics.median(roes))
            if roes
            else self.sector_median_default
        )
        cache_path.write_text(
            json.dumps({"sector_path": sector_path, "median_roe": median, "n_peers": len(roes)})
        )
        return median

    def parse_html(
        self,
        symbol: str,
        html: str,
        as_of: date | None = None,
        sector_median_roe: float | None = None,
        resolve_sector_median: bool = True,
    ) -> FundamentalsSnapshot:
        soup = BeautifulSoup(html, "html.parser")
        ratios: dict[str, float] = {}

        for li in soup.select("li.flex.flex-space-between"):
            spans = li.find_all("span")
            if len(spans) >= 2:
                key = spans[0].get_text(strip=True)
                val = _parse_number(spans[1].get_text(strip=True))
                if val is not None:
                    ratios[key] = val

        for row in soup.select("table tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                val = _parse_number(cells[-1].get_text(strip=True))
                if key and val is not None and not key.endswith(":"):
                    ratios[key] = val

        sector, sector_path = _extract_sector(soup)
        roe_pct = _ratio_get(ratios, "ROE %", "ROE", "Return on equity")
        promoter_pct = _ratio_get(
            ratios,
            "Promoter holding",
            "Promoters",
            "Promoters+",
            "Promoter shares",
        )
        eps_cagr = _ratio_get(
            ratios,
            "EPS growth 5Years",
            "EPS growth 5Y",
            "EPS growth 5 Years",
        )
        if eps_cagr is None:
            eps_cagr = _extract_compounded_5y(soup, "Compounded Profit Growth")

        if sector_median_roe is None and sector_path and resolve_sector_median:
            sector_median_roe = self._sector_median_roe(sector_path, symbol)
        elif sector_median_roe is None:
            sector_median_roe = self.sector_median_default

        snap = FundamentalsSnapshot(
            symbol=symbol.upper(),
            as_of=as_of or date.today(),
            roe_pct=roe_pct,
            sector=sector,
            sector_median_roe=sector_median_roe,
            debt_to_equity=_derive_debt_to_equity(ratios),
            eps_cagr_5y=eps_cagr,
            fcf_yield_pct=_derive_fcf_yield(ratios),
            promoter_holding_pct=promoter_pct,
            promoter_pledged=_detect_promoter_pledged(soup),
            piotroski_f_score=_ratio_get(ratios, "Piotroski score"),
            market_cap_cr=_ratio_get(ratios, "Market Cap"),
            raw_ratios=ratios,
        )
        if snap.piotroski_f_score is None:
            snap.piotroski_f_score = compute_piotroski_from_ratios(ratios)
        elif isinstance(snap.piotroski_f_score, float):
            snap.piotroski_f_score = int(snap.piotroski_f_score)
        return snap

    def fetch(self, symbol: str, force: bool = False) -> FundamentalsSnapshot:
        path = self._cache_path(symbol)
        if path.exists() and not force:
            data = json.loads(path.read_text())
            data["as_of"] = date.fromisoformat(data["as_of"])
            return FundamentalsSnapshot(**data)

        html = self._fetch_company_html(symbol)
        snap = self.parse_html(symbol, html)
        path.write_text(json.dumps(asdict(snap), default=str))
        return snap

    def fetch_from_fixture(self, symbol: str, html: str) -> FundamentalsSnapshot:
        return self.parse_html(symbol, html, resolve_sector_median=False)
