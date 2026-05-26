# Context: KALI/scripts/generate_daily_signals.py

## What it does
KALI daily AMO action plan generator: PIT Nifty150 universe, optional Screener.in fundamental filter, yfinance OHLCV, feature pipeline, regime classification, BUY signals with CMS sort and share sizing. Writes CSV; optional open-positions CSV for trailing-stop breach alerts.

## Exports / Public surface
- `load_universe_symbols(universe_path, cfg)` — CSV or config PIT list
- `prepare_live_features(symbol, start, end, cfg, force_download)` — per-symbol feature frame
- `build_action_plan(...)` — aggregated BUY plan DataFrame
- `check_open_positions(positions_path, feature_map, as_of)` — trailing stop breaches
- CLI: `--universe`, `--output`, `--portfolio` (theoretical equity for sizing), `--positions`, `--skip-fundamentals`, `--force-screener`, `--force-download`

## What it does NOT do
- No automatic portfolio ledger updates from signals
- No broker API (Zerodha/Upstox) — noted as TODO in script
- No web UI or Supabase
- Portfolio backtest (`run_portfolio_backtest`) is separate from daily script; daily script does not simulate cash/slots over time
- No SQL audit table (IMPLEMENTATION_REPORT: Phase 2 not done)

## Constraints and edge cases
- Intended cron ~15:35 IST after NSE close
- Default theoretical portfolio ₹1,000,000 for `atr_position_size`
- Screener refresh in rebalance months (Jan/Apr/Jul/Oct) or `--force-screener`
- Open positions file expected columns: symbol, entry_price, entry_atr, initial_stop, optional highest_high_since_entry, shares, entry_date
- Signals for next-session AMO; no T+1 shift on entry flags in live script

## Last read
2026-05-20 KALI/scripts/generate_daily_signals.py
