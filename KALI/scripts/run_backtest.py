#!/usr/bin/env python3
"""Run single-symbol backtrader or multi-symbol portfolio backtest."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kali.backtest.portfolio import run_portfolio_backtest, save_portfolio_results
from kali.backtest.run import run_backtest, save_results
from kali.backtest.vectorbt_pass import run_vectorbt_sanity
from kali.config import load_config
from kali.data.universe import load_nifty150_symbols


def main():
    parser = argparse.ArgumentParser(description="KALI backtest runner")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated, empty = PIT universe")
    parser.add_argument("--portfolio", action="store_true", help="Shared-capital portfolio mode")
    parser.add_argument("--start", type=str, default="2015-01-01")
    parser.add_argument("--end", type=str, default="2024-12-31")
    parser.add_argument("--force-download", action="store_true", help="Refresh yfinance cache")
    parser.add_argument("--vectorbt", action="store_true", help="Per-symbol vectorbt sanity (single mode)")
    args = parser.parse_args()
    cfg = load_config()

    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else load_nifty150_symbols(cfg)
    )

    if args.portfolio:
        result = run_portfolio_backtest(
            symbols=symbols,
            start=args.start,
            end=args.end or None,
            cfg=cfg,
            force_download=args.force_download,
        )
        m = result["metrics"]
        print("\n=== Portfolio Results ===")
        print(f"Final equity: ₹{m.get('final_equity', 0):,.0f}")
        print(f"Total return: {m.get('total_return_pct', 0):.2f}%")
        print(f"CAGR: {m.get('cagr', 0) * 100:.2f}%")
        print(f"Sharpe: {m.get('sharpe', 0):.2f}")
        print(f"Sortino: {m.get('sortino', 0):.2f}")
        print(f"Max drawdown: {m.get('max_drawdown', 0) * 100:.2f}%")
        print(f"Profit factor: {m.get('profit_factor', 0):.2f}")
        print(f"Win rate: {m.get('win_rate', 0) * 100:.1f}%")
        print(f"Trades: {m.get('num_trades', 0)}")
        if result.get("signal_stats"):
            print("\nPer-symbol signal diagnostics:")
            for s in result["signal_stats"]:
                print(
                    f"  {s['symbol']}: entries={s['long_entry_signals']}, "
                    f"weekly_align_days={s['daily_alignment_days']}, "
                    f"hurst_trending={s['hurst_trending_days']}"
                )
        out = save_portfolio_results(result)
        print(f"Output: {out}")
        return

    for sym in symbols:
        print(f"\n=== Backtest: {sym} ===")
        result = run_backtest(sym, cfg)
        print(f"Return: {result['return_pct']:.2f}%")
        save_results(result)
        if args.vectorbt:
            vbt_stats = run_vectorbt_sanity(result["features"], cfg)
            print(f"VectorBT sanity: {vbt_stats}")


if __name__ == "__main__":
    main()
