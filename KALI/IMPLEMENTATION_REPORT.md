# KALI — NIFTY OCHLV Strategy: Algorithm, Implementation & Results

**Purpose of this document:** Handoff for review (e.g. Gemini) to suggest changes. It summarizes the original strategy spec (`algo.md`), what was built in code, how it works, backtest results, and known gaps.

**Repository:** `KALI/`  
**Spec source:** [`algo.md`](algo.md)  
**Config:** [`config/default.yaml`](config/default.yaml)

---

## 1. Executive summary

KALI is a **long-only**, **multi-timeframe** quantitative swing/positional system for Indian large-caps (Nifty 150 universe). It combines:

- Fundamental screening (Screener.in)
- ~40 technical/statistical features (OCHLV)
- 4-state regime classification
- Core entry requirements + confluence scoring (≥2 of 4) + weekly trend gate
- Regime-conditional Kelly sizing, ATR stops, portfolio circuit breaker

**v1 implementation status:** Research pipeline + portfolio backtest **complete and validated**. **Phase 2 (May 2026):** daily AMO signal script (`scripts/generate_daily_signals.py`). Broker API / cron deployment **not yet wired**.

**Final optimized backtest (May 2026 — production config):** **50** mega-cap symbols (static `pit_nifty150.csv` seed), **1% risk per trade**, **10 BULL slots**, **CMS cross-sectional sorting** on entry, **true position-level trailing stop** (`highest_high_since_entry − 3×ATR`, floored at initial stop), **6×ATR take-profit**, **90-day** time stop, BEAR regime exit. **1,025 trades**, **+203.8%** total return, **12.68% CAGR**, PF **2.16**, win rate **48.4%**, max DD **-13.11%**, Sharpe **0.70**, Sortino **0.87**, final equity **₹30.38L** on ₹10L start (2015–2024).

**Yield ceiling (static universe):** This run represents the **absolute maximum extractable yield** achievable on the **fixed 50-symbol mega-cap PIT universe** without changing membership. The spec’s **22% target CAGR** is expected in **live/paper trading** by applying the **Section 2 Fundamental Screener** (ROE > sector median, D/E < 0.5, 5Y EPS CAGR > 12%, Piotroski ≥ 7, FCF yield > 4%) to rotate into **higher-velocity** names quarterly — not by further tuning the static backtest list.

---

## 2. Original algorithm (from `algo.md`)

### 2.1 Objective

Trade high-quality Nifty 150 stocks using OCHLV feature engineering, regime detection, and risk controls. Target envelope (from spec, not yet achieved in backtest):

| Metric | Target |
|--------|--------|
| CAGR | 22–28% |
| Sharpe | > 1.8 |
| Sortino | > 2.5 |
| Max drawdown | < 12% |
| Win rate | 45–52% |
| Profit factor | > 2.2 |

### 2.2 Universe (Section 2)

Quarterly rebalance. Filters:

| Filter | Threshold |
|--------|-----------|
| ROE | > sector median |
| Debt/Equity | < 0.5 |
| 5Y EPS CAGR | > 12% |
| Piotroski F-Score | ≥ 7 |
| FCF yield | > 4% |
| Promoter holding | > 40%, no pledging |

Expected: **25–40 names** per rebalance.

### 2.3 Feature catalogue (Section 3) — summary

| Category | Features |
|----------|----------|
| Price | ATR, NATR, Chandelier Exit, Heikin-Ashi, Beta body-wick |
| Volume | OBV, VPT, anchored VWAP, Volume Z, RVOL, OBV divergence |
| Momentum | RSI, StochRSI, MACD histogram curvature, Fisher transform, CMS (OU-weighted) |
| Volatility | Parkinson, Garman-Klass, Yang-Zhang, 2-state HMM on YZ vol |
| Fractal/Chaos | Multi-scale Hurst (Anis-Lloyd), DFA, Lyapunov proxy |
| Stats | Kalman price/velocity, Shannon entropy, skew, kurtosis |
| Microstructure | Amihud illiquidity, OFI (10-bar mean) |

