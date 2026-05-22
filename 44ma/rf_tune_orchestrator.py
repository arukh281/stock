#!/usr/bin/env python3
"""
Orchestrate RF tuning for SMA ladder variants: one OHLC prefetch, then sequential tunes.

Does not modify config.json. Logs to .cache/rf_tune/orchestrator.log by default.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".cache" / "rf_tune"
DEFAULT_PREFETCH = CACHE_DIR / "ohlc_prefetch.pkl"
DEFAULT_LOG = CACHE_DIR / "orchestrator.log"
TUNE_SCRIPT = ROOT / "rf_tune_portfolio.py"

# Fair comparison set: each variant RF-tuned on train years only (same scenario).
FAIR_ANTIV_VARIANTS = (
    "baseline",
    "path_floor",
    "path_floor_tol",
    "stacked_2ma",
    "stacked_3ma",
    "path_floor_stacked",
    "full_ladder",
    "ma1_gt_ma2",
)
LADDER_VARIANTS = FAIR_ANTIV_VARIANTS

# Same scenario as README baseline RF tune (40 random + 24 RF refine).
BASELINE_SCENARIO = [
    "--config",
    "config.json",
    "--start",
    "2014-01-01",
    "--end",
    "2023-12-31",
    "--train-years",
    "2014,2015,2018,2019,2022,2023",
    "--test-years",
    "2016,2017,2020,2021",
    "--objective",
    "median_yearly_return",
    "--no-tune-risk",
    "--max-risk-per-trade-inr",
    "1500",
    "--random-trials",
    "40",
    "--rf-refine-trials",
    "24",
    "--starting-cash-inr",
    "20000",
]


def _log(msg: str, log_path: Path) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run(cmd: list[str], log_path: Path, tee: Path | None = None) -> int:
    _log(f"exec: {' '.join(cmd)}", log_path)
    if tee is None:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        return subprocess.call(cmd, cwd=ROOT, env=env)
    tee.parent.mkdir(parents=True, exist_ok=True)
    with tee.open("w", encoding="utf-8") as out_f:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            out_f.write(line)
            with log_path.open("a", encoding="utf-8") as log_f:
                log_f.write(line)
        return proc.wait()


def _prefetch(python: str, cache_path: Path, log_path: Path, force: bool) -> int:
    if cache_path.is_file() and not force:
        _log(f"prefetch cache exists ({cache_path}); skip fetch (use --force-prefetch to refetch)", log_path)
        return 0
    cmd = [
        python,
        str(TUNE_SCRIPT),
        *BASELINE_SCENARIO,
        "--prefetch-only",
        "--write-prefetch-cache",
        "--prefetch-cache",
        str(cache_path),
    ]
    return _run(cmd, log_path, tee=CACHE_DIR / "prefetch.log")


def _tune_variant(
    python: str,
    variant: str,
    cache_path: Path,
    log_path: Path,
    *,
    skip_existing: bool,
) -> int:
    results = (
        CACHE_DIR / "baseline_results.json"
        if variant == "baseline"
        else CACHE_DIR / f"{variant}_results.json"
    )
    if skip_existing and results.is_file():
        _log(f"skip {variant}: {results} already exists", log_path)
        return 0
    cmd = [
        python,
        str(TUNE_SCRIPT),
        *BASELINE_SCENARIO,
        "--variant",
        variant,
        "--no-fetch",
        "--prefetch-cache",
        str(cache_path),
        "--results-out",
        str(results),
        "--json-out",
        str(
            CACHE_DIR / "config_baseline_tuned.json"
            if variant == "baseline"
            else CACHE_DIR / f"config_{variant}_tuned.json"
        ),
    ]
    return _run(cmd, log_path, tee=CACHE_DIR / f"{variant}_tune.log")


def _load_results(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_summary(
    out_path: Path,
    *,
    baseline_path: Path,
    variant_results: dict[str, Path],
) -> None:
    baseline = _load_results(baseline_path)
    rows: list[dict] = []

    def _row(name: str, data: dict | None) -> None:
        if not data:
            rows.append({"variant": name, "status": "missing"})
            return
        train = data.get("train_metrics") or {}
        test = data.get("test_metrics") or {}
        rows.append(
            {
                "variant": name,
                "best_score": data.get("best_score"),
                "best_params": data.get("best_params"),
                "train_total_pnl": train.get("total_pnl"),
                "train_median_yearly_return_pct": train.get("median_yearly_return_pct"),
                "test_total_pnl": test.get("total_pnl"),
                "test_total_return_pct": test.get("total_return_pct"),
                "test_median_yearly_return_pct": test.get("median_yearly_return_pct"),
            }
        )

    _row("baseline", baseline)
    for v, p in variant_results.items():
        _row(v, _load_results(p))

    lines = [
        "# RF tune orchestrator summary",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Scenario: 2014-01-01 .. 2023-12-31 | train 2014,2015,2018,2019,2022,2023 | "
        "test 2016,2017,2020,2021 | objective median_yearly_return | 40 random + 24 RF | "
        "₹20k cash | risk cap ₹1500 | no-tune-risk",
        "",
        "| Variant | Train score | Train PnL | Test PnL | Test return % | Test trades | Test win % | Test avg ₹/trade |",
        "|---------|-------------|-----------|----------|---------------|-------------|----------|------------------|",
    ]
    for r in rows:
        if r.get("status") == "missing":
            lines.append(f"| {r['variant']} | — | — | — | — | — | — | — |")
            continue
        vname = r["variant"]
        if vname == "baseline":
            data = baseline
        else:
            p = variant_results.get(vname)
            data = _load_results(p) if p else None
        test = (data or {}).get("test_metrics") or {}
        med = r.get("test_median_yearly_return_pct")
        med_s = f"{med:.2f}" if med is not None else "—"
        score = r.get("best_score")
        score_s = f"{score:.4f}" if score is not None else "—"
        wr = test.get("win_rate")
        wr_s = f"{100 * wr:.1f}%" if wr is not None else "—"
        lines.append(
            f"| {r['variant']} | {score_s} "
            f"| {r.get('train_total_pnl', 0):,.0f} "
            f"| {r.get('test_total_pnl', 0):,.0f} "
            f"| {r.get('test_total_return_pct', 0):.2f} "
            f"| {test.get('trades', '—')} "
            f"| {wr_s} "
            f"| {test.get('avg_pnl_per_trade', '—')} |"
        )

    if baseline:
        b_test = float((baseline.get("test_metrics") or {}).get("total_pnl") or 0)
        lines.extend(["", "## vs baseline (test total PnL delta)", ""])
        for v, p in variant_results.items():
            data = _load_results(p)
            if not data:
                lines.append(f"- **{v}**: (no results)")
                continue
            t_pnl = float((data.get("test_metrics") or {}).get("total_pnl") or 0)
            lines.append(f"- **{v}**: {t_pnl - b_test:+,.0f} INR vs baseline tuned")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote summary -> {out_path}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="RF tune orchestrator (single prefetch, sequential variants)")
    ap.add_argument("--prefetch-cache", default=str(DEFAULT_PREFETCH))
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--force-prefetch", action="store_true")
    ap.add_argument("--skip-existing", action="store_true", help="Skip variants with existing results JSON")
    ap.add_argument(
        "--variants",
        default=",".join(LADDER_VARIANTS),
        help="Comma-separated variant names",
    )
    ap.add_argument(
        "--only",
        choices=("prefetch", "tune", "all"),
        default="all",
    )
    ap.add_argument("--baseline-results", default=str(CACHE_DIR / "baseline_results.json"))
    args = ap.parse_args()

    python = sys.executable
    cache_path = Path(args.prefetch_cache).expanduser()
    log_path = Path(args.log).expanduser()
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    _log("orchestrator start", log_path)

    if args.only in ("prefetch", "all"):
        rc = _prefetch(python, cache_path, log_path, args.force_prefetch)
        if rc != 0:
            _log(f"prefetch failed exit={rc}", log_path)
            return rc

    if args.only in ("tune", "all"):
        for variant in variants:
            _log(f"=== variant {variant} ===", log_path)
            rc = _tune_variant(
                python,
                variant,
                cache_path,
                log_path,
                skip_existing=args.skip_existing,
            )
            if rc != 0:
                _log(f"variant {variant} failed exit={rc}", log_path)
                return rc

    variant_paths = {v: CACHE_DIR / f"{v}_results.json" for v in variants}
    write_summary(
        CACHE_DIR / "orchestrator_summary.md",
        baseline_path=Path(args.baseline_results),
        variant_results=variant_paths,
    )
    _log("orchestrator done", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
