# KALI Quantitative Trading System — Current State Report

**Generated:** 2026-05-20  
**Purpose:** Mechanical audit for diagnosing persistent low-return backtests  
**Primary config:** `config/default.yaml`  
**Primary execution path:** `src/kali/backtest/portfolio.py` (`run_portfolio_backtest`)

---

## 1. Current Configuration & Parameters

All values below are **active** in `config/default.yaml` unless noted as **computed in code** or **unused in code**.

### Risk & portfolio caps

| Parameter | Value | Source |
|-----------|-------|--------|
| Risk per trade | **1.0%** of portfolio equity (`risk_per_trade_pct: 0.01`) | `config/default.yaml` |
| Max positions — BULL | **10** | `risk.max_positions_bull` |
| Max positions — SIDEWAYS | **2** | `risk.max_positions_sideways` |
| Max positions — BEAR / DISTRIBUTION | **0** (implicit; no YAML key) | `max_positions_for_regime()` returns `0` for any regime not BULL or SIDEWAYS |
| Circuit breaker halt | **8%** drawdown from peak | `circuit_breaker_dd: 0.08` |
| Circuit breaker resume | **4%** drawdown from peak | `circuit_resume_dd: 0.04` |
| Correlation threshold | **0.70** (60-bar return correlation) | `correlation_threshold` |
| Correlation penalty | **30%** size reduction on both symbols | `correlation_penalty: 0.30` |

**Portfolio slot logic (code, not YAML):** When the book has open positions, `max_pos = max(cap per active position regime)`. When flat, `max_pos = max_positions_bull` (10) so cash is not stuck at the sideways cap of 2.

### Kelly fraction — floors, bootstrap, and low-CI behavior

| Regime | Bootstrap prior (`kelly_bootstrap`) | Effective before 30 trades (`/ 2`) |
|--------|-------------------------------------|--------------------------------------|
| BULL_TREND | 0.06 | **0.03** |
| SIDEWAYS | 0.03 | **0.015** |
| BEAR_TREND | 0.0 | **0.0** (blocks entries) |
| DISTRIBUTION | 0.0 | **0.0** (blocks entries) |

| Parameter | Value |
|-----------|-------|
| `kelly_min_trades` | 30 |
| `kelly_winrate_ci_floor` | 0.35 (Wilson 95% CI lower bound on win rate) |

**After ≥ 30 trades per regime** (`KellyEngine.kelly_fraction`):

1. Compute half-Kelly `f_star = (b*p - q) / b` from realized win rate and payoff ratio.
2. If **`f_star <= 0`** → return **0.0** (no new entries in that regime).
3. If **`f_star > 0`** but Wilson **`ci_low < 0.35`** (low statistical confidence on win rate):
   - BULL_TREND → **0.10** (hard fallback floor)
   - SIDEWAYS → **0.05**
   - BEAR / DISTRIBUTION → **0.0**
4. If **`ci_low >= 0.35`**:
   - BULL_TREND → `min(f_star / 2, 0.25)`
   - SIDEWAYS → `min(f_star / 2, 0.10)`
   - Other → **0.0**

Kelly is applied as a **maximum position cost cap** (`portfolio_equity * kelly_frac`), not as a multiplier on the 1% risk budget (see Section 4).

### ATR multipliers

| Use | Config key | Value | Where applied |
|-----|------------|-------|---------------|
| Initial stop loss | `atr_stop_mult` | **3** | `attach_stop_target`: `stop_loss = close - 3 * atr_14` |
| Take profit | `atr_target_mult` | **6** | Portfolio: `target = tp_anchor_price + 6 * tp_anchor_atr` |
| Pyramiding trigger | `pyramid_atr_mult` | **1.5 in YAML** | **Unused** — portfolio hardcodes **`3.0 * entry_atr`** (1R move) |
| Chandelier trail | (hardcoded) | **20-bar HH − 3×ATR**, then **`cummax`** | `features/price.py` → `exit_signal` |

### Volume Z-score & confluence

| Parameter | Value |
|-----------|-------|
| `volume_z_entry_min` | **1.0** (confluence only; not a core gate) |
| `cms_entry_min` | **0.5** (confluence) |
| `confluence_min` | **2** of 4 confluence flags required |
| `weekly_hurst_allow` | `["TRENDING", "INDETERMINATE"]` (MTF gate only) |

### Signal / hold parameters present in YAML but not wired to entry/exit