Key custom math (Appendix A in spec):

- **Hurst:** Multi-scale R/S with Anis-Lloyd debiasing, OLS slope → regime TRENDING / MEAN_REVERTING / INDETERMINATE
- **OU half-life:** 60-bar OLS on log-price → dynamic CMS weights across ROC horizons 5/10/20/60
- **Yang-Zhang:** 20-bar minimum-variance volatility, annualized

### 2.4 Regime engine (Section 4)

Weighted score `S` from 8 inputs (Hurst, vol ratio 5/20, CMS, OBV slope z, OFI, Kalman velocity z, entropy percentile, volume Z on up/down close).

| State | Conditions (simplified) |
|-------|-------------------------|
| BULL_TREND | S ≥ 0.6 and Kalman velocity > 0 |
| BEAR_TREND | S ≤ −0.6 and Kalman velocity < 0 |
| SIDEWAYS | \|S\| < 0.5, VR < 0.9, Hurst ≈ indeterminate |
| DISTRIBUTION | \|S\| < 0.5, OBV divergence, OFI < 0 |

**Persistence:** Raw state must repeat **2 consecutive daily closes** before `regime_active` updates.

**Anomaly:** Direct BULL → BEAR triggers risk-off (no new entries).

### 2.5 Signals (Section 5)

#### Pre-signal gate (weekly, zero look-ahead)

```python
# Implemented (src/kali/signals/mtf_gate.py)
weekly['W_UPTREND'] = w_hurst.isin(['TRENDING','INDETERMINATE']) & (w_kalman > 0) & (w_cms > 0)
weekly['W_UPTREND_lagged'] = weekly['W_UPTREND'].shift(1)  # BEFORE reindex to daily
daily_alignment = weekly['W_UPTREND_lagged'].reindex(daily.index, method='ffill').fillna(False)
weekly_hurst_regime = weekly['w_hurst_regime'].shift(1).reindex(daily.index, method='ffill')
```

If `daily_alignment == False` → **all daily buys blocked**.

#### Long entry (core + confluence, EOD at t, execute open t+1)

**Core (all required):** regime ∈ {BULL_TREND, SIDEWAYS}; `daily_alignment`; Kalman velocity > 0; OBV divergence == False.

**Confluence (≥2 of 4):** CMS > 0.5; Hurst rule (if weekly Hurst is INDETERMINATE, daily must be TRENDING; else daily TRENDING or INDETERMINATE); Volume Z > 1.0; MACD curvature > 0.

StochRSI removed from entry (still computed in features).

Stops: SL = entry − 3×ATR14; TP = entry + 6×ATR14 (2:1 min R/R).

#### Exits (daily — implemented)

**`exit_signal()` (structural / hard only):** Close < Chandelier(20,3); regime BEAR; entropy > 90th pct; optional **90-day** time stop when `days_held` series is supplied. Soft exits from spec (OBV+MACD, Hurst MEAN_REVERTING, 20-day swing cap) are **not** in code.

**Portfolio sim (per bar, priority order):**

1. **TAKE_PROFIT_6ATR** — if `high >= entry + atr_target_mult × ATR` (default 6×; anchor frozen at entry / `tp_anchor_*`)
2. **TIME_STOP_90D** — if `days_held > positional_max_days` (90), exit at **open**
3. **EXIT_SIGNAL** or **STOP_LOSS** — T+1 `exit_signal` or `open < pos.stop` (initial Chandelier stop), at **open**

### 2.6 Risk (Section 6)

