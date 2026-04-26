# QualMaggie — Deep Review: Strategy Fidelity, Gap Analysis & Adversarial Findings
**Date:** 2026-04-26  
**Reviewer:** Independent codebase analysis  
**Source strategy:** QualMaggie / Mark Minervini momentum methodology (transcript in `VenkatDocs/QualMaggie.txt`)  
**Note:** `QualMaggie.txt` is a speech-to-text transcription of a detailed YouTube video explaining the source trading methodology. It is the strategy specification this system is built to implement.

---

## What This Review Is

This review does three things previous reviews did not:

1. **Strategy fidelity audit** — cross-references `QualMaggie.txt` (the source methodology) against the actual implementation line by line. Several critical rules from the source strategy are either missing from the code or implemented differently than specified.

2. **Deep adversarial review** — examines the code for logic bugs, scale mismatches, and silent failures that are independent of the prior adversarial report (which was written for a different SQLite-based project and does not apply here).

3. **Configuration gap audit** — verifies which active parameters are missing from `settings.json`, using only what is confirmed by reading the actual files.

---

## Summary Table — All Issues

| # | Issue | Severity | Category |
|---|-------|----------|----------|
| 1 | `MaxStopAsADRFraction = 0.67` silently rejects all trades | **Critical** | Config bug |
| 2 | RS score scale mismatch in quality scorer — dimension 5 is non-functional | **Critical** | Logic bug |
| 3 | `EarningsHardBlock` missing from `settings.json` — defaults `False` | **Critical** | Config gap |
| 4 | No breakeven stop move after partial exit — key risk rule missing | **High** | Strategy gap |
| 5 | Stop method diverges from source strategy (ADR-based vs low-of-day) | **High** | Strategy fidelity |
| 6 | `RiskPerTrade = 1.5%` is 3× the source strategy's stated maximum | **High** | Strategy fidelity |
| 7 | Partial exit always sells exactly 50% — source says "1/3 to 1/2" | **High** | Strategy fidelity |
| 8 | SMA50 filter missing — source says "rarely buy below 50-day SMA" | **High** | Strategy gap |
| 9 | No per-position capital cap (25% max) — only aggregate cap exists | **High** | Strategy gap |
| 10 | 9 active settings missing from `settings.json` | **High** | Config gap |
| 11 | Trailing stop activates on profit threshold, not after partial exit timing | **Medium** | Strategy fidelity |
| 12 | EPS and Institutional Ownership filters always bypassed — 6-condition filter, not 8 | **Medium** | Strategy gap |
| 13 | Flag, Cup & Handle, Flat Base patterns not implemented — VCP only | **Medium** | Feature gap |
| 14 | Market filter uses SPY not NASDAQ Composite | **Medium** | Strategy fidelity |
| 15 | `BreakoutVolumeFactor = 1.0` disables volume confirmation | **Medium** | Strategy fidelity |
| 16 | Only 499 earnings dates for 1,723 tickers — block is toothless | **Medium** | Data quality |
| 17 | 14 stale tickers (up to 708 days) remain `IsActive = True` | **Medium** | Data quality |
| 18 | Backtest blocks FastAPI thread — documented known issue | **Medium** | Architecture |
| 19 | Positions History has no pagination | **Medium** | Stability |
| 20 | No backfill in `data_updater.py` — forward-only | **Medium** | Data pipeline |
| 21 | `VolumeMA20` not stored, recomputed per scan | **Low** | Performance |
| 22 | Macro event dates require manual SSMS entry | **Low** | Operational |
| 23 | No test suite exists | **Low** | Quality |
| 24 | `PartialSellDays` in `settings.json` is dead config | **Low** | Observability |
| 25 | `scripts/logs/` not gitignored — log files committed | **Low** | Repo hygiene |

---

## Part 1 — Strategy Fidelity (Source Strategy vs Implementation)

The `QualMaggie.txt` transcript is an auto-transcribed video explaining the Minervini/QualMaggie methodology in detail. The following findings compare what the source strategy specifies against what the code actually does.

