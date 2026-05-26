from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pandas as pd

from ma44.backtest import TradeResult, run_portfolio_backtest, run_symbol_backtest
from ma44.config import Settings
from ma44.data import fetch_daily
from ma44.paper import daily_step_db, dump_scan, init_db, paper_context, paper_status
from ma44.symbols import load_backtest_universe, scan_universe_positive_slope
from ma44.universe_mcap import top_symbols_by_live_ffmc


def _trade_bookends(trades: list[TradeResult]) -> tuple[str, str] | None:
    if not trades:
        return None
    earliest_in = min(t.entry_time for t in trades)
    latest_out = max(t.exit_time for t in trades)
    return str(earliest_in.date()), str(latest_out.date())


def _write_trades_csv(path: Path, trades: list[TradeResult]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["symbol", "entry_date", "exit_date", "exit_reason", "entry", "exit", "qty", "pnl"]
        )
        for t in trades:
            w.writerow(
                [
                    t.symbol,
                    t.entry_time.date(),
                    t.exit_time.date(),
                    t.exit_reason,
                    f"{t.entry:.4f}",
                    f"{t.exit:.4f}",
                    t.qty,
                    f"{t.pnl:.2f}",
                ]
            )
    return len(trades)


def _load_settings(path: str | None) -> Settings:
    return Settings.load(Path(path) if path else None)


def _ohlc_calendar_bounds(dfs: dict[str, pd.DataFrame]) -> tuple[str, str] | None:
    mins: list[pd.Timestamp] = []
    maxs: list[pd.Timestamp] = []
    for df in dfs.values():
        if df is None or df.empty:
            continue
        mins.append(pd.Timestamp(df.index.min()))
        maxs.append(pd.Timestamp(df.index.max()))
    if not mins:
        return None
    return str(min(mins).date()), str(max(maxs).date())


def _print_trade_lines(trades: list, tail_n: int, *, label: str) -> None:
    if not trades or tail_n == 0:
        return
    n = len(trades)
    if tail_n < 0 or tail_n >= n:
        chunk = trades
        note = f"all {n} trades"
    else:
        chunk = trades[-tail_n:]
        note = f"last {tail_n} of {n} trades"
    print(f"  ({label}: {note})")
    for t in chunk:
        print(
            f"  {t.symbol} {t.entry_time.date()} -> {t.exit_time.date()} {t.exit_reason} "
            f"entry={t.entry:.2f} exit={t.exit:.2f} qty={t.qty} pnl={t.pnl:.2f}"
        )


