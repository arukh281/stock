#!/usr/bin/env python3
"""Compare two ma44 backtest trade CSV exports (e.g. old vs green-hammer filter)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in ("entry_date", "exit_date"):
        df[col] = pd.to_datetime(df[col])
    df["pnl"] = df["pnl"].astype(float)
    df["trade_key"] = (
        df["symbol"].astype(str)
        + "|"
        + df["entry_date"].dt.strftime("%Y-%m-%d")
        + "|"
        + df["entry"].astype(str)
    )
    return df


def _summary(label: str, df: pd.DataFrame) -> dict:
    wins = (df["pnl"] > 0).sum()
    n = len(df)
    return {
        "label": label,
        "trades": n,
        "wins": int(wins),
        "win_pct": round(100.0 * wins / n, 1) if n else 0.0,
        "sum_pnl": round(df["pnl"].sum(), 2),
        "avg_pnl": round(df["pnl"].mean(), 2) if n else 0.0,
        "targets": int((df["exit_reason"] == "target").sum()),
        "stops": int((df["exit_reason"] == "stop").sum()),
    }


def compare(old_path: Path, new_path: Path) -> int:
    old = _load(old_path)
    new = _load(new_path)

    old_keys = set(old["trade_key"])
    new_keys = set(new["trade_key"])
    only_old = old_keys - new_keys
    only_new = new_keys - old_keys
    both = old_keys & new_keys

    print("=" * 72)
    print("TRADE CSV COMPARISON")
    print(f"  A (baseline): {old_path}")
    print(f"  B (new):      {new_path}")
    print("=" * 72)

    for s in (_summary("A baseline", old), _summary("B new", new)):
        print(
            f"\n{s['label']}: {s['trades']} trades | win {s['wins']} ({s['win_pct']}%) | "
            f"sum_pnl ₹{s['sum_pnl']:,.2f} | avg ₹{s['avg_pnl']:.2f} | "
            f"target {s['targets']} stop {s['stops']}"
        )

    delta_trades = len(new) - len(old)
    delta_pnl = new["pnl"].sum() - old["pnl"].sum()
    print(f"\nDelta (B − A): {delta_trades:+d} trades | sum_pnl ₹{delta_pnl:+,.2f}")

    print(f"\nOverlap: {len(both)} identical trades (symbol + entry_date + entry px)")
    print(f"Only in A (dropped in B): {len(only_old)}")
    print(f"Only in B (added in B):    {len(only_new)}")

    if only_old:
        dropped = old[old["trade_key"].isin(only_old)].copy()
        print(
            f"\nDropped trades PnL: ₹{dropped['pnl'].sum():,.2f} "
            f"({(dropped['pnl'] > 0).sum()} wins / {len(dropped)} trades)"
        )
        print("Top 10 dropped by |pnl|:")
        for _, r in dropped.reindex(dropped["pnl"].abs().sort_values(ascending=False).index).head(10).iterrows():
            print(
                f"  {r['symbol']} {r['entry_date'].date()} -> {r['exit_date'].date()} "
                f"{r['exit_reason']} pnl={r['pnl']:.2f}"
            )

    if only_new:
        added = new[new["trade_key"].isin(only_new)].copy()
        print(
            f"\nAdded trades PnL: ₹{added['pnl'].sum():,.2f} "
            f"({(added['pnl'] > 0).sum()} wins / {len(added)} trades)"
        )

    # Same trade key but different outcome (rare: confidence / cash ordering)
    merged = old[old["trade_key"].isin(both)].merge(
        new[new["trade_key"].isin(both)],
        on="trade_key",
        suffixes=("_a", "_b"),
    )
    changed = merged[
        (merged["pnl_a"] != merged["pnl_b"])
        | (merged["exit_reason_a"] != merged["exit_reason_b"])
        | (merged["qty_a"] != merged["qty_b"])
    ]
    if len(changed):
        print(f"\nSame entry key but different sim outcome: {len(changed)} (portfolio cash race)")
        print(f"  PnL delta on those: ₹{(changed['pnl_b'] - changed['pnl_a']).sum():,.2f}")

    by_year_a = old.groupby(old["entry_date"].dt.year)["pnl"].agg(["count", "sum"])
    by_year_b = new.groupby(new["entry_date"].dt.year)["pnl"].agg(["count", "sum"])
    years = sorted(set(by_year_a.index) | set(by_year_b.index))
    print("\nBy entry year (trades_A → trades_B | pnl_A → pnl_B | Δpnl):")
    for y in years:
        ta = int(by_year_a.loc[y, "count"]) if y in by_year_a.index else 0
        tb = int(by_year_b.loc[y, "count"]) if y in by_year_b.index else 0
        pa = float(by_year_a.loc[y, "sum"]) if y in by_year_a.index else 0.0
        pb = float(by_year_b.loc[y, "sum"]) if y in by_year_b.index else 0.0
        print(f"  {y}: {ta}→{tb} | ₹{pa:,.0f}→₹{pb:,.0f} | Δ₹{pb - pa:+,.0f}")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("baseline", type=Path, help="Older / reference trades CSV (A)")
    ap.add_argument("new", type=Path, help="New trades CSV (B)")
    args = ap.parse_args()
    return compare(args.baseline, args.new)


if __name__ == "__main__":
    raise SystemExit(main())