---

### Issue 1 — `MaxStopAsADRFraction = 0.67` Silently Rejects All Trades
**Severity:** Critical  
**File:** `settings.json`, `backend/core/backtester.py`

The backtester guard before opening any position:

```python
stop_distance_pct = (entry_price - stop_loss) / entry_price   # = adr_pct × 1.5
adr_pct = adr / entry_price
if stop_distance_pct > adr_pct * settings.max_stop_as_adr_fraction:  # 1.5 > 0.67 → always True
    continue  # EVERY trade rejected
```

`settings.json` still has `"MaxStopAsADRFraction": 0.67` and `"StopLossADRMultiplier": 1.5`. The check `1.5 > 0.67` is always true, so every candidate is silently dropped. The backtest produces zero trades and exits normally. This is the single highest priority fix in the entire codebase.

**Fix:** `"MaxStopAsADRFraction": 2.0` in `settings.json`. Enforce invariant: `MaxStopAsADRFraction > StopLossADRMultiplier` always. For V10 (`StopLossADRMultiplier: 1.8`), set to at least `2.5`. Add a startup assertion that raises with a clear message if violated.

---

### Issue 2 — RS Score Scale Mismatch in Quality Scorer
**Severity:** Critical  
**File:** `backend/core/pattern_detector.py` (`_compute_quality_score`), `backend/core/stock_filter.py`

`indicators.py` computes RS as a ratio: `stock_return / spy_return`. A stock outperforming SPY by 30% has `rs_score = 1.3`. A stock matching SPY has `rs_score = 1.0`.

`stock_filter.passes_rs_filter()` correctly uses `rs_score > 1.0` — only stocks outperforming SPY pass.

But `_compute_quality_score()` dimension 5 uses:

```python
if rs_score > 0.10:      # 2 pts
    score += 2
elif rs_score >= 0.0:    # 1 pt
    score += 1
```

Any stock with `rs_score > 0.10` (i.e. returning even 10% of what SPY returned — a badly underperforming stock) scores 2 points. Since all candidates have already passed `rs_score > 1.0` from the stock filter, every single candidate gets the full 2 points from this dimension. The RS quality dimension is a constant — it adds no differentiation.

**Fix:**
```python
if rs_score > 1.10:     # outperforming SPY by >10% — strong RS
    score += 2
elif rs_score > 1.0:    # outperforming SPY by any amount — acceptable
    score += 1
# else: failing or equal — no points (this also helps if rs_score is pre-filtered)
```

---

### Issue 3 — `EarningsHardBlock` Missing from `settings.json` — Defaults False
**Severity:** Critical  
**File:** `settings.json`, `backend/core/scanner.py`

`EarningsHardBlock` is absent from `settings.json`. Its code default is `False`. With this value, the scanner's earnings check only sets `has_earnings_warning = True` — it does NOT add `"EarningsBlock"` to `failed_filters`. Since candidates are filtered by `if not r.failed_filters`, stocks about to report earnings appear as valid trade candidates.

**Fix:** Add `"EarningsHardBlock": true` to `settings.json`. Also verify earnings date coverage (Issue 16) before trusting the block to protect you.

---

### Issue 4 — No Breakeven Stop Move After Partial Exit
**Severity:** High  
**File:** `backend/core/backtester.py`, `backend/core/position_manager.py`  
**Source:** Transcript states explicitly and repeatedly: *"sell 1/3 or 1/2 after 3–5 days, then MOVE YOUR STOP TO BREAK EVEN"*

The source strategy has a clear two-step sequence:
1. Sell partial after 3–5 days → lock in some profit
2. **Immediately move the hard stop to breakeven (entry price)** → now the trade is risk-free

Looking at `execute_partial_exit()` in `position_manager.py`, it updates `partial_sold_shares` and `partial_sold_price`, but it does **not update `current_stop`**. The hard stop remains at the original ADR-based level below entry. There is no code anywhere that moves `current_stop` to `entry_price` after a partial exit.

