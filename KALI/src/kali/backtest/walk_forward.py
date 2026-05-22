"""Walk-forward validation scaffolding (v2 automation hook)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WalkForwardWindow:
    train_years: int
    test_years: int
    step_years: int = 1


def generate_windows(
    start_year: int, end_year: int, train_years: int = 7, test_years: int = 3
) -> list[tuple[int, int, int, int]]:
    """Return (train_start, train_end, test_start, test_end) year tuples."""
    windows = []
    test_start = start_year + train_years
    while test_start + test_years <= end_year:
        windows.append(
            (start_year, test_start - 1, test_start, test_start + test_years - 1)
        )
        start_year += 1
        test_start += 1
    return windows