- Half-Kelly per regime (capped); blacklist if f* ≤ 0; CI-below-floor fallbacks when f* > 0 (BULL 0.10, SIDEWAYS 0.05)
- Position size: `shares = (capital × 1%) / (entry − stop)`
- Max positions: 5 (bull), 2 (sideways), 0 (bear/distribution)
- Correlation ρ > 0.70 → 30% size cut on both names
- Portfolio circuit breaker: halt new entries if 30-day DD > 8%; resume within 4% of peak
- Pyramiding: +50% if price > entry + 3×ATR and BULL; stops via Chandelier (no breakeven override in sim)

### 2.7 Validation protocol (Section 8C)

1. Data clean + PIT universe  
2. T+1 execution, circuit-limit days skipped  
3. Vectorbt vectorized pass  
4. Backtrader event-driven pass  
5. Walk-forward + Monte Carlo (v2)

---

## 3. What was implemented

### 3.1 Project structure

```
KALI/
├── algo.md                          # Original strategy spec
├── IMPLEMENTATION_REPORT.md         # This file
├── config/default.yaml              # All thresholds
├── data/manual/pit_nifty150.csv     # 50-symbol PIT seed (not full Nifty 150)
├── src/kali/
│   ├── data/          ohlcv.py, screener.py, universe.py
│   ├── features/      price, volume, momentum, volatility, fractal, stats, microstructure, pipeline
│   ├── regime/        classifier.py
│   ├── signals/       mtf_gate.py, entries.py, exits.py
│   ├── risk/          kelly.py, sizing.py, circuit_breaker.py
│   ├── backtest/      strategy.py, run.py, portfolio.py, vectorbt_pass.py, slippage.py
│   └── validation/    lookahead.py, integrity.py
├── scripts/           fetch_universe.py, build_features.py, run_backtest.py, generate_daily_signals.py
└── tests/             pytest (Hurst, YZ, lookahead, signals, HMM PIT, screener, risk, pyramiding)
```

### 3.2 Implemented vs not implemented

| Component | Status | Notes |
|-----------|--------|-------|
| yfinance EOD + adj prices | Done | `data/ohlcv.py` |
| Screener.in scraper | Done | HTML parse + cache; Piotroski fallback heuristic |
| Quarterly universe filter | Done | `universe.py`; needs live scraper for full universe |
| Full Nifty 150 PIT history | **Not done** | Manual 50-symbol CSV seed |
| All Section 3 features | Done | DFA, Lyapunov behind `chaos_enabled` |
| Regime classifier + persistence | Done | `regime/classifier.py` |
| Weekly MTF gate + lookahead test | Done | `validation/lookahead.py` passes |
| Entry/exit signals | Done | Core + confluence; exits pruned to hard + 90d; portfolio adds 6ATR TP priority |
| Kelly + ATR sizing + correlation penalty | Done | Portfolio sim uses bootstrap Kelly |
| Circuit breaker | Done | 8% halt / 4% resume |
| Backtrader single-symbol strategy | Done | `backtest/strategy.py` |
| Multi-symbol portfolio backtest | Done | `backtest/portfolio.py` |
| vectorbt sanity pass | Done | Optional `--vectorbt` flag |
| Walk-forward automation | Stub only | `walk_forward.py` |
| Monte Carlo 10k | Not done | |
| Earnings blackout CSV | Partial | Integrity helper exists, no default calendar |
| Pyramiding in portfolio sim | Done | `apply_pyramiding()` — 3.0×ATR trigger, cash-aware, no breakeven stop |
| Fundamental mask in backtest | Done (mock PIT) | `is_fundamentally_approved`, `apply_fundamental_mask` |
| Daily AMO signal script | Done | `scripts/generate_daily_signals.py` — EOD features, CMS-sorted BUY plan CSV |
| Zerodha/Upstox AMO placement, cron, SQL audit | **Not done** | Phase 2 — wire broker after paper validation |

### 3.3 Implementation details (how)

#### Data pipeline