**Impact:** After selling half the position at a profit, the remaining shares can still be stopped out at a loss below the original entry. The intent of the rule — that the trade becomes risk-free after partial exit — is not enforced. This is one of the core risk management mechanics of the Minervini methodology.

**Fix:** In `execute_partial_exit()`, after recording the partial sale, raise `position.current_stop` to `position.entry_price` (breakeven). The trailing stop then takes over from that floor rather than from the original hard stop.

```python
# After recording partial exit:
position.current_stop = max(float(position.current_stop), float(position.entry_price))
```

---

### Issue 5 — Stop Method: ADR-Based vs Low-of-Day
**Severity:** High  
**File:** `backend/core/position_manager.py` (`calculate_initial_stop`)  
**Source:** Transcript: *"The stop is ALWAYS lows of the entry day. Should not be wider than the ATR/ADR."*

The source strategy places the initial stop at the **low of the entry day (the breakout bar)**, then checks that this distance is not wider than the ADR. The stop is anchored to a specific price level with market structure meaning — the day the breakout fails is the natural invalidation point.

The code instead calculates the stop as a **fixed formula**: `stop = entry - max(ADR × 1.5, entry × MinStopPct%)`. This is an ADR-based stop that ignores the actual low of the breakout day. The stop could sit well below or above where the market structure actually puts it.

**Practical difference:** On a tight base where the low of the breakout day is only 0.8× ADR below entry, the source strategy would give a tighter stop. On a wide-ranging day, the source strategy would give a wider stop. The ADR formula ignores the actual price structure of the setup.

**Note:** This divergence may be a deliberate design choice (more consistent, avoids wide-ranging day entries). If intentional, it should be documented as a deviation from the source methodology. If not intentional, the correct fix is to load `low_price` from the signal day and use `stop = low_of_entry_day`, with the ADR as a maximum cap rather than the basis.

---

### Issue 6 — `RiskPerTrade = 1.5%` Is 3× the Source Strategy's Maximum
**Severity:** High  
**File:** `settings.json`  
**Source:** Transcript (multiple direct quotes): *"I rarely risk more than 0.5% on any trade"*, *"I'm risking 0.3 to 0.5 percent of my total account per trade"*, *"risking just 0.5% of total account equity per trade"*

The source strategy's defining risk management principle is ultra-small position risk — 0.3–0.5% of portfolio per trade. This allows the trader to be wrong many times while waiting for the home-run trades that pay for all the losses.

`settings.json` sets `"RiskPerTrade": 1.5` — three times the stated maximum.

**Mathematical consequence:** With a 3% stop loss and `RiskPerTrade = 1.5%`:
- Risk dollars = $1,500 on a $100k account
- Position size = $1,500 / (3% × entry price) = 50% of account in **one trade**

The system can hit `MaxCapitalDeployedPct = 50%` with a single position. After one trade, no more positions open until a partial or full exit. With 5 max positions and 50% deployment cap, at 1.5% risk each trade deploys ~10% of account, which is workable — but the risk per trade is still 3× the source methodology's intent.

**Fix:** Lower `RiskPerTrade` to `0.5` to match the source strategy. If the intent is a more aggressive approach, document the deliberate deviation. Also consider whether `MaxCapitalDeployedPct` should be raised to allow more positions to open at lower individual risk.

---

### Issue 7 — Partial Exit Always 50% — Source Says "1/3 to 1/2"
**Severity:** High  
**File:** `backend/core/position_manager.py` (`execute_partial_exit`)  
**Source:** Transcript: *"sell one third or one half of your position after three to five days"*

`execute_partial_exit()` always sells exactly `floor(shares / 2)` — always 50%. The source strategy explicitly allows for a 1/3 exit, which is less disruptive to a position in a strong trend. More critically, the exit timing is also wrong (see Issue 11 below).

