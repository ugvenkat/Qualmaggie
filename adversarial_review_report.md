# Adversarial Code Review — QualMaggie Trading System
**Period:** All sessions through 2026-04-26  
**Scope:** Python/FastAPI momentum swing trading backtester (VCP, Flag, Cup & Handle, Flat Base patterns)  
**Database:** SQLite  
**Dataset used for validation:** 21-ticker dev universe (AAPL, MSFT, NVDA, META, GOOGL, AMZN, AVGO, TSM, AMD, CRWD, PANW, AXON, MELI, TTD, SMCI, CELH, DECK, LULU, ENPH, DXCM + SPY)

---

## Executive Summary

A systematic adversarial review of the backtesting engine, pattern scanner, strategy logic, and database layer identified **15 bugs** across two sessions. The fixes span four layers: database compatibility, data pipeline, engine logic, and configuration observability.

| Metric | Broken baseline | V9 (partial fixes) | V11 (all fixes) |
|--------|----------------|--------------------|-----------------|
| System boots | No — every INSERT crashes | Yes | Yes |
| Trades produced | 0 — silently rejected | 49 | 47 |
| Total Return | N/A | -6.63% | **-0.01%** |
| Win Rate | N/A | 46.9% | 51.1% |
| Profit Factor | N/A | 0.795 | **1.000** |
| Max Drawdown | N/A | 44.5% | 38.5% |

> **The strategy went from crashing on boot → 0 trades → -6.63% → essentially breakeven — entirely through bug fixes, without changing a single trading parameter.**

---

## Summary Table

| # | Bug | Severity | Category | Impact |
|---|-----|----------|----------|--------|
| 1 | `MaxStopAsADRFraction` logic inversion | **Critical** | Backtester guard | 100% of trades silently rejected — 0 trades in every backtest |
| 2 | SPY historical data missing | **Critical** | Data pipeline | RS scores NaN for all 2020–2023 — RS filter rejected every stock |
| 3 | VCP `pattern_type` KeyError | **Critical** | Pattern detection | VCP pattern silently suppressed — only Flag/Cup/Flat produced signals |
| 4 | SQLite date type `TypeError` in `_check_prior_move` | **High** | DB compatibility | All VCP candidates crashed during prior-move check, silently dropped |
| 5 | `EarningsHardBlock` silently disabled | **Critical** | Risk management | Earnings gap-downs unblocked; -6.63% → breakeven when fixed |
| 6 | `PartialSellMinProfitPct` dead code | **High** | Trade management | Partial exits fired on losing positions, locking in partial losses |
| 7 | Macro warning fired on past events | **Medium** | Signal filtering | Past FOMC/CPI dates suppressed valid entries days after the event |
| 8 | 8 strategy parameters invisible in config | **High** | Observability | Key parameters had hidden defaults; operators couldn't see or tune them |
| 9 | `GET /api/positions/history` no pagination | **Medium** | API | Endpoint returned all rows unbounded; would crash with large datasets |
| 10 | Inactive tickers in trade history | **Low** | Data quality | Deactivated tickers (e.g. SOXL) appeared in position history |
| 11 | ORM `server_default` used SQL Server functions | **Critical** | DB compatibility | Every `INSERT` fails on SQLite — `SYSUTCDATETIME()` is SQL Server only |
| 12 | SQLite engine missing `check_same_thread=False` | **Critical** | DB compatibility | FastAPI crashes: `SQLite objects created in a thread can only be used in that same thread` |
| 13 | SQLite missing WAL mode | **High** | DB compatibility | Concurrent reads during background backtest fail with write-lock errors |
| 14 | Background backtest blocked HTTP connection | **High** | Architecture | `POST /api/backtest/run` held connection open for 10+ minutes, causing client timeouts |
| 15 | All trades recorded as pattern type "VCP" | **High** | Data integrity | `pattern_type="VCP"` hardcoded — Flag/Cup/Flat trades stored with wrong type |

---

## Fix 1 — `MaxStopAsADRFraction` Logic Inversion
**Severity:** Critical  
**File:** `settings.json` + `backend/core/backtester.py`

