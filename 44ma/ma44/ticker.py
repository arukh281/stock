from __future__ import annotations


def normalize_yahoo_symbol(raw: str) -> str:
    s = raw.strip().upper()
    if not s or s.startswith("#"):
        return ""
    if "." in s:
        return s
    if s.startswith("^"):
        return s
    return f"{s}.NS"