**Fix:** Add a `partial_exit_fraction` setting (default `0.5`, allowing `0.33` for third-position sells). Apply it in `execute_partial_exit()`:

```python
partial_shares = math.floor(position.shares * settings.partial_exit_fraction)
```

---

### Issue 8 — SMA50 Filter Missing From Stock Filter
**Severity:** High  
**File:** `backend/core/stock_filter.py`  
**Source:** Transcript: *"I RARELY buy stocks below the 50-day moving average"*, *"you want the stronger momentum stocks which invariably when they're building the pivots are actually above their 50 day"*

The stock filter checks `close > SMA200` (correct — the 200-day filter is also part of the methodology), but has **no SMA50 check**. The SMA50 is explicitly called out in the source strategy as a key level — stocks below it are lower-quality setups.

`SMA50` is computed by `indicators.py` and stored in `PriceData`. It is loaded by the scanner (`PriceData.sma50.label("sma50")`) but only used for the market filter (SPY above SMA50). It is never checked against individual stock close prices in `stock_filter.py`.

**Fix:** Add `passes_sma50_filter(close, sma50)` to `stock_filter.py` and include it in `apply_stock_filters()`. This adds a meaningful quality gate that the source strategy explicitly emphasises.

---

### Issue 9 — No Per-Position Capital Cap (25% Max Per Stock)
**Severity:** High  
**File:** `backend/core/position_manager.py` (`can_open_position`)  
**Source:** Transcript: *"you shouldn't put more than 25% of your account in any given stock EVER"*

`can_open_position()` checks three things:
1. Total open positions < `max_open_positions` (5)
2. Sector count < `max_positions_per_sector` (2)
3. Total deployed < `max_capital_deployed_pct` (50%)

There is **no individual position cap**. A single position can consume the entire `MaxCapitalDeployedPct` (50% of account) if it's the first and only position. The source strategy's 25% per-stock maximum is a separate constraint from the aggregate deployment cap.

**Fix:** Add a `max_position_pct: float = Field(25.0, alias="MaxPositionPct")` setting and check in `can_open_position()`:

```python
if shares * entry_price > portfolio_value * (settings.max_position_pct / 100):
    return False, f"Position would exceed {settings.max_position_pct}% of account"
```

---

### Issue 10 — 9 Active Strategy Settings Missing From `settings.json`
**Severity:** High  
**File:** `settings.json`, `backend/config/settings.py`

These settings are live in every run via pydantic defaults but are invisible in `settings.json`:

| Setting | Silent Default | Correct Value |
|---------|---------------|---------------|
| `EarningsHardBlock` | `False` | `true` |
| `MinStopPct` | `0.0` | `4.0` (for V10) |
| `PartialSellMinProfitPct` | `3.0%` | (verify intent) |
| `TrailActivationPct` | `3.0%` | (verify intent) |
| `WideTrailActivationPct` | `8.0%` | (verify intent) |
| `TrailEMAPeriod` | `10` | `10` |
| `WideTrailEMAPeriod` | `20` | `20` |
| `MaxHoldExtendedDays` | `60` | `60` |
| `MaxHoldExtendPct` | `5.0%` | `5.0%` |
| `MaxHoldBypassPct` | `10.0%` | `10.0%` |

**Fix:** Add all missing keys to `settings.json`. Single file edit, no code changes needed.

---

### Issue 11 — Trailing Stop Timing Diverges From Source Strategy
**Severity:** Medium  
**File:** `backend/core/backtester.py`  
**Source:** Transcript: *"sell 1/3–1/2 AFTER THREE TO FIVE DAYS, then use the 10-day moving average as your trailing stop"*

The source strategy's timing is day-count driven: sell partial on day 3–5 then trail. The code instead triggers the partial exit when `unrealized_pct >= settings.partial_sell_min_profit_pct` (currently 3.0%) and then activates the trail when `unrealized_pct >= settings.trail_activation_pct` (3.0%). Both thresholds are identical by default, which means the trail activates simultaneously with the partial exit — not after a confirmed 3–5 day hold.

