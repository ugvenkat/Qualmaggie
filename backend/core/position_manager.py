"""
Trade lifecycle management for QualMaggie.

All sizing, stop-loss, and position state rules live here.
Route handlers and the backtester call these functions; no business logic
belongs in the API layer.

Public API
----------
Sizing & stops
    calculate_initial_stop(entry_price, adr, settings)              -> float
    calculate_position_size(entry_price, stop_loss, portfolio_value,
                            already_deployed, settings)             -> int

Portfolio guards
    can_open_position(open_positions, sector, entry_price, shares,
                      portfolio_value, settings)                    -> tuple[bool, str]

Blacklist
    is_blacklisted(symbol, session, as_of_date, backtest_run_id)   -> bool
    add_to_blacklist(session, ticker_id, symbol, blacklist_date,
                     settings, backtest_run_id)                     -> Blacklist

Position lifecycle
    open_position(session, ticker_id, symbol, sector, entry_date,
                  entry_price, shares, stop_loss, pattern_type,
                  risk_amount, is_live, backtest_run_id)            -> OpenPosition
    execute_partial_exit(session, position, exit_date, exit_price)  -> None
    update_trailing_stop(position, ema10)                           -> float
    check_exit_conditions(position, current_close, current_ema10,
                          days_held, settings)                      -> tuple[bool, str]
    close_position(session, position, exit_date, exit_price,
                   exit_reason)                                     -> Trade

Warning helpers
    has_earnings_warning(earnings_date, as_of_date, settings)      -> bool
    has_macro_warning(event_dates, as_of_date, settings)           -> bool
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import Settings, get_settings
from backend.db.models import Blacklist, OpenPosition, Trade

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sizing & stop loss
# ---------------------------------------------------------------------------

def calculate_initial_stop(
    entry_price: float,
    adr: float,
    settings: Settings,
) -> float:
    """
    Initial hard stop = entry_price - max(adr × multiplier, entry × min_stop_pct%).

    Taking the max of the two distances ensures the stop is never closer than
    MinStopPct% from entry regardless of how small the ADR is.
    With MinStopPct=4.0: stop = entry - max(ADR×1.8, entry×0.04)
    """
    adr_distance = adr * settings.stop_loss_adr_multiplier
    if settings.min_stop_pct > 0:
        pct_distance = entry_price * (settings.min_stop_pct / 100.0)
        distance = max(adr_distance, pct_distance)
    else:
        distance = adr_distance
    stop = round(entry_price - distance, 4)
    # Safety: stop must be below entry
    if stop >= entry_price:
        stop = round(entry_price * 0.95, 4)
    return stop


def calculate_position_size(
    entry_price: float,
    stop_loss: float,
    portfolio_value: float,
    already_deployed: float,
    settings: Settings,
) -> int:
    """
    Calculate share count based on dollar risk per trade.

    Logic
    -----
    risk_dollars  = portfolio_value × risk_per_trade_decimal
    risk_per_share = entry_price - stop_loss
    shares        = floor(risk_dollars / risk_per_share)
    Capped so: shares × entry_price ≤ (max_capital_deployed$ - already_deployed)

    Returns 0 if stop_loss >= entry_price or the position would be zero-sized.
    """
    if stop_loss >= entry_price:
        return 0

    risk_per_share = entry_price - stop_loss
    if risk_per_share <= 0:
        return 0

    risk_dollars = portfolio_value * settings.risk_per_trade_decimal
    shares = math.floor(risk_dollars / risk_per_share)

    if shares <= 0:
        return 0

    # Cap by remaining deployable capital
    max_deployable = portfolio_value * settings.max_capital_deployed_decimal - already_deployed
    if max_deployable <= 0:
        return 0

    max_shares_by_capital = math.floor(max_deployable / entry_price)
    shares = min(shares, max_shares_by_capital)

    return max(shares, 0)


# ---------------------------------------------------------------------------
# Portfolio guards
# ---------------------------------------------------------------------------

def can_open_position(
    open_positions: list[OpenPosition],
    sector: str,
    entry_price: float,
    shares: int,
    portfolio_value: float,
    settings: Settings,
) -> tuple[bool, str]:
    """
    Check whether a new position can be opened given current portfolio state.

    Checks
    ------
    1. Total open positions < max_open_positions
    2. Positions in this sector < max_positions_per_sector
    3. New position doesn't breach max_capital_deployed

    Returns (True, "") on success, (False, "reason") on failure.
    """
    if len(open_positions) >= settings.max_open_positions:
        return False, f"Max open positions reached ({settings.max_open_positions})"

    sector_count = sum(1 for p in open_positions if p.sector == sector)
    if sector_count >= settings.max_positions_per_sector:
        return False, f"Max positions for sector '{sector}' reached ({settings.max_positions_per_sector})"

    current_deployed = sum(p.shares * entry_price for p in open_positions)
    new_cost = shares * entry_price
    max_deployed = portfolio_value * settings.max_capital_deployed_decimal
    if current_deployed + new_cost > max_deployed:
        return False, (
            f"Would exceed max capital deployed "
            f"(${current_deployed + new_cost:,.0f} > ${max_deployed:,.0f})"
        )

    return True, ""


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------

def is_blacklisted(
    symbol: str,
    session: Session,
    as_of_date: date,
    backtest_run_id: int | None = None,
) -> bool:
    """
    Return True if the symbol is on the active blacklist as of as_of_date.

    In live mode (backtest_run_id=None) only live blacklist entries are checked.
    In backtest mode only entries for that backtest run are checked.
    """
    query = (
        select(Blacklist.blacklist_id)
        .where(Blacklist.symbol == symbol)
        .where(Blacklist.is_active == True)  # noqa: E712
        .where(Blacklist.expiry_date >= as_of_date)
    )
    if backtest_run_id is None:
        query = query.where(Blacklist.backtest_run_id == None)  # noqa: E711
    else:
        query = query.where(Blacklist.backtest_run_id == backtest_run_id)

    result = session.execute(query).first()
    return result is not None


def add_to_blacklist(
    session: Session,
    ticker_id: int,
    symbol: str,
    blacklist_date: date,
    settings: Settings,
    backtest_run_id: int | None = None,
) -> Blacklist:
    """
    Add a symbol to the blacklist.  Does NOT commit.

    expiry_date = blacklist_date + blacklist_days.
    """
    expiry = blacklist_date + timedelta(days=settings.blacklist_days)
    entry = Blacklist(
        ticker_id=ticker_id,
        symbol=symbol,
        blacklist_date=blacklist_date,
        expiry_date=expiry,
        reason="StopLoss",
        is_active=True,
        backtest_run_id=backtest_run_id,
    )
    session.add(entry)
    logger.info(
        "Blacklisted %s from %s to %s (run_id=%s)",
        symbol,
        blacklist_date,
        expiry,
        backtest_run_id,
    )
    return entry


# ---------------------------------------------------------------------------
# Position lifecycle
# ---------------------------------------------------------------------------

def open_position(
    session: Session,
    ticker_id: int,
    symbol: str,
    sector: str,
    entry_date: date,
    entry_price: float,
    shares: int,
    stop_loss: float,
    pattern_type: str,
    risk_amount: float,
    is_live: bool = True,
    backtest_run_id: int | None = None,
) -> OpenPosition:
    """
    Create an OpenPosition and a corresponding open Trade record.  Does NOT commit.

    Returns the new OpenPosition ORM object.
    """
    position = OpenPosition(
        ticker_id=ticker_id,
        symbol=symbol,
        sector=sector,
        entry_date=entry_date,
        entry_price=entry_price,
        shares=shares,
        initial_stop_loss=stop_loss,
        current_stop=stop_loss,
        partial_sold_shares=0,
        partial_sold_price=None,
        partial_sold_date=None,
        pattern_type=pattern_type,
        risk_amount=risk_amount,
        is_live=is_live,
        backtest_run_id=backtest_run_id,
    )
    session.add(position)
    session.flush()   # populate position_id

    trade = Trade(
        ticker_id=ticker_id,
        symbol=symbol,
        sector=sector,
        entry_date=entry_date,
        entry_price=entry_price,
        shares=shares,
        initial_stop_loss=stop_loss,
        pattern_type=pattern_type,
        risk_amount=risk_amount,
        is_live=is_live,
        backtest_run_id=backtest_run_id,
        # Exit fields are None until position is closed
        exit_date=None,
        exit_price=None,
        pnl=None,
        pnl_pct=None,
        exit_reason=None,
    )
    session.add(trade)
    session.flush()   # populate trade_id

    # Store trade_id as in-memory attribute so close_position() can look
    # it up directly by PK rather than via a fragile compound query.
    position._open_trade_id = trade.trade_id

    logger.info(
        "Opened position: %s | %d shares @ $%.2f | stop=$%.2f | risk=$%.2f",
        symbol,
        shares,
        entry_price,
        stop_loss,
        risk_amount,
    )
    return position


def execute_partial_exit(
    session: Session,
    position: OpenPosition,
    exit_date: date,
    exit_price: float,
) -> None:
    """
    Sell 50% of the position.  Updates OpenPosition and Trade in place.
    Does NOT commit.
    """
    if position.partial_sold_shares and position.partial_sold_shares > 0:
        logger.debug("execute_partial_exit: %s already partially exited — skipping", position.symbol)
        return

    partial_shares = math.floor(position.shares / 2)
    if partial_shares <= 0:
        return

    # Normalise types before writing to DB:
    # DATE column rejects datetime objects (ODBC 22003); Numeric(18,4) needs rounding.
    _exit_date: date = exit_date.date() if isinstance(exit_date, datetime) else exit_date
    _exit_price: float = round(float(exit_price), 4)

    position.partial_sold_shares = partial_shares
    position.partial_sold_price = _exit_price
    position.partial_sold_date = _exit_date

    # Update the matching Trade row — prefer the ID stored at open time
    open_trade_id = getattr(position, "_open_trade_id", None)
    if open_trade_id is not None:
        trade = session.get(Trade, open_trade_id)
    else:
        trade = session.execute(
            select(Trade)
            .where(Trade.symbol == position.symbol)
            .where(Trade.entry_date == position.entry_date)
            .where(Trade.backtest_run_id == position.backtest_run_id)
            .where(Trade.exit_date == None)  # noqa: E711
        ).scalar_one_or_none()

    if trade is not None:
        trade.partial_exit_date = _exit_date
        trade.partial_exit_price = _exit_price
        trade.partial_shares = partial_shares

    logger.info(
        "Partial exit: %s | %d shares @ $%.2f",
        position.symbol,
        partial_shares,
        exit_price,
    )


def update_trailing_stop(position: OpenPosition, ema10: float) -> float:
    """
    Raise the trailing stop to max(current_stop, ema10).

    Updates position.current_stop in-memory; caller must flush/commit.
    Only meaningful after a partial exit has been executed.

    Returns the new stop value.
    """
    new_stop = round(max(float(position.current_stop), ema10), 4)
    position.current_stop = new_stop
    return new_stop


def check_exit_conditions(
    position: OpenPosition,
    current_close: float,
    current_trail_ema: Optional[float],
    days_held: int,
    settings: Settings,
    unrealized_pct: float = 0.0,
) -> tuple[bool, str]:
    """
    Evaluate whether the position should be closed.

    Rules (evaluated in order)
    --------------------------
    1. Hard stop:    current_close <= current_stop           → "StopLoss"
    2. Trail stop:   (after partial exit) close < trail_ema  → "TrailStop"
                     trail_ema is EMA10 normally, EMA20 when gain >= WideTrailActivationPct
    3. Tiered MaxHold:
       - gain >= MaxHoldBypassPct  → no MaxHold cap; only trail/stop can exit
       - gain >= MaxHoldExtendPct  → exit at MaxHoldExtendedDays instead of MaxHoldDays
       - otherwise                 → exit at MaxHoldDays (default 20)

    Returns (True, reason) or (False, "").
    """
    # 1. Hard stop
    if current_close <= position.current_stop:
        return True, "StopLoss"

    # 2. Trailing stop (only after partial exit)
    partial_done = position.partial_sold_shares is not None and position.partial_sold_shares > 0
    if partial_done and current_trail_ema is not None and not math.isnan(current_trail_ema):
        if current_close < current_trail_ema:
            return True, "TrailStop"

    # 3. Tiered MaxHold
    if unrealized_pct >= settings.max_hold_bypass_pct:
        pass  # bypass MaxHold entirely — only trail or stop loss can exit
    elif unrealized_pct >= settings.max_hold_extend_pct:
        if days_held >= settings.max_hold_extended_days:
            return True, "MaxHold"
    else:
        if days_held >= settings.max_hold_days:
            return True, "MaxHold"

    return False, ""


def close_position(
    session: Session,
    position: OpenPosition,
    exit_date: date,
    exit_price: float,
    exit_reason: str,
) -> Trade:
    """
    Close a position: update Trade with exit info + PnL, DELETE OpenPosition.
    If exit_reason == "StopLoss", the symbol is added to the blacklist.
    Does NOT commit.

    Returns the updated Trade ORM object.
    """
    partial_shares = position.partial_sold_shares or 0
    partial_price = position.partial_sold_price or 0.0
    remaining_shares = position.shares - partial_shares

    partial_proceeds = partial_shares * partial_price
    exit_proceeds = remaining_shares * exit_price
    total_cost = position.shares * position.entry_price

    pnl = partial_proceeds + exit_proceeds - total_cost
    pnl_pct = pnl / total_cost if total_cost > 0 else 0.0

    open_trade_id = getattr(position, "_open_trade_id", None)
    if open_trade_id is not None:
        trade = session.get(Trade, open_trade_id)
    else:
        trade = session.execute(
            select(Trade)
            .where(Trade.symbol == position.symbol)
            .where(Trade.entry_date == position.entry_date)
            .where(Trade.backtest_run_id == position.backtest_run_id)
            .where(Trade.exit_date == None)  # noqa: E711
        ).scalar_one_or_none()

    if trade is not None:
        trade.exit_date = exit_date
        trade.exit_price = exit_price
        trade.pnl = round(pnl, 2)
        trade.pnl_pct = round(pnl_pct, 4)
        trade.exit_reason = exit_reason
    else:
        logger.warning("close_position: no open Trade found for %s (entry %s)", position.symbol, position.entry_date)

    # Blacklist on stop-loss
    if exit_reason == "StopLoss":
        settings = get_settings()
        add_to_blacklist(
            session,
            position.ticker_id,
            position.symbol,
            exit_date,
            settings,
            backtest_run_id=position.backtest_run_id,
        )

    session.delete(position)

    logger.info(
        "Closed position: %s | reason=%s | pnl=$%.2f (%.1f%%)",
        position.symbol,
        exit_reason,
        pnl,
        pnl_pct * 100,
    )
    return trade


# ---------------------------------------------------------------------------
# Warning helpers
# ---------------------------------------------------------------------------

def has_earnings_warning(
    earnings_date: Optional[date],
    as_of_date: date,
    settings: Settings,
) -> bool:
    """
    Return True if earnings are within earnings_warning_days of as_of_date.
    Returns False if earnings_date is None.
    """
    if earnings_date is None:
        return False
    days_away = (earnings_date - as_of_date).days
    return 0 <= days_away <= settings.earnings_warning_days


def has_macro_warning(
    event_dates: list[date],
    as_of_date: date,
    settings: Settings,
) -> bool:
    """Return True if any macro event is within macro_event_warning_days of as_of_date."""
    if not event_dates:
        return False
    for ev in event_dates:
        if abs((ev - as_of_date).days) <= settings.macro_event_warning_days:
            return True
    return False
