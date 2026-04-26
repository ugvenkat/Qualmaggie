# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## System Status — Fully Working

All services running and verified as of 2026-04-26:
- **Backend**: Python FastAPI on `http://localhost:8000`
- **Frontend**: Next.js on `http://localhost:3000`
- **Database**: SQL Server — `QualMaggie` db, 10 tables
- **Tickers**: 1,852 total · 1,723 active · 129 deactivated (thin/stale)
- **PriceData**: 3,549,326 rows · 2010-03-10 → 2026-04-24

**All 6 frontend pages working:**
| Page | Route | Notes |
|---|---|---|
| Dashboard | `/` | Market status, scan trigger |
| Backtest | `/backtest` | Synchronous run (1800s timeout), equity curve, trades table |
| Positions | `/positions` | Open positions tab + History tab (`IsLive=0`) |
| Data Management | `/data` | CSV upload, yfinance update, earnings update, DB status table, Reset Backtests, Hard Reset |
| Performance | `/performance` | Equity curve, monthly heatmap, trade distribution bar chart |
| Settings | `/settings` | Editable form, writes settings.json, clears lru_cache on save |

---

## Startup Commands

**Terminal 1 — Backend:**
```bash
cd C:\proj\QualMaggie
source .venv/Scripts/activate
python -m uvicorn backend.main:app --reload
```

**Terminal 2 — Frontend:**
```bash
cd C:\proj\QualMaggie\frontend
npm run dev
```
Then open: `http://localhost:3000`

**Terminal 3 — Claude Code:**
```bash
cd C:\proj\QualMaggie
source .venv/Scripts/activate
claude
```

**Terminal 4 — Scripts:**
```bash
cd C:\proj\QualMaggie
source .venv/Scripts/activate
python scripts/[script_name].py
```

> **Important:** After adding a new route file to `main.py` imports, do a **hard restart** of uvicorn (Ctrl+C + relaunch) — `--reload` does not auto-discover newly imported modules.

**Install packages:**
```bash
uv pip install fastapi uvicorn python-multipart pyodbc sqlalchemy yfinance pandas \
  python-dotenv apscheduler pydantic-settings httpx aiofiles
```

---

## Known Bugs

| Bug | Detail |
|---|---|
| IsActive column mismatch | Some queries use `IsActive` vs `Active` inconsistently — audit full codebase |
| History pagination | Positions History loads all rows at once — add 50-per-page pagination |
| SOXL in history | IsActive=0 tickers not filtered from positions history — backtester should skip inactive tickers |

---

## Next Session Task List

1. **Run backtest with 1,723 tickers** — decimal columns fixed (sql/01_schema.sql column upgrades run in SSMS), should work now:
   ```bash
   python scripts/run_backtest.py --start 2024-01-01 --end 2024-12-31
   ```

2. **V10 strategy tuning** — update settings.json then run full 2020–2025 backtest:
   ```json
   "StopLossADRMultiplier": 1.8,
   "MinStopPct": 4.0
   ```
   `MinStopPct` is already in `Settings` class and `calculate_initial_stop()` — just update settings.json.

3. **Fix IsActive column name** — audit full codebase for `Active` vs `IsActive` mismatch in queries and ORM references.

4. **Pagination on Positions History** — 50 trades/page, Next/Prev buttons in `frontend/app/positions/page.tsx`.

5. **Run full 2020–2025 backtest** — after V10 settings confirmed working on 2024 single-year test.

6. **Download remaining Nasdaq tickers** — ~1,300 more from Barchart to reach ~3,300 total.

7. **Run morning_routine.py** — test the full daily workflow end-to-end:
   ```bash
   python scripts/morning_routine.py
   ```

---

## Strategy Enhancements (2026-04-26)

Five scanner/backtest improvements implemented in this session:

### Task 1 — Enhanced Scanner Signal Output ✅
`backend/core/scanner.py` and `backend/api/routes_scan.py` now return 30+ fields per candidate:
- **Entry**: `entry_price` (close×1.002 estimate), `pivot_point`, `distance_from_pivot_pct`
- **Stop & risk**: `stop_loss_price`, `stop_loss_pct`, `adr`, `adr_pct`, `max_stop_as_adr_fraction`
- **Sizing**: `recommended_shares`, `recommended_position_size_usd`, `max_loss_usd`
- **Setup quality**: `setup_quality_score` (0–10), `prior_move_pct`, `base_length_days`, `num_contractions`, `tightest_contraction_pct`, `volume_dry_up_pct`
- **RS**: `rs_rank` (percentile vs universe), `rs_vs_spy_6m`, `distance_from_52w_high_pct`, `distance_from_200sma_pct`
- **Warnings**: `earnings_date`, `days_to_earnings`, `earnings_warning`, `macro_event_warning`
- **Market**: `market_status` ("Healthy"/"Neutral"/"Weak"), `spy_above_50sma`, `spy_10ema_above_20ema`

