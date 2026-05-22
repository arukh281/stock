# Financially Free — Swing Trading Algo (Project Jist)

A Python backtesting system for a **Nifty 50 swing-trading strategy** that combines a macro market filter, Volatility Contraction Pattern (VCP) breakouts, and portfolio-level position management. Data comes from **Yahoo Finance** (`yfinance`); index membership is reconstructed **point-in-time** to reduce survivorship bias.

---

## Table of contents

1. [What this project does](#1-what-this-project-does)
2. [Repository structure](#2-repository-structure)
3. [Core algorithm — two layers](#3-core-algorithm--two-layers)
4. [Code walkthrough](#4-code-walkthrough)
5. [Point-in-time Nifty 50 universe](#5-point-in-time-nifty-50-universe)
6. [Portfolio backtest mechanics](#6-portfolio-backtest-mechanics)
7. [Parameter optimization](#7-parameter-optimization)
8. [How to run](#8-how-to-run)
9. [Results — `portfolio_trades.csv`](#9-results--portfolio_tradescsv)
10. [Results — `portfolio_optimization_results.csv`](#10-results--portfolio_optimization_resultscsv)
11. [Default vs optimized configuration](#11-default-vs-optimized-configuration)
12. [Limitations and assumptions](#12-limitations-and-assumptions)
13. [Dependencies](#13-dependencies)

---

## 1. What this project does

| Goal | How |
|------|-----|
| Test a rules-based swing system on Indian large-caps | Scans Nifty 50 constituents for VCP breakouts |
| Avoid “today’s index only” bias | Uses historical rebalance dates (`nifty50_history.py`) |
| Filter bad macro regimes | 18-month Nifty ROC + optional 200-DMA trend filter |
| Manage a real portfolio shape | Shared cash, max concurrent positions, ranking, stops, cooldown |
| Tune parameters | Grid search over ROC, exits, stops, slots, volume, cooldown |
| Compare to benchmark | Buy-and-hold `^NSEI` over the same window |

**Backtest window (default):** `2018-01-01` → `2026-05-01`  
**Initial capital (default):** ₹10,00,000  
**Commission (default):** 0.1% per side (`commission_pct=0.001`)

---

## 2. Repository structure

```
financially free/
├── swing_trading_algo.py      # Main algo: signals, single-stock & portfolio backtests, optimizer
├── nifty50_history.py         # Point-in-time Nifty 50 membership (NSE rebalances 2017–2025)
├── portfolio_trades.csv       # Closed trades from default tuned portfolio run
├── portfolio_optimization_results.csv  # Full grid-search output (96 combinations)
├── requirements.txt           # pandas, numpy, yfinance
├── .gitignore
└── .venv/                     # Local virtualenv (not committed)
```

There is no separate README; this file serves as the project reference.

---

## 3. Core algorithm — two layers

### Layer 1 — Macro filter (Algorithm 1)

Applied to the index ticker (default: `^NSEI`, Nifty 50).

| Component | Definition |
|-----------|------------|
| **18-month ROC** | Monthly close resampled to month-end; `pct_change(18)` × 100, forward-filled to daily |
| **200-DMA regime** | Daily close vs 200-day moving average on the index |
| **Entry gate** | `ROC_18M < max_roc` (default 45%). Optional: index must be **above** 200-DMA (`require_index_trend=True`) |

**Interpretation:** Only take stock breakouts when the broad market has not already run up too much (ROC cap) and, if enabled, when Nifty is in an uptrend above its 200-DMA.

Warmup: data download starts ~21 months before `backtest_start` so ROC and rolling windows are valid.

### Layer 2 — Micro signals (Algorithm 2)

Applied per stock OHLCV from `yfinance`.

| Signal | Logic |
|--------|--------|
| **Volatility contraction** | `Vol_10D < Vol_20D < Vol_40D` (rolling std of close) |
| **VCP breakout** | Yesterday contracting **and** today close > 20-day high (shifted) **and** volume > 1.2× 50-day avg volume |
| **21-EMA exit** | Close below 21-period EMA for N consecutive days (`exit_confirm_days`, default 2 → `Exit_2`) |
| **Stop loss** | Optional: exit if close < N-day rolling min low at entry (`stop_lookback`: 10, 20, or 30 precomputed) |
| **Volume filter** | `Volume / Vol_Avg_50D >= min_volume_ratio` (default 1.2 in tuned run) |

**Entry signal (`_entry_signal`):** VCP breakout on previous bar + macro filters + volume ratio.

**Execution assumption:** Signal on day *T−1* → trade at **next day open** (realistic delay). Exits are processed **before** entries each day.

---

## 4. Code walkthrough

### Class: `SwingTradingAlgo`

**Constructor**

```python
SwingTradingAlgo(index_ticker="^NSEI", lookback_roc=18)
```

| Method | Purpose |
|--------|---------|
| `calculate_macro_roc` | Download index, compute ROC + 200-DMA flags |
| `calculate_vcp_and_emas` | Per-stock indicators, breakout, exit variants, stop levels |
| `prepare_universe` | Batch download many tickers + attach macro once |
| `scan_stock` | Single ticker: full DF + valid buy events |
| `backtest_stock` | One symbol, one position at a time |
| `backtest_portfolio` | Multi-stock shared cash portfolio |
| `optimize_portfolio` | Grid search; reuses prepared universe |
| `benchmark_buy_hold` | Nifty buy at first open, sell at last close |

### Single-stock backtest (`backtest_stock`)

- Enters when: `VCP_Breakout` and `ROC_18M < 45` on previous day
- Exits when: `Exit_Signal` (2 days below 21-EMA) on previous day
- One position at a time; marks open positions at last close

### Portfolio backtest (`backtest_portfolio`)

Key parameters:

| Parameter | Default (tuned `__main__`) | Meaning |
|-----------|---------------------------|---------|
| `max_positions` | 5 | Max concurrent holdings |
| `use_historical_universe` | `True` | Only trade names in Nifty on signal day |
| `use_stop_loss` | `True` | Trailing/base low stop from `stop_lookback` |
| `rank_by_volume` | `True` | When slots limited, prefer highest volume ratio |
| `cooldown_days` | 10 | Days before re-entering same symbol after exit |
| `require_index_trend` | `True` | Nifty above 200-DMA required for entry |
| `slot_size` | `initial_capital / max_positions` | Target allocation per new position |

**Exit reasons** (recorded in trades):

- `signal` — EMA exit confirmation
- `stop_loss` — close below stop level
- `index_removal` — dropped from Nifty 50 (forced exit)

**Outputs:** `trades`, `closed_trades`, `equity_curve`, `summary` (returns, drawdown, win rate, alpha vs Nifty), `benchmark`, `open_positions`.

### CLI entry points (`python swing_trading_algo.py`)

| Command | Behavior |
|---------|----------|
| *(no args)* | Print yearly universe report → run tuned portfolio backtest → save `portfolio_trades.csv` |
| `single [TICKER]` | Single-stock backtest (default `TRENT.NS`) |
| `optimize` | Run grid search → save `portfolio_optimization_results.csv` |

---

## 5. Point-in-time Nifty 50 universe

**File:** `nifty50_history.py`

- Maps NSE symbols → Yahoo tickers (`NSE_TO_YF`)
- Rebuilds membership by **rolling back** documented semi-annual rebalances from the current set, then replaying forward from 2017
- `get_yf_constituents(date)` — members as of last rebalance on or before `date`
- `all_yf_tickers_between(start, end)` — **union** of every stock that was ever in the index during the range (used for downloads)
- `yearly_universe_report(2018, 2025)` — printed at backtest start

**Important:** `NIFTY_50_TICKERS` in `swing_trading_algo.py` is labeled **do not use for historical backtests** — it is today’s list only.

Historical names in the union include removed members (e.g. `YESBANK.NS`, `VEDL.NS`, `HDFC.NS`, `ZEEL.NS`) so delisted or rotated names can still appear in trades when they were index members.

---

## 6. Portfolio backtest mechanics

```
For each trading day (after day 1):
  1. EXITS (in order for each open position)
     - Strategy exit (N days below 21-EMA)
     - Forced exit if removed from index
     - Stop loss hit (close < stop from entry bar)
     → Sell at today's OPEN, release cash

  2. ENTRIES (if slots available and cash > 10% of slot_size)
     - Build candidates: in index on signal day, not in cooldown, entry_signal True
     - Sort by volume ratio (desc) if rank_by_volume
     - Fill up to `max_positions - len(positions)`
     → Buy at today's OPEN, allocate min(cash, slot_size)

  3. Record equity (mark-to-market all positions at close)

End: mark remaining positions at last close (still_open)
```

**Cash model:** Single pool; each new position targets `initial_capital / max_positions` (not equal-weight rebalance of existing book).

---

## 7. Parameter optimization

**Method:** `optimize_portfolio()` — compact grid maximizing **absolute strategy return** (`strategy_return_pct`).

**Grid dimensions:**

| Parameter | Values tested |
|-----------|---------------|
| `max_roc` | 25, 35, 45 |
| `exit_confirm_days` | 1, 2 |
| `cooldown_days` | 0, 10 |
| `max_positions` | 5, 10 |
| `min_volume_ratio` | 1.0, 1.2 |
| `stop_lookback` | 10, 20 |

**Total combinations:** 3 × 2 × 2 × 2 × 2 × 2 = **96**  
(Universe data is downloaded **once** and reused for all runs.)

**Fixed during optimization:** `use_historical_universe=True`, `use_stop_loss=True`, `rank_by_volume=True`, `require_index_trend=True`, `include_benchmark=True`.

Results sorted descending by `strategy_return_pct` and written to CSV.

---

## 8. How to run

```bash
cd "/path/to/financially free"
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Default portfolio backtest (saves portfolio_trades.csv)
python swing_trading_algo.py

# Grid search (saves portfolio_optimization_results.csv)
python swing_trading_algo.py optimize

# Single stock
python swing_trading_algo.py single RELIANCE.NS
```

**Requirements:** Network access for `yfinance` downloads. First run can take several minutes (many tickers × long history).

**No API keys** are required.

---

## 9. Results — `portfolio_trades.csv`

Generated by the **default tuned** portfolio run (`max_positions=5`, see [§11](#11-default-vs-optimized-configuration)).

### Schema

| Column | Description |
|--------|-------------|
| `ticker` | Yahoo symbol (e.g. `BAJFINANCE.NS`) |
| `entry_date` / `exit_date` | Trade dates |
| `entry_price` / `exit_price` | Execution prices (opens for entries/exits in backtest) |
| `return_pct` | Percent return on the position |
| `days_held` | Calendar days in trade |
| `action` | Empty for closed round-trips (BUY rows only in full `trades` output) |
| `exit_reason` | `signal` or `stop_loss` |

### Tuned portfolio summary (5 slots, 2018–2026)

| Metric | Value |
|--------|-------|
| Initial capital | ₹1,000,000 |
| Final equity | ₹1,885,708 |
| **Total return** | **+88.57%** |
| Max drawdown | −27.56% |
| Nifty buy & hold | +129.04% |
| Alpha vs Nifty | −40.47% |
| Beat Nifty | No |

### Summary statistics (262 closed trades)

| Metric | Value |
|--------|-------|
| Period | 2018-01-08 → 2026-03-17 |
| Unique tickers | 55 |
| Win rate | **40.8%** |
| Mean return per trade | **+1.78%** |
| Median return per trade | **−1.61%** |
| Mean holding period | **37.2 days** |
| Exit breakdown | 258 `signal`, 4 `stop_loss` |

### Best closed trades (by `return_pct`)

| Ticker | Entry | Return |
|--------|-------|--------|
| COALINDIA.NS | 2023-09-04 | +89.52% |
| BAJAJ-AUTO.NS | 2023-11-16 | +60.24% |
| SHRIRAMFIN.NS | 2025-10-03 | +59.46% |
| JSWSTEEL.NS | 2021-03-22 | +58.23% |
| TATASTEEL.NS | 2021-04-30 | +37.98% |

### Worst closed trades

| Ticker | Entry | Return |
|--------|-------|--------|
| JSWSTEEL.NS | 2019-09-23 | −12.12% |
| BPCL.NS | 2021-03-03 | −11.25% |
| YESBANK.NS | 2019-01-16 | −10.03% |
| ADANIPORTS.NS | 2023-03-06 | −9.77% |
| INDUSINDBK.NS | 2023-03-09 | −9.72% |

### Mean return by exit year

| Year | Trades | Avg return % | Sum return % |
|------|--------|--------------|--------------|
| 2018 | 40 | −1.72 | −68.60 |
| 2019 | 33 | −0.96 | −31.62 |
| 2020 | 28 | +2.25 | +62.95 |
| 2021 | 32 | +7.28 | +232.98 |
| 2022 | 20 | −2.11 | −42.19 |
| 2023 | 38 | −0.01 | −0.37 |
| 2024 | 31 | +6.76 | +209.66 |
| 2025 | 31 | +1.54 | +47.60 |
| 2026 | 9 | +6.11 | +54.99 |

*Note: Per-trade sums are not portfolio total return; capital is shared and position sizing affects compounded results.*

---

## 10. Results — `portfolio_optimization_results.csv`

**96 rows** — one per parameter combination, sorted by `strategy_return_pct` (best first).

### Schema

| Column | Description |
|--------|-------------|
| `max_roc`, `exit_confirm_days`, `cooldown_days`, `max_positions`, `min_volume_ratio`, `stop_lookback` | Grid parameters |
| `strategy_return_pct` | Total portfolio return over window |
| `max_drawdown_pct` | Peak-to-trough from daily equity curve |
| `nifty_return_pct` | Buy-and-hold benchmark (same for all rows) |
| `alpha_vs_nifty_pct` | Strategy minus Nifty |
| `num_trades_closed` | Closed round-trips |
| `win_rate_pct` | % winning closed trades |

### Benchmark (constant across all runs)

| Metric | Value |
|--------|-------|
| Nifty buy-and-hold (`^NSEI`) | **+129.04%** (2018-01-01 → 2026-05-01) |

### Best parameter set (row 1)

| Parameter | Value |
|-----------|-------|
| max_roc | 45 |
| exit_confirm_days | 2 |
| cooldown_days | 10 |
| max_positions | **10** |
| min_volume_ratio | 1.2 |
| stop_lookback | 20 |

| Outcome | Value |
|---------|-------|
| Strategy return | **+50.62%** |
| Max drawdown | **−18.36%** |
| Alpha vs Nifty | **−78.42%** |
| Closed trades | 451 |
| Win rate | 42.8% |

### Worst parameter set (row 96)

| Parameter | Value |
|-----------|-------|
| max_roc | 25 |
| exit_confirm_days | 1 |
| cooldown_days | 0 |
| max_positions | 5 |
| min_volume_ratio | 1.0 |
| stop_lookback | 10 |

| Outcome | Value |
|---------|-------|
| Strategy return | **+17.63%** |
| Max drawdown | **−24.21%** |
| Alpha vs Nifty | **−111.41%** |
| Closed trades | 265 |
| Win rate | 32.8% |

### Optimization insights

1. **All 96 combinations underperformed** buy-and-hold Nifty over this period (alpha roughly −79% to −111%).
2. **Higher `max_roc` (45)** and **2-day exit confirmation** consistently rank at the top.
3. **`cooldown_days=10`** helps vs 0 (fewer whipsaw re-entries).
4. **`max_positions=10`** beats 5 on total return in the grid (more diversification + more signals), but drawdown is similar.
5. **`min_volume_ratio` 1.0 vs 1.2** often ties when other params match — stricter volume filter is already partly enforced by VCP’s 1.2× rule on breakout day.
6. **`stop_lookback=20`** slightly beats 10 at the top of the table.
7. **Tighter `max_roc=25`** lowers drawdown in some rows but cuts return and trade count.

---

## 11. Default vs optimized configuration

The script uses **two different “best” configs** depending on mode:

### Default portfolio run (`python swing_trading_algo.py`)

Hard-coded “tuned” dict in `__main__`:

```python
{
    "max_positions": 5,
    "max_roc": 45,
    "exit_confirm_days": 2,
    "cooldown_days": 10,
    "min_volume_ratio": 1.2,
    "stop_lookback": 20,
    "require_index_trend": True,
}
```

→ Produces **`portfolio_trades.csv`** (262 closed trades, 5 slots).

### Optimizer best (`python swing_trading_algo.py optimize`)

Top grid row uses **`max_positions=10`** (same other params as above) → **+50.62%** strategy return, 451 trades.

To align live backtest with optimizer winner, change `max_positions` to `10` in the `tuned` dict or pass optimizer output into `backtest_portfolio`.

---

## 12. Limitations and assumptions

| Area | Limitation |
|------|------------|
| **Data** | Yahoo Finance only; corporate actions/splits depend on yfinance quality |
| **Membership** | Rebalance history is manually curated, not official NSE files |
| **Execution** | Next-day open fills; no slippage model beyond commission |
| **Liquidity** | No market-impact or position-size vs ADV constraints |
| **Survivorship** | Union-of-members download helps, but delisted/bankrupt names may have incomplete history |
| **Optimization** | In-sample grid on same period as evaluation; no walk-forward or out-of-sample split |
| **Objective** | Optimizer maximizes raw return, not Sharpe, drawdown, or risk-adjusted metrics |
| **Macro** | 18M ROC on Nifty may not suit all regimes; strategy lagged buy-and-hold 2018–2026 |
| **Single market** | India Nifty 50 only; `lookback_roc=20` noted in docstring for smallcap variant but not implemented here |

---

## 13. Dependencies

From `requirements.txt`:

```
pandas>=2.0.0
numpy>=1.24.0
yfinance>=0.2.40
```

---

## Quick reference — signal checklist for one entry

1. Stock was in **Nifty 50** on the signal day (historical membership).
2. **Nifty 18M ROC** < `max_roc` (default 45%).
3. Optional: **Nifty above 200-DMA** (`require_index_trend`).
4. Prior day: **volatility contracting** (10d < 20d < 40d std).
5. Prior day: **breakout** above 20-day high with **volume > 1.2×** 50-day average.
6. **Volume ratio** ≥ `min_volume_ratio`.
7. Portfolio has a free slot and cash; not in **cooldown** for that symbol.
8. If competing for slots, higher **volume ratio** wins.

**Exit:** 2 closes below 21-EMA, or stop under N-day low, or index removal — sell next open.

---

*Generated as a project reference. Re-run backtests after code or data changes; CSV files reflect the last successful run.*
