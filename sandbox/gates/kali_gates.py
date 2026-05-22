from __future__ import annotations

from typing import Any

from sandbox.adapters._paths import setup_paths


def gate_breakdown(symbol: str) -> dict[str, Any]:
    setup_paths()
    from kali.backtest.run import prepare_symbol_features
    from kali.config import load_config
    from kali.signals.entries import (
        confluence_score_series,
        core_conditions_mask,
        hurst_confluence_mask,
        long_entry_signal,
    )

    cfg = load_config()
    sym = symbol.strip().upper()
    try:
        df = prepare_symbol_features(sym, cfg)
    except Exception as exc:
        return {"algo_id": "kali", "symbol": sym, "error": str(exc)}

    if df.empty:
        return {"algo_id": "kali", "symbol": sym, "error": "no_data"}

    i = len(df) - 1
    row = df.iloc[i]
    sig_cfg = cfg["signals"]
    confluence_min = int(sig_cfg.get("confluence_min", 2))

    regime_ok = str(row.get("regime_active", "")) in ("BULL_TREND", "SIDEWAYS")
    aligned = bool(row.get("daily_alignment", False))
    kalman_ok = float(row.get("kalman_velocity", 0)) > 0
    obv_ok = not bool(row.get("obv_divergence", False))
    core = bool(core_conditions_mask(df).iloc[i])

    conf_cms = float(row.get("cms", 0)) > sig_cfg["cms_entry_min"]
    conf_hurst = bool(hurst_confluence_mask(df).iloc[i])
    conf_vol = float(row.get("volume_z", 0)) > sig_cfg["volume_z_entry_min"]
    conf_macd = float(row.get("macd_curvature", 0)) > 0
    score = int(confluence_score_series(df, cfg).iloc[i])
    entry = bool(long_entry_signal(df, cfg).iloc[i])

    gates = [
        {
            "id": "regime",
            "label": "Regime BULL_TREND or SIDEWAYS",
            "pass": regime_ok,
            "detail": str(row.get("regime_active", "")),
        },
        {
            "id": "weekly_alignment",
            "label": "Weekly MTF gate (daily_alignment)",
            "pass": aligned,
        },
        {
            "id": "kalman_velocity",
            "label": "Kalman velocity > 0",
            "pass": kalman_ok,
            "detail": f"v={float(row.get('kalman_velocity', 0)):.4f}",
        },
        {
            "id": "obv_no_divergence",
            "label": "No OBV bearish divergence",
            "pass": obv_ok,
        },
        {"id": "core_conditions", "label": "Core conditions (all above)", "pass": core},
        {
            "id": "conf_cms",
            "label": f"CMS > {sig_cfg['cms_entry_min']}",
            "pass": conf_cms,
            "detail": f"cms={float(row.get('cms', 0)):.3f}",
        },
        {
            "id": "conf_hurst",
            "label": "Hurst confluence",
            "pass": conf_hurst,
            "detail": f"daily={row.get('hurst_regime')} weekly={row.get('weekly_hurst_regime')}",
        },
        {
            "id": "conf_volume_z",
            "label": f"Volume Z > {sig_cfg['volume_z_entry_min']}",
            "pass": conf_vol,
            "detail": f"z={float(row.get('volume_z', 0)):.2f}",
        },
        {
            "id": "conf_macd_curvature",
            "label": "MACD curvature > 0",
            "pass": conf_macd,
        },
        {
            "id": "confluence_score",
            "label": f"Confluence score ≥ {confluence_min}",
            "pass": score >= confluence_min,
            "detail": f"score={score}/{confluence_min} required",
        },
        {"id": "long_entry", "label": "Long entry signal", "pass": entry},
    ]
    failed = [g["id"] for g in gates if not g["pass"]]

    return {
        "algo_id": "kali",
        "symbol": sym,
        "asof": str(df.index[i].date()),
        "close": float(row["close"]),
        "signal": entry,
        "gates": gates,
        "failed": failed,
    }


def run_compare(*, start: str = "2015-01-01", end: str = "2024-12-31") -> dict[str, Any]:
    setup_paths()
    from kali.backtest.portfolio import run_portfolio_backtest
    from kali.config import load_config
    from kali.data.universe import load_nifty150_symbols

    cfg = load_config()
    symbols = load_nifty150_symbols(cfg)[:30]

    variants = [
        ("confluence_min=2 (default)", {}),
        ("confluence_min=3 (stricter)", {"signals": {**cfg["signals"], "confluence_min": 3}}),
    ]

    rows: list[dict[str, Any]] = []
    for name, patch in variants:
        run_cfg = cfg
        if patch:
            import copy

            run_cfg = copy.deepcopy(cfg)
            run_cfg["signals"].update(patch["signals"])

        result = run_portfolio_backtest(
            symbols=symbols,
            start=start,
            end=end,
            cfg=run_cfg,
            force_download=False,
        )
        m = result.get("metrics", {})
        rows.append(
            {
                "variant": name,
                "trades": int(m.get("num_trades", 0)),
                "win_pct": round(float(m.get("win_rate", 0)) * 100, 1),
                "sum_pnl": round(float(m.get("final_equity", 0)) - float(cfg["backtest"]["initial_capital"]), 2),
                "total_return_pct": round(float(m.get("total_return_pct", 0)), 2),
            }
        )

    return {
        "algo_id": "kali",
        "start": start,
        "end": end,
        "symbols": len(symbols),
        "variants": rows,
        "note": "30-symbol subset for faster compare",
    }
