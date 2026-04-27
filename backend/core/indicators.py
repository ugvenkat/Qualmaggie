"""
Technical indicator computation for QualMaggie.

Two-layer design
----------------
1. Pure computation  — stateless functions, take DataFrames, return DataFrames.
                       No DB dependency; fully testable in isolation.
2. DB integration    — load PriceData, call pure functions, bulk-UPDATE back.

DataFrame column contract (DB-loaded input)
-------------------------------------------
    price_data_id, trade_date, open_price, high_price, low_price,
    close_price, volume

Computed columns added by compute_indicators()
----------------------------------------------
    SMA50, SMA200, EMA10, EMA20, ATRPct, ADR, RSScore, VolumeMA20

    VolumeMA20 is NOT stored in PriceData (no DB column).  It is
    returned in the DataFrame for in-memory use by filters / pattern
    detection and silently excluded from the bulk UPDATE.

Warmup note
-----------
    SMA200 requires 200 prior bars.  For full 2020 backtest coverage
    load price data from at least 2019-01-01 so the warmup period is
    satisfied before the first backtest date.  NaN cells (warmup rows)
    are written as NULL to the DB.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.db.database import get_engine
from backend.db.models import PriceData, Ticker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Compute Average True Range (ATR).

    True Range = max(High−Low, |High−PrevClose|, |Low−PrevClose|)
    ATR        = rolling(period).mean() of True Range
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _compute_rs_score(
    close: pd.Series,
    spy_close: pd.Series,
    lookback: int,
) -> pd.Series:
    """
    Compute Relative Strength vs SPY.

    RS = (stock_close / stock_close[lookback]) / (spy_close / spy_close[lookback])
    RS > 1.0  → stock outperforming SPY over the lookback window.
    RS < 1.0  → underperforming.

    Both series must share the same index (trade_date).  The spy_close
    series is reindexed to match close's index; missing spy dates are
    forward-filled (holiday handling).
    """
    spy_aligned = spy_close.reindex(close.index).ffill()
    stock_return = close / close.shift(lookback)
    spy_return = spy_aligned / spy_aligned.shift(lookback)
    return stock_return / spy_return


# indicators.PY

# indicators.PY

def _safe_decimal(value: float, scale: int, max_magnitude: Optional[float] = None) -> Optional[float]:
    """
    Prepare a float for insertion into a DECIMAL(p, scale) column via pyodbc.

    Steps applied in order:
      1. NaN / inf / None   -> return None
      2. Magnitude check    -> return None if abs(value) > max_magnitude
                               (DECIMAL(10,6) only holds up to 9999.999999)
      3. Round to 'scale'   -> prevents precision overflow

    WHY THIS IS NEEDED
    ------------------
    session.execute(update(Model), list_of_dicts) bypasses SQLAlchemy's
    TypeDecorator.process_bind_param entirely. The pyodbc driver raises
    "numeric value out of range" for two distinct reasons:
        a) Too many decimal places: 151.60324986605232 -> DECIMAL(18,4)  [step 3]
        b) Value too large for column: ATRPct=15000.0 -> DECIMAL(10,6)   [step 2]
    """
    if value is None:
        return None
    try:
        if math.isnan(value) or math.isinf(value):
            return None
    except (TypeError, ValueError):
        return None
    
    v = float(value)
    if max_magnitude is not None and abs(v) > max_magnitude:
        return None # store NULL rather than error
        
    return round(v, scale)


def _nan_to_none(value: float) -> Optional[float]:
    """Round to 4 d.p. and convert NaN/inf to None. For DECIMAL(18, 4) columns."""
    return _safe_decimal(value, 4)


def _clamp_decimal10_6(value: float) -> Optional[float]:
    """
    Round to 6 d.p., clamp magnitude, convert NaN/inf to None.
    For DECIMAL(10, 6) columns - only 4 digits before decimal point,
    so max representable value is 9999.999999.
    """
    return _safe_decimal(value, 6, max_magnitude=9999.999999)


# ---------------------------------------------------------------------------
# Pure computation (public)
# ---------------------------------------------------------------------------

def compute_indicators(
    df: pd.DataFrame,
    spy_df: Optional[pd.DataFrame] = None,
    rs_lookback: int = 126,
) -> pd.DataFrame:
    """
    Compute all technical indicators on a price DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns: close_price, high_price, low_price, volume,
        trade_date.  Must have at least one row.
    spy_df : pd.DataFrame | None
        SPY (market filter ticker) OHLCV with the same column names.
        Required to compute RSScore.  If None, RSScore column is all NaN.
    rs_lookback : int
        Number of trading days for RS calculation (default 126 ≈ 6 months).

    Returns
    -------
    pd.DataFrame
        Same DataFrame with additional columns:
        SMA50, SMA200, EMA10, EMA20, ATRPct, ADR, RSScore, VolumeMA20
    """
    if df.empty:
        return df

    # Ensure chronological order — rolling windows require it
    df = df.sort_values("trade_date").copy()

    close = df["close_price"]
    high = df["high_price"]
    low = df["low_price"]
    volume = df["volume"].astype(float)

    # Moving averages
    df["SMA50"] = close.rolling(50).mean()
    df["SMA200"] = close.rolling(200).mean()
    df["EMA10"] = close.ewm(span=10, adjust=False).mean()
    df["EMA20"] = close.ewm(span=20, adjust=False).mean()

    # ATR %
    atr = _compute_atr(high, low, close, period=14)
    df["ATRPct"] = (atr / close * 100).round(4)

    # ADR — Average Daily Range in dollars (20-day)
    df["ADR"] = (high - low).rolling(20).mean().round(4)

    # Volume MA (in-memory only — no DB column)
    df["VolumeMA20"] = volume.rolling(20).mean()

    # Relative Strength vs SPY
    if spy_df is not None and not spy_df.empty:
        spy_sorted = spy_df.sort_values("trade_date").copy()
        spy_close = spy_sorted.set_index("trade_date")["close_price"]
        stock_close = close.copy()
        stock_close.index = pd.to_datetime(df["trade_date"])
        spy_close.index = pd.to_datetime(spy_close.index)
        df["RSScore"] = _compute_rs_score(stock_close, spy_close, rs_lookback).values
    else:
        df["RSScore"] = np.nan

    return df


# ---------------------------------------------------------------------------
# DB integration (public)
# ---------------------------------------------------------------------------

def _load_ticker_df(ticker_id: int) -> pd.DataFrame:
    """Load all PriceData rows for a ticker into a DataFrame via pd.read_sql."""
    engine = get_engine()
    query = (
        select(
            PriceData.price_data_id.label("price_data_id"),
            PriceData.trade_date.label("trade_date"),
            PriceData.open_price.label("open_price"),
            PriceData.high_price.label("high_price"),
            PriceData.low_price.label("low_price"),
            PriceData.close_price.label("close_price"),
            PriceData.volume.label("volume"),
        )
        .where(PriceData.ticker_id == ticker_id)
        .order_by(PriceData.trade_date)
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def calculate_and_store(symbol: str, session: Session) -> int:
    """
    Compute all indicators for one ticker and bulk-UPDATE PriceData.

    Loads SPY data internally for RS Score computation.  Both SPY and the
    target ticker must have PriceData rows already inserted.

    Parameters
    ----------
    symbol : str
        Ticker symbol (must exist in Tickers table).
    session : Session
        Open SQLAlchemy session.

    Returns
    -------
    int
        Number of PriceData rows updated.

    Raises
    ------
    ValueError
        If symbol or SPY not found in Tickers table.
    """
    symbol = symbol.upper()
    settings = get_settings()

    # ------------------------------------------------------------------
    # Resolve TickerIDs
    # ------------------------------------------------------------------
    ticker_id: int | None = session.execute(
        select(Ticker.ticker_id).where(Ticker.symbol == symbol)
    ).scalar_one_or_none()
    if ticker_id is None:
        raise ValueError(f"Symbol '{symbol}' not found in Tickers table")

    spy_symbol = settings.market_filter_ticker.upper()
    spy_ticker_id: int | None = session.execute(
        select(Ticker.ticker_id).where(Ticker.symbol == spy_symbol)
    ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Load price data
    # ------------------------------------------------------------------
    df = _load_ticker_df(ticker_id)
    if df.empty:
        logger.warning("%s: no PriceData rows — skipping indicator calculation", symbol)
        return 0

    spy_df: pd.DataFrame | None = None
    if spy_ticker_id is not None and symbol != spy_symbol:
        spy_df = _load_ticker_df(spy_ticker_id)

    # ------------------------------------------------------------------
    # Compute indicators
    # ------------------------------------------------------------------
    df = compute_indicators(df, spy_df=spy_df, rs_lookback=settings.rs_lookback_days)

    # ------------------------------------------------------------------
    # Bulk UPDATE — only the indicator columns; VolumeMA20 excluded
    # ------------------------------------------------------------------
    records = []
    for row in df.itertuples(index=False):
        for _col, _raw in [("SMA50", row.SMA50), ("SMA200", row.SMA200),
                        ("EMA10", row.EMA10), ("EMA20", row.EMA20),
                        ("ATRPct", row.ATRPct), ("ADR", row.ADR),
                        ("RSScore", row.RSScore)]:
            if _raw is not None:
                try:
                    import math as _math
                    if not _math.isnan(float(_raw)) and not _math.isinf(float(_raw)):
                        _fv = float(_raw)
                        _limit = 9999.999999 if _col in ("ATRPct", "RSScore") else 99999999999999.9999
                        if abs(_fv) > _limit:
                            logger.error("RAW OVERFLOW %s pid=%s col=%s raw_val=%.10f limit=%s",
                                        symbol, row.price_data_id, _col, _fv, _limit)
                except Exception:
                    logger.error("RAW UNPROCESSABLE %s pid=%s col=%s raw_val=%r",
                                symbol, row.price_data_id, _col, _raw)

        records.append({
            "price_data_id": int(row.price_data_id),
            "sma50":         _nan_to_none(row.SMA50),
            "sma200":        _nan_to_none(row.SMA200),
            "ema10":         _nan_to_none(row.EMA10),
            "ema20":         _nan_to_none(row.EMA20),
            "atr_pct":       _clamp_decimal10_6(row.ATRPct),
            "adr":           _nan_to_none(row.ADR),
            "rs_score":      _clamp_decimal10_6(row.RSScore),
        })

    session.execute(update(PriceData), records)
    logger.info("%s: updated indicators on %d rows", symbol, len(records))
    return len(records)


def calculate_all(session: Session) -> dict[str, int]:
    """
    Compute and store indicators for all active tickers.

    SPY is processed first so its data is available for RS Score
    calculation for all subsequent tickers.

    Returns
    -------
    dict[str, int]
        Mapping of symbol → rows updated.  Symbols that failed are omitted
        (errors are logged).
    """
    settings = get_settings()
    spy_symbol = settings.market_filter_ticker.upper()

    active_symbols: list[str] = list(
        session.execute(
            select(Ticker.symbol).where(Ticker.is_active == True)  # noqa: E712
        ).scalars().all()
    )

    # Process SPY first so RS scores are available for all others
    symbols = [spy_symbol] + sorted(s for s in active_symbols if s != spy_symbol)

    results: dict[str, int] = {}
    for symbol in symbols:
        try:
            count = calculate_and_store(symbol, session)
            session.commit()
            results[symbol] = count
        except Exception as exc:
            session.rollback()
            logger.error("Failed to calculate indicators for %s: %s", symbol, exc)

    total = sum(results.values())
    logger.info(
        "calculate_all complete: %d symbols processed, %d total rows updated",
        len(results),
        total,
    )
    return results
