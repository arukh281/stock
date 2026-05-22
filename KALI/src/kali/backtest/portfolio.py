"""Multi-symbol portfolio backtest with shared capital."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from kali.backtest.run import compute_metrics, prepare_symbol_features
from kali.config import load_config, project_root
from kali.data.ohlcv import download_ohlcv
from kali.data.universe import load_nifty150_symbols
from kali.risk.kelly import KellyEngine
from kali.risk.sizing import apply_correlation_penalty, atr_position_size, max_positions_for_regime


@dataclass
class Position:
    symbol: str
    shares: int
    initial_shares: int
    entry_price: float
    entry_atr: float
    stop: float
    entry_date: pd.Timestamp
    regime: str
    has_pyramided: bool = False
    tp_anchor_price: float = 0.0
    tp_anchor_atr: float = 0.0
    highest_high_since_entry: float = 0.0


@dataclass
class TradeRecord:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    regime: str
    exit_reason: str = ""


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)


def download_symbols(
    symbols: list[str],
    start: str,
    end: str | None,
    cfg: dict,
    force: bool = False,
) -> dict[str, pd.DataFrame]:
    """Download live OHLCV for all symbols."""
    from kali.data.ohlcv import load_ohlcv

    ohlcv_map = {}
    for sym in symbols:
        print(f"  Downloading {sym}...")
        if force:
            ohlcv_map[sym] = download_ohlcv(sym, start=start, end=end, cfg=cfg)
        else:
            try:
                ohlcv_map[sym] = load_ohlcv(sym, cfg)
                if ohlcv_map[sym].index.min() > pd.Timestamp(start):
                    ohlcv_map[sym] = download_ohlcv(sym, start=start, end=end, cfg=cfg)
            except Exception:
                ohlcv_map[sym] = download_ohlcv(sym, start=start, end=end, cfg=cfg)
        if end:
            ohlcv_map[sym] = ohlcv_map[sym].loc[:end]
        ohlcv_map[sym] = ohlcv_map[sym].loc[start:]
    return ohlcv_map


def prepare_portfolio_features(
    symbols: list[str],
    start: str,
    end: str | None,
    cfg: dict,
    force: bool = False,
) -> dict[str, pd.DataFrame]:
    """Download and build feature frames per symbol."""
    from kali.data.ohlcv import _ticker_base, load_ohlcv
    from kali.features.pipeline import build_features, build_weekly_features
    from kali.regime.classifier import classify_regime
    from kali.signals.entries import attach_stop_target, long_entry_signal
    from kali.signals.exits import exit_signal
    from kali.data.universe import apply_fundamental_mask
    from kali.signals.mtf_gate import attach_mtf_columns
    from kali.validation.integrity import apply_t1_execution, tag_unexecutable

    if force:
        download_symbols(symbols, start, end, cfg, force=True)
    features = {}
    min_bars = cfg["features"]["min_history_bars"]

    for sym in symbols:
        print(f"  Building features {sym}...")
        try:
            ohlcv = load_ohlcv(sym, cfg)
            if len(ohlcv) == 0 or ohlcv.index.min() > pd.Timestamp(start) or force:
                ohlcv = download_ohlcv(sym, start=start, end=end, cfg=cfg)
        except (ValueError, OSError) as exc:
            print(f"    WARNING: {sym} data unavailable ({exc}), skipping")
            continue
        ohlcv = ohlcv.sort_index()
        ohlcv = ohlcv.loc[pd.Timestamp(start) :]
        if end:
            ohlcv = ohlcv.loc[: pd.Timestamp(end)]
        if len(ohlcv) < min_bars:
            print(f"    WARNING: {sym} only {len(ohlcv)} bars, skipping")
            continue
        df = build_features(ohlcv, cfg)
        weekly = build_weekly_features(ohlcv, cfg)
        df = attach_mtf_columns(df, weekly, cfg)
        df = classify_regime(df, cfg)
        df = apply_fundamental_mask(df, sym, cfg)
        df["long_entry_signal"] = long_entry_signal(df, cfg)
        df = attach_stop_target(df, cfg)
        df["unexecutable"] = tag_unexecutable(df, cfg["backtest"]["circuit_limit_default"])
        df["exit_signal_raw"] = exit_signal(df, cfg=cfg)
        df["long_entry"] = apply_t1_execution(df["long_entry_signal"])
        df["exit_signal"] = apply_t1_execution(df["exit_signal_raw"])
        df = df.iloc[min_bars:]
        base = _ticker_base(sym)
        features[base] = df
        print(f"    {base}: {len(df)} bars, entries={df['long_entry'].sum():.0f}")
    return features


def _portfolio_value(state: PortfolioState, prices: dict[str, float]) -> float:
    holdings = sum(
        pos.shares * prices.get(sym, pos.entry_price)
        for sym, pos in state.positions.items()
    )
    return state.cash + holdings


def _max_positions(cfg: dict, regimes: list[str]) -> int:
    caps = [max_positions_for_regime(r, cfg) for r in regimes if r]
    return max(caps) if caps else cfg["risk"]["max_positions_sideways"]


def _close_position(
    state: PortfolioState,
    sym: str,
    pos: Position,
    dt: pd.Timestamp,
    exit_px: float,
    reason: str,
    kelly: KellyEngine,
    friction: float = 0.001,
) -> None:
    pnl = (exit_px - pos.entry_price) * pos.shares
    state.cash += pos.shares * exit_px * (1 - friction)
    state.trades.append(
        TradeRecord(
            symbol=sym,
            entry_date=pos.entry_date,
            exit_date=dt,
            entry_price=pos.entry_price,
            exit_price=exit_px,
            shares=pos.shares,
            pnl=pnl,
            regime=pos.regime,
            exit_reason=reason,
        )
    )
    kelly.record_trade(pos.regime, pnl)


def _cms_score(row: pd.Series) -> float:
    """Composite Momentum Score for cross-sectional entry ranking (higher = stronger)."""
    if "cms" not in row.index:
        return -np.inf
    v = row["cms"]
    if pd.isna(v):
        return -np.inf
    return float(v)


def _sort_entry_candidates(candidates: list[dict]) -> list[dict]:
    """Rank by CMS so scarce slots/cash go to highest-velocity names first."""
    return sorted(candidates, key=lambda c: c["cms"], reverse=True)


def _take_profit_target(pos: Position, cfg: dict) -> float:
    mult = cfg["signals"]["atr_target_mult"]
    anchor_price = pos.tp_anchor_price if pos.tp_anchor_price > 0 else pos.entry_price
    anchor_atr = pos.tp_anchor_atr if pos.tp_anchor_atr > 0 else pos.entry_atr
    return anchor_price + mult * anchor_atr


def apply_pyramiding(
    state: PortfolioState,
    rows: dict,
    cfg: dict,
    friction: float = 0.001,
) -> None:
    """Add 50% to winners in BULL when price > entry + 3 ATR (1R)."""
    risk = cfg["risk"]
    pyramid_size_frac = risk.get("pyramid_size_frac", 0.5)

    for sym, pos in list(state.positions.items()):
        if sym not in rows or pos.has_pyramided:
            continue
        row = rows[sym]
        if row.get("regime_active") != "BULL_TREND":
            continue
        px = float(row["open"])
        threshold = pos.entry_price + 3.0 * pos.entry_atr
        if px <= threshold:
            continue
        addon = int(pos.initial_shares * pyramid_size_frac)
        desired_addon_cost = addon * px
        if desired_addon_cost > state.cash:
            addon = math.floor(state.cash / px)
        if addon <= 0:
            continue
        cost = addon * px * (1 + friction)
        if cost > state.cash:
            addon = math.floor(state.cash / (px * (1 + friction)))
        if addon <= 0:
            continue
        new_shares = pos.shares + addon
        pos.entry_price = (pos.shares * pos.entry_price + addon * px) / new_shares
        pos.shares = new_shares
        pos.has_pyramided = True
        state.cash -= cost


def run_portfolio_backtest(
    symbols: list[str] | None = None,
    start: str = "2015-01-01",
    end: str | None = "2024-12-31",
    cfg: dict | None = None,
    force_download: bool = False,
) -> dict:
    cfg = cfg or load_config()
    symbols = symbols or load_nifty150_symbols(cfg)
    symbols = [s.replace(".NS", "").upper() for s in symbols]

    print(f"Portfolio backtest: {len(symbols)} symbols, {start} -> {end or 'latest'}")
    feature_map = prepare_portfolio_features(symbols, start, end, cfg, force=force_download)
    if not feature_map:
        raise RuntimeError("No symbols produced valid feature histories")

    calendar = sorted(set().union(*[set(df.index) for df in feature_map.values()]))
    calendar = [d for d in calendar if pd.Timestamp(start) <= d <= (pd.Timestamp(end) if end else d)]

    initial = cfg["backtest"]["initial_capital"]
    state = PortfolioState(cash=initial)
    kelly = KellyEngine(
        bootstrap=cfg["risk"]["kelly_bootstrap"],
        min_trades=cfg["risk"]["kelly_min_trades"],
        winrate_ci_floor=cfg["risk"]["kelly_winrate_ci_floor"],
    )

    returns_panel = pd.DataFrame(
        {sym: df["close"].pct_change() for sym, df in feature_map.items()}
    )

    for i, dt in enumerate(calendar):
        prices = {}
        rows = {}
        for sym, df in feature_map.items():
            if dt not in df.index:
                continue
            row = df.loc[dt]
            prices[sym] = float(row["open"])
            rows[sym] = row

        if not prices:
            continue

        apply_pyramiding(state, rows, cfg)

        max_hold_days = cfg["signals"].get("positional_max_days", 90)

        # Exits: 6ATR TP (intrabar high) -> trailing stop -> time stop -> BEAR regime
        to_close = []
        for sym, pos in list(state.positions.items()):
            if sym not in rows:
                continue
            row = rows[sym]
            current_high = float(row["high"])
            current_close = float(row["close"])
            current_atr = float(row.get("atr_14", pos.entry_atr))
            next_open = float(row["open"])

            # 1) Update highest high for this active trade only.
            if current_high > pos.highest_high_since_entry:
                pos.highest_high_since_entry = current_high

            # 2) Position-level trailing stop anchored to this trade's highs.
            trailing_stop = pos.highest_high_since_entry - (3.0 * current_atr)

            # 3) Never trail below the initial hard stop.
            trailing_stop = max(trailing_stop, pos.stop)
            target_price = _take_profit_target(pos, cfg)

            if current_high >= target_price:
                _close_position(
                    state, sym, pos, dt, target_price, "TAKE_PROFIT_6ATR", kelly
                )
                to_close.append(sym)
                continue

            if current_close < trailing_stop:
                _close_position(state, sym, pos, dt, next_open, "TRAILING_STOP", kelly)
                to_close.append(sym)
                continue

            days_held = (dt - pos.entry_date).days
            if days_held > max_hold_days:
                _close_position(state, sym, pos, dt, next_open, "TIME_STOP_90D", kelly)
                to_close.append(sym)
                continue

            if row.get("exit_signal", False):
                _close_position(state, sym, pos, dt, next_open, "BEAR_REGIME", kelly)
                to_close.append(sym)
        for sym in to_close:
            del state.positions[sym]

        equity = _portfolio_value(state, {s: rows[s]["close"] for s in rows if s in state.positions or s in prices})
        for sym, df in feature_map.items():
            if dt in df.index:
                prices[sym] = float(df.loc[dt, "close"])
        equity = _portfolio_value(state, prices)
        state.equity_curve.append((dt, equity))

        active_regimes = [rows[s].get("regime_active", "BEAR_TREND") for s in state.positions if s in rows]
        if active_regimes:
            max_pos = _max_positions(cfg, active_regimes)
        else:
            # Flat book: use bull cap so cash is not stuck at sideways limit (2)
            max_pos = cfg["risk"]["max_positions_bull"]
        if len(state.positions) >= max_pos:
            continue

        candidates: list[dict] = []
        for sym, row in rows.items():
            if sym in state.positions:
                continue
            if row.get("unexecutable", False):
                continue
            if not row.get("long_entry", False):
                continue
            if not row.get("is_fundamentally_approved", True):
                continue
            regime = row.get("regime_active", "BEAR_TREND")
            if max_positions_for_regime(regime, cfg) == 0:
                continue
            if row.get("regime_risk_off", False):
                continue
            kelly_frac = kelly.kelly_fraction(regime)
            if kelly_frac <= 0:
                continue
            candidates.append(
                {
                    "symbol": sym,
                    "row": row,
                    "regime": regime,
                    "kelly_frac": kelly_frac,
                    "cms": _cms_score(row),
                }
            )

        if not candidates:
            continue

        # Relative strength: fill slots/cash with highest-CMS names first (not ticker order).
        candidates = _sort_entry_candidates(candidates)
        slots = max_pos - len(state.positions)

        proposed_sizes = {}
        stop_by_sym: dict[str, float] = {}
        remaining_cash = state.cash
        for cand in candidates[:slots]:
            sym = cand["symbol"]
            row = cand["row"]
            kelly_frac = cand["kelly_frac"]
            entry = float(row["open"])
            stop = float(row.get("stop_loss", entry - 3 * row.get("atr_14", 1)))
            stop_by_sym[sym] = stop
            size = atr_position_size(
                equity,
                entry,
                stop,
                risk_pct=cfg["risk"]["risk_per_trade_pct"],
                kelly_frac=kelly_frac,
                available_cash=remaining_cash,
                friction=0.001,
            )
            if size > 0:
                proposed_sizes[sym] = size
                remaining_cash -= size * entry * (1 + 0.001)

        if len(proposed_sizes) >= 2:
            proposed_sizes = apply_correlation_penalty(
                proposed_sizes,
                returns_panel.loc[:dt].tail(60),
                threshold=cfg["risk"]["correlation_threshold"],
                penalty=cfg["risk"]["correlation_penalty"],
            )

        for cand in candidates[:slots]:
            sym = cand["symbol"]
            if sym not in proposed_sizes:
                continue
            row = cand["row"]
            regime = cand["regime"]
            shares = proposed_sizes[sym]
            entry = float(row["open"])
            cost = shares * entry * (1 + 0.001)
            if cost > state.cash:
                shares = int(state.cash / (entry * 1.001))
            if shares <= 0:
                continue
            state.cash -= shares * entry * (1 + 0.001)
            entry_atr = float(row.get("atr_14", 1))
            state.positions[sym] = Position(
                symbol=sym,
                shares=shares,
                initial_shares=shares,
                entry_price=entry,
                entry_atr=entry_atr,
                stop=stop_by_sym.get(sym, entry - 3 * entry_atr),
                entry_date=dt,
                regime=regime,
                tp_anchor_price=entry,
                tp_anchor_atr=entry_atr,
                highest_high_since_entry=entry,
            )

    # Close remaining at last close
    if calendar:
        last = calendar[-1]
        for sym, pos in list(state.positions.items()):
            if sym in feature_map and last in feature_map[sym].index:
                exit_px = float(feature_map[sym].loc[last, "close"])
                _close_position(
                    state, sym, pos, last, exit_px, "END_OF_BACKTEST", kelly
                )
        state.positions.clear()
        prices = {sym: float(feature_map[sym].loc[last, "close"]) for sym in feature_map if last in feature_map[sym].index}
        state.equity_curve.append((last, _portfolio_value(state, prices)))

    equity_series = pd.Series(
        {d: v for d, v in state.equity_curve},
        name="equity",
    ).sort_index()
    trade_pnls = [t.pnl for t in state.trades]
    metrics = compute_metrics(equity_series, trade_pnls, rf=cfg["backtest"]["rf_rate"])
    metrics["total_return_pct"] = (equity_series.iloc[-1] / initial - 1) * 100 if len(equity_series) else 0
    metrics["num_trades"] = len(state.trades)
    metrics["final_equity"] = equity_series.iloc[-1] if len(equity_series) else initial

    signal_stats = []
    for sym, df in feature_map.items():
        signal_stats.append(
            {
                "symbol": sym,
                "bars": len(df),
                "long_entry_signals": int(df["long_entry"].sum()),
                "daily_alignment_days": int(df["daily_alignment"].sum()),
                "hurst_trending_days": int((df["hurst_regime"] == "TRENDING").sum()),
            }
        )

    return {
        "symbols": symbols,
        "start": start,
        "end": end,
        "metrics": metrics,
        "equity_curve": equity_series,
        "trades": state.trades,
        "feature_map": feature_map,
        "signal_stats": signal_stats,
    }


def save_portfolio_results(result: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or project_root() / "data" / "cache" / "backtest" / "portfolio"
    out_dir.mkdir(parents=True, exist_ok=True)

    result["equity_curve"].to_frame().to_parquet(out_dir / "equity_curve.parquet")

    trades_df = pd.DataFrame(
        [
            {
                "symbol": t.symbol,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "pnl": t.pnl,
                "regime": t.regime,
                "exit_reason": t.exit_reason,
            }
            for t in result["trades"]
        ]
    )
    trades_df.to_csv(out_dir / "trades.csv", index=False)

    metrics_df = pd.DataFrame([result["metrics"]])
    metrics_df.to_csv(out_dir / "metrics.csv", index=False)

    if result.get("signal_stats"):
        pd.DataFrame(result["signal_stats"]).to_csv(out_dir / "signal_stats.csv", index=False)

    summary_path = out_dir / "summary.txt"
    m = result["metrics"]
    summary_path.write_text(
        "\n".join(
            [
                f"Symbols: {', '.join(result['symbols'])}",
                f"Period: {result['start']} -> {result['end']}",
                f"Final equity: ₹{m.get('final_equity', 0):,.0f}",
                f"Total return: {m.get('total_return_pct', 0):.2f}%",
                f"CAGR: {m.get('cagr', 0)*100:.2f}%",
                f"Sharpe: {m.get('sharpe', 0):.2f}",
                f"Sortino: {m.get('sortino', 0):.2f}",
                f"Max DD: {m.get('max_drawdown', 0)*100:.2f}%",
                f"Profit factor: {m.get('profit_factor', 0):.2f}",
                f"Win rate: {m.get('win_rate', 0)*100:.1f}%",
                f"Trades: {m.get('num_trades', 0)}",
            ]
        ),
        encoding="utf-8",
    )
    print(f"\nResults saved to {out_dir}")
    return out_dir