| Parameter | Value | Status |
|-----------|-------|--------|
| `stoch_rsi_max` | 80 | **Not referenced** in `entries.py` or portfolio |
| `swing_max_days` | 90 | **Not referenced** — only `positional_max_days` (90) used for time stop |

### Backtest & friction

| Parameter | Value |
|-----------|-------|
| `initial_capital` | ₹1,000,000 |
| `circuit_limit_default` | 19% (marks `unexecutable` bars) |
| `slippage_amihud_scale` | 1.0 | **Not used** in portfolio sim (fixed **0.1%** friction hardcoded) |
| `rf_rate` | 6% (metrics only) |
| `min_history_bars` | 120 (warmup dropped before backtest) |

---

## 2. Implemented Entry Logic

**Source:** `src/kali/signals/entries.py`, `src/kali/signals/mtf_gate.py`, `src/kali/regime/classifier.py`  
**Execution flag:** `long_entry` = T+1 shift of `long_entry_signal` (`validation/integrity.py`)

### Final entry boolean

```
long_entry_signal =
    core_conditions_mask(df)
    AND (confluence_score >= confluence_min)   # default: >= 2
```

Portfolio additionally requires (same bar, at open):

- `long_entry == True` (T+1 shifted)
- `is_fundamentally_approved == True` (PIT membership proxy)
- `unexecutable == False` (prior-day |return| < 19%)
- `regime_risk_off == False`
- `max_positions_for_regime(regime) > 0`
- `kelly.kelly_fraction(regime) > 0`
- Circuit breaker allows entries
- Open slots under `max_pos`
- CMS-ranked candidate with affordable size

### Weekly MTF gate (`daily_alignment`)

Computed on **weekly** bars, then **lagged 1 week** and forward-filled to daily (PIT-safe):

```
W_UPTREND =
    weekly_hurst_regime IN weekly_hurst_allow     # default: TRENDING or INDETERMINATE
    AND w_kalman_velocity > 0
    AND w_cms > 0

daily_alignment = W_UPTREND.shift(1).reindex(daily, method='ffill')
```

This is a **hard core gate** via `core_conditions_mask` (`aligned = daily_alignment`).

### Core conditions (all required)

```
regime_active IN ('BULL_TREND', 'SIDEWAYS')
AND daily_alignment == True
AND kalman_velocity > 0
AND obv_divergence == False
```

**Not required for entry:** StochRSI cap, entropy, chandelier, MACD curvature (except as confluence), volume Z alone, fundamental ROE filters (only PIT list membership).

### Confluence conditions (score 0–4; need ≥ 2)

| # | Condition | Points |
|---|-----------|--------|
| 1 | `cms > cms_entry_min` (0.5) | +1 |
| 2 | Hurst confluence (see below) | +1 |
| 3 | `volume_z > volume_z_entry_min` (1.0) | +1 |
| 4 | `macd_curvature > 0` | +1 |

**Hurst confluence** (`hurst_confluence_mask`):

```
(weekly_hurst == 'INDETERMINATE' AND daily_hurst == 'TRENDING')
OR
(weekly_hurst == 'TRENDING' AND daily_hurst IN ('TRENDING', 'INDETERMINATE'))
```

`weekly_hurst_regime` on daily rows is the **lagged** weekly Hurst (`attach_mtf_columns`).

### Regime classifier (feeds core gate)

Weighted score from Hurst, vol ratio, CMS, OBV slope Z, OFI, Kalman velocity Z, entropy percentile, volume Z → `regime_raw` → **2-bar persistence** → `regime_active`.

**`regime_risk_off`:** `True` on the bar where `regime_active` transitions **BULL_TREND → BEAR_TREND** (blocks new entries that day).

---

## 3. Implemented Exit Logic

Two layers: **feature-level** `exit_signal` (shifted T+1) and **portfolio-level** overrides with intrabar TP and fixed stop.

### Soft exits

**Removed.** `exit_signal` docstring: *"Structural hard exits only; soft OBV/MACD/Hurst exits removed."*  
Confirmed in `tests/test_signals.py` and `tests/test_exits_portfolio.py`: OBV divergence + negative MACD curvature + MEAN_REVERTING Hurst do **not** fire exits.

`algo.md` still documents soft exits (exit_4 ∧ exit_5, exit_6); **code does not implement them.**

### Feature-level `exit_signal` (`signals/exits.py`)