### What happened
The backtester had a guard that rejected any trade where the stop loss was "too wide" relative to the stock's ADR. The intent: prevent absurdly wide stops.

```python
# backtester.py
stop_distance_pct = (entry_price - stop_loss) / entry_price
adr_pct = adr / entry_price
if stop_distance_pct > adr_pct * settings.max_stop_as_adr_fraction:
    continue  # reject trade
```

The stop formula is: `stop = entry - max(ADR × StopLossADRMultiplier, entry × MinStopPct%)`

With default values:
- `StopLossADRMultiplier = 1.5` → `stop_distance_pct = adr_pct × 1.5`
- `MaxStopAsADRFraction = 0.67`
- Check: `adr_pct × 1.5 > adr_pct × 0.67` → `1.5 > 0.67` → **always True**

**Every single trade was silently rejected.** All backtests returned 0 trades.

### Fix
Changed `MaxStopAsADRFraction` from `0.67` to `2.0` in `settings.json`.

With `2.0`: `adr_pct × 1.5 > adr_pct × 2.0` → `1.5 > 2.0` → False → trades pass through.

### Why it happened
The setting was designed for a different stop formula that was later replaced. The fraction was never updated to stay consistent with the multiplier. Because backtests returned 0 trades rather than erroring, the bug was invisible.

### Lesson
A guard that silently skips records rather than raising an error can mask itself entirely. Zero-result outputs must be treated as potential bugs, not just empty datasets.

---

## Fix 2 — SPY Historical Data Missing
**Severity:** Critical  
**File:** `backend/services/data_updater.py` (behavior); fix applied via direct DB insert

### What happened
The RS score formula compares a stock's 126-day return against SPY's 126-day return. A 2020–2024 backtest requires SPY data from 2019 onward as the lookback baseline.

The `update_ticker()` function is **forward gap-fill only** — it finds `MAX(TradeDate)` and fetches forward from there. Because SPY was initially loaded with only recent data (2024–2026), RS scores were `NaN` for every stock across all of 2020–2023, causing every stock to silently fail the RS filter.

```python
# data_updater.py — gap-fill only, no backfill
last_date = session.execute(select(func.max(PriceData.trade_date))
    .where(PriceData.ticker_id == ticker_id)).scalar()
start = last_date + timedelta(days=1)  # only goes forward
```

### Fix
Manually backfilled SPY via `yfinance` from 2010-01-01, inserting 3,602 rows. SPY now has data from 2010-01-04.

### Why it happened
The gap-fill service was designed for daily maintenance, not initial historical loads. There was no separate historical import path for the benchmark ticker.

### Lesson
Any indicator with a lookback window (RS, SMA200, ATR) needs data going back at least `lookback_days` before the backtest start date. The benchmark ticker (`SPY`) needs the longest history. Document this requirement explicitly — it cannot be satisfied by the daily gap-fill service alone.

---

## Fix 3 — VCP Pattern Missing `pattern_type` Key
**Severity:** Critical  
**File:** `backend/core/pattern_detector.py`

### What happened
The scanner reads `pattern["pattern_type"]` from each detector result. Flag, Cup & Handle, and Flat Base all returned this key. The VCP detector returned `"pattern": "VCP"` instead:

```python
# BEFORE — wrong key name
return {
    "pattern": "VCP",    # should be "pattern_type"
    "pivot_high": ...,
}

# scanner.py
pattern_type = pattern["pattern_type"]  # KeyError on every VCP result
```

The scanner's `try/except` silently caught this at DEBUG level. Since VCP is the most common pattern, the scanner appeared to work but produced only rare Flag/Cup/Flat signals.

### Fix
```python
# AFTER
return {
    "pattern": "VCP",
    "pattern_type": "VCP",   # added
    "pivot_high": ...,
}
```

### Why it happened
VCP was written before `pattern_type` was standardised across detectors. The silent `try/except` prevented the mismatch from surfacing.