The `PartialSellDays` setting (= 4 in `settings.json`) references the `partial_exit_due()` method in the ORM model, but **the backtester never calls `partial_exit_due()`**. The day-count approach is coded but not wired in.

**Impact:** In a fast-moving stock that immediately gains 3% on day 1, both the partial exit and trailing stop activate immediately rather than waiting for the pattern to resolve. In a slow mover that takes weeks to reach 3%, the trade is held much longer than the 3–5 day rule would dictate.

**Fix option A (source-faithful):** Wire `partial_exit_due()` into the backtester loop. Sell partial at `day >= partial_sell_days` AND `unrealized_pct > 0` (only if profitable), then move stop to breakeven and activate trail.

**Fix option B (keep profit-based but align thresholds):** If the profit-based approach is preferred, ensure `PartialSellMinProfitPct` and `TrailActivationPct` are intentionally different values (e.g. partial at 3%, trail at 5%) so the trail activates after the partial, not simultaneously.

---

### Issue 12 — EPS and Institutional Ownership Filters Always Bypassed
**Severity:** Medium  
**File:** `backend/core/stock_filter.py`

The filter is documented and coded as 8 conditions including `Positive EPS` and `Institutional Ownership > 30%`. Neither field exists in the database. The scanner always passes `eps=None, inst_pct=None`, and both functions return `True` when passed `None`. Every stock passes these two checks unconditionally.

The source strategy strongly emphasises fundamentals — earnings growth is described as "rocket fuel" and the transcript devotes significant time to reading earnings acceleration. Institutional ownership is a proxy for smart money interest.

**Fix (pragmatic):** yfinance's `.info` dict returns `trailingEps` and `heldPercentInstitutions`. Add both to the `Tickers` table and a weekly-refresh script. Until then, remove these from the documented filter count and mark them as `# NOT ACTIVE — data not loaded` in code.

---

### Issue 13 — Flag, Cup & Handle, Flat Base Patterns Not Implemented
**Severity:** Medium  
**File:** `backend/core/pattern_detector.py`  
**Source:** Transcript: *"it's Flags, triangles, pennants, VCPs, Cup & Handles, Flat Bases, Darvas Boxes... the four trial patterns you'll see momentum stocks put in time and time again"*

The system is VCP-only. The transcript makes clear that Flags are often the most common and most actionable setup — simpler to identify, faster to play. The source trader explicitly says he built his returns across all four pattern types.

VCP-only scanning likely explains the low trade counts in backtests (4–14 trades/year). This is a Phase 3 roadmap item but has significant impact on signal frequency.

---

### Issue 14 — Market Filter Uses SPY Instead of NASDAQ Composite
**Severity:** Medium  
**File:** `backend/core/market_filter.py`, `settings.json`  
**Source:** Transcript: *"NASDAQ Composite is the relevant index... 90% of the stocks I trade are in the NASDAQ"*, *"you don't need to look at anything other than the NASDAQ"*

The market filter is applied against SPY (`MarketFilterTicker: "SPY"`), which tracks the S&P 500. The source strategy explicitly uses the NASDAQ Composite (ticker: `^IXIC` on yfinance) as the regime filter and explicitly rejects the S&P 500 as the relevant benchmark for this style of momentum trading.

The filter logic itself (close above SMA50 AND EMA10 > EMA20) is correct per the methodology — but it should be applied to the NASDAQ Composite, not SPY. These two indices can and do diverge, especially during tech-heavy bull markets when NASDAQ leads and SPY lags.

**Fix:** Change `"MarketFilterTicker": "SPY"` to `"MarketFilterTicker": "^IXIC"` in `settings.json`. Ensure `^IXIC` historical data is loaded via the update scripts (yfinance supports it). Note: the index has no earnings data to load, so `update_earnings.py` can skip it.

---