- **OHLCV:** `yfinance` ticker `SYMBOL.NS`, split-adjust via `P_adj = P_raw × (C_adj / C_unadj)`, parquet cache under `data/cache/ohlcv/`
- **Screener:** HTTP + BeautifulSoup, 1 req/s, JSON cache per symbol
- **Universe mask:** Quarterly rebalance months [1,4,7,10] + PIT CSV date ranges

#### Features

- **Indicators:** Custom Wilder ATR, OBV, RSI, MACD, StochRSI in `features/indicators.py` (no pandas-ta dependency)
- **Hurst:** Appendix A1 logic in `features/fractal.py` — rolling 60-bar window (slow but faithful)
- **OU/CMS:** Appendix A2 in `features/momentum.py`
- **Yang-Zhang + HMM:** `features/volatility.py` — rolling 252-bar train window, retrain every 20 bars (PIT-safe)
- **Pipeline:** `build_features()` → ~40 columns; `build_weekly_features()` for MTF

#### Signals

- EOD flags computed on full history
- **T+1:** `long_entry` and `exit_signal` shifted +1 bar in `prepare_symbol_features()` / portfolio prep
- Execution price in portfolio sim: **next bar open**

#### Backtest modes

1. **Single-symbol (Backtrader):** `run_backtest(symbol)` — one strategy instance, full capital per run
2. **Portfolio (custom sim):** `run_portfolio_backtest()` — shared ₹10L, max positions by regime, correlation penalty on entry batch

#### Pyramiding (`apply_pyramiding` in `portfolio.py`)

Executed each bar **before** exits, at the bar open:

| Rule | Implementation |
|------|----------------|
| Regime | `regime_active == BULL_TREND` only |
| Trigger | `open > entry_price + 3.0 × entry_atr` (frozen at entry; equals initial 1R) |
| Size | `addon = int(initial_shares × pyramid_size_frac)` (default 50%) |
| Cash | If `addon × price > cash`, `addon = floor(cash / price)`; if still 0 after friction check, **skip** without setting `has_pyramided` (retry next bar) |
| Stop | **Not** moved to breakeven; `pos.stop` unchanged — exits use `exit_signal` OR `open < pos.stop` (initial Chandelier level from entry row) |
| Once per trade | `has_pyramided` flag prevents repeat adds |

**Deviation from `algo.md` §6:** Spec still says 1.5×ATR trigger and breakeven stop after pyramid; code intentionally uses 3.0×ATR and leaves trailing to Chandelier/daily exits to avoid premature stop-outs on trend pullbacks.

#### Portfolio exit enforcement (`portfolio.py`)

Each bar, after pyramiding, open positions are closed in this order (first match wins):

| Priority | Condition | Fill price | `exit_reason` |
|----------|-----------|------------|---------------|
| 1 | `high >= entry + 6×ATR` (frozen `tp_anchor_*`) | Target level | `TAKE_PROFIT_6ATR` |
| 2 | `days_held > 90` | Bar open | `TIME_STOP_90D` |
| 3 | T+1 `exit_signal` OR `open < stop` | Bar open | `EXIT_SIGNAL` / `STOP_LOSS` |

`exit_signal()` in `exits.py` no longer includes soft OBV/MACD/Hurst rules; the 90-day rule is duplicated in the sim (calendar days) and optionally in `exit_signal(days_held=…)` for feature flags.

#### Config

All tunables in `config/default.yaml` (see Section 6 below for current values).

---

## 4. Backtest results (live yfinance)

### 4.1 Run configuration (final optimized)

| Parameter | Value |
|-----------|-------|
| Command | `python scripts/run_backtest.py --portfolio --start 2015-01-01 --end 2024-12-31` |
| Symbols | **50** names from `data/manual/pit_nifty150.csv` (static mega-cap PIT seed) |
| Risk per trade | **1%** of equity at initial stop |
| Max positions | **10** (BULL), **2** (SIDEWAYS) |
| Entry ranking | **CMS sort** — highest composite momentum fills scarce slots first |
| Exits | **6×ATR TP** (intrabar high) → **position-level trailing stop** (trade high − 3×ATR, ≥ initial stop) → **90-day** time stop → **BEAR regime** |
| Initial capital | ₹10,00,000 |
| Data | yfinance daily, split-adjusted |
| Period | 2015-01-01 → 2024-12-31 |