def cmd_backtest(args: argparse.Namespace) -> int:
    settings = _load_settings(args.config)
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = load_backtest_universe(settings)
    start_cash = float(settings.starting_cash_inr)
    summary_rows: list[tuple[str, int, int, float, float]] = []

    if args.portfolio and len(syms) >= 1:
        dfs: dict[str, pd.DataFrame] = {}
        for sym in syms:
            sym = sym.strip()
            if not sym:
                continue
            df = fetch_daily(sym, start=args.start, end=args.end)
            if df.empty:
                print(f"{sym}: no data")
                continue
            dfs[sym] = df
        if dfs:
            trades, eq = run_portfolio_backtest(dfs, settings)
            wins = sum(1 for t in trades if t.pnl > 0)
            sum_pnl = float(sum(t.pnl for t in trades)) if trades else 0.0
            last_eq = float(eq.iloc[-1]) if not eq.empty else start_cash
            print("\n== PORTFOLIO BASELINE (one shared cash pool) ==")
            print(f"symbols: {len(dfs)} loaded (master pool; daily top-{settings.universe_top_n} mcap filter in sim)")
            print(
                "slope: checked on every bar inside the strategy "
                "(rising/monotone 44 SMA + min slope + touch + green/hammer + optional close>prev)"
            )
            print(
                f"mcap_universe: top {settings.universe_top_n} by prior close×shares each session; "
                "entries ranked by signal confidence when cash is shared"
            )
            print(f"fetch --start: {args.start} (Yahoo daily history loaded from here onward)")
            bounds = _ohlc_calendar_bounds(dfs)
            if bounds:
                print(f"yahoo_bar_calendar_range: {bounds[0]} .. {bounds[1]} (union across symbols)")
            if not eq.empty:
                print(
                    f"equity_curve_simulation: {eq.index[0].date()} .. {eq.index[-1].date()} "
                    f"(starts after SMA warmup)"
                )
            print(f"starting_cash: ₹{start_cash:,.2f}")
            print(f"trades: {len(trades)} win_rate: {wins}/{len(trades) if trades else 0}")
            print(f"sum_realized_pnl: {sum_pnl:.2f}")
            print(f"last_equity_mtm: {last_eq:.2f}")
            if trades:
                be = _trade_bookends(trades)
                if be:
                    print(
                        f"closed_trade_calendar_span: {be[0]} (earliest entry) .. "
                        f"{be[1]} (latest exit)"
                    )
                print(
                    "trade_log_order: chronological by exit (when each round-trip completed in the sim)"
                )
            if args.trades_csv and trades:
                p = Path(args.trades_csv).expanduser()
                n = _write_trades_csv(p, trades)
                print(f"trades_csv: wrote {n} rows -> {p}")
            if trades and not args.no_trades:
                _print_trade_lines(trades, args.tail_trades, label="detail")
        else:
            print("Portfolio mode: no symbol had usable OHLC data for the given --start.")
        return 0

    csv_trades_accum: list[TradeResult] = []
    for sym in syms:
        sym = sym.strip()
        if not sym:
            continue
        df = fetch_daily(sym, start=args.start, end=args.end)
        if df.empty:
            print(f"{sym}: no data")
            continue
        trades, eq = run_symbol_backtest(df, sym, settings)
        wins = sum(1 for t in trades if t.pnl > 0)
        sum_pnl = float(sum(t.pnl for t in trades)) if trades else 0.0
        last_eq = float(eq.iloc[-1]) if not eq.empty else start_cash

        print(f"\n== {sym} ==")
        print(f"fetch --start: {args.start}")
        if not df.empty:
            print(f"yahoo_bar_calendar_range: {df.index.min().date()} .. {df.index.max().date()}")
        if not eq.empty:
            print(
                f"equity_curve_simulation: {eq.index[0].date()} .. {eq.index[-1].date()} "
                f"(starts after SMA warmup)"
            )
        print(f"trades: {len(trades)} win_rate: {wins}/{len(trades) if trades else 0}")
        print(f"sum_pnl: {sum_pnl:.2f}")
        if trades:
            be = _trade_bookends(trades)
            if be:
                print(
                    f"closed_trade_calendar_span: {be[0]} (earliest entry) .. "
                    f"{be[1]} (latest exit)"
                )
            print("trade_log_order: chronological by exit")
            if args.trades_csv:
                csv_trades_accum.extend(trades)
        if trades and not args.no_trades:
            _print_trade_lines(trades, args.tail_trades, label=sym)
        print(f"last_equity: {last_eq:.2f}")
        summary_rows.append((sym, len(trades), wins, sum_pnl, last_eq))

    if args.trades_csv and csv_trades_accum:
        p = Path(args.trades_csv).expanduser()
        combined = sorted(csv_trades_accum, key=lambda t: (t.exit_time, t.symbol))
        n = _write_trades_csv(p, combined)
        print(f"\ntrades_csv: wrote {n} rows (all symbols, sorted by exit date) -> {p}")

    if summary_rows:
        print("\n" + "=" * 92)
        print(
            f"BACKTEST SUMMARY — each row is a separate ₹{start_cash:,.0f} account for that symbol only "
            f"(not one combined portfolio). Use --portfolio for one shared ₹{start_cash:,.0f} pool."
        )
        hdr = f"{'symbol':<18} {'trades':>7} {'wins':>6} {'sum_pnl':>12} {'pnl_%':>8} {'last_equity':>14}"
        print(hdr)
        print("-" * len(hdr))
        for sym, nt, w, sp, le in sorted(summary_rows, key=lambda r: r[3], reverse=True):
            pct = (sp / start_cash * 100.0) if start_cash else 0.0
            print(f"{sym:<18} {nt:>7} {w:>6} {sp:>12.2f} {pct:>7.2f}% {le:>14.2f}")
        if args.csv:
            print("\ncsv:")
            print("symbol,trades,wins,sum_pnl,pnl_pct_start,last_equity")
            for sym, nt, w, sp, le in sorted(summary_rows, key=lambda r: r[0]):
                pct = (sp / start_cash * 100.0) if start_cash else 0.0
                print(f"{sym},{nt},{w},{sp:.2f},{pct:.2f},{le:.2f}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    settings = _load_settings(args.config)
    lines = daily_step_db(Path(args.db), settings)
    for ln in lines:
        print(ln)
    if not lines:
        print("(no actions)")
    st = paper_status(Path(args.db))
    print(f"\nCash: ₹{st['cash']:.2f}")
    if st["positions"]:
        print("Open positions:", st["positions"])
    print()
    print(paper_context(Path(args.db), settings)["text"])
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    settings = _load_settings(args.config)
    ctx = paper_context(Path(args.db), settings)
    print(ctx["text"])
    if args.json:
        print(json.dumps(ctx["data"], indent=2, default=str))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    st = paper_status(Path(args.db))
    print(json.dumps(st, indent=2, default=str))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    p = Path(args.db)
    init_db(p)
    cash = str(args.cash)
    con = sqlite3.connect(p)
    try:
        con.execute(
            "INSERT INTO meta(k,v) VALUES('cash',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (cash,),
        )
        con.commit()
    finally:
        con.close()
    print(f"Initialized {p} with cash={cash}")
    return 0


def cmd_universe(args: argparse.Namespace) -> int:
    settings = _load_settings(args.config)
    n = int(args.top or getattr(settings, "universe_top_n", 100) or 100)
    syms = top_symbols_by_live_ffmc(settings, n=n)
    print(f"top_{n}_by_live_mcap_proxy ({len(syms)} names, cached per calendar day):")
    for i, sym in enumerate(syms, 1):
        print(f"  {i:3d}. {sym}")
    return 0


def cmd_scan_slope(args: argparse.Namespace) -> int:
    import math

    settings = _load_settings(args.config)
    if args.max and args.max > 0:
        settings = replace(settings, universe_top_n=int(args.max))
    rows = scan_universe_positive_slope(settings)
    okct = sum(1 for r in rows if r.get("ok"))
    print(f"positive_slope: {okct}/{len(rows)}")
    for r in rows:
        if not args.all and not r.get("ok"):
            continue
        if r.get("ok"):
            sl = float(r["sma_slope_vs_L"])
            sls = "nan" if math.isnan(sl) else f"{sl:.4f}"
            conf = float(r.get("confidence", 0.0))
            print(
                f"{r['symbol']}\t{r.get('asof')}\tconf={conf:.3f}\tclose={float(r['close']):.2f}\t"
                f"sma44={float(r['sma44']):.2f}\tsma_now_minus_sma_L={sls}"
            )
        else:
            print(f"{r['symbol']}\tFAIL\t{r.get('reason', '')}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    settings = _load_settings(args.config)
    for ln in dump_scan(settings):
        print(ln)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m ma44")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_bt = sub.add_parser("backtest", help="Historical simulation per symbol")
    p_bt.add_argument("--config", default=None, help="Path to JSON config")
    p_bt.add_argument("--start", default="2015-01-01")
    p_bt.add_argument("--end", default=None, help="Optional inclusive history end date (YYYY-MM-DD)")
    p_bt.add_argument(
        "--symbols",
        default=None,
        help="Comma list override (skips NSE master pool; for quick tests)",
    )
    p_bt.add_argument(
        "--no-trades",
        action="store_true",
        help="Omit per-trade lines (summary + per-symbol headline only)",
    )
    p_bt.add_argument(
        "--tail-trades",
        type=int,
        default=15,
        metavar="N",
        help="Print only the last N closed trades in the detail block (default 15). "
        "Use -1 to print every trade.",
    )
    p_bt.add_argument("--csv", action="store_true", help="Append CSV block after summary table")
    p_bt.add_argument(
        "--trades-csv",
        default=None,
        metavar="PATH",
        help="Write every closed trade to a UTF-8 CSV (overwrites). Portfolio: one file. "
        "Multi-symbol non-portfolio: one file sorted by exit date.",
    )
    p_bt.add_argument(
        "--portfolio",
        action="store_true",
        help="One shared starting_cash pool across all symbols (competing for cash). "
        "Default mode runs an isolated ₹account per symbol.",
    )
    p_bt.set_defaults(func=cmd_backtest)

    p_d = sub.add_parser("daily", help="Paper-trade step (run once per session EOD)")
    p_d.add_argument("--config", default=None)
    p_d.add_argument("--db", default="paper.db")
    p_d.set_defaults(func=cmd_daily)

    p_s = sub.add_parser("status", help="Show paper DB cash/positions/pending (JSON)")
    p_s.add_argument("--db", default="paper.db")
    p_s.set_defaults(func=cmd_status)

    p_ctx = sub.add_parser(
        "context",
        help="Human-readable paper snapshot: cash, MTM equity, positions, pendings, risk settings",
    )
    p_ctx.add_argument("--config", default=None)
    p_ctx.add_argument("--db", default="paper.db")
    p_ctx.add_argument("--json", action="store_true", help="Also print structured JSON after the text block")
    p_ctx.set_defaults(func=cmd_context)

    p_i = sub.add_parser("init-paper", help="Create/reset paper DB cash")
    p_i.add_argument("--db", default="paper.db")
    p_i.add_argument("--cash", type=float, default=20000)
    p_i.set_defaults(func=cmd_init)

    p_sc = sub.add_parser("scan", help="Last-bar scan on today's top-N mcap universe (+slope filter)")
    p_sc.add_argument("--config", default=None)
    p_sc.set_defaults(func=cmd_scan)

    p_u = sub.add_parser("universe", help="Print today's top-N symbols by NSE free-float market cap")
    p_u.add_argument("--config", default=None)
    p_u.add_argument("--top", type=int, default=0, help="Override universe_top_n from config")
    p_u.set_defaults(func=cmd_universe)

    p_ss = sub.add_parser("scan-slope", help="List tickers in today's top-N mcap universe that pass +slope")
    p_ss.add_argument("--config", default=None)
    p_ss.add_argument("--max", type=int, default=0, help="Override universe_top_n for this scan")
    p_ss.add_argument("--all", action="store_true", help="Print failures too")
    p_ss.set_defaults(func=cmd_scan_slope)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
