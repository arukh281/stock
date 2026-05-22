#!/usr/bin/env python3
"""Fetch fundamentals for universe symbols."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kali.config import load_config
from kali.data.screener import ScreenerClient
from kali.data.universe import load_nifty150_symbols


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else load_nifty150_symbols(cfg)
    )
    client = ScreenerClient(cfg)
    for sym in symbols:
        try:
            snap = client.fetch(sym, force=args.force)
            status = "PASS" if snap.passes_filters(cfg) else "FAIL"
            print(f"{sym}: {status} F={snap.piotroski_f_score} ROE={snap.roe_pct}")
        except Exception as e:
            print(f"{sym}: ERROR {e}")


if __name__ == "__main__":
    main()