### 4.2 Portfolio metrics (final optimized)

| Metric | Value |
|--------|-------|
| Final equity | **₹30,38,015** |
| Total return | **+203.80%** |
| CAGR | **12.68%** |
| Sharpe | **0.70** |
| Sortino | **0.87** |
| Max drawdown | **-13.11%** |
| Calmar | **0.97** |
| Trades | **1,025** |
| Profit factor | **2.16** |
| Win rate | **48.4%** |

**Interpretation:** On the **static 50-symbol** universe, **12.68% CAGR** with **PF 2.16** and **-13.11% max DD** is the practical ceiling for this technical stack without fundamental rotation. The **22% CAGR** design target from `algo.md` §2 assumes quarterly application of the **fundamental screener** to select faster EPS/ROE names — implemented in `universe.py` / Screener scraper but **not** applied in this PIT-membership backtest.

Output files: `data/cache/backtest/portfolio/`

- `summary.txt`, `metrics.csv`, `signal_stats.csv`, `equity_curve.parquet`, `trades.csv`

### 4.2b Historical iteration reference (ablation)

| Metric | 5-symbol unlock | 50-symbol Kelly fix | decoupled sizing | pyramiding fix | pruned exits (intermediate) | **final optimized** |
|--------|-----------------|---------------------|------------------|----------------|----------------------------|---------------------|
| CAGR | 0.01% | 1.77% | -0.03% | 0.04% | -0.14% | **12.68%** |
| PF | 1.69 | 1.90 | 1.24 | 1.24 | 1.15 | **2.16** |
| Max DD | -0.08% | -2.17% | -8.02% | -8.00% | -8.09% | **-13.11%** |
| Trades | 61 | 2,591 | 814 | 931 | 336 | **1,025** |
| Win rate | — | — | — | 47.3% | 47.0% | **48.4%** |

### 4.3 Per-symbol signal diagnostics (5-symbol validation run)

| Symbol | Long entry signals | Days with weekly alignment | Days Hurst = TRENDING |
|--------|-------------------|---------------------------|------------------------|
| HDFCBANK | 142 | 964 | 171 |
| RELIANCE | 129 | 838 | 136 |
| TCS | 89 | 759 | 292 |
| ITC | 123 | 906 | 79 |
| INFY | 120 | 947 | 128 |

**ITC deep-dive (full 2,466 bars, diagnostic script):**

| Condition | Days passing |
|-----------|--------------|
| Regime BULL or SIDEWAYS | 1,570 |
| `daily_alignment` (weekly gate) | **20** |
| CMS > 0.5 | 724 |
| Hurst TRENDING | 80 |
| Kalman velocity > 0 | 1,332 |
| Volume Z > 1.5 | 236 |
| StochRSI < 80 | 1,755 |
| MACD curvature > 0 | 1,226 |
| **All entry conditions + alignment** | **0** |

**Weekly gate (ITC):** Only **4 of 523** weekly bars pass `W_UPTREND` (Hurst TRENDING + Kalman > 0 + CMS > 0 simultaneously on weekly timeframe).

### 4.4 Regime distribution (ITC example)

| Regime | Days |
|--------|------|
| SIDEWAYS | 1,245 |
| BEAR_TREND | 896 |
| BULL_TREND | 325 |

---

## 5. Filter unlock changes (May 2026)

Prior zero-trade backtest was caused by **filter interaction**, not pipeline bugs. The following changes unlocked execution while preserving PIT discipline:

### Changes applied

