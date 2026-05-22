"""Appendix A4 — zero look-ahead verification."""

from __future__ import annotations

import pandas as pd


def count_lookahead_violations(clean_daily_df: pd.DataFrame, weekly_df: pd.DataFrame) -> int:
    """Return number of look-ahead violations (0 = pass)."""
    failures = 0
    for current_date, row in clean_daily_df.iterrows():
        daily_state = row.get("daily_alignment") or row.get("Weekly_Aligned")
        valid_weekly = weekly_df[weekly_df.index < current_date]
        if valid_weekly.empty:
            continue
        if "W_UPTREND_lagged" not in valid_weekly.columns:
            continue
        expected_state = valid_weekly["W_UPTREND_lagged"].iloc[-1]
        if pd.notna(daily_state) and pd.notna(expected_state):
            if bool(daily_state) != bool(expected_state):
                failures += 1
    return failures


def assert_no_lookahead(clean_daily_df: pd.DataFrame, weekly_df: pd.DataFrame) -> None:
    failures = count_lookahead_violations(clean_daily_df, weekly_df)
    assert failures == 0, f"FAILED: {failures} look-ahead violations detected."
