#!/usr/bin/env python3
"""Build and cache feature DataFrame for a symbol."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kali.backtest.run import prepare_symbol_features, save_results
from kali.config import cache_dir, load_config
from kali.data.ohlcv import download_ohlcv, load_ohlcv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="e.g. ITC or ITC.NS")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if args.download:
        download_ohlcv(args.symbol, cfg=cfg)
    else:
        load_ohlcv(args.symbol, cfg=cfg)
    df = prepare_symbol_features(args.symbol, cfg)
    out = cache_dir(cfg) / "features"
    out.mkdir(parents=True, exist_ok=True)
    sym = args.symbol.replace(".NS", "").upper()
    path = out / f"{sym}_features.parquet"
    df.to_parquet(path)
    print(f"Saved {len(df)} rows to {path}")
    save_results({"symbol": args.symbol, "features": df})


if __name__ == "__main__":
    main()
