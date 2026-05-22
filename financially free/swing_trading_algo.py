import pandas as pd
import numpy as np
import yfinance as yf

from nifty50_history import (
    all_yf_tickers_between as nifty50_all_yf_tickers_between,
    get_yf_constituents as nifty50_get_yf_constituents,
    yearly_universe_report as nifty50_yearly_universe_report,
)
from nifty_midcap_history import (
    all_yf_tickers_between as midcap_all_yf_tickers_between,
    get_yf_constituents as midcap_get_yf_constituents,
    yearly_universe_report as midcap_yearly_universe_report,
)

UNIVERSE_NIFTY50 = "nifty50"
UNIVERSE_MIDCAP150 = "midcap150"

_INDEX_DEFAULTS = {
    UNIVERSE_NIFTY50: {
        "index_ticker": "^NSEI",
        "lookback_roc": 18,
        "default_max_roc": 45,
    },
    UNIVERSE_MIDCAP150: {
        "index_ticker": "NIFTYMIDCAP150.NS",
        "lookback_roc": 20,
        "default_max_roc": 100,
    },
}


def _universe_helpers(universe: str):
    if universe == UNIVERSE_MIDCAP150:
        return midcap_all_yf_tickers_between, midcap_get_yf_constituents, midcap_yearly_universe_report
    return nifty50_all_yf_tickers_between, nifty50_get_yf_constituents, nifty50_yearly_universe_report

# Today's Nifty 50 only — do NOT use for historical backtests (survivorship bias).
NIFTY_50_TICKERS = [
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "APOLLOHOSP.NS",
    "ASIANPAINT.NS",
    "AXISBANK.NS",
    "BAJAJ-AUTO.NS",
    "BAJFINANCE.NS",
    "BAJAJFINSV.NS",
    "BHARTIARTL.NS",
    "BPCL.NS",
    "BRITANNIA.NS",
    "CIPLA.NS",
    "COALINDIA.NS",
    "DIVISLAB.NS",
    "DRREDDY.NS",
    "EICHERMOT.NS",
    "GRASIM.NS",
    "HCLTECH.NS",
    "HDFCBANK.NS",
    "HDFCLIFE.NS",
    "HEROMOTOCO.NS",
    "HINDALCO.NS",
    "HINDUNILVR.NS",
    "ICICIBANK.NS",
    "ITC.NS",
    "INDUSINDBK.NS",
    "INFY.NS",
    "JSWSTEEL.NS",
    "KOTAKBANK.NS",
    "LT.NS",
    "M&M.NS",
    "MARUTI.NS",
    "NTPC.NS",
    "NESTLEIND.NS",
    "ONGC.NS",
    "POWERGRID.NS",
    "RELIANCE.NS",
    "SBILIFE.NS",
    "SBIN.NS",
    "SHRIRAMFIN.NS",
    "SUNPHARMA.NS",
    "TCS.NS",
    "TATACONSUM.NS",
    "TATAMOTORS.NS",
    "TATASTEEL.NS",
    "TECHM.NS",
    "TITAN.NS",
    "TRENT.NS",
    "ULTRACEMCO.NS",
    "WIPRO.NS",
]


def _flatten_yfinance_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output (handles single- and multi-ticker downloads)."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def _normalize_ohlc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Naive daily index (avoids tz-aware vs naive comparison errors)."""
    if df.empty:
        return df
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    return out.sort_index()


