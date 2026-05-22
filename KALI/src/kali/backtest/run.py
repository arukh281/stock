"""Backtest runner and performance metrics."""

from __future__ import annotations

from pathlib import Path

import backtrader as bt
import numpy as np
import pandas as pd

from kali.config import load_config, project_root
from kali.data.ohlcv import load_ohlcv
from kali.features.pipeline import build_features, build_weekly_features
from kali.regime.classifier import classify_regime
from kali.signals.entries import attach_stop_target, long_entry_signal
from kali.signals.exits import exit_signal
from kali.signals.mtf_gate import attach_mtf_columns
from kali.validation.integrity import apply_t1_execution, tag_unexecutable


def prepare_symbol_features(symbol: str, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    ohlcv = load_ohlcv(symbol, cfg)
    df = build_features(ohlcv, cfg)
    weekly = build_weekly_features(ohlcv, cfg)
    df = attach_mtf_columns(df, weekly, cfg)
    df = classify_regime(df, cfg)
    df["long_entry_signal"] = long_entry_signal(df, cfg)
    df = attach_stop_target(df, cfg)
    df["unexecutable"] = tag_unexecutable(df, cfg["backtest"]["circuit_limit_default"])
    df["exit_signal_raw"] = exit_signal(df, cfg=cfg)
    df["long_entry"] = apply_t1_execution(df["long_entry_signal"])
    df["exit_signal"] = apply_t1_execution(df["exit_signal_raw"])
    return df


def run_backtest(
    symbol: str,
    cfg: dict | None = None,
    plot: bool = False,
) -> dict:
    cfg = cfg or load_config()
    df = prepare_symbol_features(symbol, cfg)

    cerebro = bt.Cerebro()
    data = bt.feeds.PandasData(
        dataname=df,
        datetime=None,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=-1,
    )
    cerebro.adddata(data)
    cerebro.addstrategy(
        __import__("kali.backtest.strategy", fromlist=["KaliStrategy"]).KaliStrategy,
        features=df,
        cfg=cfg,
        symbol=symbol,
    )
    cerebro.broker.setcash(cfg["backtest"]["initial_capital"])
    cerebro.broker.setcommission(commission=0.001)
    results = cerebro.run()
    strat = results[0]
    final = cerebro.broker.getvalue()
    initial = cfg["backtest"]["initial_capital"]
    return {
        "symbol": symbol,
        "initial": initial,
        "final": final,
        "return_pct": (final - initial) / initial * 100,
        "strategy": strat,
        "features": df,
    }


def compute_metrics(equity_curve: pd.Series, trades: list[float], rf: float = 0.06) -> dict:
    if len(equity_curve) < 2:
        return {}
    ret = equity_curve.pct_change().dropna()
    years = len(ret) / 252
    total_ret = equity_curve.iloc[-1] / equity_curve.iloc[0]
    cagr = total_ret ** (1 / years) - 1 if years > 0 else 0
    excess = ret - rf / 252
    sharpe = excess.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    downside = ret[ret < 0]
    sortino = excess.mean() / downside.std() * np.sqrt(252) if len(downside) > 0 else 0
    peak = equity_curve.expanding().max()
    dd = (equity_curve - peak) / peak
    max_dd = dd.min()
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else float("inf")
    return {
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "profit_factor": pf,
        "win_rate": len(wins) / len(trades) if trades else 0,
        "calmar": cagr / abs(max_dd) if max_dd != 0 else 0,
    }


def save_results(result: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or project_root() / "data" / "cache" / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    sym = result["symbol"].replace(".NS", "")
    result["features"].to_parquet(out_dir / f"{sym}_features.parquet")
    return out_dir
