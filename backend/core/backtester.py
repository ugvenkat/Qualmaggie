"""
Historical backtester for QualMaggie.

Error-handling design (SQL Server safe)
----------------------------------------
* NO savepoints — SQL Server puts the outer transaction into XACT_STATE=-1 on
  errors like DECIMAL overflow, making it uncommittable even after a savepoint
  rollback.  Instead every day is committed individually so a rollback only
  loses one day.
* Per-ticker try/except in position updates and new-position opens — one bad
  ticker never aborts the day.
* Per-day try/except in the main loop — one bad day never aborts the run.
* End-of-backtest close-out: per-position try/except.
* On any day failure: session.rollback(), reload open positions from DB,
  restore Python cash/portfolio from pre-day snapshot, continue.
* On unrecoverable crash: save partial results, set status="Partial", return
  normally so FastAPI sees 200 not 500.

Public API
----------
    run_backtest(session, start_date, end_date, run_name) -> BacktestRun
"""

from __future__ import annotations

import gc
import json
import logging
import math
import traceback as tb
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.core.indicators import compute_indicators
from backend.core.position_manager import (
    calculate_initial_stop,
    calculate_position_size,
    can_open_position,
    check_exit_conditions,
    close_position,
    execute_partial_exit,
    is_blacklisted,
    open_position,
    update_trailing_stop,
)
from backend.core.scanner import scan_daily
from backend.services.earnings_updater import load_earnings_by_symbol
from backend.db.database import get_engine
from backend.db.models import (
    BacktestRun,
    MacroEventDate,
    OpenPosition,
    PerformanceReport,
    PortfolioSnapshot,
    Ticker,
    Trade,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional psutil — soft import, falls back to gc.collect() only
# ---------------------------------------------------------------------------

try:
    import psutil as _psutil  # type: ignore[import]
    _HAS_PSUTIL = True
except ImportError:
    _psutil = None
    _HAS_PSUTIL = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COMMIT_EVERY_N_DAYS: int = 1          # commit after every day (safe on SQL Server)
_MEMORY_LOG_EVERY_N_DAYS: int = 100
_MEMORY_GC_THRESHOLD_PCT: float = 80.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Timezone-naive UTC datetime — safe for SQL Server DATETIME2 columns."""
    return datetime.utcnow()


def _check_memory(run_id: int, day_idx: int) -> None:
    if _HAS_PSUTIL:
        vm = _psutil.virtual_memory()
        rss_mb = _psutil.Process().memory_info().rss / 1024 / 1024
        logger.info(
            "memory | run_id=%d day=%d | rss=%.0f MB | system=%.1f%% used",
            run_id, day_idx, rss_mb, vm.percent,
        )
        if vm.percent >= _MEMORY_GC_THRESHOLD_PCT:
            logger.warning(
                "memory | run_id=%d: system %.1f%% ≥ threshold — gc.collect()",
                run_id, vm.percent,
            )
            gc.collect()
    else:
        gc.collect()


def _reload_open_positions(session: Session, backtest_run_id: int) -> list[OpenPosition]:
    """
    Re-query open positions from DB after a session.rollback().
    Required because the rollback may have wiped in-memory ORM objects.
    """
    try:
        return list(
            session.execute(
                select(OpenPosition).where(OpenPosition.backtest_run_id == backtest_run_id)
            ).scalars().all()
        )
    except Exception as exc:
        logger.error(
            "_reload_open_positions run_id=%d failed: %s", backtest_run_id, exc
        )
        return []


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_all_price_data(session: Session) -> tuple[dict[int, pd.DataFrame], dict[int, dict]]:
    from backend.db.models import PriceData

    settings = get_settings()
    engine = get_engine()

    ticker_rows = session.execute(
        select(
            Ticker.ticker_id,
            Ticker.symbol,
            Ticker.sector,
            Ticker.exchange,
            Ticker.index_membership,
        ).where(Ticker.is_active == True)  # noqa: E712
    ).all()

    ticker_meta: dict[int, dict] = {
        row.ticker_id: {
            "symbol": row.symbol,
            "sector": row.sector or "",
            "exchange": row.exchange or "",
            "index_membership": row.index_membership or "",
        }
        for row in ticker_rows
    }

    ticker_ids = list(ticker_meta.keys())
    if not ticker_ids:
        return {}, ticker_meta

    query = (
        select(
            PriceData.price_data_id.label("price_data_id"),
            PriceData.ticker_id.label("ticker_id"),
            PriceData.trade_date.label("trade_date"),
            PriceData.open_price.label("open_price"),
            PriceData.high_price.label("high_price"),
            PriceData.low_price.label("low_price"),
            PriceData.close_price.label("close_price"),
            PriceData.volume.label("volume"),
            PriceData.sma50.label("sma50"),
            PriceData.sma200.label("sma200"),
            PriceData.ema10.label("ema10"),
            PriceData.ema20.label("ema20"),
            PriceData.atr_pct.label("atr_pct"),
            PriceData.adr.label("adr"),
            PriceData.rs_score.label("rs_score"),
        )
        .where(PriceData.ticker_id.in_(ticker_ids))
        .order_by(PriceData.ticker_id, PriceData.trade_date)
    )
    with engine.connect() as conn:
        all_df = pd.read_sql(query, conn)

    all_df["trade_date"] = pd.to_datetime(all_df["trade_date"])

    spy_symbol = settings.market_filter_ticker.upper()
    spy_ticker_id: int | None = next(
        (tid for tid, m in ticker_meta.items() if m["symbol"] == spy_symbol), None
    )
    spy_df: pd.DataFrame | None = (
        all_df[all_df["ticker_id"] == spy_ticker_id].copy() if spy_ticker_id else None
    )

    ticker_dfs: dict[int, pd.DataFrame] = {}
    for ticker_id in ticker_meta:
        df = all_df[all_df["ticker_id"] == ticker_id].copy().reset_index(drop=True)
        if df.empty:
            continue
        try:
            df = compute_indicators(df, spy_df=spy_df, rs_lookback=settings.rs_lookback_days)
        except Exception as exc:
            logger.warning("compute_indicators failed for ticker_id=%d: %s", ticker_id, exc)
        ticker_dfs[ticker_id] = df

    logger.info(
        "_load_all_price_data: loaded %d tickers, %d total rows",
        len(ticker_dfs), len(all_df),
    )
    return ticker_dfs, ticker_meta


# ---------------------------------------------------------------------------
# Per-day simulation
# ---------------------------------------------------------------------------

def _process_day(
    session: Session,
    trade_date: date,
    ticker_dfs: dict[int, pd.DataFrame],
    ticker_meta: dict[int, dict],
    open_positions: list[OpenPosition],
    cash: float,
    portfolio_value: float,
    backtest_run_id: int,
    settings,
    error_state: dict,
    earnings_dates: dict[str, list] | None = None,
    macro_event_dates: list | None = None,
) -> tuple[list[OpenPosition], float, float]:
    """
    Process one trading day.  All per-ticker steps are individually wrapped
    in try/except so a single bad ticker never aborts the day.

    Raises only for genuinely unrecoverable failures (e.g. the snapshot
    INSERT itself fails) — those are caught in the caller.
    """
    trade_date_ts = pd.Timestamp(trade_date)

    # ------------------------------------------------------------------
    # A. Update existing positions  (per-position try/except)
    # ------------------------------------------------------------------
    for position in list(open_positions):
        try:
            if position.ticker_id not in ticker_dfs:
                continue

            ticker_df = ticker_dfs[position.ticker_id]
            today_rows = ticker_df[ticker_df["trade_date"] == trade_date_ts]
            if today_rows.empty:
                continue

            today = today_rows.iloc[0]
            current_close = float(today["close_price"])

            def _get_ema(col: str) -> Optional[float]:
                val = today.get(col) if col in today.index else None
                if val is None:
                    return None
                try:
                    f = float(val)
                    return None if math.isnan(f) else f
                except (TypeError, ValueError):
                    return None

            ema_col      = f"EMA{settings.trail_ema_period}"
            wide_ema_col = f"EMA{settings.wide_trail_ema_period}"
            current_ema10 = _get_ema(ema_col)
            current_ema20 = _get_ema(wide_ema_col)

            entry_date = position.entry_date
            if hasattr(entry_date, "date"):
                entry_date = entry_date.date()
            days_held = (trade_date - entry_date).days

            entry_price_f  = float(position.entry_price)
            unrealized_pct = (current_close - entry_price_f) / entry_price_f * 100

            partial_not_done = (
                not position.partial_sold_shares or position.partial_sold_shares == 0
            )
            if partial_not_done and unrealized_pct >= settings.partial_sell_min_profit_pct:
                execute_partial_exit(session, position, trade_date, current_close)
                cash += (position.partial_sold_shares or 0) * current_close

            partial_done = (
                position.partial_sold_shares is not None
                and position.partial_sold_shares > 0
            )
            active_trail_ema: Optional[float] = None
            if partial_done:
                if unrealized_pct >= settings.wide_trail_activation_pct and current_ema20 is not None:
                    update_trailing_stop(position, current_ema20)
                    active_trail_ema = current_ema20
                elif unrealized_pct >= settings.trail_activation_pct and current_ema10 is not None:
                    update_trailing_stop(position, current_ema10)
                    active_trail_ema = current_ema10

            should_exit, reason = check_exit_conditions(
                position, current_close, active_trail_ema, days_held, settings,
                unrealized_pct=unrealized_pct,
            )
            if should_exit:
                remaining = position.shares - (position.partial_sold_shares or 0)
                close_position(session, position, trade_date, current_close, reason)
                cash += remaining * current_close
                open_positions.remove(position)

        except Exception as exc:
            error_state["tickers_with_errors"] += 1
            logger.error(
                "_process_day(%s): position update FAILED for %s — skipping ticker:\n%s",
                trade_date, getattr(position, "symbol", "?"), tb.format_exc(),
            )

    # ------------------------------------------------------------------
    # B. Run scanner (already has per-ticker protection internally)
    # ------------------------------------------------------------------
    scan = scan_daily(
        session,
        as_of_date=trade_date,
        backtest_run_id=backtest_run_id,
        preloaded_dfs=ticker_dfs,
        earnings_dates=earnings_dates,
        macro_event_dates=macro_event_dates,
    )

    # ------------------------------------------------------------------
    # C. Flush so Blacklist inserts are visible, then open new positions
    #    (per-candidate try/except)
    # ------------------------------------------------------------------
    session.flush()

    if scan["market_healthy"]:
        symbol_to_id = {meta["symbol"]: tid for tid, meta in ticker_meta.items()}
        open_symbols  = {p.symbol for p in open_positions}

        for candidate in scan["candidates"]:
            try:
                symbol    = candidate.symbol
                ticker_id = symbol_to_id.get(symbol)
                if ticker_id is None:
                    continue
                if symbol in open_symbols:
                    continue
                if is_blacklisted(symbol, session, trade_date, backtest_run_id):
                    continue
                if ticker_id not in ticker_dfs:
                    continue

                ticker_df = ticker_dfs[ticker_id]

                # Fix 1: Enter at NEXT trading day's open, not today's close.
                # Signal detected on trade_date (day N); entry is day N+1 open.
                signal_rows = ticker_df[ticker_df["trade_date"] == trade_date_ts]
                if signal_rows.empty:
                    continue
                signal_day = signal_rows.iloc[0]

                next_day_rows = ticker_df[ticker_df["trade_date"] > trade_date_ts].head(1)
                if next_day_rows.empty:
                    continue  # end of data — no next-day open available
                next_day = next_day_rows.iloc[0]

                entry_price = float(next_day["open_price"])
                entry_date_val = next_day["trade_date"]
                entry_date = (
                    entry_date_val.date()
                    if hasattr(entry_date_val, "date")
                    else entry_date_val
                )

                # ADR from signal day — represents volatility at time of signal
                adr_val = signal_day.get("ADR") if "ADR" in signal_day.index else signal_day.get("adr")
                adr = (
                    float(adr_val)
                    if adr_val is not None and not math.isnan(float(adr_val))
                    else entry_price * 0.02
                )

                stop_loss = calculate_initial_stop(entry_price, adr, settings)

                # Fix 3: Reject if stop is too wide relative to ADR.
                # stop_distance_pct > adr_pct × MaxStopAsADRFraction → skip.
                stop_distance_pct = (entry_price - stop_loss) / entry_price
                adr_pct = adr / entry_price
                if stop_distance_pct > adr_pct * settings.max_stop_as_adr_fraction:
                    logger.debug(
                        "_process_day(%s): skip %s — stop %.2f%% > %.2f× ADR (%.2f%%)",
                        trade_date, symbol,
                        stop_distance_pct * 100,
                        settings.max_stop_as_adr_fraction,
                        adr_pct * 100,
                    )
                    continue

                already_deployed = sum(p.shares * entry_price for p in open_positions)
                shares = calculate_position_size(
                    entry_price, stop_loss, portfolio_value, already_deployed, settings
                )
                if shares <= 0:
                    continue

                ok, reason = can_open_position(
                    open_positions, candidate.sector, entry_price, shares,
                    portfolio_value, settings,
                )
                if not ok:
                    logger.debug("Cannot open %s: %s", symbol, reason)
                    continue

                risk_amount = shares * (entry_price - stop_loss)
                position = open_position(
                    session=session,
                    ticker_id=ticker_id,
                    symbol=symbol,
                    sector=candidate.sector,
                    entry_date=entry_date,
                    entry_price=entry_price,
                    shares=shares,
                    stop_loss=stop_loss,
                    pattern_type="VCP",
                    risk_amount=round(risk_amount, 2),
                    is_live=False,
                    backtest_run_id=backtest_run_id,
                )
                open_positions.append(position)
                open_symbols.add(symbol)
                cash -= shares * entry_price

            except Exception:
                error_state["tickers_with_errors"] += 1
                logger.error(
                    "_process_day(%s): open position FAILED for %s — skipping:\n%s",
                    trade_date, getattr(candidate, "symbol", "?"), tb.format_exc(),
                )

    # ------------------------------------------------------------------
    # D. Portfolio snapshot
    # ------------------------------------------------------------------
    open_value = 0.0
    for position in open_positions:
        if position.ticker_id not in ticker_dfs:
            continue
        pos_df = ticker_dfs[position.ticker_id]
        pos_today = pos_df[pos_df["trade_date"] == trade_date_ts]
        if not pos_today.empty:
            open_value += position.shares * float(pos_today.iloc[0]["close_price"])

    portfolio_value = cash + open_value
    snapshot = PortfolioSnapshot(
        snapshot_date=trade_date,
        total_value=round(portfolio_value, 2),
        cash_balance=round(cash, 2),
        open_positions_value=round(open_value, 2),
        open_positions_count=len(open_positions),
        daily_pnl=0.0,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        is_live=False,
        backtest_run_id=backtest_run_id,
    )
    session.add(snapshot)

    return open_positions, cash, portfolio_value


# ---------------------------------------------------------------------------
# Performance report
# ---------------------------------------------------------------------------

def _generate_performance_report(
    session: Session,
    backtest_run_id: int,
    initial_capital: float,
    final_capital: float,
    settings,
) -> PerformanceReport:
    trades = session.execute(
        select(Trade)
        .where(Trade.backtest_run_id == backtest_run_id)
        .where(Trade.exit_date != None)  # noqa: E711
    ).scalars().all()

    pnls   = [float(t.pnl) for t in trades if t.pnl is not None]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_trades   = len(pnls)
    winning_trades = len(wins)
    losing_trades  = len(losses)
    win_rate       = winning_trades / total_trades if total_trades > 0 else 0.0
    avg_win_pct    = (
        sum(t.pnl_pct for t in trades if t.pnl is not None and t.pnl > 0) / winning_trades
        if winning_trades > 0 else 0.0
    )
    avg_loss_pct   = (
        sum(t.pnl_pct for t in trades if t.pnl is not None and t.pnl <= 0) / losing_trades
        if losing_trades > 0 else 0.0
    )
    profit_factor  = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 0.0
    total_return_pct = (final_capital - initial_capital) / initial_capital if initial_capital > 0 else 0.0

    snapshots = session.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.backtest_run_id == backtest_run_id)
        .order_by(PortfolioSnapshot.snapshot_date)
    ).scalars().all()

    max_drawdown_pct = 0.0
    sharpe_ratio     = 0.0
    if snapshots:
        values = [float(s.total_value) for s in snapshots]
        peak = values[0]
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd
        daily_vals = np.array(values, dtype=float)
        if len(daily_vals) > 1:
            daily_returns = np.diff(daily_vals) / daily_vals[:-1]
            mean_r = float(np.mean(daily_returns))
            std_r  = float(np.std(daily_returns, ddof=1))
            sharpe_ratio = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

    report = PerformanceReport(
        report_date=date.today(),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=round(win_rate, 4),
        avg_win_pct=round(avg_win_pct, 4),
        avg_loss_pct=round(avg_loss_pct, 4),
        profit_factor=round(profit_factor, 4),
        max_drawdown_pct=round(max_drawdown_pct, 4),
        sharpe_ratio=round(sharpe_ratio, 4),
        total_return_pct=round(total_return_pct, 4),
        is_live=False,
        backtest_run_id=backtest_run_id,
    )
    session.add(report)
    return report


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backtest(
    session: Session,
    start_date: date,
    end_date: date,
    run_name: str = "Backtest",
) -> BacktestRun:
    """
    Run a historical backtest.

    Return status values
    --------------------
    Completed           all days processed cleanly
    CompletedWithErrors all days processed but some had recoverable errors
    Partial             crashed mid-run; committed trades are preserved
    """
    settings = get_settings()

    # ------------------------------------------------------------------
    # 0. Clean up orphaned OpenPositions from incomplete prior runs.
    #    Covers 'Partial' (crashed), 'Running' (server killed mid-run),
    #    and 'Failed' (future-proofed).  Wrapped so a cleanup failure
    #    never blocks the new run from starting.
    # ------------------------------------------------------------------
    try:
        stale_ids = session.execute(
            select(BacktestRun.backtest_run_id).where(
                BacktestRun.status.in_(["Partial", "Running", "Failed"])
            )
        ).scalars().all()
        if stale_ids:
            result = session.execute(
                delete(OpenPosition).where(
                    OpenPosition.backtest_run_id.in_(stale_ids)
                )
            )
            session.commit()
            logger.info(
                "run_backtest: removed %d orphaned OpenPositions from %d stale run(s) %s",
                result.rowcount, len(stale_ids), list(stale_ids),
            )
    except Exception as exc:
        logger.warning(
            "run_backtest: stale OpenPositions cleanup failed (continuing): %s", exc
        )
        try:
            session.rollback()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 1. Create BacktestRun record
    # ------------------------------------------------------------------
    backtest_run = BacktestRun(
        run_name=run_name,
        start_date=start_date,
        end_date=end_date,
        initial_capital=settings.portfolio_size,
        status="Running",
        settings_json=json.dumps(settings.model_dump(mode="json"), default=str),
    )
    session.add(backtest_run)
    session.flush()
    backtest_run_id: int = backtest_run.backtest_run_id
    session.commit()

    logger.info(
        "run_backtest: started run_id=%d '%s' %s → %s",
        backtest_run_id, run_name, start_date, end_date,
    )

    error_state: dict = {"days_with_errors": 0, "tickers_with_errors": 0}
    portfolio_value: float = settings.portfolio_size
    cash: float = portfolio_value
    open_positions: list[OpenPosition] = []
    day_idx: int = -1

    try:
        # ------------------------------------------------------------------
        # 2. Load all price data into memory
        # ------------------------------------------------------------------
        ticker_dfs, ticker_meta = _load_all_price_data(session)

        # ------------------------------------------------------------------
        # 2b. Earnings + macro dates
        # ------------------------------------------------------------------
        earnings_dates = load_earnings_by_symbol(session)

        macro_rows = session.execute(
            select(MacroEventDate.event_date).order_by(MacroEventDate.event_date)
        ).scalars().all()
        macro_event_dates = [d.date() if hasattr(d, "date") else d for d in macro_rows]
        logger.info(
            "run_backtest run_id=%d: %d macro dates loaded", backtest_run_id, len(macro_event_dates),
        )

        # ------------------------------------------------------------------
        # 3. Collect trading dates
        # ------------------------------------------------------------------
        all_dates: set[date] = set()
        for df in ticker_dfs.values():
            in_range = df[
                (df["trade_date"] >= pd.Timestamp(start_date))
                & (df["trade_date"] <= pd.Timestamp(end_date))
            ]["trade_date"]
            all_dates.update(d.date() if hasattr(d, "date") else d for d in in_range)

        trading_dates = sorted(all_dates)
        logger.info(
            "run_backtest run_id=%d: %d trading dates", backtest_run_id, len(trading_dates),
        )

        # ------------------------------------------------------------------
        # 4. Simulation loop — per-day try/except + per-day commit
        # ------------------------------------------------------------------
        for day_idx, trade_date in enumerate(trading_dates):

            # Snapshot Python state so we can recover if this day fails
            positions_before  = list(open_positions)
            cash_before       = cash
            portfolio_before  = portfolio_value

            try:
                open_positions, cash, portfolio_value = _process_day(
                    session=session,
                    trade_date=trade_date,
                    ticker_dfs=ticker_dfs,
                    ticker_meta=ticker_meta,
                    open_positions=open_positions,
                    cash=cash,
                    portfolio_value=portfolio_value,
                    backtest_run_id=backtest_run_id,
                    settings=settings,
                    error_state=error_state,
                    earnings_dates=earnings_dates,
                    macro_event_dates=macro_event_dates,
                )
            except Exception:
                error_state["days_with_errors"] += 1
                logger.error(
                    "run_backtest run_id=%d: day %s FAILED (error #%d) — "
                    "rolling back and continuing:\n%s",
                    backtest_run_id, trade_date,
                    error_state["days_with_errors"],
                    tb.format_exc(),
                )
                try:
                    session.rollback()
                except Exception:
                    pass
                # Reload positions from DB (consistent with last commit)
                open_positions = _reload_open_positions(session, backtest_run_id)
                cash           = cash_before
                portfolio_value = portfolio_before
                continue

            # Commit this day's changes (every _COMMIT_EVERY_N_DAYS days)
            if (day_idx + 1) % _COMMIT_EVERY_N_DAYS == 0:
                try:
                    session.commit()
                except Exception as commit_exc:
                    logger.error(
                        "run_backtest run_id=%d: commit failed at day %s: %s",
                        backtest_run_id, trade_date, commit_exc,
                    )
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    open_positions = _reload_open_positions(session, backtest_run_id)

            # Memory check
            if (day_idx + 1) % _MEMORY_LOG_EVERY_N_DAYS == 0:
                _check_memory(backtest_run_id, day_idx + 1)

        # Final commit for any remaining uncommitted days
        try:
            session.commit()
        except Exception as exc:
            logger.error("run_backtest run_id=%d: final commit failed: %s", backtest_run_id, exc)
            session.rollback()

        # ------------------------------------------------------------------
        # 5. Close still-open positions at end_date  (per-position try/except)
        # ------------------------------------------------------------------
        last_date = trading_dates[-1] if trading_dates else end_date
        for position in list(open_positions):
            try:
                if position.ticker_id not in ticker_dfs:
                    continue
                last_ts   = pd.Timestamp(last_date)
                last_rows = ticker_dfs[position.ticker_id][
                    ticker_dfs[position.ticker_id]["trade_date"] == last_ts
                ]
                if last_rows.empty:
                    continue
                last_close = float(last_rows.iloc[0]["close_price"])
                remaining  = position.shares - (position.partial_sold_shares or 0)
                close_position(session, position, last_date, last_close, "EndOfBacktest")
                cash += remaining * last_close
            except Exception:
                logger.error(
                    "run_backtest run_id=%d: end-of-backtest close FAILED for %s:\n%s",
                    backtest_run_id, getattr(position, "symbol", "?"), tb.format_exc(),
                )

        try:
            session.commit()
        except Exception as exc:
            logger.error(
                "run_backtest run_id=%d: end-of-backtest commit failed: %s",
                backtest_run_id, exc,
            )
            session.rollback()

        portfolio_value = cash

        # ------------------------------------------------------------------
        # 6. Performance report
        # ------------------------------------------------------------------
        report = _generate_performance_report(
            session, backtest_run_id, settings.portfolio_size, portfolio_value, settings
        )
        session.flush()

        # ------------------------------------------------------------------
        # 7. Finalise
        # ------------------------------------------------------------------
        days_err    = error_state["days_with_errors"]
        tickers_err = error_state["tickers_with_errors"]
        final_status = "Completed" if days_err == 0 else "CompletedWithErrors"

        backtest_run.status        = final_status
        backtest_run.final_capital = round(portfolio_value, 2)
        backtest_run.completed_at  = _utcnow()

        if days_err > 0 or tickers_err > 0:
            try:
                existing = json.loads(backtest_run.settings_json)
                existing["_error_summary"] = {
                    "days_with_errors": days_err,
                    "tickers_with_errors": tickers_err,
                }
                backtest_run.settings_json = json.dumps(existing, default=str)
            except Exception:
                pass

        session.commit()

        logger.info(
            "run_backtest run_id=%d %s: final=$%.2f return=%.1f%% "
            "days_err=%d tickers_err=%d",
            backtest_run_id, final_status, portfolio_value,
            report.total_return_pct * 100, days_err, tickers_err,
        )

    except Exception:
        # ------------------------------------------------------------------
        # Unrecoverable — save partial results, return 200 not 500
        # ------------------------------------------------------------------
        logger.error(
            "run_backtest run_id=%d CRASHED at day_idx=%d "
            "(days_err=%d tickers_err=%d):\n%s",
            backtest_run_id, day_idx,
            error_state.get("days_with_errors", 0),
            error_state.get("tickers_with_errors", 0),
            tb.format_exc(),
        )

        try:
            session.rollback()
        except Exception:
            pass

        # Try to build a perf report from committed trades
        try:
            _generate_performance_report(
                session, backtest_run_id, settings.portfolio_size, portfolio_value, settings
            )
            session.flush()
        except Exception as rep_exc:
            logger.error(
                "run_backtest run_id=%d: partial perf report failed: %s",
                backtest_run_id, rep_exc,
            )

        try:
            backtest_run.status        = "Partial"
            backtest_run.final_capital = round(portfolio_value, 2)
            backtest_run.completed_at  = _utcnow()
            try:
                existing = json.loads(backtest_run.settings_json)
                existing["_error_summary"] = {
                    "status": "Partial",
                    "crashed_at_day_idx": day_idx,
                    "days_with_errors": error_state.get("days_with_errors", 0),
                    "tickers_with_errors": error_state.get("tickers_with_errors", 0),
                    "traceback": tb.format_exc()[-2000:],
                }
                backtest_run.settings_json = json.dumps(existing, default=str)
            except Exception:
                pass
            session.commit()
        except Exception as save_exc:
            logger.error(
                "run_backtest run_id=%d: failed to save Partial status: %s",
                backtest_run_id, save_exc,
            )

    return backtest_run
