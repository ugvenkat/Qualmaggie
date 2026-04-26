"""
Earnings date fetcher for QualMaggie.

Fetches upcoming and historical earnings dates from yfinance for all active
tickers and stores them in the EarningsDates table.

Public API
----------
    update_earnings_dates(session) -> dict[str, int]
    load_earnings_by_symbol(session) -> dict[str, list[date]]
"""

from __future__ import annotations

import logging
from datetime import date

import yfinance as yf
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.db.models import EarningsDate, Ticker

logger = logging.getLogger(__name__)

# Instruments that never report earnings (ETFs, leveraged funds)
_NO_EARNINGS: frozenset[str] = frozenset({"SPY", "QQQ", "IWM", "SOXL", "TQQQ", "SPXL"})


def update_earnings_dates(session: Session) -> dict[str, int]:
    """
    Fetch earnings dates from yfinance for all active tickers and store in DB.

    Replaces all existing dates for each ticker on each call so the table
    stays current.  Commits per ticker so partial runs are preserved on error.

    Returns dict of symbol -> number of dates stored.
    """
    symbols: list[str] = list(
        session.execute(
            select(Ticker.symbol).where(Ticker.is_active == True)  # noqa: E712
        ).scalars().all()
    )

    results: dict[str, int] = {}
    for symbol in symbols:
        if symbol.upper() in _NO_EARNINGS:
            logger.debug("%s: skipped (ETF / no earnings)", symbol)
            continue
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.earnings_dates

            if df is None or df.empty:
                logger.warning("%s: no earnings dates returned by yfinance", symbol)
                continue

            # Replace existing rows for this symbol
            session.execute(delete(EarningsDate).where(EarningsDate.symbol == symbol))

            seen: set[date] = set()
            count = 0
            for idx in df.index:
                # idx is a tz-aware Timestamp; strip timezone for date comparison
                ed: date = idx.date() if hasattr(idx, "date") else idx
                if ed in seen:
                    continue
                seen.add(ed)
                session.add(EarningsDate(symbol=symbol, earnings_date=ed))
                count += 1

            session.commit()
            results[symbol] = count
            logger.info("%s: stored %d earnings dates", symbol, count)

        except Exception as exc:
            session.rollback()
            logger.error("update_earnings_dates: %s failed: %s", symbol, exc)

    logger.info(
        "update_earnings_dates complete: %d / %d symbols processed",
        len(results),
        len(symbols),
    )
    return results


def load_earnings_by_symbol(session: Session) -> dict[str, list[date]]:
    """
    Load all earnings dates from DB into a dict keyed by symbol.

    Used by the backtester to pre-load earnings data once before the
    simulation loop, avoiding per-day DB queries.

    Returns {symbol: [date, ...]} sorted oldest-first.
    """
    rows = session.execute(
        select(EarningsDate.symbol, EarningsDate.earnings_date)
        .order_by(EarningsDate.symbol, EarningsDate.earnings_date)
    ).all()

    result: dict[str, list[date]] = {}
    for symbol, ed in rows:
        ed_date = ed.date() if hasattr(ed, "date") else ed
        result.setdefault(symbol, []).append(ed_date)

    logger.info("load_earnings_by_symbol: loaded dates for %d symbols", len(result))
    return result
