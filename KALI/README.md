# KALI — NIFTY OCHLV Quantitative Strategy

Implementation of the multi-timeframe OCHLV strategy described in `algo.md`.

## Setup

Requires **Python 3.10–3.13** (3.11 recommended; `vectorbt`/`numba` do not support 3.14 yet).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
# Fetch universe fundamentals (Screener.in)
python scripts/fetch_universe.py --symbols ITC,RELIANCE

# Daily AMO plan (PIT list + Screener filter; quarterly live refresh)
python scripts/generate_daily_signals.py --force-download

# Download OHLCV
python scripts/build_features.py --symbol ITC.NS

# Run backtest
python scripts/run_backtest.py --symbols ITC.NS,RELIANCE.NS --start 2020-01-01
```

## Tests

```bash
pytest tests/ -v
```