`pattern_detector.py` now includes `base_length_days` in the VCP result dict.

### Task 2 — Backtest Reset Feature ✅
- **`sql/reset_backtests.sql`** — soft reset (deletes Blacklist, OpenPositions, PortfolioSnapshot, PerformanceReport, Trades, BacktestRuns; reseeds identities)
- **`sql/hard_reset.sql`** — hard reset (everything + EarningsDates, MacroEventDates, PriceData, Tickers)
- **`POST /api/data/reset-backtests`** — runs soft reset, returns rows deleted per table
- **`POST /api/data/hard-reset`** — requires `{"confirmation": "CONFIRM"}` in body
- **Frontend**: "Reset Backtests" (yellow, window.confirm) and "Hard Reset" (red, type-CONFIRM dialog) buttons on `/data`

### Task 3 — Entry Price Bug Fix ✅ (was already implemented)
`backtester.py` already uses next-day open price for entry (`Fix 1` comment in `_process_day`). If N+1 data unavailable, trade is skipped. No code change needed.

### Task 4 — Prior Move Check ✅ (was already implemented)
`pattern_detector.py` already rejects VCPs where `prior_move_pct < MinPriorMovePct` (30%). The check is in `detect_vcp()` → `_check_prior_move()`. No code change needed.

### Task 5 — ADR Stop Rejection ✅
`MaxStopAsADRFraction: 0.67` was already in settings.json and backtester. **New**: the same rejection now also runs in `_scan_ticker` (scanner) so live-scan candidates are pre-filtered consistently with the backtester. Adds `"StopTooWide"` to `failed_filters` when `stop_distance > ADR × 0.67`.

---

## Scripts Available

All scripts live in `scripts/`. Logs are written to `scripts/logs/` (auto-created).

```bash
python scripts/morning_routine.py               # Daily: prices → earnings → scan → health check
python scripts/update_prices.py                 # Pull missing OHLCV from yfinance (batches of 50)
python scripts/update_prices.py --dry-run       # Preview only
python scripts/update_prices.py --symbol AAPL   # Single ticker
python scripts/update_earnings.py               # Refresh EarningsDates from yfinance
python scripts/run_scanner.py                   # VCP scan → signals_YYYY-MM-DD.csv + top 5
python scripts/db_health_check.py               # Table counts, date ranges, stale ticker flags
python scripts/run_backtest.py --start 2024-01-01 --end 2024-12-31
python scripts/run_backtest.py --capital 100000 --risk 1.5 --timeout 1800
python scripts/export_trades.py --run_id 45     # CSV export → data/exports/
python scripts/fix_duplicate_trades.py --dry-run  # Preview dupe cleanup
python scripts/fix_duplicate_trades.py           # Apply: delete dupes + add UNIQUE constraint
```

See `scripts/README.txt` for full argument reference.

---

## Project Overview

QualMaggie is a momentum swing trading system (VCP, Flags, Cup & Handle, Flat Bases).
Stack: Python 3.11 + FastAPI backend, Next.js frontend, SQL Server database.

## Environment & Commands

```bash
# Activate venv
source .venv/Scripts/activate        # Git Bash / bash on Windows

# Install dependencies
uv pip install -r requirements.txt

# Run backend dev server
uvicorn backend.main:app --reload

# Run a single test (pytest, once tests/ exists)
pytest tests/path/to/test_file.py::test_name -v
```

**DB connection** — set `DB_CONNECTION_STRING` in a `.env` file at project root (not committed).
Default uses Windows auth: `mssql+pyodbc://localhost/QualMaggie?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes`

## Directory Structure

