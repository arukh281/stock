"""Add sibling algo directories to sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def setup_paths() -> None:
    for name in ("44ma", "KALI"):
        p = ROOT / name
        if p.exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    for ff_name in ("financially free", "financially_free"):
        ff = ROOT / ff_name
        if ff.exists() and str(ff) not in sys.path:
            sys.path.insert(0, str(ff))
            break
    kali_src = ROOT / "KALI" / "src"
    if kali_src.exists() and str(kali_src) not in sys.path:
        sys.path.insert(0, str(kali_src))
