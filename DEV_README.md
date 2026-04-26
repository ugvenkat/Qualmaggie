# QualMaggie — Developer README

> **Momentum swing trading system** — VCP pattern scanner, backtester, and live dashboard.
> Strategy based on Mark Minervini's Volatility Contraction Pattern (VCP) methodology.

---

## 1. System Overview

### What This System Does

QualMaggie scans ~1,700+ US stocks daily for high-quality momentum breakout setups using the
Volatility Contraction Pattern (VCP). It applies an 8-condition stock filter, a SPY market
regime filter, and a 0–10 quality scoring system to rank candidates. A historical backtester
simulates trades from 2020–present to validate and tune the strategy. All results are displayed
in a dark-themed web dashboard.

The system generates **signals only** — trade execution remains manual via your broker.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                                      │
│                                                                             │
│   Barchart.com (CSV)          yfinance (Python)         Manual SSMS        │
│   Daily OHLCV history         Gap fills + earnings       Macro event dates  │
│        │                            │                          │             │
└────────┼────────────────────────────┼──────────────────────────┼────────────┘
         │                            │                          │
         ▼                            ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SQL SERVER  (QualMaggie DB)                           │
│                                                                             │
│   Tickers │ PriceData │ Trades │ OpenPositions │ BacktestRuns              │
│   Blacklist │ PortfolioSnapshot │ EarningsDates │ MacroEventDates          │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PYTHON FASTAPI BACKEND  (:8000)                          │
│                                                                             │
│   indicators.py  →  market_filter.py  →  stock_filter.py                  │
│   pattern_detector.py  →  scanner.py  →  backtester.py                    │
│   position_manager.py                                                       │
│                                                                             │
│   REST API: /api/scan /api/backtest /api/positions /api/data /api/settings │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │  JSON
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     NEXT.JS DASHBOARD  (:3000)                              │
│                                                                             │
│   Dashboard │ Backtest │ Positions │ Data Management │ Performance          │
│   Settings                                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Language | Python | 3.11 |
| API framework | FastAPI + Uvicorn | Latest |
| ORM | SQLAlchemy | 2.x |
| DB driver | pyodbc | Latest |
| Data | pandas, yfinance, numpy | Latest |
| Settings | pydantic-settings | v2 |
| Frontend | Next.js + TypeScript | Latest |
| Styling | Tailwind CSS (via CSS vars) | Latest |
| Charts | Recharts | Latest |
| HTTP client | Axios | Latest |
| Database | SQL Server | Any edition |
| Package manager | uv | Latest |

---

## 2. Prerequisites

Install all of the following before starting.

### Required Software

| Tool | Where to Get | Notes |
|------|-------------|-------|
| Windows 11 | — | Windows-only project (path conventions) |
| Python 3.11 | python.org | Exact version — uv enforces this |
| uv | `pip install uv` | Fast Python package manager |
| Node.js 24+ | nodejs.org | LTS or Current |
| Git | git-scm.com | Any recent version |
| SQL Server | microsoft.com | Express edition is free and sufficient |
| SSMS | microsoft.com | SQL Server Management Studio |
| ODBC Driver 17 | microsoft.com | "ODBC Driver 17 for SQL Server" |

### VS Code Extensions (Recommended)

Install all of these for the best developer experience:

```
Python (Microsoft)          ms-python.python
Pylance                     ms-python.vscode-pylance
ESLint                      dbaeumer.vscode-eslint
Prettier                    esbenp.prettier-vscode
SQL Server (mssql)          ms-mssql.mssql
Tailwind CSS IntelliSense   bradlc.vscode-tailwindcss
Thunder Client              rangav.vscode-thunder-client
GitLens                     eamodio.gitlens
Error Lens                  usernamehw.errorlens
```

### External Accounts

- **Barchart.com** — Free account required to download historical OHLCV data as CSV
- **TradingView.com** — Free account for chart analysis (not integrated, used manually)

---

## 3. First-Time Setup

Follow these steps exactly in order.

### a) Get the Project

