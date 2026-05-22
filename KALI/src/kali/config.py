"""Load strategy configuration from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _ROOT / "config" / "default.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else _DEFAULT_CONFIG
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def project_root() -> Path:
    return _ROOT


def cache_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    d = _ROOT / cfg["data"]["cache_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d