class SwingTradingAlgo:
    def __init__(self, index_ticker="^NSEI", lookback_roc=18):
        """
        Initialize the algorithm parameters.

        index_ticker: The macro index used for the ROC filter (e.g., Nifty 50).
        lookback_roc: 18 months for Nifty 50, 20 months for Smallcap.
        """
        self.index_ticker = index_ticker
        self.lookback_roc = lookback_roc

    def _warmup_start(self, backtest_start):
        """Extra history for 18M ROC and ~50-day rolling indicators."""
        start = pd.Timestamp(backtest_start)
        return (start - pd.DateOffset(months=self.lookback_roc + 3)).strftime(
            "%Y-%m-%d"
        )

    @staticmethod
    def _prev_trading_day(df, date):
        loc = df.index.get_indexer([date], method="pad")[0]
        if loc < 1:
            return None
        return df.index[loc - 1]

    def calculate_macro_roc(self, start_date, end_date):
        """
        Algorithm 1: 18-Month Rate of Change (Macro Filter)

        Calculates the monthly ROC of the index to determine if we are in a buy/sell regime.
        """
        print(f"Fetching macro index data for {self.index_ticker}...", flush=True)
        idx_data = yf.download(
            self.index_ticker, start=start_date, end=end_date, progress=False
        )
        idx_data = _normalize_ohlc_index(_flatten_yfinance_columns(idx_data))
        idx_data["Index_200DMA"] = idx_data["Close"].rolling(window=200).mean()
        idx_data["Index_Above_200DMA"] = (
            idx_data["Close"] > idx_data["Index_200DMA"]
        )

        monthly_data = idx_data["Close"].resample("ME").last().to_frame()
        monthly_data["ROC_18M"] = (
            monthly_data["Close"].pct_change(periods=self.lookback_roc) * 100
        )

        daily_roc = monthly_data[["ROC_18M"]].resample("D").ffill()
        daily_regime = idx_data[["Index_Above_200DMA"]].resample("D").ffill()
        return daily_roc.join(daily_regime, how="left").ffill()

    def calculate_vcp_and_emas(self, df):
        """
        Algorithm 2: Volatility Contraction Pattern (VCP), Stage 2 trend template, 21-EMA.

        Identifies tightening price action, Minervini Stage 2 uptrend, breakout signals,
        and calculates exits.
        """
        df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()

        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df["SMA_150"] = df["Close"].rolling(window=150).mean()
        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["High_52W"] = df["High"].rolling(window=252).max()
        df["Low_52W"] = df["Low"].rolling(window=252).min()

        df["Stage_2_Uptrend"] = (
            (df["Close"] > df["SMA_150"])
            & (df["Close"] > df["SMA_200"])
            & (df["SMA_150"] > df["SMA_200"])
            & (df["SMA_50"] > df["SMA_150"])
            & (df["SMA_50"] > df["SMA_200"])
            & (df["SMA_200"] > df["SMA_200"].shift(20))
            & (df["Close"] >= df["High_52W"] * 0.75)
            & (df["Close"] >= df["Low_52W"] * 1.30)
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
        # Precompute multiple stop windows for optimization.
        df["Stop_Level_10"] = df["Low"].rolling(window=10).min().shift(1)
        df["Stop_Level_20"] = df["Low"].rolling(window=20).min().shift(1)
        df["Stop_Level_30"] = df["Low"].rolling(window=30).min().shift(1)
        df["Volume_Ratio"] = df["Volume"] / df["Vol_Avg_50D"]

        df["Return_6M"] = df["Close"].pct_change(periods=126)
        df["Return_12M"] = df["Close"].pct_change(periods=252)
        df["RS_Blend"] = 0.4 * df["Return_6M"] + 0.6 * df["Return_12M"]

        df["VCP_Breakout"] = (
            df["Is_Contracting"].shift(1)
            & (df["Close"] > df["Resistance_20D"])
            & df["High_Volume"]
            & df["Stage_2_Uptrend"].shift(1)
        )

        df["Below_21_EMA"] = df["Close"] < df["EMA_21"]
        # Exit variants for parameter search.
        df["Exit_1"] = df["Below_21_EMA"]
        df["Exit_2"] = df["Below_21_EMA"] & df["Below_21_EMA"].shift(1)
        df["Exit_3"] = (
            df["Below_21_EMA"]
            & df["Below_21_EMA"].shift(1)
            & df["Below_21_EMA"].shift(2)
        )
        df["Exit_Signal"] = df["Exit_2"]

        return df

    def _entry_signal(
        self,
        row,
        max_roc=45,
        min_volume_ratio=1.0,
        require_index_trend=False,
    ):
        if pd.isna(row["ROC_18M"]) or row["ROC_18M"] >= max_roc:
            return False
        if require_index_trend:
            if pd.isna(row.get("Index_Above_200DMA")) or not bool(
                row.get("Index_Above_200DMA")
            ):
                return False
        vol_ratio = row.get("Volume_Ratio", np.nan)
        if pd.isna(vol_ratio) or vol_ratio < min_volume_ratio:
            return False
        return bool(row["VCP_Breakout"])

    def prepare_universe(self, tickers, backtest_start, end_date):
        """
        Download and compute signals for many tickers (macro fetched once).
        Returns dict[ticker] -> OHLCV + signal columns (includes warmup rows).
        """
        data_start = self._warmup_start(backtest_start)
        macro_roc = self.calculate_macro_roc(data_start, end_date)

        tickers = list(dict.fromkeys(tickers))
        print(f"Downloading {len(tickers)} stocks ({data_start} → {end_date})...", flush=True)
        raw = yf.download(
            tickers,
            start=data_start,
            end=end_date,
            group_by="ticker",
            progress=len(tickers) > 20,
            threads=True,
        )

        universe = {}
        single = len(tickers) == 1
        for ticker in tickers:
            try:
                if single:
                    df = _flatten_yfinance_columns(raw.copy())
                else:
                    if ticker not in raw.columns.get_level_values(0):
                        continue
                    df = raw[ticker].dropna(how="all").copy()
                if df.empty or len(df) < 270:
                    continue
                df = _normalize_ohlc_index(df)
                df = df.join(macro_roc, how="left")
                df = self.calculate_vcp_and_emas(df)
                universe[ticker] = df
            except (KeyError, TypeError):
                continue

        print(f"Loaded {len(universe)} / {len(tickers)} tickers.", flush=True)
        return universe

    def benchmark_buy_hold(
        self, start_date, end_date, initial_capital=1_000_000.0
    ):
        """Nifty buy-and-hold over the same window (enter first open, exit last close)."""
        data_start = self._warmup_start(start_date)
        raw = yf.download(
            self.index_ticker,
            start=data_start,
            end=end_date,
            progress=False,
        )
        idx = _flatten_yfinance_columns(raw)
        idx = idx.loc[start_date:end_date]
        if idx.empty:
            return None

        entry = idx["Open"].iloc[0]
        exit_px = idx["Close"].iloc[-1]
        final = initial_capital * (exit_px / entry)
        total_return = (final / initial_capital - 1) * 100
        return {
            "benchmark": self.index_ticker,
            "final_equity": round(final, 2),
            "total_return_pct": round(total_return, 2),
        }

    def scan_stock(self, ticker, start_date, end_date):
        """
        Combines macro and micro algorithms to run a historical scan on a specific stock.

        Returns (full_dataframe, valid_buy_events) or None if no stock data.
        """
        macro_roc = self.calculate_macro_roc(start_date, end_date)

        print(f"Fetching stock data for {ticker}...")
        df = yf.download(ticker, start=start_date, end=end_date, progress=False)
        df = _flatten_yfinance_columns(df)

        if df.empty:
            return None

        df = df.join(macro_roc, how="left")
        df = self.calculate_vcp_and_emas(df)

        buy_signals = df[df["VCP_Breakout"]].copy()
        valid_buys = buy_signals[buy_signals["ROC_18M"] < 45]

        return df, valid_buys

    def backtest_stock(
        self,
        ticker,
        start_date,
        end_date,
        initial_capital=100_000.0,
        commission_pct=0.001,
        enter_next_open=True,
        exit_next_open=True,
    ):
        """
        Simulate one position at a time: enter on valid VCP + macro filter,
        exit on two consecutive closes below 21-EMA.

        Returns dict with trades DataFrame and summary metrics.
        """
        result = self.scan_stock(ticker, start_date, end_date)
        if result is None:
            return None

        df, _ = result
        df = df.loc[start_date:end_date].copy()

        entries = df["VCP_Breakout"] & (df["ROC_18M"] < 45)
        exits = df["Exit_Signal"]

        capital = initial_capital
        shares = 0.0
        in_position = False
        entry_price = None
        entry_date = None
        trades = []

        for i, (date, row) in enumerate(df.iterrows()):
            if i == 0:
                continue
            prev_date = df.index[i - 1]
            price = row["Open"] if enter_next_open or exit_next_open else row["Close"]

            if not in_position and entries.loc[prev_date]:
                # Signal fired yesterday; enter today at open (or same-day close).
                exec_price = row["Open"] if enter_next_open else df.loc[prev_date, "Close"]
                shares = (capital * (1 - commission_pct)) / exec_price
                capital = 0.0
                in_position = True
                entry_price = exec_price
                entry_date = date

            elif in_position and exits.loc[prev_date]:
                exec_price = row["Open"] if exit_next_open else df.loc[prev_date, "Close"]
                capital = shares * exec_price * (1 - commission_pct)
                ret_pct = (exec_price / entry_price - 1) * 100
                trades.append(
                    {
                        "entry_date": entry_date,
                        "exit_date": date,
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(exec_price, 2),
                        "return_pct": round(ret_pct, 2),
                        "days_held": (date - entry_date).days,
                    }
                )
                shares = 0.0
                in_position = False
                entry_price = None
                entry_date = None

        # Mark open position to last close
        if in_position:
            last_close = df["Close"].iloc[-1]
            capital = shares * last_close
            ret_pct = (last_close / entry_price - 1) * 100
            trades.append(
                {
                    "entry_date": entry_date,
                    "exit_date": df.index[-1],
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(last_close, 2),
                    "return_pct": round(ret_pct, 2),
                    "days_held": (df.index[-1] - entry_date).days,
                    "still_open": True,
                }
            )

        trades_df = pd.DataFrame(trades)
        final_equity = capital if not in_position else shares * df["Close"].iloc[-1]
        total_return = (final_equity / initial_capital - 1) * 100

        if not trades_df.empty and "still_open" in trades_df.columns:
            closed = trades_df[trades_df["still_open"].fillna(False) != True]
        else:
            closed = trades_df
        wins = (closed["return_pct"] > 0).sum() if not closed.empty else 0
        n_closed = len(closed)

        summary = {
            "ticker": ticker,
            "start": start_date,
            "end": end_date,
            "initial_capital": initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return, 2),
            "num_trades": len(trades_df),
            "num_closed": n_closed,
            "win_rate_pct": round(100 * wins / n_closed, 1) if n_closed else None,
            "avg_return_pct": round(closed["return_pct"].mean(), 2) if n_closed else None,
            "avg_days_held": round(closed["days_held"].mean(), 1) if n_closed else None,
        }
        return {"trades": trades_df, "summary": summary}

    def backtest_portfolio(
        self,
        tickers=None,
        start_date="2018-01-01",
        end_date="2026-05-01",
        initial_capital=1_000_000.0,
        max_positions=10,
        commission_pct=0.001,
        use_historical_universe=True,
        use_stop_loss=True,
        rank_by_volume=False,
        rank_by_rs=True,
        rs_top_pct=0.20,
        include_benchmark=True,
        max_roc=45,
        min_volume_ratio=1.0,
        exit_confirm_days=2,
        stop_lookback=20,
        cooldown_days=0,
        require_index_trend=True,
        prepared_universe=None,
        universe=UNIVERSE_NIFTY50,
    ):
        """
        Shared-cash portfolio across many stocks.

        - One pool of cash; up to max_positions at once.
        - Each new position targets initial_capital / max_positions.
        - Enter on valid VCP + macro.
        - Exit on configurable closes below 21-EMA (`exit_confirm_days`).
        - use_stop_loss: exit if close falls below 20-day base low at entry.
        - rank_by_rs: prefer highest blended RS (0.4×6M + 0.6×12M return) when slots limited.
        - rank_by_volume: legacy; used only when rank_by_rs is False.
        - rs_top_pct: stock must be in top this fraction of index RS on signal day (e.g. 0.20).
        - require_index_trend: only enter when ^NSEI is above 200-DMA.
        - include_benchmark: add Nifty buy-and-hold comparison to summary.
        - Fills at next-day open; exits processed before entries each day.
        - use_historical_universe: only trade index members for that date
          (point-in-time membership from universe history module).
        - universe: "nifty50" or "midcap150".
        """
        all_tickers_fn, get_constituents_fn, _report_fn = _universe_helpers(universe)
        if tickers is None:
            if use_historical_universe:
                tickers = all_tickers_fn(start_date, end_date)
            else:
                tickers = NIFTY_50_TICKERS

        universe = (
            prepared_universe
            if prepared_universe is not None
            else self.prepare_universe(tickers, start_date, end_date)
        )
        if not universe:
            return None

        bt_start = pd.Timestamp(start_date)
        bt_end = pd.Timestamp(end_date)
        all_dates = sorted(
            {
                d
                for df in universe.values()
                for d in df.index
                if bt_start <= d <= bt_end
            }
        )
        if len(all_dates) < 2:
            return None

        cash = float(initial_capital)
        positions = {}
        trades = []
        equity_curve = []
        slot_size = initial_capital / max(1, int(max_positions))

        def portfolio_value(on_date):
            total = cash
            for sym, pos in positions.items():
                df = universe[sym]
                if on_date not in df.index:
                    continue
                total += pos["shares"] * df.loc[on_date, "Close"]
            return total

        membership_cache = {}
        last_exit_date = {}
        exit_col = f"Exit_{int(exit_confirm_days)}"
        if exit_col not in next(iter(universe.values())).columns:
            exit_col = "Exit_2"
        stop_col = f"Stop_Level_{int(stop_lookback)}"
        if stop_col not in next(iter(universe.values())).columns:
            stop_col = "Stop_Level_20"

        def allowed_tickers(on_date):
            key = pd.Timestamp(on_date).normalize()
            if key not in membership_cache:
                membership_cache[key] = set(get_constituents_fn(on_date))
            return membership_cache[key]

        for i, date in enumerate(all_dates[1:], start=1):
            prev_calendar = all_dates[i - 1]
            index_members_today = allowed_tickers(date)
            index_members_prev = allowed_tickers(prev_calendar)

            # --- exits (strategy exit or dropped from Nifty 50) ---
            for sym in list(positions.keys()):
                df = universe[sym]
                prev = self._prev_trading_day(df, date)
                if prev is None or date not in df.index:
                    continue

                pos = positions[sym]
                exit_signal = bool(df.loc[prev, exit_col])
                dropped_from_index = sym not in index_members_today
                stop_price = pos.get("stop_price")
                stop_hit = (
                    use_stop_loss
                    and stop_price is not None
                    and not pd.isna(stop_price)
                    and df.loc[prev, "Close"] < stop_price
                )
                if not exit_signal and not dropped_from_index and not stop_hit:
                    continue

                pos = positions.pop(sym)
                exec_price = df.loc[date, "Open"]
                proceeds = pos["shares"] * exec_price * (1 - commission_pct)
                cash += proceeds
                ret_pct = (exec_price / pos["entry_price"] - 1) * 100
                if stop_hit:
                    reason = "stop_loss"
                elif dropped_from_index and not exit_signal:
                    reason = "index_removal"
                else:
                    reason = "signal"
                trades.append(
                    {
                        "ticker": sym,
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "entry_price": round(pos["entry_price"], 2),
                        "exit_price": round(exec_price, 2),
                        "return_pct": round(ret_pct, 2),
                        "days_held": (date - pos["entry_date"]).days,
                        "exit_reason": reason,
                    }
                )
                last_exit_date[sym] = date

            # --- entries (only if in Nifty 50 on signal day) ---
            slots = max_positions - len(positions)
            if slots > 0 and cash > slot_size * 0.1:
                rs_pool = []
                if rank_by_rs and rs_top_pct > 0:
                    for sym, df in universe.items():
                        if use_historical_universe and sym not in index_members_prev:
                            continue
                        prev_rs = self._prev_trading_day(df, date)
                        if prev_rs is None:
                            continue
                        rs_val = df.loc[prev_rs, "RS_Blend"]
                        if not pd.isna(rs_val):
                            rs_pool.append(float(rs_val))
                rs_cutoff = (
                    float(np.quantile(rs_pool, 1 - rs_top_pct))
                    if rs_pool and rs_top_pct > 0
                    else None
                )

                candidates = []
                for sym, df in universe.items():
                    if sym in positions:
                        continue
                    if (
                        cooldown_days > 0
                        and sym in last_exit_date
                        and (date - last_exit_date[sym]).days <= cooldown_days
                    ):
                        continue
                    if use_historical_universe and sym not in index_members_prev:
                        continue
                    prev = self._prev_trading_day(df, date)
                    if prev is None or date not in df.index:
                        continue
                    row = df.loc[prev]
                    if self._entry_signal(
                        row,
                        max_roc=max_roc,
                        min_volume_ratio=min_volume_ratio,
                        require_index_trend=require_index_trend,
                    ):
                        rs_score = row.get("RS_Blend", np.nan)
                        if rank_by_rs:
                            if pd.isna(rs_score):
                                continue
                            if rs_cutoff is not None and float(rs_score) < rs_cutoff:
                                continue
                            rank_key = float(rs_score)
                        else:
                            vol_ratio = row["Volume_Ratio"]
                            rank_key = float(vol_ratio) if not pd.isna(vol_ratio) else 0.0
                        candidates.append((sym, rank_key, prev))

                if rank_by_rs or rank_by_volume:
                    candidates.sort(key=lambda x: x[1], reverse=True)
                else:
                    candidates.sort(key=lambda x: x[0])
                for sym, _vol, prev_for_sym in candidates[:slots]:
                    df = universe[sym]
                    invest = min(cash, slot_size)
                    if invest < 1000:
                        break
                    exec_price = df.loc[date, "Open"]
                    if pd.isna(exec_price) or exec_price <= 0:
                        continue
                    shares = invest * (1 - commission_pct) / exec_price
                    cash -= invest
                    stop = (
                        float(df.loc[prev_for_sym, stop_col])
                        if use_stop_loss
                        and not pd.isna(df.loc[prev_for_sym, stop_col])
                        else None
                    )
                    positions[sym] = {
                        "shares": shares,
                        "entry_price": exec_price,
                        "entry_date": date,
                        "stop_price": stop,
                    }
                    trades.append(
                        {
                            "ticker": sym,
                            "entry_date": date,
                            "exit_date": pd.NaT,
                            "entry_price": round(exec_price, 2),
                            "exit_price": np.nan,
                            "return_pct": np.nan,
                            "days_held": np.nan,
                            "action": "BUY",
                        }
                    )

            equity_curve.append(
                {"date": date, "equity": round(portfolio_value(date), 2)}
            )

        last_date = all_dates[-1]
        for sym, pos in list(positions.items()):
            df = universe[sym]
            last_close = df.loc[last_date, "Close"]
            ret_pct = (last_close / pos["entry_price"] - 1) * 100
            trades.append(
                {
                    "ticker": sym,
                    "entry_date": pos["entry_date"],
                    "exit_date": last_date,
                    "entry_price": round(pos["entry_price"], 2),
                    "exit_price": round(last_close, 2),
                    "return_pct": round(ret_pct, 2),
                    "days_held": (last_date - pos["entry_date"]).days,
                    "still_open": True,
                }
            )

        trades_df = pd.DataFrame(trades)
        closed = trades_df[trades_df.get("action") != "BUY"].copy()
        if "still_open" in closed.columns:
            closed = closed[closed["still_open"].fillna(False) != True]

        final_equity = portfolio_value(last_date)
        total_return = (final_equity / initial_capital - 1) * 100

        eq = pd.DataFrame(equity_curve)
        max_dd = None
        if not eq.empty:
            peak = eq["equity"].cummax()
            dd = (eq["equity"] - peak) / peak * 100
            max_dd = round(dd.min(), 2)

        wins = (closed["return_pct"] > 0).sum() if not closed.empty else 0
        n_closed = len(closed)

        exit_breakdown = {}
        if n_closed and "exit_reason" in closed.columns:
            exit_breakdown = closed["exit_reason"].value_counts().to_dict()

        open_syms = list(positions.keys())
        summary = {
            "universe_mode": (
                f"historical_{universe}" if use_historical_universe else "static_list"
            ),
            "universe": universe,
            "index_ticker": self.index_ticker,
            "lookback_roc": self.lookback_roc,
            "use_stop_loss": use_stop_loss,
            "rank_by_volume": rank_by_volume,
            "rank_by_rs": rank_by_rs,
            "rs_top_pct": rs_top_pct,
            "max_roc": max_roc,
            "min_volume_ratio": min_volume_ratio,
            "exit_confirm_days": exit_confirm_days,
            "stop_lookback": stop_lookback,
            "cooldown_days": cooldown_days,
            "require_index_trend": require_index_trend,
            "tickers_downloaded": len(tickers),
            "tickers_with_data": len(universe),
            "start": start_date,
            "end": end_date,
            "initial_capital": initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": max_dd,
            "max_positions": max_positions,
            "slot_size": round(slot_size, 2),
            "num_trades_closed": n_closed,
            "open_positions": len(open_syms),
            "win_rate_pct": round(100 * wins / n_closed, 1) if n_closed else None,
            "avg_return_pct": round(closed["return_pct"].mean(), 2)
            if n_closed
            else None,
            "exit_reason_counts": exit_breakdown,
        }

        benchmark = None
        if include_benchmark:
            benchmark = self.benchmark_buy_hold(start_date, end_date, initial_capital)
            if benchmark:
                summary["nifty_return_pct"] = benchmark["total_return_pct"]
                summary["alpha_vs_nifty_pct"] = round(
                    summary["total_return_pct"] - benchmark["total_return_pct"], 2
                )
                summary["beat_nifty"] = (
                    summary["total_return_pct"] > benchmark["total_return_pct"]
                )

        return {
            "trades": trades_df,
            "closed_trades": closed,
            "equity_curve": eq,
            "summary": summary,
            "benchmark": benchmark,
            "open_positions": open_syms,
        }

    def optimize_portfolio(
        self,
        start_date="2018-01-01",
        end_date="2026-05-01",
        initial_capital=1_000_000.0,
        max_combinations=None,
        universe=UNIVERSE_NIFTY50,
    ):
        """
        Run a compact grid search to maximize absolute strategy return.
        """
        all_tickers_fn, _, _ = _universe_helpers(universe)
        tickers = all_tickers_fn(start_date, end_date)
        prepared = self.prepare_universe(tickers, start_date, end_date)
        if not prepared:
            return None

        if universe == UNIVERSE_MIDCAP150:
            max_roc_values = [45, 75, 100]
            stop_lookbacks = [10, 20, 30]
            rs_top_pct_values = [0.20, 0.30]
        else:
            max_roc_values = [25, 35, 45]
            stop_lookbacks = [10, 20]
            rs_top_pct_values = [0.20]
        exit_confirm_values = [1, 2]
        cooldown_values = [0, 10]
        max_positions_values = [5, 10]
        min_volume_values = [1.0, 1.2]

        combos = []
        for max_roc in max_roc_values:
            for exit_days in exit_confirm_values:
                for cooldown in cooldown_values:
                    for max_pos in max_positions_values:
                        for min_vol in min_volume_values:
                            for stop_lb in stop_lookbacks:
                                for rs_pct in rs_top_pct_values:
                                    combos.append(
                                        {
                                            "max_roc": max_roc,
                                            "exit_confirm_days": exit_days,
                                            "cooldown_days": cooldown,
                                            "max_positions": max_pos,
                                            "min_volume_ratio": min_vol,
                                            "stop_lookback": stop_lb,
                                            "rs_top_pct": rs_pct,
                                        }
                                    )
        if max_combinations is not None and max_combinations < len(combos):
            # Keep search diverse instead of only earliest nested-loop combinations.
            idx = np.linspace(0, len(combos) - 1, max_combinations, dtype=int)
            combos = [combos[i] for i in idx]
        print(f"Testing {len(combos)} parameter combinations...", flush=True)

        rows = []
        for i, c in enumerate(combos, start=1):
            print(f"  [{i}/{len(combos)}] {c}", flush=True)
            bt = self.backtest_portfolio(
                tickers=tickers,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                max_positions=c["max_positions"],
                use_historical_universe=True,
                use_stop_loss=True,
                rank_by_volume=False,
                rank_by_rs=True,
                rs_top_pct=c["rs_top_pct"],
                include_benchmark=True,
                max_roc=c["max_roc"],
                min_volume_ratio=c["min_volume_ratio"],
                exit_confirm_days=c["exit_confirm_days"],
                stop_lookback=c["stop_lookback"],
                cooldown_days=c["cooldown_days"],
                require_index_trend=True,
                prepared_universe=prepared,
                universe=universe,
            )
            if not bt:
                continue
            s = bt["summary"]
            rows.append(
                {
                    **c,
                    "strategy_return_pct": s["total_return_pct"],
                    "max_drawdown_pct": s["max_drawdown_pct"],
                    "nifty_return_pct": s.get("nifty_return_pct"),
                    "alpha_vs_nifty_pct": s.get("alpha_vs_nifty_pct"),
                    "num_trades_closed": s.get("num_trades_closed"),
                    "win_rate_pct": s.get("win_rate_pct"),
                }
            )

        if not rows:
            return None
        df = pd.DataFrame(rows).sort_values("strategy_return_pct", ascending=False)
        return df