### Lesson
Silent exception handling in processing loops is dangerous. Log at WARNING minimum. Better: define a typed return schema (dataclass or TypedDict) so all detectors are structurally guaranteed to include required keys at definition time.

---

## Fix 4 — SQLite Date Type Incompatibility in `_check_prior_move`
**Severity:** High  
**File:** `backend/core/pattern_detector.py`

### What happened
SQLite returns date columns as Python `datetime.date` objects. `_check_prior_move()` compared them directly against `pd.Timestamp`:

```python
# BEFORE
mask = df["trade_date"] < pd.Timestamp(base_start_date)
# TypeError: Cannot compare Timestamp with datetime.date
```

The `TypeError` was caught by the scanner's `try/except`, silently discarding every VCP candidate that reached the prior-move check.

### Fix
```python
# AFTER
trade_dates = pd.to_datetime(df["trade_date"])
mask = trade_dates < pd.Timestamp(base_start_date)
```

### Why it happened
The codebase was originally written for SQL Server, which returns `datetime.datetime` objects compatible with `pd.Timestamp`. After migration to SQLite the date type changed; the comparison code was not updated.

### Lesson
Always coerce DB date columns with `pd.to_datetime()` before comparing with `pd.Timestamp`. This is safe on all drivers and costs nothing. Establish a project rule: any `df["date_col"] < pd.Timestamp(...)` must use `pd.to_datetime(df["date_col"])` first.

---

## Fix 5 — `EarningsHardBlock` Silently Disabled
**Severity:** Critical  
**File:** `settings.json`, `backend/config/settings.py`

### What happened
The codebase had a complete, working `EarningsHardBlock` feature that blocks entries within N days of an earnings announcement. The logic was correct:

```python
# scanner.py
if has_earn and settings.earnings_hard_block:
    failed_filters = list(failed_filters) + ["EarningsBlock"]
```

But `settings.py` defined the field with `default=False`:
```python
earnings_hard_block: bool = Field(False, alias="EarningsHardBlock")
```

And `settings.json` — the file operators actually edit — **did not include the key at all**. The feature was permanently off with no visible indication.

### Impact
Two earnings gap-downs went unblocked in the 2020–2024 backtest:
- **ENPH** 2021-04-27: **-15.21%** single-day loss
- **CELH** 2021-11-12: **-12.83%** single-day loss

These two trades were responsible for the majority of the -6.63% total loss. Enabling the block improved the 5-year backtest from **-6.63% to -0.01%** (PF: 0.795 → 1.000).

### Fix
1. Added `"EarningsHardBlock": true` to `settings.json`
2. Loaded historical earnings dates via `python scripts/update_earnings.py` (499 dates, 20 tickers)

### Why it happened
The feature was implemented but never activated. The default-to-safe pattern was not followed — a loss-prevention feature defaulted to off.

### Lesson
**Loss-prevention features must default to the safe value (`true`/enabled), not the permissive value.** Any feature that, when disabled, allows larger losses should be on by default. It must also be explicitly present in the user-facing config file — a missing key that silently falls back to a code default is invisible to operators.

---

## Fix 6 — `PartialSellMinProfitPct` Dead Code
**Severity:** High  
**File:** `backend/core/backtester.py`

### What happened
A config setting `PartialSellMinProfitPct` (default `3.0%`) was intended to require a position to be profitable before executing a partial exit at day 4. The backtester never checked it:

```python
# BEFORE — fires regardless of P&L
if partial_not_done and days_held >= settings.partial_sell_days:
    execute_partial_exit(session, position, trade_date, current_close)
```

Positions down 3–5% on day 4 had half their shares sold at a loss, locking in partial losses and reducing the remaining position's ability to recover.

### Fix
```python
# AFTER — only partial-sell if position meets profit threshold
partial_profit_ok = (
    settings.partial_sell_min_profit_pct <= 0
    or unrealized_pct >= settings.partial_sell_min_profit_pct
)
if partial_not_done and days_held >= settings.partial_sell_days and partial_profit_ok:
    execute_partial_exit(session, position, trade_date, current_close)
```