```
QualMaggie/
├── sql/
│   ├── 01_schema.sql                   # 10-table DDL + indexes + column upgrades (run once in SSMS)
│   ├── 02_seed_tickers.sql             # Starter tickers (MERGE, safe to re-run)
│   └── 03_daily_maintenance.sql        # Daily: stats refresh, index rebuild, stale data cleanup
├── backend/
│   ├── config/settings.py              # Pydantic BaseSettings — loads settings.json + .env
│   ├── db/
│   │   ├── models.py                   # SQLAlchemy 2.x ORM models
│   │   └── database.py                 # Engine + session factory (get_db)
│   ├── services/
│   │   ├── data_importer.py            # Barchart CSV → PriceData
│   │   ├── data_updater.py             # yfinance gap-fill → PriceData
│   │   └── earnings_updater.py         # yfinance earnings dates → EarningsDates table
│   ├── core/
│   │   ├── indicators.py               # Compute SMA/EMA/ATR/ADR/RS → bulk UPDATE PriceData
│   │   ├── market_filter.py            # SPY regime filter (close>SMA50, EMA10>EMA20)
│   │   ├── stock_filter.py             # 8-condition universe filter
│   │   ├── pattern_detector.py         # VCP detection (Flag/Cup&Handle/FlatBase planned)
│   │   ├── scanner.py                  # Daily scan engine (market→stock→VCP→ranked candidates)
│   │   ├── position_manager.py         # Trade lifecycle (open/partial/trail/close/blacklist)
│   │   └── backtester.py               # Historical simulation loop
│   └── api/
│       ├── routes_market.py            # GET /api/market/status
│       ├── routes_scan.py              # POST /api/scan/run
│       ├── routes_positions.py         # GET /api/positions, /api/positions/history, /api/trades
│       ├── routes_data.py              # POST /api/data/import-csv, /update, /update-earnings, /reset-backtests, /hard-reset; GET /api/data/status
│       ├── routes_performance.py       # GET /api/performance, /api/performance/snapshots
│       ├── routes_backtest.py          # POST /api/backtest/run; GET /api/backtest, /{id}, /{id}/trades, /{id}/snapshots
│       └── routes_settings.py          # GET /api/settings, POST /api/settings
├── scripts/
│   ├── morning_routine.py              # Master daily script (runs all 4 below in order)
│   ├── update_prices.py                # yfinance gap-fill for all active tickers
│   ├── update_earnings.py              # Refresh EarningsDates from yfinance
│   ├── run_scanner.py                  # VCP scan → signals CSV + console top 5
│   ├── db_health_check.py              # Table counts + stale ticker detection
│   ├── run_backtest.py                 # Submit backtest via API, poll, save JSON
│   ├── export_trades.py                # Export trades for a run_id to CSV
│   ├── fix_duplicate_trades.py         # One-time dupe cleanup + UNIQUE constraint
│   ├── logs/                           # Auto-created; YYYY-MM-DD named log files
│   └── README.txt                      # Full argument reference for all scripts
├── settings.json                       # All trading parameters (PascalCase keys)
├── .env                                # DB_CONNECTION_STRING and secrets (not committed)
├── requirements.txt
└── data/
    ├── exports/                        # CSV exports from export_trades.py
    └── cache/
```

## Architecture

### Settings
`backend/config/settings.py` — single `Settings` class (pydantic-settings v2).
`settings.json` uses PascalCase keys; Python attributes are snake_case.
Import pattern: `from backend.config.settings import get_settings; s = get_settings()`.
`get_settings()` is `@lru_cache` — singleton for the process.

### Database Session (`backend/db/database.py`)
Lazy-initialised — importing the module never probes SQL Server (safe in CI).
- `get_db()` — `@contextmanager` / FastAPI `Depends` — yields `Session`, commits on success, rolls back on exception.
- `get_engine()` — returns raw `Engine` for `pd.read_sql` / Alembic.
- `sessionmaker` uses `expire_on_commit=False` to prevent `DetachedInstanceError` in background jobs.

Pattern:
```python
from backend.db.database import get_db
with get_db() as session:
    session.add(obj)
# FastAPI: def route(session: Session = Depends(get_db)): ...
```

### Data Ingestion Services

**`backend/services/data_importer.py`** — Barchart CSV → PriceData
- `import_csv(file_path: Path, symbol: str, session: Session) -> int`
  - Renames `Last→Close`, drops `Change/%Change`, strips commas from Volume, strips footer line via `errors="coerce"` on date parse, reverses rows oldest-first.
  - One query to fetch existing dates; inserts only new rows. Does NOT commit.
- `import_folder(folder: Path, session: Session) -> dict[str, int]`
  - Globs `*.csv`, infers symbol from stem, commits per file. Bad files are logged and skipped.

**`backend/services/data_updater.py`** — yfinance gap-fill → PriceData
- `update_ticker(symbol: str, session: Session) -> int`
  - Finds `MAX(TradeDate)` for ticker; fetches from next business day to today.
  - `auto_adjust=True` (split/dividend adjusted). Handles yfinance MultiIndex columns. Does NOT commit.
