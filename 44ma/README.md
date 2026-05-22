# 44 MA breakout (paper + backtest)

Daily **44 SMA pullback** on Indian equities: historical backtest, scan, and SQLite paper ledger. Default **₹20,000** cash and **₹1,000** risk per trade in `config.json`.

**Not financial advice.** Yahoo data can differ from your broker; verify signals on your charts.

## Strategy

1. 44 SMA trend: rising vs 5 bars ago, **monotone** for `sma_monotone_days` (default 3), and slope ≥ `sma_slope_min_pct` (default 0.8%)  
2. Price touches the SMA zone; same bar is **green** or a **confirmed hammer** (hammer close in upper half of candle range); optional `require_close_above_prev`  
3. Buy stop above that candle’s high  
4. Stop below that candle’s low  
5. Exit at stop or target (`risk_reward`, e.g. 3.5 → 1:3.5)

**Universe:** top **100** NSE names by free-float market cap (refreshed daily). When several setups compete for cash, **higher-confidence** signals fill first (SMA slope, touch quality, candle, volume)—not alphabetical order.

## Setup

```bash
cd /Users/aradhyakhandelwal/Desktop/Work/stocks/44ma
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # edit risk / touch params if needed
```

## Config (`config.json`)

| Key | Role |
|-----|------|
| `universe_top_n` | Daily top-N by mcap (default 100) |
| `universe_master_pool` | `0` = auto 2× top-N for backtest prefetch |
| `risk_reward` | Target multiple of initial risk (e.g. 3.5) |
| `risk_per_trade_inr` | Fixed ₹ risk per position |
| `starting_cash_inr` | Portfolio starting capital |
| `commission_pct` | Per-side transaction cost fraction (e.g. `0.0003` = 0.03%) |
| `slippage_pct` | Per-side adverse fill assumption in backtest/paper (e.g. `0.0005` = 0.05%) |
| `max_open_positions` | Portfolio cap (`0` disables cap) |
| `sma_monotone_days` | Require SMA44 up each of last N sessions (`0` = off) |
| `sma_slope_min_pct` | Min SMA rise over `sma_rising_lookback` (e.g. `0.008` = 0.8%) |
| `require_close_above_prev` | Signal bar close must exceed prior close |

### Recommended robust defaults

Current `config.json` uses a conservative profile tuned for forward robustness:

- `commission_pct: 0.0003`
- `slippage_pct: 0.0005`
- `max_open_positions: 8`

Both backtest and paper now use the same cost model (commission + slippage on entry/exit), so paper behavior is closer to historical simulation assumptions.

## Commands

**Portfolio backtest** (one shared cash pool—use this for real evaluation):

```bash
python -m ma44 backtest --config config.json --portfolio --start 2018-01-01
python -m ma44 backtest --config config.json --portfolio --start 2018-01-01 --no-trades
python -m ma44 backtest --config config.json --portfolio --start 2018-01-01 --trades-csv trades.csv
```

**Today’s universe & scan:**

```bash
python -m ma44 universe --config config.json
python -m ma44 scan-slope --config config.json
python -m ma44 scan --config config.json
```

**Paper trading** (EOD, after the session):

```bash
python -m ma44 init-paper --db paper.db --cash 20000
python -m ma44 daily --config config.json --db paper.db
python -m ma44 status --db paper.db
python -m ma44 context --config config.json --db paper.db
```

## Optional: RF parameter search

Experimental only (`rf_tune_portfolio.py`). Use **year holdout** and compare against `config.json` on test years before changing production params. Example:

```bash
python rf_tune_portfolio.py --config config.json \
  --start 2014-01-01 --end 2023-12-31 \
  --train-years 2014,2015,2018,2019,2022,2023 \
  --test-years 2016,2017,2020,2021 \
  --objective median_yearly_return \
  --no-tune-risk --max-risk-per-trade-inr 1500 \
  --random-trials 40 --rf-refine-trials 24
```

Do not adopt RF output unless **test-year PnL beats baseline** on years the search never optimized.

## Universe & confidence

- **Live / paper:** NSE ffmc snapshot → top-N; filter +slope on latest bar; cache in `.cache/universe/`.  
- **Backtest:** prefetch master pool; each session new entries only in daily top-N by prior close × shares (Yahoo, cached).  
- **Confidence** (`signal_bar_confidence`): `0.42×slope + 0.33×touch + 0.25×candle + volume_bonus`.

## Data

**yfinance** daily OHLC (unadjusted). Symbols like `RELIANCE.NS`. Some NSE names return empty data on Yahoo—skipped in the run.

## Scheduling

End-of-day only: run `daily` once per session (cron, GitHub Actions, or your machine). Persist `paper.db` or copy state if using remote cron.