if __name__ == "__main__":
    import sys

    mode = UNIVERSE_NIFTY50
    if len(sys.argv) > 1 and sys.argv[1] in (UNIVERSE_MIDCAP150, "midcap"):
        mode = UNIVERSE_MIDCAP150
        sys.argv.pop(1)

    idx = _INDEX_DEFAULTS[mode]
    algo = SwingTradingAlgo(
        index_ticker=idx["index_ticker"],
        lookback_roc=idx["lookback_roc"],
    )
    start = "2019-01-01" if mode == UNIVERSE_MIDCAP150 else "2018-01-01"
    end = "2026-05-01"
    _, _, yearly_report = _universe_helpers(mode)

    if len(sys.argv) > 1 and sys.argv[1] == "single":
        stock_to_test = sys.argv[2] if len(sys.argv) > 2 else "TRENT.NS"
        bt = algo.backtest_stock(stock_to_test, start, end)
        if bt:
            print(bt["summary"])
            print(bt["trades"].tail(20).to_string(index=False))
        raise SystemExit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "optimize":
        print("=" * 50)
        print(f"Portfolio optimizer — {mode} (maximize return)")
        print("=" * 50)
        out = algo.optimize_portfolio(start_date=start, end_date=end, universe=mode)
        if out is None or out.empty:
            raise SystemExit("Optimization failed.")
        print("\nTop 10 parameter sets:")
        print(out.head(10).to_string(index=False))
        opt_path = (
            "portfolio_optimization_results_midcap.csv"
            if mode == UNIVERSE_MIDCAP150
            else "portfolio_optimization_results.csv"
        )
        out.to_csv(opt_path, index=False)
        best = out.iloc[0].to_dict()
        print("\nBest config:")
        for k, v in best.items():
            print(f"  {k}: {v}")
        print(f"\nSaved all runs to {opt_path}")
        raise SystemExit(0)

    label = "Nifty Midcap 150" if mode == UNIVERSE_MIDCAP150 else "Nifty 50"
    print("=" * 50)
    print(f"Portfolio backtest — {label} + Stage 2 + RS (point-in-time universe)")
    print("=" * 50)
    report_start = 2019 if mode == UNIVERSE_MIDCAP150 else 2018
    print(yearly_report(report_start, 2025).to_string(index=False))
    print()
    if mode == UNIVERSE_MIDCAP150:
        tuned = {
            "max_positions": 5,
            "max_roc": 75,
            "exit_confirm_days": 2,
            "cooldown_days": 0,
            "min_volume_ratio": 1.0,
            "stop_lookback": 20,
            "require_index_trend": True,
            "rank_by_rs": True,
            "rs_top_pct": 0.20,
            "universe": mode,
        }
    else:
        tuned = {
            "max_positions": 5,
            "max_roc": idx["default_max_roc"],
            "exit_confirm_days": 2,
            "cooldown_days": 10,
            "min_volume_ratio": 1.2,
            "stop_lookback": 20,
            "require_index_trend": True,
            "rank_by_rs": True,
            "rs_top_pct": 0.20,
            "universe": mode,
        }
    print("Using tuned config:")
    for k, v in tuned.items():
        print(f"  {k}: {v}")
    print()
    bt = algo.backtest_portfolio(
        start_date=start,
        end_date=end,
        initial_capital=1_000_000,
        use_historical_universe=True,
        **tuned,
    )
    if not bt:
        raise SystemExit("Portfolio backtest failed.")

    for k, v in bt["summary"].items():
        print(f"  {k}: {v}")

    if bt.get("benchmark"):
        b = bt["benchmark"]
        print("\n" + "=" * 50)
        print(f"Benchmark: buy & hold {b['benchmark']}")
        print("=" * 50)
        print(f"  Nifty return: {b['total_return_pct']}%")
        print(f"  Strategy return: {bt['summary']['total_return_pct']}%")
        print(f"  Alpha: {bt['summary'].get('alpha_vs_nifty_pct')}%")
        print(f"  Beat Nifty: {bt['summary'].get('beat_nifty')}")

    trades_path = (
        "portfolio_trades_midcap.csv"
        if mode == UNIVERSE_MIDCAP150
        else "portfolio_trades.csv"
    )
    bt["closed_trades"].to_csv(trades_path, index=False)
    print(f"\nClosed trades saved to {trades_path}")

    if bt["open_positions"]:
        print(f"\nStill holding: {', '.join(bt['open_positions'])}")

    closed = bt["closed_trades"]
    if not closed.empty:
        print(f"\nLast 10 closed trades:")
        cols = ["ticker", "entry_date", "exit_date", "return_pct", "days_held", "exit_reason"]
        show = [c for c in cols if c in closed.columns]
        print(closed[show].tail(10).to_string(index=False))