Evaluated on **prior bar** (T+1) at today's **open**:

```
exit_signal_raw =
    (close < chandelier_exit)           # exit_1
    OR (regime_active == 'BEAR_TREND')  # exit_2
    OR (entropy_pct > 0.90)           # exit_3
    OR (days_held > positional_max_days)  # exit_7 — ONLY if days_held passed in
```

In portfolio feature prep, **`days_held` is NOT passed** → exit_7 in `exit_signal` is **always False** in the feature matrix. Time stop is handled only in the portfolio loop.

### Portfolio exit hierarchy (per bar, per position)

Order in `run_portfolio_backtest` after `apply_pyramiding`:

| Priority | Condition | Fill price | `exit_reason` |
|----------|-----------|------------|---------------|
| 1 | `high >= take_profit_target` | **Exact TP level** (not open) | `TAKE_PROFIT_6ATR` |
| 2 | `(dt - entry_date).days > positional_max_days` (90) | **Open** | `TIME_STOP_90D` |
| 3 | `exit_signal == True` **OR** `open < pos.stop` | **Open** | `EXIT_SIGNAL` or `STOP_LOSS` |

**Take profit logic:**

- Target = `tp_anchor_price + atr_target_mult * tp_anchor_atr` (default **6× ATR**).
- Anchors set at **initial entry** (`tp_anchor_price`, `tp_anchor_atr`); **unchanged after pyramiding** (tested).
- Trigger uses **same-bar intrabar `high`** (not close).
- Fill assumes **limit fill at target** when high touches target.

**Trailing stop / chandelier:**

- **Not** tied to `pos.stop`. Position stop is **fixed** at entry: `close_signal_bar - 3 * atr_14` (stored in `Position.stop`).
- Chandelier is a **symbol-level series**: `rolling(20).max(high) - 3*atr`, then **`.cummax()` over full history** — monotonic ratchet, **not reset per trade**.
- Portfolio exit via chandelier only when **`close < chandelier_exit`** on the **lagged** `exit_signal` bar, executed at **next open**.
- **No** intrabar stop on chandelier; **no** update of `pos.stop` to breakeven or chandelier level.

**Backtrader path (`strategy.py`):** Only checks `exit_signal` — **no** TP, time stop, fixed ATR stop, or pyramiding. Portfolio backtest is the authoritative multi-symbol engine.

---

## 4. Position Sizing & Pyramiding Mechanics

### Share calculation at entry (`atr_position_size`)

**ATR risk and Kelly do not multiply.** Sequence:

1. `target_risk_capital = portfolio_equity * risk_pct` (1%).
2. `theoretical_shares = floor(target_risk_capital / (entry - stop))`.
3. `max_position_cost = portfolio_equity * kelly_frac`.
4. If `theoretical_shares * entry > max_position_cost` → cap shares by Kelly cost limit; else use theoretical shares.
5. Cap by `available_cash / (entry * (1 + friction))`.

So Kelly **shrinks position size** when the full 1% risk allocation would exceed `equity × kelly_frac` notional. With low Kelly (e.g. 0.03 bootstrap), deployable capital per name is often **far below** 1% risk utilization.

**Multi-name day:** Candidates sorted by CMS; fill up to `slots`; sequential `remaining_cash`; if ≥2 names, `apply_correlation_penalty` may cut both sizes by 30%.

**Entry price:** Same-bar **open**; friction **0.1%** on buy and sell.

### Pyramiding (`apply_pyramiding`)

| Rule | Implementation |
|------|----------------|
| Regime | **BULL_TREND only** |
| Already pyramided | Skip (`has_pyramided`) |
| Trigger | `open > entry_price + 3.0 * entry_atr` (**hardcoded 3.0**, not `pyramid_atr_mult: 1.5`) |
| Add size | `floor(initial_shares * pyramid_size_frac)` → default **50%** of initial lot |
| Cash | Deduct `addon * open * (1 + friction)`; shrink addon if insufficient cash |
| Stop loss | **Unchanged** (`pos.stop` stays at original 3 ATR stop) |
| Breakeven move | **None** |
| Average entry | Updated: `(old_shares * entry_price + addon * open) / new_shares` |
| TP anchor | **Unchanged** (still initial entry price / ATR) |
| Max pyramids | **Once** per position |

Chandelier is **not** used to manage pyramided stops (comment in code: *"Chandelier manages stops"* refers to exit_signal only, not `pos.stop`).

