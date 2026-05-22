# Context: nifty50_history.py

## What it does
Point-in-time Nifty 50 membership from manually curated NSE semi-annual rebalances (2017–2025), mapping NSE symbols to Yahoo tickers for survivorship-aware backtests.

## Exports / Public surface
- `NSE_TO_YF` — dict NSE symbol → `.NS` yfinance ticker
- `get_nse_constituents(as_of_date)` — frozenset of NSE symbols on date
- `get_yf_constituents(as_of_date)` — list of Yahoo tickers for that date
- `get_yf_constituents_for_year(year)` — membership as of Jan 1
- `all_yf_tickers_between(start_date, end_date)` — union of all members ever in range
- `yearly_universe_report(start_year, end_year)` — DataFrame year/count/sample

## What it does NOT do
- No Midcap 150, Smallcap 250, or other index membership
- Not sourced from official NSE files
- No liquidity/market-cap metadata

## Constraints and edge cases
- Timeline built by rolling back `_CURRENT_NSE` through `_REBALANCES`, then replaying forward from 2016-01-01
- `get_nse_constituents` uses last rebalance on or before `as_of_date`
- Symbols without `NSE_TO_YF` mapping are dropped
- Historical removed names included (YESBANK, VEDL, HDFC, etc.)

## Last read
2026-05-20 nifty50_history.py