```bash
# Clone or copy project to:
C:\proj\QualMaggie
```

All commands below assume this working directory unless noted.

### b) Install uv

```bash
pip install uv
```

### c) Create the Python Virtual Environment

```bash
# From C:\proj\QualMaggie
uv venv --python 3.11
```

Activate it (use the correct shell):

```bash
# Git Bash / bash on Windows:
source .venv/Scripts/activate

# PowerShell:
.venv\Scripts\activate
```

Your prompt should show `(.venv)` when active. **Always activate before running Python commands.**

### d) Install Python Packages

```bash
uv pip install fastapi uvicorn python-multipart pyodbc sqlalchemy yfinance pandas \
  python-dotenv apscheduler pydantic-settings httpx aiofiles numpy
```

Verify the install:

```bash
python -c "import fastapi, sqlalchemy, pandas, yfinance; print('OK')"
```

### e) Install Node Packages

```bash
cd frontend
npm install
cd ..
```

### f) Install Claude Code (Optional — AI coding assistant)

```bash
npm install -g @anthropic-ai/claude-code
```

### g) Set Up SQL Server

1. Open **SSMS** and connect to `localhost`
2. Create a new database named `QualMaggie`
3. Open and run **`sql/01_schema.sql`** — creates all 10 tables with correct column types
4. Open and run **`sql/02_seed_tickers.sql`** — inserts ~20 starter tickers (AAPL, MSFT, etc.)

> **Important:** `01_schema.sql` must be run before anything else. It also contains `ALTER TABLE`
> statements to widen decimal columns — run the entire file from top to bottom.

### h) Configure settings.json

Open `settings.json` in the project root and verify these paths match your machine:

```json
{
  "DataInputFolder": "C:/proj/QualMaggie/dataInput/",
  "DataFolder":      "C:/proj/QualMaggie/data/",
  "ExportFolder":    "C:/proj/QualMaggie/data/exports/",
  "CacheFolder":     "C:/proj/QualMaggie/data/cache/"
}
```

Create the `dataInput/` folder if it doesn't exist:

```bash
mkdir dataInput
```

The `data/` subfolders are created automatically when the backend starts.

### i) Load Initial Price Data

```bash
# Terminal 1 — Start backend:
python -m uvicorn backend.main:app --reload

# Terminal 2 — Start frontend:
cd frontend
npm run dev
```

Open **http://localhost:3000/data** and:

1. Click **Import CSV Folder** — imports any CSVs already in `dataInput/`
2. Click **Update All Prices (yfinance)** — fills gaps for all active tickers
3. Click **Update Earnings Dates** — fetches next earnings dates

Or via script:

```bash
python scripts/update_prices.py
python scripts/update_earnings.py
```

After data is loaded, indicators (SMA, EMA, ATR, RS) are calculated automatically on import.
You now have a working system.

---

## 4. Daily Startup

Open four terminal windows:

**Terminal 1 — Backend**

```bash
cd C:\proj\QualMaggie
source .venv/Scripts/activate
python -m uvicorn backend.main:app --reload
```

**Terminal 2 — Frontend**

```bash
cd C:\proj\QualMaggie\frontend
npm run dev
```

**Terminal 3 — Claude Code (optional)**

```bash
cd C:\proj\QualMaggie
source .venv/Scripts/activate
claude
```

**Terminal 4 — Scripts**

```bash
cd C:\proj\QualMaggie
source .venv/Scripts/activate
# Run scripts from here as needed
```

Open browser: **http://localhost:3000**

---

## 5. Daily Operations

### Pre-Market Routine (Before 9:30 AM EST)

**Step 1 — Start the system**

```bash
# Terminal 1:
python -m uvicorn backend.main:app --reload

# Terminal 2:
cd frontend && npm run dev
```

Open http://localhost:3000

---

**Step 2 — Run the morning routine**

```bash
python scripts/morning_routine.py
```

This single command automatically runs all four stages:

| Stage | What It Does | Typical Duration |
|-------|-------------|-----------------|
| a) Update prices | yfinance gap-fill for all 1,700+ active tickers. Fills any missing days since last run. | 5–15 min |
| b) Update earnings | Fetches next earnings date for every ticker from yfinance. Powers the earnings hard-block filter. | 2–5 min |
| c) Run VCP scanner | Scans all tickers for breakout setups. Scores 0–10. Saves `signals_YYYY-MM-DD.csv` to `scripts/logs/`. | 3–8 min |
| d) DB health check | Flags stale/thin tickers. Reports table row counts. Saves health report to `scripts/logs/`. | < 1 min |

---

**Step 3 — Review scanner signals**

Open **http://localhost:3000** (Dashboard)

Review the top signals sorted by quality score. For each signal, verify:

- Setup quality score **>= 6** (minimum threshold)
- Risk/reward ratio **>= 2:1** (minimum)
- No earnings within **21 days**
- No macro events within **3 days**
- Market filter is **GREEN** (SPY above 50 SMA, 10 EMA above 20 EMA)

---

**Step 4 — Analyze top candidates**

For each signal scoring 7 or above:

- Pull up the chart on **TradingView.com**
- Confirm the visual VCP pattern (contractions visible, volume dried up)
- Check sector ETF strength (XLK for tech, XLV for health, etc.)
- Review recent news for any unexpected catalysts
- Confirm the prior move before the base was at least 30%
- Note the earnings date — exit before if within 21 days

---

**Step 5 — Plan trades**

For each trade you decide to take:

- Note the **entry price** (next day's open)
- Note the **stop loss price** (from signal card)
- Note the **position size** in shares (from signal card)
- Set price alerts on TradingView
- Note any earnings warning displayed on the signal card

---

### During Market Hours

**Step 6 — Monitor open positions**

Open **http://localhost:3000/positions** → Open tab

Check for:
- Any stop losses hit (close position in broker immediately)
- Any partial exit triggers (gain >= configured PartialSellMinProfitPct)
- Any earnings warnings that have appeared since entry
- Any macro event warnings (FOMC, CPI, NFP)

**Step 7 — Execute trades manually**

> The system generates **signals only**. You execute all trades in your broker (Robinhood, TD
> Ameritrade, Schwab, etc.). The system does not place orders automatically.

---

### Post-Market Routine (After 4:00 PM EST)

**Step 8 — Run daily maintenance (SSMS)**

Open SSMS, connect to localhost, open and run:

```
sql/03_daily_maintenance.sql
```

This refreshes SQL Server statistics and cleans stale index fragmentation. Takes < 1 minute.

**Step 9 — Review the day**

Open **http://localhost:3000/performance** to review:
- Any closed positions from today
- Running PnL and equity curve
- Trade distribution chart

---

### Weekly Tasks (Every Friday or Weekend)

```bash
# Download new Barchart CSVs for all tickers (manual, see Section 9)
# Drop CSVs into dataInput/ folder
# Then import via UI or script

# Refresh all earnings dates for the coming week:
python scripts/update_earnings.py

# Check database health:
python scripts/db_health_check.py

# Run a validation backtest on a recent year:
python scripts/run_backtest.py --start 2024-01-01 --end 2024-12-31 --name "Weekly check"
```

Review performance metrics vs SPY benchmark. If win rate or profit factor has degraded,
consider settings tuning.

---

### Monthly Tasks

```bash
# Export full trade history for a run:
python scripts/export_trades.py --run_id 45

# Run full backtest (takes 20-40 minutes):
python scripts/run_backtest.py --start 2020-01-01 --end 2024-12-31 --name "Full 2020-2024"
```

In SSMS, deactivate any delisted or permanently halted tickers:

```sql
UPDATE dbo.Tickers
SET IsActive = 0
WHERE Symbol IN ('DEADCO', 'DELISTEDX');
```

Add new tickers discovered from momentum screeners via the Data Management page or by dropping
CSVs into `dataInput/` and running Import CSV Folder.

---

### Key URLs

| URL | Page |
|-----|------|
| http://localhost:3000 | Dashboard — market status, scan trigger |
| http://localhost:3000/backtest | Backtest runner + equity curve + trades table |
| http://localhost:3000/positions | Open positions + history tab |
| http://localhost:3000/data | Data management — import, update, reset |
| http://localhost:3000/performance | Equity curve, monthly heatmap, trade distribution |
| http://localhost:3000/settings | Edit all settings.json values |
| http://localhost:8000/docs | FastAPI interactive API docs (Swagger UI) |

---

### External Tools Used Daily

| Tool | URL | Purpose |
|------|-----|---------|
| TradingView | tradingview.com | Chart analysis, visual pattern confirmation |
| Barchart | barchart.com | Historical OHLCV CSV downloads |
| Trading Economics | tradingeconomics.com/calendar | Macro events (FOMC, CPI, NFP) |
| Yahoo Finance | finance.yahoo.com | Earnings date verification |

---

### Trading Reminders

```
⚠️  NEVER trade in the first 15 minutes of market open (9:30–9:45 AM)
⚠️  ALWAYS check market filter before taking any new position
⚠️  NEVER average down on a losing position
⚠️  ALWAYS set the stop loss before entering
⚠️  EXIT before earnings if earnings are within 21 days
⚠️  MAXIMUM 5 open positions at any time
⚠️  MAXIMUM 2 positions in the same sector
⚠️  Only take new trades when SPY filter is GREEN
```

---

## 6. Project Structure

```
C:\proj\QualMaggie\
│
├── backend/
│   ├── api/
│   │   ├── routes_market.py        GET  /api/market/status
│   │   ├── routes_scan.py          POST /api/scan/run
│   │   ├── routes_positions.py     GET  /api/positions, /history, /trades
│   │   ├── routes_data.py          POST /api/data/import-csv, /update, /reset-backtests
│   │   ├── routes_performance.py   GET  /api/performance, /snapshots
│   │   ├── routes_backtest.py      POST /api/backtest/run; GET /api/backtest/{id}
│   │   └── routes_settings.py      GET/POST /api/settings
│   │
│   ├── core/
│   │   ├── indicators.py           SMA, EMA, ATR, ADR, RS — bulk UPDATE PriceData
│   │   ├── market_filter.py        SPY regime check (close>SMA50, EMA10>EMA20)
│   │   ├── stock_filter.py         8-condition universe filter
│   │   ├── pattern_detector.py     VCP detection (Flag/Cup stubs planned)
│   │   ├── scanner.py              Daily scan engine — market→stock→VCP→ranked
│   │   ├── position_manager.py     Trade lifecycle (open/partial/trail/close/blacklist)
│   │   └── backtester.py           Historical simulation loop
│   │
│   ├── db/
│   │   ├── models.py               SQLAlchemy 2.x ORM — 10 tables
│   │   └── database.py             Engine + session factory (get_db)
│   │
│   ├── services/
│   │   ├── data_importer.py        Barchart CSV → PriceData
│   │   ├── data_updater.py         yfinance gap-fill → PriceData
│   │   └── earnings_updater.py     yfinance earnings dates → EarningsDates
│   │
│   ├── config/
│   │   └── settings.py             Pydantic BaseSettings — loads settings.json + .env
│   │
│   └── main.py                     FastAPI app entry point
│
├── frontend/
│   └── app/
│       ├── layout.tsx / globals.css  Dark terminal theme (CSS variables)
│       ├── page.tsx                  Dashboard
│       ├── backtest/page.tsx         Backtest runner
│       ├── positions/page.tsx        Open positions + history
│       ├── data/page.tsx             Data management + DB reset
│       ├── performance/page.tsx      Equity curve + heatmap
│       └── settings/page.tsx        Settings editor
│
├── sql/
│   ├── 01_schema.sql               Run once — creates all 10 tables + column upgrades
│   ├── 02_seed_tickers.sql         Safe to re-run — inserts starter tickers via MERGE
│   ├── 03_daily_maintenance.sql    Run daily in SSMS after market close
│   ├── reset_backtests.sql         Soft reset — deletes backtest data, keeps prices
│   └── hard_reset.sql              ⚠️  DESTRUCTIVE — deletes everything
│
├── scripts/
│   ├── morning_routine.py          Master daily script — runs all 4 stages in order
│   ├── update_prices.py            yfinance gap-fill for all active tickers
│   ├── update_earnings.py          Refresh EarningsDates from yfinance
│   ├── run_scanner.py              VCP scan → signals CSV + console top 5
│   ├── run_backtest.py             Submit backtest via API, poll, save JSON
│   ├── db_health_check.py          Table counts + stale ticker detection
│   ├── export_trades.py            Export trades for a run_id to CSV
│   ├── fix_duplicate_trades.py     One-time dupe cleanup + UNIQUE constraint
│   ├── logs/                       Auto-created — YYYY-MM-DD named log files
│   └── README.txt                  Full argument reference for all scripts
│
├── data/
│   ├── cache/                      Cached files
│   └── exports/                    CSV exports from export_trades.py
│
├── dataInput/                      Drop Barchart CSVs here for import
│
├── settings.json                   All trading parameters (edit via UI or directly)
├── .env                            DB_CONNECTION_STRING secret (not committed)
├── requirements.txt                Python dependencies
├── CLAUDE.md                       AI assistant memory and architecture notes
└── DEV_README.md                   This file
```

---

## 7. Strategy Rules

### Market Filter

**All conditions must be true to take any new trade:**

| Condition | Rule |
|-----------|------|
| SPY trend | Close price must be above 50-day SMA |
| SPY momentum | 10-day EMA must be above 20-day EMA |

If either fails → No new positions that day. Existing positions continue to be managed.

---

### Stock Universe Filters

**All 8 conditions must pass:**

| Filter | Rule |
|--------|------|
| Price | > $20 |
| Volume | Average daily volume > 2,000,000 shares |
| Volatility | ATR% between 2% and 8% |
| Trend | Close price above 200-day SMA |
| Relative strength | Outperforming SPY over last 6 months (126 days) |
| Earnings | Positive EPS |
| Institutional | Institutional ownership > 30% |
| Index | Member of S&P 500 or Nasdaq 100 |

---

### VCP Pattern Rules

| Rule | Value |
|------|-------|
| Minimum contractions | 2 (prefer 3+) |
| Contraction tightness | Each range ≤ 25% of previous |
| Maximum contraction depth | 35% from high to low |
| Base duration | 10–60 calendar days |
| Volume requirement | Must dry up (declining avg volume through contractions) |
| Higher lows | Every swing low strictly above the previous (no exceptions) |
| Prior move | Stock must have moved 30%+ in the 126 days before the base started |
| Distance from pivot | Price must be within 3% below the last pivot high |

---

### Entry Rules

| Rule | Detail |
|------|--------|
| Signal day | VCP detected on Close of day N |
| Entry day | Open price of day N+1 |
| If N+1 unavailable | Skip trade (end of data) |
| Earnings block | Hard skip if earnings within 21 days |
| Macro warning | Skip if FOMC/CPI/NFP within 3 days |

---

### Position Sizing

```
risk_dollars  = portfolio_value × RiskPerTrade%
stop_distance = max(ADR × StopLossADRMultiplier,  entry × MinStopPct%)
shares        = floor(risk_dollars / stop_distance)
max_size      = (max_capital_deployed - already_deployed) / entry_price
actual_shares = min(shares, max_size)
```

---

### Exit Rules

| Trigger | Rule |
|---------|------|
| Hard stop | Close ≤ current stop → exit all remaining shares |
| Partial exit | Gain ≥ PartialSellMinProfitPct → sell 50% of shares |
| Trail (narrow) | After partial exit, gain ≥ TrailActivationPct → trail 10 EMA |
| Trail (wide) | Gain ≥ WideTrailActivationPct → switch to 20 EMA trail |
| Time exit — tight | Day 20 if gain < 5% |
| Time exit — extended | Day 60 if gain ≥ 5% but < 10% |
| Time exit — bypass | Gain ≥ 10% → no time limit, only trail or stop exits |

---

### Risk Controls

| Rule | Value |
|------|-------|
| Max concurrent positions | 5 |
| Max positions per sector | 2 |
| Max capital deployed | 50% of portfolio |
| After stop-out | Symbol blacklisted for 10 days |
| Re-entry after blacklist | Only on a fresh VCP setup |
| Averaging down | Never |

---

## 8. Settings Reference

All values live in `settings.json` in the project root. Edit via **http://localhost:3000/settings**
or directly in the file (backend must be restarted for the cache to clear after direct edits).

| Setting | Default | Description |
|---------|---------|-------------|
| PortfolioSize | 100000 | Starting capital in dollars |
| RiskPerTrade | 1.5 | Percent of portfolio risked per trade |
| MaxOpenPositions | 5 | Maximum concurrent open positions |
| MaxCapitalDeployedPct | 50 | Max percent of portfolio in market at once |
| StopLossADRMultiplier | 1.8 | Stop loss = ADR × this multiplier |
| MinStopPct | 4.0 | Minimum stop distance as % of entry price |
| PartialSellMinProfitPct | 2.0 | Gain % required to trigger 50% partial exit |
| TrailActivationPct | 2.0 | Gain % to activate 10 EMA trailing stop |
| WideTrailActivationPct | 8.0 | Gain % to switch from 10 EMA to 20 EMA trail |
| MaxHoldDays | 20 | Default maximum hold days |
| MaxHoldExtendedDays | 60 | Extended hold limit when gain ≥ MaxHoldExtendPct |
| MaxHoldExtendPct | 5.0 | Gain % required to extend hold to 60 days |
| MaxHoldBypassPct | 10.0 | Gain % to bypass all time limits entirely |
| MinStockPrice | 20 | Minimum stock price filter |
| MinAvgVolume | 2000000 | Minimum 20-day average daily volume |
| MinATRPct | 2.0 | Minimum ATR as percent of price |
| MaxATRPct | 8.0 | Maximum ATR as percent of price |
| MinInstitutionalOwnershipPct | 30.0 | Minimum institutional ownership % |
| EarningsWarningDays | 21 | Days before earnings that trigger hard block |
| EarningsHardBlock | true | Promotes earnings warning to hard filter |
| MacroEventWarningDays | 3 | Days before macro event to skip new entries |
| BlacklistDays | 10 | Days a symbol is banned after a stop-out |
| VCPMinContractions | 2 | Minimum number of VCP contractions |
| VCPPreferredContractions | 3 | Preferred contractions for preferred_quality flag |
| VCPTightnessFactorPct | 25 | Max % width of the final (tightest) contraction |
| VCPMaxDepthPct | 35 | Max % depth of any single contraction |
| VCPMinDaysInBase | 10 | Minimum calendar days for the base |
| VCPMaxDaysInBase | 60 | Maximum calendar days for the base |
| MinPriorMovePct | 30 | Minimum % move before the base started |
| MaxStopAsADRFraction | 0.67 | Reject if stop distance > ADR × this |
| MinSetupQualityScore | 6 | Minimum quality score (0–10) to surface a signal |
| RSLookbackDays | 126 | Lookback days for relative strength calculation (~6 months) |
| MarketFilterTicker | SPY | Ticker used for the market regime filter |

---

## 9. Data Management

### Downloading from Barchart

1. Go to **barchart.com** and log in
2. Search for a ticker (e.g. AAPL)
3. Click **Historical Data** tab
4. Select **Daily** frequency
5. Set your date range (ideally 2019-01-01 to today for full backtest warmup)
6. Click **Download** — saves as CSV
7. Multiple tickers can be combined into a single file
8. Drop the CSV into `C:\proj\QualMaggie\dataInput\`
9. In the dashboard → **Data Management** → **Import CSV Folder**

### Expected CSV Format (Barchart)

```
Symbol,Time,Open,High,Low,Last,Change,%Change,Volume
AAPL,04/25/2026,169.21,171.05,168.44,170.33,1.12,0.66%,52847300
...
```

| Field | Notes |
|-------|-------|
| `Last` | This is the Close price (renamed to `Close` on import) |
| Footer line | Automatically stripped (rows with unparseable dates) |
| Order | Newest-first in file → auto-reversed to oldest-first on insert |
| New tickers | Auto-created in `Tickers` table on import |
| Duplicates | Skipped via unique constraint — safe to re-import |

### Updating Prices via yfinance

```bash
# All active tickers (fills gaps from last known date):
python scripts/update_prices.py

# Single ticker only:
python scripts/update_prices.py --symbol AAPL

# Preview without writing to DB:
python scripts/update_prices.py --dry-run
```

### Updating Earnings Dates

```bash
python scripts/update_earnings.py
```

Fetches next 2 earnings dates per ticker from yfinance. Safe to re-run daily.

---

## 10. Troubleshooting

### Port 3000 already in use

```powershell
# PowerShell — find and kill the process:
Stop-Process -Id (Get-NetTCPConnection -LocalPort 3000).OwningProcess -Force
```

Then restart: `npm run dev`

### Port 8000 already in use

```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess -Force
```

Then restart uvicorn.

### uv pip vs pip confusion

```bash
# CORRECT — always use uv pip with venv activated:
source .venv/Scripts/activate
uv pip install <package>

# WRONG — installs to system Python, not the venv:
pip install <package>
```

### SQL Server connection fails

Verify `settings.json` DatabaseUrl:

```
mssql+pyodbc://localhost/QualMaggie?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes
```

Check that ODBC Driver 17 is installed:

```bash
python -c "import pyodbc; print([d for d in pyodbc.drivers() if 'SQL Server' in d])"
# Should print: ['ODBC Driver 17 for SQL Server']
```

### New route not picked up by uvicorn

After adding a new `routes_*.py` file to `main.py` imports:

```bash
# --reload does NOT auto-discover new imports
# Full restart required:
Ctrl+C  then  python -m uvicorn backend.main:app --reload
```

### Backtest taking too long

```bash
# Use a shorter range for testing:
python scripts/run_backtest.py --start 2024-01-01 --end 2024-12-31

# Full 2020–present backtest = 20–40 minutes on 1,700+ tickers
```

### yfinance rate limiting

The update script batches tickers and adds a 2-second delay between batches.
If errors persist, lower the batch size in `scripts/update_prices.py`:

```python
BATCH_SIZE = 25   # reduce from 50
```

### Decimal overflow error in backtest

Stale `OpenPositions` rows from a previous crashed run can cause numeric overflow.
Clean them up before retrying:

**Option A — via UI:**
`http://localhost:3000/data` → **Reset Backtests**

**Option B — via SSMS:**
Run `sql/reset_backtests.sql`

**Option C — via API:**
```bash
curl -X POST http://localhost:8000/api/data/reset-backtests
```

### CORS errors in browser console

Verify `backend/main.py` has CORS middleware configured:

```python
allow_origins=["http://localhost:3000"]
```

### `ModuleNotFoundError` when running scripts

The virtual environment is not active. Always run:

```bash
source .venv/Scripts/activate   # Git Bash
# or
.venv\Scripts\activate          # PowerShell
```

### Indicator calculation fails with DECIMAL overflow

The `ATRPct` and `RSScore` columns are `DECIMAL(10,6)` — only 4 integer digits allowed.
Stocks with extreme price history (penny stocks, massive RS scores) are auto-handled:
values outside `±9999.9999` are stored as `NULL` rather than crashing.
This is expected behavior — the affected rows show `NULL` indicators.

---

## 11. Backtest Guide

### Running a Backtest

**Option A — via UI:**

1. Open http://localhost:3000/backtest
2. Set start date, end date, run name
3. Click **Run Backtest**
4. Results appear when complete (timeout: 30 min)

**Option B — via script:**

```bash
python scripts/run_backtest.py \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --capital 100000 \
  --risk 1.5 \
  --name "V10 Full Backtest"
```

See `scripts/README.txt` for all available arguments.

### Understanding Backtest Results

| Metric | What It Means | Target |
|--------|--------------|--------|
| Total Return % | Overall gain or loss vs starting capital | > 0% |
| Win Rate % | Percentage of trades that made money | > 50% |
| Profit Factor | Gross wins ÷ gross losses | > 1.5 |
| Max Drawdown % | Worst peak-to-trough drop in equity | < 20% |
| Avg Win % | Average return on winning trades | — |
| Avg Loss % | Average loss on losing trades | Avg Win > Avg Loss |
| Sharpe Ratio | Risk-adjusted return (annualised) | > 1.0 |

A strategy is viable when: **Profit Factor > 1.5** and **Max Drawdown < 20%**.

### Strategy Version History

| Version | Key Change |
|---------|-----------|
| V1–V4 | Initial parameter development and tuning |
| V5 | ATR ceiling set to 8%; SOXL deactivated (ETF, no earnings data) |
| V6 | EarningsWarningDays increased from 10 → 21 days |
| V7 | FOMC/macro event dates added; MacroEventWarningDays = 5 |
| V8 | MacroEventWarningDays reduced from 5 → 3 days |
| V9 | Tiered MaxHold — extends profitable positions past day 20 |
| **V10** | StopLossADRMultiplier 1.5 → 1.8; MinStopPct 4.0% floor (current) |

### V9 Stable Baseline (for comparison)

| Year | Return | Trades | Win Rate | Profit Factor |
|------|--------|--------|----------|---------------|
| 2021 | -2.3% | 9 | 44% | 0.73 |
| 2022 | -4.9% | 4 | 50% | 0.08 |
| 2023 | -1.1% | 14 | 50% | 0.87 |
| 2024 | -1.6% | 10 | 60% | 0.75 |

### When to Reset Backtest Data

Run `sql/reset_backtests.sql` (or use the UI Reset Backtests button) when:

- Starting fresh with new strategy parameters
- Many failed/partial runs are cluttering the database
- You want identity sequences to restart from 1
- Pre-existing stale OpenPositions are causing Decimal overflow errors

> ⚠️ This deletes all backtest history. Price data and tickers are preserved.

---

## 12. Known Limitations

| Limitation | Detail |
|-----------|--------|
| EOD data only | No intraday signals — entries are at next-day open |
| Paper trading only | No broker integration in Phase 1 |
| Manual data downloads | Barchart CSVs must be downloaded by hand |
| Single machine | No cloud deployment in current form |
| Windows only | Path conventions and venv scripts are Windows-specific |
| yfinance rate limits | Bulk price updates throttled; takes 5–15 min for 1,700+ tickers |
| Backtest blocks API | Long backtests hold the FastAPI thread — run via script instead |
| No real-time prices | Dashboard reflects yesterday's close until prices are updated |

---

## 13. Future Roadmap

### Phase 2 — Live Paper Trading
- Alpaca broker API integration
- Real-time price feeds via WebSocket
- Automated signal → order execution
- Automated position tracking vs broker state

### Phase 3 — Enhanced Pattern Detection
- Flag pattern detector
- Cup & Handle detector
- Flat Base / Darvas Box detector
- Multi-pattern scoring and ranking

### Phase 4 — Scale & Automation
- Expand universe to 3,300+ tickers (full Nasdaq)
- Automated Barchart downloads (no manual CSV step)
- Scheduled morning routine (Windows Task Scheduler or cron)
- Email / SMS signal alerts

### Phase 5 — Cloud Deployment
- Docker containerisation
- Cloud-hosted SQL Server (Azure SQL)
- HTTPS web hosting
- Multi-user authentication

---

## 14. Contact & Support

| Field | Value |
|-------|-------|
| Built by | Venkat |
| Started | April 2026 |
| Strategy | Based on QualMaggie / Mark Minervini momentum approach |
| AI assistance | Claude Code (Anthropic) |
| Bug reports | Check `scripts/logs/` for detailed error logs |

For Claude Code AI assistance within this project:

```bash
source .venv/Scripts/activate
claude
```

Type `/help` inside Claude Code for available commands.