- `update_all_tickers(session: Session) -> dict[str, int]`
  - All active tickers + `MarketFilterTicker` (SPY). Commits per symbol so partial runs are preserved.

Both services: `created_at=datetime.utcnow()` must be supplied explicitly (no `server_default` on `PriceData.created_at` in the ORM model). Indicator columns (`sma50`, `sma200`, `ema10`, `ema20`, `atr_pct`, `adr`, `rs_score`) are inserted as `None` and populated by `indicators.calculate_and_store()`.

### Core Analytical Layer (`backend/core/`)

**`indicators.py`** — Two-layer design: pure computation + DB integration.
- `compute_indicators(df, spy_df, rs_lookback)` → adds `SMA50, SMA200, EMA10, EMA20, ATRPct, ADR, RSScore, VolumeMA20` to DataFrame. Pure function, no DB.
- `calculate_and_store(symbol, session)` → loads from DB via `pd.read_sql`, computes, bulk-UPDATEs PriceData. Uses SQLAlchemy 2.x `session.execute(update(PriceData), records)` with list of dicts keyed by `price_data_id`.
- `calculate_all(session)` → processes SPY first (needed for RS score), then all active tickers.
- `VolumeMA20` is computed but NOT stored (no DB column); callers derive it from `df["volume"].rolling(20).mean()`.
- SMA200 warmup requires 200 bars — load data from 2019-01-01 for full 2020 backtest coverage.

**`market_filter.py`** — SPY regime check.
- `is_market_healthy(df) -> bool` — latest row: `close_price > sma50` AND `ema10 > ema20`. Returns False if any indicator is NaN.
- `get_market_status(df) -> dict` — full status with all indicator values.
- `check_market(session) -> dict` — loads SPY from DB, returns status.

**`stock_filter.py`** — 8-condition universe filter.
- Individual `passes_*_filter()` functions for each rule (price, volume, ATR, SMA200, RS, index, EPS, institutional).
- `apply_stock_filters(symbol, latest_row, settings, eps, inst_pct) -> tuple[bool, list[str]]` — returns pass/fail + list of failed filter names.
- `filter_universe(candidates, settings) -> list[dict]` — batch filter, returns passing tickers.
- EPS and institutional ownership: pass `None` to skip check (data not in DB yet); logged as debug.
- `volume_ma20` must be computed by caller: `df["volume"].rolling(20).mean().iloc[-1]`.
- `index_membership` values that pass: `'SP500'`, `'Nasdaq100'`, `'Both'`.

**`pattern_detector.py`** — VCP only (Flag/Cup&Handle/FlatBase stubs planned).
- `detect_vcp(df, settings) -> dict | None` — uses last 120 bars; requires 3+ contractions with shrinking range AND declining volume, last range < 15%, price within 3% of pivot high.
- Returns detail dict including `pivot_high` (entry trigger), `is_breakout_candidate` (within 2%), `contractions_count`.
- `scan_vcp(symbols, session, settings) -> dict[str, dict]` — batch scanner; loads 120 bars from DB per ticker.

### Database Models (`backend/db/models.py`)
SQLAlchemy 2.x with `Mapped` / `mapped_column`. 10 tables:

| Model | Table | Purpose |
|---|---|---|
| `Ticker` | Tickers | Universe of tracked symbols |
| `PriceData` | PriceData | Daily OHLCV + computed indicators (SMA50/200, EMA10/20, ATRPct, ADR, RSScore) |
| `BacktestRun` | BacktestRuns | Backtest run metadata + settings snapshot |
| `Trade` | Trades | Closed trades (live + backtest) |
| `OpenPosition` | OpenPositions | In-flight positions with trailing stop |
| `Blacklist` | Blacklist | 10-day re-entry ban after stop-out |
| `PortfolioSnapshot` | PortfolioSnapshot | Daily EOD portfolio state |
| `PerformanceReport` | PerformanceReport | Aggregated win rate, profit factor, drawdown |
| `EarningsDate` | EarningsDates | Upcoming/historical earnings per symbol (fetched from yfinance) |
| `MacroEventDate` | MacroEventDates | FOMC/CPI/NFP dates for macro warning/block |

Live records: `IsLive=True`, `BacktestRunID=NULL`.
Backtest records: `IsLive=False`, `BacktestRunID=<id>`.

