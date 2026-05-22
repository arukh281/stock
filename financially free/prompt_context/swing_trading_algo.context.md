# Context: swing_trading_algo.py

## What it does
Python backtesting system for a Nifty 50 swing strategy: macro index filter (18M ROC + optional 200-DMA), per-stock VCP breakouts, portfolio cash/position management, grid optimization, and Nifty buy-and-hold benchmark.

## Exports / Public surface
- `_flatten_yfinance_columns(df)` — normalizes yfinance MultiIndex columns; returns DataFrame
- `NIFTY_50_TICKERS` — static today's list (not for historical backtests)
- `SwingTradingAlgo(index_ticker="^NSEI", lookback_roc=18)` — constructor
  - `calculate_macro_roc(start, end)` — downloads index, computes monthly ROC forward-filled daily + Index_Above_200DMA; returns daily DataFrame
  - `calculate_vcp_and_emas(df)` — adds EMA_21, vol contraction, VCP_Breakout, Exit_1/2/3, stop levels, Volume_Ratio; returns df
  - `_entry_signal(row, max_roc, min_volume_ratio, require_index_trend)` — bool: ROC < max_roc, optional index above 200-DMA, volume ratio, VCP_Breakout
  - `prepare_universe(tickers, backtest_start, end_date)` — batch download + macro join + indicators; dict[ticker]->df
  - `benchmark_buy_hold(start, end, initial_capital)` — index B&H return dict
  - `scan_stock(ticker, start, end)` — single ticker scan; returns (df, valid_buys) or None
  - `backtest_stock(...)` — one position at a time; macro + VCP entry, Exit_2 exit
  - `backtest_portfolio(...)` — multi-stock shared cash; uses `get_yf_constituents` when `use_historical_universe=True`; ranks by volume ratio; exits on EMA/stop/index removal
  - `optimize_portfolio(...)` — 96-combo grid on Nifty 50 historical universe
- CLI: no args → portfolio backtest; `optimize` → grid; `single [TICKER]` → one stock

## What it does NOT do
- No Nifty Midcap 150 / Smallcap 250 universe or membership history
- No per-stock Stage 2 trend filter (150/200 SMA, 50 > 200 SMA)
- No relative strength (RS) ranking vs index — only `rank_by_volume` on Volume_Ratio
- No walk-forward / out-of-sample optimization
- `backtest_stock` ignores `require_index_trend` and portfolio params
- Docstring mentions `lookback_roc=20` for smallcap but no smallcap code path

## Constraints and edge cases
- Warmup: `lookback_roc + 3` months before backtest_start
- Entries: signal on prev bar, fill at next open; exits before entries each day
- Min 60 rows per ticker to load; skips failed downloads
- `use_historical_universe` hard-wired to `nifty50_history.get_yf_constituents` / `all_yf_tickers_between`
- Optimizer maximizes `strategy_return_pct` only
- Commission default 0.1% per side

## Last read
2026-05-20 swing_trading_algo.py