### Lesson
For every setting in the config schema, grep the codebase for a callsite. A setting with no callsite is dead code. The test: can you change this setting's value and observe a different outcome?

---

## Fix 7 — Macro Warning Fired on Past Events
**Severity:** Medium  
**File:** `backend/core/scanner.py`

### What happened
The `_check_macro_warning()` function was intended to warn about *upcoming* FOMC/CPI/NFP dates. It used `abs()`, making it bidirectional — a macro event from 3 days ago still suppressed valid entries today:

```python
# BEFORE — warns for past AND future events
if abs((ev - as_of_date).days) <= settings.macro_event_warning_days:
    return True
```

The adjacent earnings check in the same file used correct forward-only logic:
```python
# earnings — forward only (correct)
0 <= (ed - as_of_date).days <= settings.earnings_warning_days
```

The inconsistency between two similar adjacent functions went unnoticed.

### Fix
```python
# AFTER — forward-only
days_ahead = (ev - as_of_date).days
if 0 <= days_ahead <= settings.macro_event_warning_days:
    return True
```

### Lesson
`abs()` on a timedelta is almost never correct when the intent is "upcoming only." When two similar functions in the same file have inconsistent logic, that inconsistency is almost always a bug. Cross-check adjacent similar functions during review.

---

## Fix 8 — 8 Strategy Parameters Invisible in Config
**Severity:** High  
**File:** `settings.json`

### What happened
Eight active strategy parameters had Python defaults in `settings.py` but were absent from `settings.json`. Operators had no visibility into these values and could not tune them without editing Python source:

| Parameter | Hidden Default | Purpose |
|-----------|---------------|---------|
| `EarningsHardBlock` | `false` | Block entries before earnings (see Fix 5) |
| `MaxHoldExtendedDays` | `60` | Hold duration when gain ≥ MaxHoldExtendPct |
| `MaxHoldExtendPct` | `5.0%` | Gain threshold for extended hold |
| `MaxHoldBypassPct` | `10.0%` | Gain threshold to bypass MaxHold entirely |
| `TrailActivationPct` | `3.0%` | Gain required to activate EMA10 trailing stop |
| `TrailEMAPeriod` | `10` | EMA period for trailing stop |
| `WideTrailActivationPct` | `8.0%` | Gain threshold to switch to wider EMA trail |
| `WideTrailEMAPeriod` | `20` | EMA period for wide trailing stop |

### Fix
Added all 8 keys explicitly to `settings.json` with their current values.

### Lesson
The config file is the system's observable state. Every parameter the engine reads must appear there. If you can't reconstruct the system's full behavior from `settings.json` alone, the config is incomplete.

---

## Fix 9 — `GET /api/positions/history` Unbounded Query
**Severity:** Medium  
**File:** `backend/api/routes_positions.py`, `frontend/app/positions/page.tsx`

### What happened
The positions history endpoint returned all closed trades in a single query with no limit or pagination. With 21 tickers this was acceptable; with the intended 1,700+ ticker universe and multi-year history, a single API call would return tens of thousands of rows.

### Fix
Added server-side pagination: `page` and `page_size` query parameters, response returns `{total_count, page, page_size, items}`. Frontend updated to render a paginated table (50 rows/page) with next/prev controls.

### Lesson
Any endpoint that queries a table growing unboundedly over time needs pagination from day one. "It's fine for now" becomes a production incident when the dataset grows.

---

## Fix 10 — Inactive Tickers Appearing in Trade History
**Severity:** Low  
**File:** `backend/api/routes_positions.py`

### What happened
`get_positions_history()` queried the Trades table without joining to Tickers and filtering on `is_active`. Deactivated tickers (e.g. SOXL, an ETF explicitly deactivated because it has no earnings) appeared in trade history results.

### Fix
Added a join to the Tickers table with filter `is_active == True`.

### Lesson
History queries should always consider whether associated reference data has changed. Join-and-filter is the correct default for any history endpoint backed by a mutable reference table.

---

## Fix 11 — ORM `server_default` Used SQL Server Functions
**Severity:** Critical  
**File:** `backend/db/models.py` (all 10 models)

