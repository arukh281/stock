"""Load sandbox/.env and repo PYTHONPATH before any algo imports."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for sub in ("", "44ma", "hybrid_swing", "KALI/src", "financially free"):
    p = str(ROOT / sub) if sub else str(ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / "sandbox" / ".env")
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

# Defaults when .env omits them
os.environ.setdefault("ANALYZE_API_KEY", "dev-secret")
os.environ.setdefault("USE_NIFTY100", "true")
