"""
Market filter for QualMaggie.

Strategy rule: only trade when SPY (or the configured MarketFilterTicker) is
in a healthy regime:
    1. Close > 50-day SMA
    2. 10-day EMA > 20-day EMA

Both conditions must be True.  If either indicator is NaN (warmup period
not complete), the market is treated as unhealthy (False).

Public API
----------
    is_market_healthy(df)   -> bool     pure function, uses latest row
    get_market_status(df)   -> dict     full detail dict
    check_market(session)   -> dict     loads SPY from DB, returns status
"""

from __future__ import annotations

import logging
import math
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.db.database import get_engine
from backend.db.models import PriceData, Ticker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def is_market_healthy(df: pd.DataFrame) -> bool:
    """
    Return True if the market filter passes on the latest row of df.

    Conditions (both required):
        close_price > sma50
        ema10 > ema20

    Returns False if df is empty or any required indicator is NaN.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with columns: close_price, sma50, ema10, ema20.
        Should be sorted oldest-first; latest row is used.
    """
    if df.empty:
        logger.warning("is_market_healthy: received empty DataFrame")
        return False

    row = df.sort_values("trade_date").iloc[-1]

    required = ["close_price", "sma50", "ema10", "ema20"]
    for col in required:
        val = row.get(col)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            logger.warning(
                "is_market_healthy: '%s' is NaN/None on %s — indicators not yet computed",
                col,
                row.get("trade_date"),
            )
            return False

    close_above_sma50 = float(row["close_price"]) > float(row["sma50"])
    ema10_above_ema20 = float(row["ema10"]) > float(row["ema20"])

    healthy = close_above_sma50 and ema10_above_ema20
    logger.debug(
        "Market filter: close_above_sma50=%s, ema10_above_ema20=%s → healthy=%s",
        close_above_sma50,
        ema10_above_ema20,
        healthy,
    )
    return healthy


def get_market_status(df: pd.DataFrame) -> dict:
    """
    Return a full market status dict using the latest row of df.

    Suitable for dashboard display and logging.

    Returns
    -------
    dict with keys:
        is_healthy          bool
        as_of_date          date | None
        close               float | None
        sma50               float | None
        ema10               float | None
        ema20               float | None
        close_above_sma50   bool
        ema10_above_ema20   bool
    """
    if df.empty:
        return {
            "is_healthy": False,
            "as_of_date": None,
            "close": None,
            "sma50": None,
            "ema10": None,
            "ema20": None,
            "close_above_sma50": False,
            "ema10_above_ema20": False,
        }

    row = df.sort_values("trade_date").iloc[-1]

    def _safe(col: str) -> float | None:
        val = row.get(col)
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) or math.isinf(f) else round(f, 4)
        except (TypeError, ValueError):
            return None

    close = _safe("close_price")
    sma50 = _safe("sma50")
    ema10 = _safe("ema10")
    ema20 = _safe("ema20")

    close_above_sma50 = (close is not None and sma50 is not None and close > sma50)
    ema10_above_ema20 = (ema10 is not None and ema20 is not None and ema10 > ema20)

    trade_date = row.get("trade_date")
    as_of_date: date | None = (
        trade_date.date() if hasattr(trade_date, "date") else trade_date
    )

    return {
        "is_healthy": close_above_sma50 and ema10_above_ema20,
        "as_of_date": as_of_date,
        "close": close,
        "sma50": sma50,
        "ema10": ema10,
        "ema20": ema20,
        "close_above_sma50": close_above_sma50,
        "ema10_above_ema20": ema10_above_ema20,
    }


# ---------------------------------------------------------------------------
# DB integration
# ---------------------------------------------------------------------------

def check_market(session: Session) -> dict:
    """
    Load the market filter ticker from DB and return its status dict.

    Indicators (sma50, ema10, ema20) must already be computed by
    indicators.calculate_and_store() before calling this function.

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session.

    Returns
    -------
    dict
        Same structure as get_market_status().  Returns all-False/None
        dict if the ticker has no data.
    """
    settings = get_settings()
    spy_symbol = settings.market_filter_ticker.upper()

    spy_ticker_id: int | None = session.execute(
        select(Ticker.ticker_id).where(Ticker.symbol == spy_symbol)
    ).scalar_one_or_none()

    if spy_ticker_id is None:
        logger.error(
            "check_market: '%s' not found in Tickers table — run 02_seed_tickers.sql",
            spy_symbol,
        )
        return get_market_status(pd.DataFrame())

    engine = get_engine()
    query = (
        select(
            PriceData.trade_date.label("trade_date"),
            PriceData.close_price.label("close_price"),
            PriceData.sma50.label("sma50"),
            PriceData.ema10.label("ema10"),
            PriceData.ema20.label("ema20"),
        )
        .where(PriceData.ticker_id == spy_ticker_id)
        .order_by(PriceData.trade_date)
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    status = get_market_status(df)
    logger.info(
        "Market status (%s, %s): healthy=%s  close=%.2f  sma50=%.2f  ema10=%.2f  ema20=%.2f",
        spy_symbol,
        status.get("as_of_date"),
        status["is_healthy"],
        status.get("close") or 0,
        status.get("sma50") or 0,
        status.get("ema10") or 0,
        status.get("ema20") or 0,
    )
    return status
