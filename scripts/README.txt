QualMaggie — Scripts Reference
===============================
All scripts live in  scripts/  and are run from the project root.
Logs are written to  scripts/logs/  (created automatically).

Activate the venv first:
  source .venv/Scripts/activate


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DAILY ROUTINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

morning_routine.py
  Master script — runs update_prices → update_earnings →
  run_scanner → db_health_check in order.

  Usage:
    python scripts/morning_routine.py
    python scripts/morning_routine.py --no-fail-fast

  --no-fail-fast   Continue remaining steps even if one fails.
                   Default behaviour: stop on first failure.

  Output:
    Streams each child script's output to console.
    Prints a pass/fail summary table with elapsed times.
    Exit code 0 = all passed, 1 = at least one failed.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATA MAINTENANCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

update_prices.py
  Pull missing OHLCV data from yfinance for all active tickers.
  On first run: adds MaxDate / TotalRecords / LastUpdated columns
  to the Tickers table (idempotent — safe to run again).
  Processes tickers in batches of 50 with a 2-second pause between
  batches to avoid yfinance rate limits.

  Usage:
    python scripts/update_prices.py
    python scripts/update_prices.py --dry-run       # show plan only
    python scripts/update_prices.py --symbol AAPL   # single ticker

  Log:  scripts/logs/update_prices_YYYY-MM-DD.log


update_earnings.py
  Fetch upcoming/historical earnings dates from yfinance for all
  active tickers and store them in the EarningsDates table.
  Replaces existing rows per ticker on each run so dates stay current.
  Automatically skips ETFs (SPY, QQQ, SOXL, TQQQ, etc.).

  Usage:
    python scripts/update_earnings.py
    python scripts/update_earnings.py --symbol AAPL

  Log:  scripts/logs/update_earnings_YYYY-MM-DD.log


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCANNING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

run_scanner.py
  Run the daily VCP scanner via the FastAPI backend.
  Requires the backend to be running (uvicorn on port 8000).

  Pipeline: market filter → stock filters → VCP detection → ranking
  Ranking: breakout candidates first, then preferred quality, then RS.

  Usage:
    python scripts/run_scanner.py
    python scripts/run_scanner.py --url http://localhost:8000

  Output:
    scripts/logs/signals_YYYY-MM-DD.csv   — full candidate list
    console                               — market status + top 5 signals

  CSV columns:
    symbol, sector, exchange, index_membership, close, volume_ma20,
    atr_pct, sma200, ema10, rs_score, pivot_high, distance_from_high_pct,
    contractions_count, preferred_quality, is_breakout_candidate,
    has_earnings_warning, has_macro_warning


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BACKTESTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

run_backtest.py
  Submit a backtest run via the FastAPI backend and save full results.
  Requires the backend to be running.

  Usage:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --start 2020-01-01 --end 2024-12-31
    python scripts/run_backtest.py --capital 100000 --risk 1.5
    python scripts/run_backtest.py --name "V10 test" --timeout 1800

  Arguments:
    --start     Backtest start date (default: 2024-01-01)
    --end       Backtest end date   (default: 2024-12-31)
    --capital   Initial capital — updates settings.json before run,
                restores original value afterwards (optional)
    --risk      RiskPerTrade % — same patch/restore behaviour (optional)
    --name      Run label stored in BacktestRuns table
    --timeout   HTTP read timeout in seconds (default: 1800)

  Progress is printed every 30 seconds while waiting.

  Output:
    scripts/logs/backtest_YYYY-MM-DD_HH-MM.json
      Contains: run metadata, performance report, all trades, equity curve.


export_trades.py
  Export all trades for a specific backtest run to CSV.

  Usage:
    python scripts/export_trades.py --run_id 45
    python scripts/export_trades.py --run_id 45 --out-dir C:/myreports

  Arguments:
    --run_id    BacktestRunID to export (required)
    --out-dir   Output directory (default: data/exports from settings.json)

  Output:
    data/exports/trades_{run_id}_{YYYY-MM-DD}.csv

  CSV columns:
    TradeID, Symbol, Sector, PatternType,
    EntryDate, ExitDate, EntryPrice, ExitPrice, Shares, InitialStopLoss,
    PartialExitDate, PartialExitPrice, PartialShares,
    PnL, PnLPct, ExitReason, RiskAmount, BacktestRunID

  Note: PnLPct is stored as a decimal ratio (0.0312 = 3.12%).


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MONITORING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

db_health_check.py
  Checks database integrity and data freshness.

  Reports:
    - Row counts for all tables (Tickers, PriceData, BacktestRuns,
      Trades, OpenPositions, Blacklist, PortfolioSnapshot,
      PerformanceReport, EarningsDates, MacroEventDates)
    - PriceData date range (min, max, total rows) per ticker
    - STALE flag for tickers whose MaxDate is older than N days

  Usage:
    python scripts/db_health_check.py
    python scripts/db_health_check.py --stale-days 5

  Output:
    console
    scripts/logs/db_health_YYYY-MM-DD.txt


fix_duplicate_trades.py
  One-time cleanup: deletes duplicate rows from the Trades table
  (keeping the lowest TradeID per BacktestRunID+Symbol+EntryDate group)
  and adds a UNIQUE constraint to prevent future duplicates.

  Usage:
    python scripts/fix_duplicate_trades.py --dry-run   # preview
    python scripts/fix_duplicate_trades.py             # apply


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Backend dependency
  run_backtest.py and run_scanner.py call the FastAPI backend.
  Start it before running those scripts:
    python -m uvicorn backend.main:app --reload

DB connection
  All scripts read the connection string from settings.json
  (key: DatabaseUrl) or the DB_CONNECTION_STRING environment variable.
  Default uses Windows Authentication — no username/password required.

Log rotation
  Log files are named with today's date. Re-running on the same day
  appends to the existing file (file handlers use append mode).
  Old logs are not auto-deleted — clean scripts/logs/ manually as needed.