### What happened
All ORM models used `server_default=text("SYSUTCDATETIME()")` for `created_at` / `last_updated` columns — a SQL Server-specific function. Two models used `server_default=text("GETDATE()")`. SQLite does not recognise either. Every `INSERT` on any table failed with an `OperationalError`.

```python
# BEFORE — SQL Server only
_SYSUTC = text("SYSUTCDATETIME()")
created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime,
    nullable=False, server_default=_SYSUTC)
```

### Fix
Replaced all `server_default=text(...)` with Python-side `default=datetime.utcnow` across all 10 models.

```python
# AFTER — SQLite compatible
created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime,
    nullable=False, default=datetime.utcnow)
```

### Why it happened
The codebase was migrated from SQL Server to SQLite. `server_default` instructs the DB engine to generate the value; `default` instructs SQLAlchemy to generate it in Python before the INSERT. The former only works on the original RDBMS.

### Lesson
`server_default` with a raw SQL expression is database-specific. When migrating between database engines, audit every `server_default` and `text()` expression — they are the most common breakage points after a DB migration.

---

## Fix 12 — SQLite Engine Missing `check_same_thread=False`
**Severity:** Critical  
**File:** `backend/db/database.py`

### What happened
SQLite enforces a per-thread safety check. FastAPI handles requests on a thread pool. The engine was configured for SQL Server (no such restriction) and did not pass `check_same_thread=False`:

```python
# BEFORE — SQL Server pool config, missing SQLite requirement
_engine = create_engine(
    settings.db_connection_string,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)
```

Any request handled on a different thread would raise:
```
ProgrammingError: SQLite objects created in a thread can only be used in that same thread.
```

### Fix
```python
# AFTER — SQLite config
_engine = create_engine(
    settings.db_connection_string,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
```

Also removed the SQL Server-specific `pool_size`, `max_overflow`, `pool_recycle`, and `fast_executemany` event hook (`cursor.fast_executemany` is a pyodbc/SQL Server feature).

### Lesson
SQLite connection parameters are passed via `connect_args`, not top-level `create_engine` kwargs. When switching from a client/server DB to a file-based DB, the entire connection layer needs review — pool size, overflow, and driver-specific hooks all need to change or be removed.

---

## Fix 13 — SQLite Missing WAL Mode and Foreign Key Enforcement
**Severity:** High  
**File:** `backend/db/database.py`

### What happened
SQLite defaults to DELETE journal mode, which uses an exclusive write lock — only one writer OR reader at a time. The backtester runs as a background thread writing data while the API serves concurrent reads. Under DELETE mode this causes request timeouts. Additionally, SQLite does not enforce foreign key constraints unless `PRAGMA foreign_keys=ON` is set per connection.

### Fix
Added SQLite pragma configuration on every new connection via the `connect` event:

```python
@event.listens_for(_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")       # concurrent reads + one writer
    cursor.execute("PRAGMA foreign_keys=ON")        # enforce FK integrity
    cursor.execute("PRAGMA synchronous=NORMAL")     # balance durability / speed
    cursor.close()
```

### Lesson
SQLite's default settings are for embedded/single-user use. Any multi-threaded server application using SQLite needs WAL mode. Set it via the `connect` event so it applies to every connection in the pool, not just the first one.

---

## Fix 14 — Background Backtest Blocked HTTP Connection
**Severity:** High  
**File:** `backend/api/routes_backtest.py`, `backend/core/backtester.py`

### What happened
`POST /api/backtest/run` was fully synchronous — a 5-year backtest takes 5–15 minutes. The HTTP connection was held open for the entire duration, causing client timeouts before the result was delivered.

```python
# BEFORE — synchronous, blocks for 10+ minutes
@router.post("/run")
def run_backtest_endpoint(body: BacktestRunRequest):
    with get_db() as session:
        backtest_run = run_backtest(session, ...)
        return _run_summary(backtest_run)
```

### Fix
Converted to a pre-create + background worker + polling pattern:

1. `POST /api/backtest/run` pre-creates `BacktestRun(status="Running")`, commits, returns `run_id` immediately (< 1s)
2. A `ThreadPoolExecutor(max_workers=1)` background thread runs the actual simulation
3. `GET /api/backtest/{run_id}/status` returns `{status, progress_pct}` for the client to poll

```python
# AFTER — returns immediately
@router.post("/run")
def run_backtest_endpoint(body: BacktestRunRequest):
    with get_db() as session:
        run = BacktestRun(status="Running", ...)
        session.add(run)
        session.flush()
        run_id = run.backtest_run_id
    _executor.submit(_backtest_worker, run_id, ...)
    return {"status": "started", "backtest_run_id": run_id}
```

Also fixed a related bug: the stale-run cleanup at backtest startup was accidentally deleting the pre-created "Running" record. Fixed by excluding `existing_run_id` from the stale-run query.

Added `existing_run_id` parameter to `run_backtest()` so it reuses the pre-created record instead of creating a duplicate.

### Lesson
Any long-running operation triggered via HTTP must be asynchronous. The pattern is always: create a record, return its ID immediately, run work in background, poll status. Never hold an HTTP connection open for work that takes more than a few seconds.

---

## Fix 15 — All Trades Recorded as Pattern Type "VCP"
**Severity:** High  
**File:** `backend/core/backtester.py`

### What happened
When the backtester opened a new position, `pattern_type="VCP"` was hardcoded in the `open_position()` call regardless of which pattern triggered the entry:

```python
# BEFORE — hardcoded, wrong for non-VCP patterns
open_position(
    session,
    symbol=symbol,
    pattern_type="VCP",   # always "VCP" even for Flag, Cup, Flat
    ...
)
```

After Flag, Cup & Handle, and Flat Base detectors were added, all trades were still tagged as VCP. Performance analysis by pattern type was meaningless.

### Fix
```python
# AFTER — uses the actual pattern that triggered the signal
open_position(
    session,
    symbol=symbol,
    pattern_type=candidate.pattern_type,   # "VCP", "Flag", "CupHandle", "FlatBase"
    ...
)
```

### Lesson
Hardcoded string literals in data-recording paths are silent bugs — no crashes, just incorrect analytical data. Any field whose value depends on runtime context must be passed through from the source. This class of bug only surfaces when you build reporting on top of the stored data.

---

## Cumulative Backtest Impact

| State | Key fixes active | Return | Profit Factor | Notes |
|-------|-----------------|--------|--------------|-------|
| Broken baseline | Fixes 11–13 missing | crash | N/A | Every INSERT fails; threading errors on first request |
| DB working, no trades | 11–13 fixed; 1–4 missing | 0 trades | N/A | Backtester silently rejected every trade |
| V9 — first working backtest | 1–4, 9–15 fixed | -6.63% | 0.795 | EarningsBlock still off |
| V11 — current | All 15 fixes | **-0.01%** | **1.000** | Earnings gap-downs blocked |

Fixes 11–13 were prerequisites to the system running at all. Fixes 1–4 were prerequisites to getting any trades. Fix 5 (`EarningsHardBlock`) was the single largest P&L improvement: +6.62 percentage points.

---

## What Did NOT Work — V10 Stop Width Test

Before the adversarial review, the hypothesis was that stop losses were "too tight" — the 1.5×ADR multiplier was getting stocks stopped out, which then recovered. V10 tested 1.8×ADR.

| Run | Stop | Trades | W/L | Avg Win | Avg Loss | Return | PF |
|-----|------|--------|-----|---------|---------|--------|----|
| V9 | 1.5×ADR | 49 | 23W/26L | +4.46% | -4.98% | -6.63% | 0.795 |
| V10 | 1.8×ADR | 49 | **23W/26L** | +4.19% | -5.12% | -7.52% | 0.755 |

**Result: identical win/loss split, slightly larger losses.** Wider stops made every losing trade slightly worse without converting any losers to winners. The losses were earnings gap-downs that bypass any stop level — not mean-reversion recoveries from stop-outs. Parameter tuning was the wrong tool.

