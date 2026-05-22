"""CLI: python -m sandbox.gates scan 44ma ETERNAL.NS | compare 44ma"""

from __future__ import annotations

import argparse
import json
import sys

from sandbox.gates.registry import gate_breakdown, list_algos, run_compare


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_scan(args: argparse.Namespace) -> int:
    data = gate_breakdown(args.algo, args.symbol)
    if args.json:
        _print_json(data)
        return 0 if "error" not in data else 1

    if "error" in data:
        print(f"Error: {data['error']}")
        return 1

    print(f"=== {data['algo_id']} gate scan: {data['symbol']} @ {data.get('asof', '?')} ===")
    print(f"Signal: {'YES' if data.get('signal') else 'NO'}")
    if data.get("confidence") is not None:
        print(f"Confidence: {data['confidence']}")
    if data.get("close") is not None:
        print(f"Close: {data['close']}")
    print()
    for g in data.get("gates", []):
        mark = "PASS" if g.get("pass") else "FAIL"
        detail = f"  ({g['detail']})" if g.get("detail") else ""
        print(f"  [{mark}] {g.get('label', g.get('id'))}{detail}")
    if data.get("failed"):
        print(f"\nFailed gates: {', '.join(data['failed'])}")
    if data.get("note"):
        print(f"\nNote: {data['note']}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    data = run_compare(args.algo, start=args.start, end=args.end or None)
    if args.json:
        _print_json(data)
        return 0

    if "error" in data:
        print(f"Error: {data['error']}")
        return 1

    print(f"=== {data['algo_id']} backtest gate compare ===")
    if data.get("note"):
        print(data["note"])
    print(f"{'variant':<28} {'trades':>7} {'win%':>7} {'sum_pnl':>14}")
    for row in data.get("variants", []):
        sp = row.get("sum_pnl", row.get("total_return_pct", 0))
        print(
            f"{row['variant']:<28} {row['trades']:>7} {row.get('win_pct', 0):>6.1f}% "
            f"{sp:>14}"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Signal gate diagnostics for sandbox algos")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Per-symbol gate breakdown")
    p_scan.add_argument("algo", choices=list_algos())
    p_scan.add_argument("symbol", help="Yahoo symbol e.g. ETERNAL.NS")
    p_scan.add_argument("--json", action="store_true")

    p_cmp = sub.add_parser("compare", help="Backtest variants (filter ablation)")
    p_cmp.add_argument("algo", choices=list_algos())
    p_cmp.add_argument("--start", default="2018-01-01")
    p_cmp.add_argument("--end", default=None, help="End date (FF/KALI only)")
    p_cmp.add_argument("--json", action="store_true")

    args = ap.parse_args()
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "compare":
        return cmd_compare(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