**Decimal column precision** (enforced by `sql/01_schema.sql` — CREATE TABLE uses correct types; ALTER TABLE section upgrades existing DBs):
- Price / indicator columns: `DECIMAL(18,4)` — OHLC, SMA, EMA, ADR, stop prices
- Dollar amount columns: `DECIMAL(18,2)` — PnL, RiskAmount, portfolio values
- Percentage / ratio columns: `DECIMAL(10,6)` — ATRPct, RSScore, PnLPct, TotalPnLPct

### Strategy Rules (reference for all modules)
- **Market filter**: SPY above 50 SMA AND 10 EMA > 20 EMA
- **Stock filter**: price > $20, avg volume > 2M, ATR 2–8%, above 200 SMA, RS > SPY (126 days), positive EPS, institutional ownership > 30%, S&P500 or Nasdaq only
- **Entry**: breakout on volume ≥ `BreakoutVolumeFactor` × avg volume; patterns: VCP, Flag, CupHandle, FlatBase
- **Earnings hard block**: skip entry if earnings within `EarningsWarningDays` days — `EarningsHardBlock=true`
- **Macro warning**: skip entry if FOMC/macro event within `MacroEventWarningDays` (3) days
- **Stop loss**: `ADR × StopLossADRMultiplier` (1.5 current V9). V10 pending: 1.8 with 4% floor.
- **Partial exit**: sell 50% when unrealized gain ≥ `PartialSellMinProfitPct`
- **Trail remaining**: EMA10 trail activates when gain ≥ `TrailActivationPct`; switches to EMA20 when gain ≥ `WideTrailActivationPct` (8%)
- **Tiered MaxHold**: exit at 20d if gain < 5%; exit at 60d if gain ≥ 5%; no MaxHold if gain ≥ 10% (only trail/stop exits)
- **Sizing**: risk `RiskPerTrade`% of portfolio per trade; max `MaxCapitalDeployedPct`% deployed
- **Limits**: max 5 open positions, max 2 per sector
- **Blacklist**: 10-day ban after stop-out; fresh VCP required on re-entry
- **Warnings** (no auto-exit, manual decision): earnings within N days, macro events within 3 days
- **Never**: average down on a losing position

### Backtester Architecture (`backend/core/backtester.py`)
- Loads ALL PriceData + earnings dates + macro dates into memory once at start
- `_process_day()`: A) update positions → B) run scanner → C) flush + open new positions → D) snapshot
- `scan_daily()` receives `preloaded_dfs`, `earnings_dates`, `macro_event_dates` — no per-day DB reads
- `autoflush=False` on sessionmaker — must call `session.flush()` before step C so Blacklist inserts are visible to `is_blacklisted()`
- `position._open_trade_id` — in-memory Python attribute set after flush in `open_position()`; used for direct PK lookup in `execute_partial_exit` / `close_position` to avoid stale-session issues
- Commits every 50 days; final commit after loop; second commit after closing all open positions at end_date

**Decimal overflow fix (2026-04-25):** `position_manager.update_trailing_stop()` now rounds `current_stop` to 4 decimal places before assignment. Raw EMA floats like `146.41179188825717` previously overflowed `DECIMAL(12,4)`. Both the DB columns (via `sql/01_schema.sql` ALTER TABLE section) and the ORM `Numeric()` types have been widened.

### Services
- `backend/services/earnings_updater.py` — `update_earnings_dates(session)` fetches yfinance `ticker.earnings_dates`, stores in EarningsDates table; `load_earnings_by_symbol(session)` returns `dict[str, list[date]]` for backtester pre-load
- `backend/services/data_updater.py` — yfinance gap-fill; requires `lxml` package (install if missing)
- API endpoint `POST /api/data/update-earnings` triggers earnings refresh

### Strategy Version History

| Version | Key Change |
|---|---|
| V1–V4 | Initial parameter tuning |
| V5 | ATR ceiling 8%; SOXL deactivated (ETF, no earnings) |
| V6 | `EarningsWarningDays` 10 → 21 days |
| V7 | FOMC/macro dates added; `MacroEventWarningDays` = 5 |
| V8 | `MacroEventWarningDays` 5 → 3 days |
| V9 | Tiered MaxHold — extends hold for profitable positions |
| **V10 PENDING** | Wider stops: `StopLossADRMultiplier` 1.5 → 1.8, `MinStopPct` 4.0% floor |

### Pending V10 Backtest Tuning
**Root cause identified (2024 backtest analysis):** ~80% of stop-loss exits are "stop too tight" —
AMD recovered +28%, AVGO +32%, TSM +19% within 3 weeks after being stopped out.
Normal daily volatility in large-cap growth stocks (~2%) eats through a 1.5×ADR stop.

