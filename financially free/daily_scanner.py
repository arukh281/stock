"""
Daily post-close scanner for Nifty Smallcap 250 VCP setups (paper trading).

Run after market close (e.g. 4:00 PM IST):
    python daily_scanner.py

Outputs buy candidates (VCP + Stage 2 + RS top 20%) and 21-EMA exit alerts
for the next session, with gap-up and ADV guardrails.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from tabulate import tabulate

from nifty_smallcap_history import (
    SMALLCAP_INDEX_TICKER,
    current_smallcap_tickers,
)
from swing_trading_algo import _flatten_yfinance_columns

warnings.filterwarnings("ignore")

CHUNK_SIZE = 50


class DailyScanner:
    def __init__(
        self,
        index_ticker: str = SMALLCAP_INDEX_TICKER,
        roc_lookback: int = 20,
        max_roc: float = 75.0,
        rs_top_pct: float = 0.2,
    ):
        self.index_ticker = index_ticker
        self.roc_lookback = roc_lookback
        self.max_roc = max_roc
        self.rs_top_pct = rs_top_pct

        self.max_adv_allocation = 0.01
        self.max_chase_pct = 0.02

    def get_macro_regime(self) -> tuple[bool, float]:
        """True when Smallcap index 20-month ROC is below max_roc (buy gate open)."""
        print(f"Checking Macro Regime ({self.index_ticker})...")
        end_date = datetime.today()
        start_date = end_date - timedelta(days=1000)

        idx_data = yf.download(
            self.index_ticker,
            start=start_date,
            end=end_date,
            progress=False,
        )
        idx_data = _flatten_yfinance_columns(idx_data)
        if idx_data.empty or len(idx_data) < self.roc_lookback + 2:
            print("Error: Could not fetch enough macro index data.")
            return False, 0.0

        monthly = idx_data["Close"].resample("ME").last().to_frame()
        monthly["ROC"] = monthly["Close"].pct_change(periods=self.roc_lookback) * 100
        current_roc = float(monthly["ROC"].iloc[-1])

        if np.isnan(current_roc):
            print("Error: Macro ROC is NaN (index history too short).")
            return False, 0.0

        is_safe = current_roc < self.max_roc
        status = "SAFE (Buy Gate Open)" if is_safe else "DANGER (Buy Gate Closed)"
        print(
            f"Current {self.roc_lookback}-Month ROC: {current_roc:.2f}% | Status: {status}"
        )
        return is_safe, current_roc

    def calculate_technical_state(self, df: pd.DataFrame) -> pd.Series | None:
        if len(df) < 252:
            return None

        df = df.copy()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df["SMA_150"] = df["Close"].rolling(window=150).mean()
        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["High_52W"] = df["High"].rolling(window=252).max()
        df["Low_52W"] = df["Low"].rolling(window=252).min()

        df["Stage_2"] = (
            (df["Close"] > df["SMA_150"])
            & (df["Close"] > df["SMA_200"])
            & (df["SMA_150"] > df["SMA_200"])
            & (df["SMA_50"] > df["SMA_150"])
            & (df["SMA_50"] > df["SMA_200"])
            & (df["SMA_200"] > df["SMA_200"].shift(20))
            & (df["Close"] >= (df["High_52W"] * 0.75))
            & (df["Close"] >= (df["Low_52W"] * 1.30))
        )

        df["Vol_40D"] = df["Close"].rolling(window=40).std()
        df["Vol_20D"] = df["Close"].rolling(window=20).std()
        df["Vol_10D"] = df["Close"].rolling(window=10).std()
        df["Is_Contracting"] = (df["Vol_10D"] < df["Vol_20D"]) & (
            df["Vol_20D"] < df["Vol_40D"]
        )

        df["Resistance_20D"] = df["High"].rolling(window=20).max().shift(1)
        df["Vol_Avg_50D"] = df["Volume"].rolling(window=50).mean().shift(1)
        df["High_Volume"] = df["Volume"] > (df["Vol_Avg_50D"] * 1.2)

        df["Buy_Signal"] = (
            df["Is_Contracting"].shift(1)
            & (df["Close"] > df["Resistance_20D"])
            & df["High_Volume"]
            & df["Stage_2"].shift(1)
        )

        df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
        df["Exit_Signal"] = df["Close"] < df["EMA_21"]

        df["RS_Blend"] = (0.4 * df["Close"].pct_change(126)) + (
            0.6 * df["Close"].pct_change(252)
        )
        df["ADV_INR"] = (df["Close"] * df["Volume"]).rolling(window=20).mean()

        return df.iloc[-1]

    def _parse_chunk(self, raw: pd.DataFrame, tickers: list[str]) -> list[dict]:
        rows = []
        single = len(tickers) == 1
        for ticker in tickers:
            try:
                if single:
                    df = _flatten_yfinance_columns(raw.copy())
                else:
                    if not isinstance(raw.columns, pd.MultiIndex):
                        continue
                    if ticker not in raw.columns.get_level_values(0):
                        continue
                    df = raw[ticker].dropna(how="all")
                if df.empty:
                    continue

                state = self.calculate_technical_state(df)
                if state is None:
                    continue

                rows.append(
                    {
                        "Ticker": ticker,
                        "Close": float(state["Close"]),
                        "Breakout_Pivot": float(state["Resistance_20D"]),
                        "Buy_Signal": bool(state["Buy_Signal"]),
                        "Exit_Signal": bool(state["Exit_Signal"]),
                        "RS_Blend": float(state["RS_Blend"])
                        if pd.notna(state["RS_Blend"])
                        else np.nan,
                        "ADV_INR": float(state["ADV_INR"])
                        if pd.notna(state["ADV_INR"])
                        else np.nan,
                        "Stop_Loss": float(state["EMA_21"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return rows

    def scan_universe(self, tickers: list[str]) -> pd.DataFrame:
        print(f"Scanning {len(tickers)} tickers...")
        end_date = datetime.today()
        start_date = end_date - timedelta(days=400)

        all_rows: list[dict] = []
        chunks = [
            tickers[i : i + CHUNK_SIZE]
            for i in range(0, len(tickers), CHUNK_SIZE)
        ]
        for n, chunk in enumerate(chunks, start=1):
            print(f"  Batch {n}/{len(chunks)} ({len(chunk)} tickers)...", flush=True)
            raw = yf.download(
                chunk,
                start=start_date,
                end=end_date,
                progress=False,
                group_by="ticker",
                threads=True,
            )
            all_rows.extend(self._parse_chunk(raw, chunk))

        print(f"Loaded technical state for {len(all_rows)} / {len(tickers)} tickers.")
        return pd.DataFrame(all_rows)


def _print_action_list(
    scanner: DailyScanner,
    results_df: pd.DataFrame,
    is_safe_to_buy: bool,
    current_roc: float,
) -> None:
    rs_threshold = results_df["RS_Blend"].quantile(1 - scanner.rs_top_pct)

    buy_candidates = results_df[
        (results_df["Buy_Signal"])
        & (results_df["RS_Blend"] >= rs_threshold)
    ].sort_values(by="RS_Blend", ascending=False)

    exit_candidates = results_df[results_df["Exit_Signal"]]

    print("\n" + "=" * 60)
    print(" ACTION LIST FOR TOMORROW OPEN")
    print("=" * 60)

    if is_safe_to_buy and not buy_candidates.empty:
        print("\nNEW BUY SETUPS (Ranked by RS):")
        display_buys = []
        for _, row in buy_candidates.iterrows():
            max_buy_price = row["Close"] * (1 + scanner.max_chase_pct)
            max_pos_size = row["ADV_INR"] * scanner.max_adv_allocation
            display_buys.append(
                [
                    row["Ticker"],
                    f"₹{row['Close']:.2f}",
                    f"₹{row['Breakout_Pivot']:.2f}",
                    f"₹{max_buy_price:.2f}",
                    f"₹{row['Stop_Loss']:.2f}",
                    f"₹{max_pos_size:,.0f}",
                ]
            )
        headers = [
            "Ticker",
            "Close",
            "Pivot",
            "Cancel if Opens >",
            "Init. Stop (21 EMA)",
            "Max Pos Size (1% ADV)",
        ]
        print(tabulate(display_buys, headers=headers, tablefmt="grid"))
    elif not is_safe_to_buy:
        print(
            f"\nNO BUYS: Macro filter closed "
            f"(ROC {current_roc:.2f}% >= {scanner.max_roc}%)."
        )
    else:
        print("\nNo VCP buy setups met all criteria today.")

    print("\nEXIT ALERTS (Sell at open if you currently hold):")
    if not exit_candidates.empty:
        display_exits = [
            [row["Ticker"], f"₹{row['Close']:.2f}"]
            for _, row in exit_candidates.iterrows()
        ]
        print(tabulate(display_exits, headers=["Ticker", "Close"], tablefmt="grid"))
    else:
        print("No holdings triggered 21-EMA exit today.")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    scanner = DailyScanner(
        index_ticker=SMALLCAP_INDEX_TICKER,
        roc_lookback=20,
        max_roc=75.0,
    )

    is_safe_to_buy, current_roc = scanner.get_macro_regime()

    print("\nLoading Universe...")
    tickers = current_smallcap_tickers()
    print(f"Universe: {len(tickers)} Smallcap 250 names")

    results_df = scanner.scan_universe(tickers)

    if results_df.empty:
        print("Error: Scan returned empty dataframe.")
    else:
        _print_action_list(scanner, results_df, is_safe_to_buy, current_roc)

        stamp = datetime.today().strftime("%Y%m%d")
        out_path = f"scan_results_{stamp}.csv"
        results_df.to_csv(out_path, index=False)
        print(f"\nFull scan saved to {out_path}")
