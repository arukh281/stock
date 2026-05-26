# Context: financially free/daily_scanner.py

## What it does
Daily post-close scanner for Nifty Smallcap 250: macro ROC gate on smallcap index, VCP + Stage 2 + RS top 20%, prints buy/exit action tables and saves `scan_results_YYYYMMDD.csv`. No portfolio or trade logging.

## Exports / Public surface
- `DailyScanner(index_ticker, roc_lookback, max_roc, rs_top_pct)` — constructor
  - `get_macro_regime()` — bool safe_to_buy, current ROC
  - `calculate_technical_state(df)` — last-bar indicators
  - `scan_universe(tickers)` — batch yfinance download → DataFrame
- `_print_action_list(scanner, results_df, is_safe_to_buy, current_roc)` — console tables
- `__main__`: uses `current_smallcap_tickers()` from `nifty_smallcap_history`

## What it does NOT do
- No shared-cash portfolio simulator in live mode (that exists only in `swing_trading_algo.backtest_portfolio`)
- No persistence of open positions, cash, or executed trades
- No Supabase or web UI
- Not wired to Midcap 150 production config (`swing_trading_algo midcap` is backtest-only)

## Constraints and edge cases
- Run after market close; ADV cap 1% of 20d avg dollar volume; max chase 2% above close
- Needs ~252 bars per ticker; chunk size 50 for downloads
- Exit alerts are informational (21-EMA); user must track holdings manually

## Last read
2026-05-20 financially free/daily_scanner.py