**V10 changes to implement:**
```json
"StopLossADRMultiplier": 1.8,
"MinStopPct": 4.0
```
**Logic:** `stop = entry - max(ADR × 1.8, entry × 0.04)`
- `MinStopPct` is a **new settings field** — add to `Settings` class:
  `min_stop_pct: float = Field(0.0, alias="MinStopPct")`
- Update `calculate_initial_stop()` in `position_manager.py` to apply the floor:
  `stop = min(stop, entry_price - entry_price * settings.min_stop_pct / 100)`
- Never allow stop closer than 4% from entry regardless of ADR
- Expected to convert most 2024 "stop too tight" losses into eventual winners
- **Test strategy:** run 2024 first, then full 2020–2025 before committing

**V9 stable baseline (run_ids 34–37):**
| Year | Return | Trades | Win Rate | Profit Factor |
|------|--------|--------|----------|---------------|
| 2021 | -2.3%  |   9    |   44%    |     0.73      |
| 2022 | -4.9%  |   4    |   50%    |     0.08      |
| 2023 | -1.1%  |  14    |   50%    |     0.87      |
| 2024 | -1.6%  |  10    |   60%    |     0.75      |

### Frontend (`frontend/`)

Next.js app with dark terminal theme. All pages are `"use client"` components.

```
frontend/
├── app/
│   ├── layout.tsx / globals.css        # Dark theme CSS vars (--bg, --green, --red, etc.)
│   ├── page.tsx                        # Dashboard
│   ├── backtest/page.tsx               # Backtest runner + results
│   ├── positions/page.tsx              # Open positions + history tabs
│   ├── data/page.tsx                   # Data management
│   ├── performance/page.tsx            # Equity curve + heatmap + distribution
│   └── settings/page.tsx              # Settings editor
└── lib/
    └── api.ts                          # Axios client (baseURL=localhost:8000, timeout=300s)
```

**API client (`lib/api.ts`) exports:**
`getMarketStatus`, `runScan`, `getOpenPositions`, `getPositionsHistory`, `getTrades`,
`updatePrices`, `updateEarnings`, `importFolder`, `importCsvFile`, `getDataStatus`, `resetBacktests`, `hardReset`,
`getPerformance`, `getSnapshots`, `getSettings`, `saveSettings`,
`runBacktest`, `listBacktests`, `getBacktest`, `getBacktestTrades`, `getBacktestSnapshots`

**Numeric values from the API** — SQL Server `Numeric` columns deserialize as strings in some drivers. Always wrap with `parseFloat(String(v))` before arithmetic or `.toFixed()`. The performance and backtest pages use a helper `f(v)` for this.

**Route ordering in FastAPI** — sub-path routes (`/positions/history`) must be registered *before* the base route (`/positions`) in the router file. Same rule applies to `/{id}/trades` before `/{id}`.

### Price Data (Barchart CSV format)
Columns: `Time, Open, High, Low, Last, Change, %Change, Volume`
- `Last` = close price
- Strip footer line before parsing
- Rows are newest-first — reverse before inserting

## Windows Compatibility Rules

These rules apply to every Python file in this project, no exceptions:

| Rule | Implementation |
|---|---|
| All file paths | Use `pathlib.Path`. Never build paths with string concatenation or hardcode separators. |
| Path defaults | Derive from `PROJECT_ROOT = Path(__file__).resolve().parents[N]`, not hardcoded strings. |
| File reads | `open(path, encoding="utf-8")` — always explicit encoding. |
| File writes | `open(path, "w", encoding="utf-8")` — always explicit encoding. |
| CSV writes | `open(path, "w", encoding="utf-8", newline="")` — `newline=""` prevents double CR on Windows. |
| SQL Server | Connection string must use `ODBC+Driver+17+for+SQL+Server`. |
| Path from settings | `settings.data_folder`, `settings.export_folder`, `settings.cache_folder` are already `Path` objects — use them directly, do not call `str()` on them unless passing to a library that requires it. |

```python
# Correct pattern for every file read
with open(some_path, encoding="utf-8") as f:
    content = f.read()

# Correct pattern for every file write
with open(some_path, "w", encoding="utf-8") as f:
    f.write(content)

# Correct pattern for CSV writes
import csv
with open(csv_path, "w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)
    ...

# Correct path construction
output = get_settings().export_folder / f"{symbol}_{date}.csv"
```
