# stock: paper-trading lab for NSE equities

A set of rules-based strategies for Indian equities, plus a hosted sandbox that paper-trades them side by side. Each strategy runs in its own isolated ledger. After the NSE close, a scheduled job asks each strategy what it would have done that day.

**Not financial advice.** Market data comes from Yahoo Finance and can differ from your broker.

## What's in here

| Folder | What it is |
| --- | --- |
| [`sandbox/`](sandbox/) | The hosted paper-trading platform: a FastAPI API, Supabase ledgers and a Next.js dashboard, plus signal-gate diagnostics and filter ablations |
| [`KALI/`](KALI/) | Multi-timeframe OHLCV strategy on the Nifty 150: HMM regime detection, Hurst exponent, regime-conditional Kelly sizing, with backtests |
| [`44ma/`](44ma/) | 44-day SMA pullback strategy: backtest, daily scan and paper ledger, on the top 100 NSE stocks by free-float market cap |
| [`financially free/`](financially%20free/) | Volatility Contraction Pattern swing strategy on the Nifty Midcap 150 |
| [`screnner/`](screnner/) | Screener tooling built with Playwright |

## The sandbox

Four algorithms run in separate portfolios: `44ma`, `44ma_stacked_2ma`, `financially_free` and `kali`.

- **Daily run:** [`.github/workflows/eod-analyze.yml`](.github/workflows/eod-analyze.yml) runs Monday to Friday at 5:30 PM IST, after the NSE cash close. It triggers each algorithm's end-of-day analysis in turn and can email a summary.
- **Deploy:** [`render.yaml`](render.yaml) defines two Render services, the Docker API and the Next.js dashboard.
- **Setup:** see [`sandbox/README.md`](sandbox/README.md) for setup, the API and the signal-gate tools.

## Running a strategy on its own

Each strategy folder has its own README with setup steps. For example, [`KALI/README.md`](KALI/README.md) covers fetching data and running backtests.
