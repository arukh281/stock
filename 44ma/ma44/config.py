from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Settings:
    # Universe: top N Indian equities by NSE free-float market cap (refreshed daily).
    universe_top_n: int = 100
    universe_master_pool: int = 0  # 0 = auto 2× universe_top_n for backtest prefetch
    universe_cache_dir: str = ".cache/universe"
    universe_history_start: str = "2018-01-01"
    universe_fetch_sleep_sec: float = 0.2
    universe_file: str | None = None  # optional override: one ticker per line

    sma_period: int = 44
    sma_rising_lookback: int = 5
    sma_monotone_days: int = 0
    sma_slope_min_pct: float = 0.0
    # Stacked 44 SMA ladder (MA1=now, MA2=−offset, MA3=−2×offset) — filters V-shaped recoveries.
    sma_stacked_enabled: bool = False
    sma_stacked_offset: int = 44
    sma_stacked_relax_third: bool = True
    sma_stacked_require_third: bool = True
    # No V in the latest SMA window: min(SMA last N) > SMA[N bars ago].
    sma_path_floor_days: int = 0
    sma_path_floor_tol_pct: float = 0.0
    # At most N sessions in lookback may close below the 44 SMA (Paytm-style dips).
    sma_close_below_max_days: int = 0
    sma_close_below_lookback: int = 44
    require_close_above_prev: bool = False
    touch_above_pct: float = 0.003
    touch_below_pct: float = 0.012
    entry_buffer_pct: float = 0.0005
    stop_buffer_pct: float = 0.0005
    max_initial_risk_pct: float = 0.0
    breakout_hold_days: int = 5
    risk_reward: float = 3.0
    starting_cash_inr: float = 20_000.0
    risk_per_trade_inr: float = 100.0
    commission_pct: float = 0.0
    slippage_pct: float = 0.0
    max_open_positions: int = 0  # 0 = no cap

    @classmethod
    def load(cls, path: Path | None) -> "Settings":
        base = cls()
        if path is None or not path.exists():
            return base
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        for k, v in data.items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base