---

## 5. Codebase Anomalies or Hardcoded Limitations

Mechanical choke points likely contributing to **low returns** and **cash drag**:

### Capital deployment restrictions

1. **Kelly bootstrap halved** before 30 trades (BULL effective **3%** max notional cap vs 6% YAML prior).
2. **BEAR / DISTRIBUTION Kelly = 0** and **max positions = 0** — zero new exposure in bear labels.
3. **SIDEWAYS max 2 positions** when any open position is sideways-classified; mixed book can still use bull cap (10) if any bull position exists.
4. **Correlation penalty** applies **0.7×** to **both** correlated names when |ρ| > 0.7 — compounding under-diversification on entry day.
5. **Sequential cash reservation** in the entry loop — later CMS-ranked names may get **zero shares** even with slots left.
6. **Circuit breaker** halts **all** new entries at 8% DD until DD ≤ 4%.

### Entry gate strictness

7. **Five-way AND on core**: regime + weekly MTF + Kalman > 0 + no OBV divergence — eliminates many bars (see `signal_stats.csv`: entries ≪ alignment days).
8. **Confluence ≥ 2 of 4** still filters aggressively when CMS or volume Z weak.
9. **`regime_risk_off`** one-bar blackout after bull→bear flip.
10. **`stoch_rsi_max: 80` in YAML but unused** — if intended as filter, it's absent; if spec assumed it was active, behavior is looser than doc.

### Exit / profit capture

11. **Fixed `pos.stop` at entry** — does not trail with price; winners give back gains until TP, time stop, chandelier close signal, or bear regime exit.
12. **Chandelier `cummax` over full symbol history** — trail floor never resets per trade; can stay elevated after old peaks → **`close < chandelier_exit`** may fire more often than a per-trade chandelier.
13. **TP anchor frozen at initial entry** after pyramiding — effective TP distance in R multiples **shrinks** on averaged-up positions.
14. **TP fill at exact limit** when `high >= target` — optimistic vs open/slippage.
15. **Exit at open** on signal/stop — gap risk not modeled beyond circuit tag.

### Config / code drift

16. **`pyramid_atr_mult: 1.5` unused**; code uses **3.0** (same as stop distance → pyramid at +1R).
17. **`swing_max_days` unused**; only 90-day positional time stop in portfolio.
18. **`slippage_amihud_scale` unused** in portfolio; flat 10 bps only.
19. **Fundamental filters in YAML** (ROE, D/E, Piotroski, etc.) apply to **screener/universe builder**, not per-bar `is_fundamentally_approved` — which is **PIT CSV membership only** (`universe.py` TODO).
20. **`exit_signal` time component dead** in features; time stop only in portfolio (calendar days, not trading days).

### Engine divergence

21. **`KaliStrategy` (Backtrader)** lacks portfolio TP, time stop, stop-loss-at-open, pyramiding, and multi-symbol logic — single-symbol runs are **not** comparable to `run_portfolio_backtest`.

### Double-discounting check

- **Not** double-multiplying Kelly × risk%: Kelly caps **notional**, not risk dollars (by design).
- **Effective** double constraint: low Kelly cap + 1% risk formula + correlation penalty + cash sequencing can produce **much smaller** positions than 1% risk implies.

### Observed signal density (cached portfolio run)

From `data/cache/backtest/portfolio/signal_stats.csv` (example): symbols show **~80–190** `long_entry` signals over **~2347** bars vs **~700–1000+** `daily_alignment` days — MTF + core + confluence + portfolio gates collapse deployable signals substantially.

---

## Appendix: Key file map

| Concern | File |
|---------|------|
| Config | `config/default.yaml` |
| Entries | `src/kali/signals/entries.py` |
| MTF gate | `src/kali/signals/mtf_gate.py` |
| Exits (feature) | `src/kali/signals/exits.py` |
| Regime | `src/kali/regime/classifier.py` |
| Chandelier / ATR | `src/kali/features/price.py` |
| Portfolio sim | `src/kali/backtest/portfolio.py` |
| Sizing | `src/kali/risk/sizing.py` |
| Kelly | `src/kali/risk/kelly.py` |
| T+1 | `src/kali/validation/integrity.py` |

---

*End of report. For spec vs implementation gaps, compare to `algo.md` Section 5 (soft exits, swing 20-day stop, and Amihud slippage are spec-heavy but code-pruned).*
