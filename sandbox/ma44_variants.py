"""44MA sandbox variants: algo_id → config file and labels."""

from __future__ import annotations

from pathlib import Path

# Repo root (stocks/) — avoid importing sandbox.adapters (circular with ma44_adapter).
ROOT = Path(__file__).resolve().parent.parent
MA44_DIR = ROOT / "44ma"

# algo_id used in Supabase portfolio_meta and API
MA44_ALGO_IDS = ("44ma", "44ma_stacked_2ma")

VARIANTS: dict[str, dict[str, str]] = {
    "44ma": {
        "label": "44 MA Full Ladder",
        "description": "Anti-V: path floor + 3-segment SMA ladder + close buffer",
        "config_file": "config.json",
        "variant": "full_ladder",
    },
    "44ma_stacked_2ma": {
        "label": "44 MA Stacked 2MA",
        "description": "MA1 > MA2@44d — simpler rising-trend filter",
        "config_file": "config.stacked_2ma.json",
        "variant": "stacked_2ma",
    },
}


def is_ma44_algo(algo_id: str) -> bool:
    return algo_id in VARIANTS


def config_path_for(algo_id: str) -> Path:
    if algo_id not in VARIANTS:
        raise KeyError(f"Unknown 44MA algo_id: {algo_id}")
    return MA44_DIR / VARIANTS[algo_id]["config_file"]