### Issue 15 — `BreakoutVolumeFactor = 1.0` Disables Volume Confirmation
**Severity:** Medium  
**File:** `settings.json`  
**Source:** Transcript: *"high volume around the pivot... bullish synchronicity where you get high relative volume and a widespread candlestick"*

`BreakoutVolumeFactor = 1.0` means any volume ≥ 1× average passes. The source strategy specifically looks for volume meaningfully above average as confirmation that institutional money is participating. Any positive volume qualifies with the current setting.

**Fix:** Set `"BreakoutVolumeFactor": 1.5` as a starting baseline. The transcript describes volume sometimes trading a full day's average in the first 15–30 minutes on a strong EP — suggesting 2× or higher is the ideal signal. Test 1.5 first.

---

## Part 2 — Technical / Adversarial Findings

---

### Issue 16 — Only 499 Earnings Dates for 1,723 Tickers
**Severity:** Medium

The DB health log shows `EarningsDates: 499` rows for 1,723 active tickers. 71% of tickers have no earnings date loaded. With `EarningsHardBlock = True`, stocks with no earnings date silently pass the block. The protection is absent for most of the universe.

**Fix:** Run `python scripts/update_earnings.py` and investigate why coverage is low. Flag stocks with no earnings date on the scan output so operators know protection is absent.

---

### Issue 17 — 14 Stale Tickers (Up to 708 Days Old) Remain `IsActive = True`
**Severity:** Medium

The DB health log shows 14 tickers flagged as stale, some over 700 days (BRP: 708 days, ARC: 520 days). These are likely delisted or halted stocks still included in scans and backtests. `db_health_check.py` detects them but takes no action.

**Fix:** Add auto-deactivation (`IsActive = False`) in `morning_routine.py` for tickers with no data in 60+ days.

---

### Issue 18 — Backtest Blocks FastAPI Thread
**Severity:** Medium  
**Also documented in:** `DEV_README.md` section 12

`POST /api/backtest/run` runs synchronously and blocks the entire server for the duration of the backtest (potentially 20–30 minutes for a full multi-year run). No other API endpoint can respond during this time.

**Fix:** Move `run_backtest()` to a background thread. Return `status="Running"` immediately. Let the frontend poll `GET /api/backtest/{id}`.

---

### Issue 19 — Positions History Has No Pagination
**Severity:** Medium  
**Also noted in:** `CLAUDE.md` known bugs

`GET /api/positions/history` returns all trades with no limit. Must be fixed before running the full 2020–2025 backtest which will produce hundreds of trades.

---

### Issue 20 — No Backfill in `data_updater.py`
**Severity:** Medium

`update_ticker()` only fills forward from `MAX(TradeDate)`. Adding new tickers or running after a hard reset leaves all historical data empty. RS scores will be `NaN` across the entire historical backtest period for any newly added ticker.

**Fix:** Add `--start-date` flag to `update_prices.py` and a `backfill_ticker()` function.

---

### Issue 21 — `VolumeMA20` Not Stored in DB
**Severity:** Low

`indicators.py` computes `VolumeMA20` but never stores it. Every scan recomputes it from raw volume. All other indicators are stored. This is inconsistent and wastes per-scan computation across 1,723 tickers.

---

### Issue 22 — Macro Event Dates Require Manual SSMS Entry
**Severity:** Low

No API, no script, no UI for adding FOMC/CPI/NFP dates. Requires direct DB access.

---

### Issue 23 — No Test Suite
**Severity:** Low

No `tests/` directory. Critical pure functions (`calculate_initial_stop`, `detect_vcp`, `apply_stock_filters`, the `MaxStopAsADRFraction` guard) have no automated coverage. Issues 1 and 2 in this report would have been caught by basic unit tests.

---

### Issue 24 — `PartialSellDays` in `settings.json` Is Dead Config
**Severity:** Low

`"PartialSellDays": 4` in `settings.json` implies day-4 partial exits. The backtester never calls `partial_exit_due()`. The actual trigger is `PartialSellMinProfitPct`. Remove or mark deprecated to avoid confusion.