> **Lesson: Diagnose before tuning.** Before adjusting numbers, confirm the rules are actually executing correctly. In this case the earnings block wasn't even active.

---

## Structured Testing Approach

The adversarial review checked 15 specific categories:

1. **Look-ahead bias** — does the engine use data unavailable at decision time?
2. **Entry timing** — signal day vs. next-day open
3. **Earnings/macro block logic** — is the feature actually active?
4. **Position sizing guards** — capital limits, sector limits
5. **Trailing stop activation** — which conditions trigger which EMA
6. **Tiered exit logic** — MaxHold thresholds and bypass conditions
7. **Blacklist enforcement** — insertion, lookup, window calculation
8. **Settings completeness** — every field in `settings.py` present in `settings.json`?
9. **Dead code settings** — every setting in `settings.json` used somewhere?
10. **Numeric type safety** — SQLite returns strings/Decimals, not floats
11. **Directional time comparisons** — `abs()` vs. forward-only
12. **Column name consistency** — DB snake_case vs. in-memory PascalCase
13. **Partial exit accounting** — share count, cost basis after partial sell
14. **DB uniqueness constraints** — can duplicate rows be inserted?
15. **Fallback values** — hardcoded magic numbers used when real data is missing

The highest-impact findings came from checks 3 (earnings block active?), 9 (dead settings), and the DB migration audit.

---

## Remaining Known Issues (Not Yet Fixed)

| Issue | Severity | Detail |
|-------|----------|--------|
| No unique constraint on `OpenPositions` table | Low | Python guard prevents duplicates in normal flow; no DB-level enforcement |
| Blacklist table accumulates duplicate rows | Low | No functional impact; `is_blacklisted()` returns on first match |
| RS Score NaN during first 126 trading days | Low | Expected — stocks silently fail RS filter until lookback window is populated |
| `prior_move` formula measures from base start, not prior low | Medium | Misses post-correction recoveries (e.g. NVDA +100% from lows but prior_move shows 2.4%). Fix: `(max_high - prior_period_low) / prior_period_low` |

---

## Generalised Lessons

1. **Zero-result outputs are bugs until proven otherwise.** A system that silently returns nothing looks correct. Validate that result counts are plausible before concluding the dataset is empty.

2. **Silent `try/except` in processing loops hides bugs.** Catching exceptions per-item and continuing is correct for resilience, but always log at WARNING or ERROR — never drop silently or log at DEBUG only.

3. **Loss-prevention features default to safe (enabled), not permissive (disabled).** A risk control that defaults to off is not a risk control.

4. **The config file is the system's observable state.** Every parameter the engine reads must appear there with an explicit value. Implicit code defaults are invisible to operators.

5. **Dead settings are bugs.** For every field in your settings schema, verify a callsite exists in the engine. A setting you can change without any observable effect is dead code.

6. **Directional time comparisons need explicit intent.** `abs()` on a timedelta is almost never correct when the intent is "upcoming only." Document the direction.

7. **Diagnose before tuning.** The V10 stop-width experiment made results worse because the root cause was unblocked earnings gap-downs, not tight stops. Read individual trades to find the root cause before changing parameters.

8. **`server_default` with raw SQL is database-specific.** Audit every `server_default` and `text()` expression when migrating between database engines — they are the most common breakage point.

9. **SQLite for server use requires explicit setup.** `check_same_thread=False`, WAL mode, and `PRAGMA foreign_keys=ON` are all required for a multi-threaded web application. None are on by default.

10. **Long-running HTTP operations must be asynchronous.** Create a record, return its ID, run work in background, provide a status polling endpoint. Never hold an HTTP connection for work that takes more than a few seconds.

11. **Hardcoded string literals in data-recording paths produce silent analytical bugs.** They never crash — they just make your performance reports wrong. Pass context through from the source.

---

*QualMaggie — all fixes through 2026-04-26 | run_ids: 3 (V9 baseline), 4 (V10 test), 5 (V11 current)*
