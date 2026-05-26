# Context: ma44/paper.py + ma44/__main__.py

## What it does
44 MA breakout strategy for Indian equities (NSE top-N by mcap). CLI supports historical backtest, EOD scan, and SQLite paper ledger with positions, pending breakouts, cash, and journal.

## Exports / Public surface
- `init_db(path)` — creates SQLite schema (meta, pending, positions, journal)
- `daily_step(db_path, settings)` — EOD run: manage exits, fill pendings, create new signals; returns list of action strings
- `paper_status(db_path)` — dict: cash, positions, pending_recent
- `paper_context(db_path, settings)` — human-readable snapshot + structured data (equity, MTM, positions)
- `dump_scan(settings, db_symbols)` — read-only scan, no DB mutation
- CLI (`python -m ma44`): `backtest`, `daily`, `status`, `context`, `init-paper`, `scan`, `universe`, `scan-slope`

## What it does NOT do
- No web UI, HTTP API, or Supabase
- No remote/cloud persistence (local `paper.db` only)
- `daily_step` assumes one run per session after daily bar; no intraday
- No multi-user or per-algo isolation in one DB (single ledger per db file)

## Constraints and edge cases
- Default cash ₹20,000; `risk_per_trade_inr`, `max_open_positions` from `config.json`
- Yahoo daily OHLC via `fetch_daily`; symbols like `RELIANCE.NS`
- Pending breakouts expire after `breakout_hold_days`; entries ranked by signal confidence when cash constrained
- Costs: commission + slippage on entry/exit in backtest and paper

## Last read
2026-05-20 ma44/paper.py, ma44/__main__.py