---

### Issue 25 — `scripts/logs/` Not Gitignored
**Severity:** Low

`db_health_2026-04-25.txt` (86 KB) is committed. Add `scripts/logs/` to `.gitignore`.

---

## Part 3 — What The Code Gets Right (Strategy-Faithful Elements)

Before listing what to fix, it is worth noting what is correctly aligned with the source methodology:

- **EOD-only entry** — the backtester uses next-day open price for entry. Since this is a swing system based on daily charts, this is correct. The "opening range high" concept in the transcript applies to live intraday trading — the backtester's next-open approximation is the right simulation approach.
- **Trail on close, not intraday** — `check_exit_conditions()` uses the EOD `close_price`. The transcript is explicit: *"wait for the first CLOSE below the 10-day"*. Correct.
- **Hard stop → trail after partial exit** — the trail activates only after `partial_done` is true (line 411 in `position_manager.py`). This matches the sequence: initial hard stop → sell partial → trail remainder. The breakeven move (Issue 4) is the missing piece.
- **EMA10 and EMA20 for trailing** — the code uses EMA10 trail switching to EMA20 trail at `WideTrailActivationPct`. This matches the source: *"use the 10-day... switches to 20-day"* for bigger moves.
- **Higher lows required in VCP** — Fix 5 enforces strict higher swing lows in `_check_higher_lows()`. The transcript repeatedly emphasises this: *"orderly pullback in consolidation with HIGHER LOWS"*.
- **Prior move requirement** — `_check_prior_move()` enforces ≥30% run-up before the base. Source: *"a big move high in the past one to three months, anywhere from 30 to 100%+"*.
- **Volume dry-up in VCP** — the quality scorer checks volume in the final contraction vs the first contraction. Source: *"volume drying up... tells you there isn't much supply around"*.
- **Market regime filter** — SPY-based (ideally NASDAQ per Issue 14) EMA10 > EMA20 and close above SMA50. Structurally correct per the methodology.
- **Blacklist after stop-out** — 10-day re-entry ban after a stop-loss is architecturally correct and matches the *"fresh VCP required on re-entry"* principle.
- **Tiered MaxHold** — `MaxHoldBypassPct` (10%) disables the time cap for big winners. The transcript: *"no point selling at 20 if the stock goes up 300% — completely miss the point"*.
- **Sector limit** — max 2 positions per sector. The transcript warns against over-concentration in one sector/theme.

---

## Priority Action Plan

**Fix these before running any backtest or live scan:**

1. **`settings.json`** — single file edit resolves Issues 1, 3, and 10:
   - `"MaxStopAsADRFraction": 2.0`
   - `"EarningsHardBlock": true`
   - Add all 9 missing settings
   - `"RiskPerTrade": 0.5` (to match source strategy)
   - `"BreakoutVolumeFactor": 1.5`
   - `"MarketFilterTicker": "^IXIC"`

2. **Add breakeven stop after partial exit** (Issue 4) — one-line change in `execute_partial_exit()`. This is a core risk management rule from the source strategy.

3. **Fix RS quality score thresholds** (Issue 2) — change `rs_score > 0.10` to `rs_score > 1.10` in `_compute_quality_score()`. One-line change that makes quality scoring functional.

4. **Add SMA50 individual stock filter** (Issue 8) — add `passes_sma50_filter()` to `stock_filter.py` and wire into `apply_stock_filters()`.

**Before running the full 2020–2025 backtest:**

5. **Pagination for Positions History** (Issue 19)
6. **Earnings date coverage** (Issue 16) — run `update_earnings.py`
7. **Stale ticker deactivation** (Issue 17)

**Strategy tuning (after above are fixed):**

8. Run 2024 backtest with corrected settings to establish a clean V10 baseline
9. Implement Flag detection (Issue 13) — highest impact on signal frequency
10. Implement breakeven stop and run backtest again to measure impact on drawdown

---

*Report generated: 2026-04-26*