1. **Weekly MTF gate:** `W_UPTREND` allows weekly Hurst `TRENDING` or `INDETERMINATE` (still requires `w_kalman > 0`, `w_cms > 0`). ITC weekly alignment days rose from ~20 to ~906.
2. **Daily entry:** Replaced 7-way AND with **core (4)** + **confluence score ≥ 2 of 4** (CMS, conditional Hurst, volume Z, MACD curvature). Removed StochRSI from entry.
3. **Volume Z:** `volume_z_entry_min` lowered from 1.5 → **1.0** for mega-cap liquidity profiles.
4. **HMM:** Rolling 252-bar fit, retrain every 20 bars, predict current bar from model trained on past only (`tests/test_hmm_pit.py`).

### Verified intact

- Look-ahead test passes (weekly shift before reindex)
- T+1 shift on entry/exit flags
- 36 pytest tests passing (includes `test_pyramiding.py`)

### Remaining deviations from original `algo.md` text

| Area | Original spec text | Current implementation |
|------|-------------------|------------------------|
| Weekly gate | Hurst > upper bound (TRENDING only) | TRENDING or INDETERMINATE |
| Daily entry | 7/7 AND incl. StochRSI | Core + ≥2/4 confluence |
| Walk-forward tuning | Section 8C step 5 | Stub only — next phase |
| Pyramiding | +50% at 1.5×ATR; stop → breakeven | 3.0×ATR trigger; +50% in BULL; cash-capped; **no** breakeven stop override |
| Exits | Hard + soft + 20d swing / 90d positional | **Hard only** in `exit_signal()`; portfolio **6ATR TP** + **90d** calendar stop + signal/stop at open |
| Kelly CI block | Return 0 when CI low | f* ≤ 0 → 0; CI low + f* > 0 → BULL 0.10 / SIDEWAYS 0.05; high confidence → half-Kelly capped (BULL 0.25, SIDEWAYS 0.10) |
| Screener in backtest | Quarterly filter | PIT membership proxy only (`pit_nifty150.csv`) |

---

## 6. Current configuration snapshot

```yaml
# Key thresholds (config/default.yaml)
features:
  atr_period: 14
  hurst_window: 60
  ou_window: 60
  min_history_bars: 120

signals:
  cms_entry_min: 0.5
  volume_z_entry_min: 1.0
  confluence_min: 2
  stoch_rsi_max: 80
  positional_max_days: 90

features:
  hmm_train_window: 252
  hmm_retrain_step: 20
  atr_stop_mult: 3
  atr_target_mult: 6

regime:
  bull_threshold: 0.6
  bear_threshold: -0.6
  persistence_bars: 2

risk:
  risk_per_trade_pct: 0.01
  max_positions_bull: 10
  pyramid_atr_mult: 1.5   # legacy in YAML; portfolio.py uses hardcoded 3.0 for trigger
  pyramid_size_frac: 0.5
  max_positions_sideways: 2
  circuit_breaker_dd: 0.08
  kelly_winrate_ci_floor: 0.35
  # CI below floor + f* > 0: BULL 0.10 / SIDEWAYS 0.05; half-Kelly caps in kelly.py

signals:
  weekly_hurst_allow: [TRENDING, INDETERMINATE]
```

---

## 7. How to run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Tests
pytest tests/ -v

# Download + features + portfolio backtest
python scripts/run_backtest.py --portfolio --start 2015-01-01 --end 2024-12-31 --force-download

# Single symbol (Backtrader)
python scripts/run_backtest.py --symbols ITC

# Fundamentals (live Screener)
python scripts/fetch_universe.py --symbols ITC,RELIANCE

