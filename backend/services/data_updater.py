"""
yfinance gap-fill service for QualMaggie.

For each active ticker (plus the market-filter ticker, SPY by default),
finds the latest stored date in PriceData and downloads any missing rows
from yfinance.

Public API
----------
    update_ticker(symbol, session)     -> int            (rows inserted)
    update_all_tickers(session)        -> dict[str, int]
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.db.models import PriceData, Ticker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOOKBACK_DAYS: int = 730   # 2 years back when a ticker has no stored data


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _next_business_day(d: date) -> date:
    """
    Return the next calendar day that is not Saturday (5) or Sunday (6).

    US market holidays are not enumerated here — yfinance simply returns no
    data for those days and the empty-result guard in update_ticker handles
    them correctly without any special casing.
    """
    next_day = d + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return next_day


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_ticker(symbol: str, session: Session) -> int:
    """
    Download missing price rows for one ticker from yfinance and insert them
    into PriceData.

    Does NOT commit — the caller owns the transaction boundary.

    Parameters
    ----------
    symbol : str
        Ticker symbol (must exist in the Tickers table).
    session : Session
        An open SQLAlchemy session.

    Returns
    -------
    int
        Number of new rows inserted.

    Raises
    ------
    ValueError
        If the symbol is not found in the Tickers table.
    """
    symbol = symbol.upper()

    # ------------------------------------------------------------------
    # 1. Look up TickerID
    # ------------------------------------------------------------------
    ticker_id: int | None = session.execute(
        select(Ticker.ticker_id).where(Ticker.symbol == symbol)
    ).scalar_one_or_none()

    if ticker_id is None:
        raise ValueError(
            f"Symbol '{symbol}' not found in Tickers table. "
            "Run 02_seed_tickers.sql or insert the ticker first."
        )

    # ------------------------------------------------------------------
    # 2. Find the last stored date for this ticker
    # ------------------------------------------------------------------
    last_date: date | None = session.execute(
        select(func.max(PriceData.trade_date)).where(
            PriceData.ticker_id == ticker_id
        )
    ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # 3. Compute fetch window
    # ------------------------------------------------------------------
    if last_date is None:
        start_date = date.today() - timedelta(days=_LOOKBACK_DAYS)
        logger.info("%s: no existing data — fetching last %d days", symbol, _LOOKBACK_DAYS)
    else:
        start_date = _next_business_day(last_date)
        logger.info("%s: last stored date %s — fetching from %s", symbol, last_date, start_date)

    # end is exclusive in yfinance; date.today() means "up to yesterday's close"
    # which is correct — today's bar may be incomplete if the market is open.
    end_date = date.today()

    if start_date >= end_date:
        logger.info("%s: already up to date", symbol)
        return 0

    # ------------------------------------------------------------------
    # 4. Download from yfinance
    # ------------------------------------------------------------------
    raw: pd.DataFrame = yf.download(
        symbol,
        start=start_date,
        end=end_date,
        auto_adjust=True,   # Adjusts OHLC for splits/dividends; removes Adj Close
        progress=False,
    )

    if raw.empty:
        logger.info("%s: yfinance returned no data for %s → %s", symbol, start_date, end_date)
        return 0

    # ------------------------------------------------------------------
    # 5. Normalise columns
    #    yfinance 0.2.x may return a MultiIndex (field, symbol) for a single
    #    ticker download.  Drop the symbol level so columns are plain strings.
    # ------------------------------------------------------------------
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)

    # ------------------------------------------------------------------
    # 6. Normalise index → TradeDate column of datetime.date
    # ------------------------------------------------------------------
    raw = raw.reset_index()
    raw.rename(columns={"Date": "TradeDate"}, inplace=True)
    raw["TradeDate"] = pd.to_datetime(raw["TradeDate"], errors="coerce").dt.date
    raw.dropna(subset=["TradeDate"], inplace=True)

    # Keep only expected OHLCV columns; drop anything extra yfinance adds
    ohlcv_cols = ["TradeDate", "Open", "High", "Low", "Close", "Volume"]
    raw = raw[[c for c in ohlcv_cols if c in raw.columns]].copy()

    if raw.empty:
        logger.info("%s: no valid rows after normalisation", symbol)
        return 0

    # ------------------------------------------------------------------
    # 7. Fetch existing dates (one query)
    # ------------------------------------------------------------------
    existing_dates: set = set(
        session.execute(
            select(PriceData.trade_date).where(PriceData.ticker_id == ticker_id)
        ).scalars().all()
    )

    # ------------------------------------------------------------------
    # 8. Filter to new rows
    # ------------------------------------------------------------------
    new_df = raw[~raw["TradeDate"].isin(existing_dates)].copy()

    if new_df.empty:
        logger.info("%s: all fetched rows already present", symbol)
        return 0

    # ------------------------------------------------------------------
    # 9. Insert
    # ------------------------------------------------------------------
    inserted = 0
    now = datetime.utcnow()

    for row in new_df.itertuples(index=False):
        obj = PriceData(
            ticker_id=ticker_id,
            trade_date=row.TradeDate,
            open_price=float(row.Open),
            high_price=float(row.High),
            low_price=float(row.Low),
            close_price=float(row.Close),
            volume=int(row.Volume),
            # Indicator columns populated later by indicator service
            sma50=None,
            sma200=None,
            ema10=None,
            ema20=None,
            atr_pct=None,
            adr=None,
            rs_score=None,
            created_at=now,
        )
        session.add(obj)
        inserted += 1

    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        logger.warning(
            "%s: IntegrityError during flush — possible concurrent update; "
            "rolled back. Re-run to retry.",
            symbol,
        )
        return 0

    logger.info("%s: inserted %d new rows", symbol, inserted)
    return inserted


def update_all_tickers(session: Session) -> dict[str, int]:
    """
    Update price data for all active tickers and the market-filter ticker.

    Each symbol is committed independently so that a mid-run failure (e.g.
    yfinance rate-limit) does not discard symbols already processed.

    Parameters
    ----------
    session : Session
        An open SQLAlchemy session.

    Returns
    -------
    dict[str, int]
        Mapping of symbol → rows inserted.  Symbols that raised an error
        are omitted from the dict (errors are logged).
    """
    settings = get_settings()

    # ------------------------------------------------------------------
    # 1. Collect symbols: all active tickers + market filter (SPY)
    # ------------------------------------------------------------------
    active_symbols: list[str] = list(
        session.execute(
            select(Ticker.symbol).where(Ticker.is_active == True)  # noqa: E712
        ).scalars().all()
    )

    symbols: set[str] = set(active_symbols)
    symbols.add(settings.market_filter_ticker.upper())

    logger.info("update_all_tickers: processing %d symbols", len(symbols))

    # ------------------------------------------------------------------
    # 2. Update each symbol independently
    # ------------------------------------------------------------------
    results: dict[str, int] = {}

    for symbol in sorted(symbols):
        try:
            count = update_ticker(symbol, session)
            session.commit()
            results[symbol] = count
        except Exception as exc:
            session.rollback()
            logger.error("Failed to update %s: %s", symbol, exc)

    total = sum(results.values())
    logger.info(
        "update_all_tickers complete: %d symbols updated, %d total rows inserted",
        len(results),
        total,
    )
    return results
