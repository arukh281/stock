"""Yahoo Finance fetch helpers with rate-limit retries."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

_RATE_HINTS = (
    "too many requests",
    "rate limit",
    "429",
    "yfratelimit",
    "unexpectedly terminated",
)


def is_rate_limit_error(exc: BaseException | None = None, text: str = "") -> bool:
    blob = f"{exc or ''} {text}".lower()
    return any(h in blob for h in _RATE_HINTS)


def call_with_rate_limit_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_sleep_sec: float = 4.0,
    label: str = "yahoo",
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_rate_limit_error(exc) or attempt >= max_attempts:
                raise
            wait = min(90.0, base_sleep_sec * (2 ** (attempt - 1)))
            print(
                f"[{label}] rate limited (attempt {attempt}/{max_attempts}); "
                f"sleep {wait:.0f}s …",
                flush=True,
            )
            time.sleep(wait)
    raise last_exc  # pragma: no cover