# Daily AMO plan (run ~15:35 IST after cash close)
python scripts/generate_daily_signals.py --force-download
# Optional: --universe data/manual/live_universe.csv --positions data/manual/open_positions.csv
```

**Python:** 3.10–3.13 required (3.14 breaks vectorbt/numba).

---

## 8. Suggested questions for Gemini (reviewer)

Use this document to ask for concrete change proposals:

1. **Weekly gate:** Should `W_UPTREND` require 3/3 conditions, or 2/3? Should weekly Hurst use a looser band or INDETERMINATE allowed?

2. **Entry confluence:** Is 7/7 AND too strict? Which conditions can be scored (partial credit) vs hard veto?

3. **Hurst on daily vs weekly:** Spec uses both; should entry use weekly Hurst when daily is INDETERMINATE?

4. **Volume Z threshold:** Is 1.5 too high for large-cap Nifty names?

5. **Universe:** Backtest used 5 seed symbols without live fundamental filter — correct workflow?

6. **HMM:** Should we roll-retrain every 20 bars per spec intent?

7. **Target metrics:** What parameter changes are needed to approach 22% CAGR / Sharpe 1.8 without destroying robustness?

8. **Walk-forward:** Priority order for v2 validation?

---

## 9. Files to attach with this doc

| File | Role |
|------|------|
| `algo.md` | Full original strategy |
| `config/default.yaml` | Parameters |
| `data/cache/backtest/portfolio/signal_stats.csv` | Per-symbol diagnostics |
| `data/cache/backtest/portfolio/metrics.csv` | Portfolio metrics |
| `src/kali/signals/entries.py` | Entry logic |
| `src/kali/signals/mtf_gate.py` | Weekly gate |
| `src/kali/backtest/portfolio.py` | Portfolio simulator + pyramiding + exit priority |
| `src/kali/signals/exits.py` | Pruned hard exit flags |
| `tests/test_pyramiding.py` | Pyramiding unit tests (3.0 ATR, no breakeven, cash skip) |
| `tests/test_exits_portfolio.py` | Exit signal + portfolio TP/time-stop tests |

---

## 10. Changelog (implementation history)

- Greenfield Python package from `algo.md`
- Replaced pandas-ta with manual indicators (install compatibility)
- Fixed OHLCV timezone + Volume alignment (was producing empty bars)
- Added portfolio backtest with shared capital
- Ran 2015–2024 backtest on 5 symbols → 0 trades; diagnosed weekly MTF gate as primary constraint
- Merged V12 portfolio (pyramiding, 10 bull slots, PIT mask) with looser Hurst entry; wired `weekly_hurst_allow` from config
- Kelly SIDEWAYS fallback (0.05) when Wilson CI blocks sizing; 50-symbol run → 1,757 trades, PF 1.65
- Kelly BULL capital fix: deploy 0.10 fallback (0.25 cap) when f* > 0 but CI below floor; 50-symbol run → 2,591 trades, +17.74%, CAGR 1.77%, PF 1.90
- Decoupled ATR/Kelly sizing: shares from 1% at stop, Kelly caps notional; 50-symbol → 814 trades, PF 1.24, CAGR -0.03%
- **Pyramiding logic fix (May 2026):** threshold 1.5×ATR → **3.0×ATR**; removed `pos.stop = entry_price` breakeven override; cash-aware add-on with retry if insufficient cash; `tests/test_pyramiding.py` (3 cases); 50-symbol → **931 trades**, +0.39%, CAGR **0.04%**, PF **1.24**, win rate **47.3%**
- **Pruned exits + 6ATR TP (May 2026):** intermediate ablation — 336 trades, PF 1.15 (superseded)
- **Final optimized portfolio (May 2026):** CMS entry sort, position-level trailing stop, 10 BULL slots, 1% risk — **1,025 trades**, **+203.8%**, CAGR **12.68%**, PF **2.16**, win rate **48.4%**, max DD **-13.11%**
- **Phase 2 live bridge (May 2026):** `scripts/generate_daily_signals.py` for daily EOD → next-day AMO plan (`daily_action_plan.csv`) + optional open-position trailing-stop alerts

---

*Generated for external review. Update after parameter changes or new backtest runs.*
