"""
Daily scan engine for QualMaggie.

Orchestrates:  market filter → per-ticker stock filters → VCP detection → ranked candidates.

Public API
----------
    scan_daily(session, as_of_date=None, backtest_run_id=None,
               macro_event_dates=None, preloaded_dfs=None)  -> dict
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import Settings, get_settings
from backend.core.market_filter import check_market, get_market_status
from backend.core.pattern_detector import detect_vcp
from backend.core.stock_filter import apply_stock_filters
from backend.db.database import get_engine
from backend.db.models import PriceData, Ticker

logger = logging.getLogger(__name__)

# Bars loaded per ticker for the live-scan DB path.
# Must cover _PATTERN_LOOKBACK (120) + PriorMoveLookbackDays (126) + buffer.
_SCAN_LOOKBACK: int = 260


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    symbol: str
    sector: str
    exchange: str
    index_membership: str
    close: float
    volume_ma20: float
    atr_pct: Optional[float]
    sma200: Optional[float]
    ema10: Optional[float]
    rs_score: Optional[float]
    pivot_high: Optional[float]          # entry trigger from VCP
    distance_from_high_pct: Optional[float]
    contractions_count: Optional[int]
    preferred_quality: bool              # VCP has >= vcp_preferred_contractions
    is_breakout_candidate: bool          # price within 2% of pivot high
    has_earnings_warning: bool
    has_macro_warning: bool
    quality_score: int = 0               # Fix 4: 0–10 composite setup quality
    failed_filters: list[str] = field(default_factory=list)   # empty = passed all
    vcp_details: Optional[dict] = None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_market_status_asof(session: Session, as_of_date: date) -> dict:
    """Load SPY PriceData up to as_of_date and return market status dict."""
    settings = get_settings()
    spy_symbol = settings.market_filter_ticker.upper()

    spy_ticker_id: int | None = session.execute(
        select(Ticker.ticker_id).where(Ticker.symbol == spy_symbol)
    ).scalar_one_or_none()

    if spy_ticker_id is None:
        logger.error("_get_market_status_asof: '%s' not found in Tickers table", spy_symbol)
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
        .where(PriceData.trade_date <= as_of_date)
        .order_by(PriceData.trade_date)
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    return get_market_status(df)


def _check_macro_warning(
    event_dates: list[date] | None,
    as_of_date: date,
    settings: Settings,
) -> bool:
    """Return True if any macro event is within macro_event_warning_days of as_of_date."""
    if not event_dates:
        return False
    threshold = timedelta(days=settings.macro_event_warning_days)
    for ev in event_dates:
        if abs((ev - as_of_date).days) <= settings.macro_event_warning_days:
            return True
    return False


def _scan_ticker(
    session: Session,
    ticker_row,
    as_of_date: date,
    macro_event_dates: list[date] | None,
    settings: Settings,
    preloaded_dfs: dict[int, pd.DataFrame] | None = None,
    earnings_dates: dict[str, list[date]] | None = None,
) -> ScanResult:
    """Run all filters and VCP detection for one ticker."""
    ticker_id: int = ticker_row.ticker_id
    symbol: str = ticker_row.symbol
    sector: str = ticker_row.sector or ""
    exchange: str = ticker_row.exchange or ""
    index_membership: str = ticker_row.index_membership or ""

    _FAILED = ScanResult(
        symbol=symbol,
        sector=sector,
        exchange=exchange,
        index_membership=index_membership,
        close=0.0,
        volume_ma20=0.0,
        atr_pct=None,
        sma200=None,
        ema10=None,
        rs_score=None,
        pivot_high=None,
        distance_from_high_pct=None,
        contractions_count=None,
        preferred_quality=False,
        is_breakout_candidate=False,
        has_earnings_warning=False,
        has_macro_warning=False,
        failed_filters=["InsufficientData"],
    )

    # ------------------------------------------------------------------
    # Load price data
    # ------------------------------------------------------------------
    if preloaded_dfs is not None and ticker_id in preloaded_dfs:
        full_df = preloaded_dfs[ticker_id]
        # Pass all rows up to as_of_date — detect_vcp uses .tail(120) internally
        # for pattern detection and accesses the full slice for prior-move check.
        df = full_df[full_df["trade_date"] <= pd.Timestamp(as_of_date)].copy()
    else:
        engine = get_engine()
        query = (
            select(
                PriceData.trade_date.label("trade_date"),
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
            .where(PriceData.ticker_id == ticker_id)
            .where(PriceData.trade_date <= as_of_date)
            .order_by(PriceData.trade_date.desc())
            .limit(_SCAN_LOOKBACK)
        )
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
        df = df.sort_values("trade_date").reset_index(drop=True)

    if len(df) < 20:
        return _FAILED

    # ------------------------------------------------------------------
    # Compute caller-side metrics
    # ------------------------------------------------------------------
    volume_ma20 = float(df["volume"].rolling(20).mean().iloc[-1])
    latest = df.iloc[-1]

    close = float(latest["close_price"]) if latest["close_price"] is not None else 0.0
    atr_pct = float(latest["atr_pct"]) if latest.get("atr_pct") is not None else None
    sma200 = float(latest["sma200"]) if latest.get("sma200") is not None else None
    ema10 = float(latest["ema10"]) if latest.get("ema10") is not None else None
    rs_score = float(latest["rs_score"]) if latest.get("rs_score") is not None else None

    latest_row = {
        "close_price": close,
        "atr_pct": atr_pct,
        "sma200": sma200,
        "ema10": ema10,
        "ema20": float(latest["ema20"]) if latest.get("ema20") is not None else None,
        "rs_score": rs_score,
        "volume_ma20": volume_ma20,
        "index_membership": index_membership,
    }

    # ------------------------------------------------------------------
    # Stock filters
    # ------------------------------------------------------------------
    passed_filters, failed_filters = apply_stock_filters(symbol, latest_row, settings)

    # ------------------------------------------------------------------
    # Earnings check — uses preloaded dict when available (backtester),
    # otherwise falls back to DB-free False (live scan without data).
    # EarningsHardBlock=true promotes a warning to a hard filter failure.
    # ------------------------------------------------------------------
    ticker_earnings: list[date] = (earnings_dates or {}).get(symbol, [])
    has_earn = any(
        0 <= (ed - as_of_date).days <= settings.earnings_warning_days
        for ed in ticker_earnings
    )
    if has_earn and settings.earnings_hard_block:
        failed_filters = list(failed_filters) + ["EarningsBlock"]

    # ------------------------------------------------------------------
    # VCP detection (always run for logging; not gated on filter pass)
    # ------------------------------------------------------------------
    vcp = detect_vcp(df, settings)

    has_macro = _check_macro_warning(macro_event_dates, as_of_date, settings)

    return ScanResult(
        symbol=symbol,
        sector=sector,
        exchange=exchange,
        index_membership=index_membership,
        close=close,
        volume_ma20=volume_ma20,
        atr_pct=atr_pct,
        sma200=sma200,
        ema10=ema10,
        rs_score=rs_score,
        pivot_high=vcp["pivot_high"] if vcp else None,
        distance_from_high_pct=vcp["distance_from_high_pct"] if vcp else None,
        contractions_count=vcp["contractions_count"] if vcp else None,
        preferred_quality=vcp["preferred_quality"] if vcp else False,
        is_breakout_candidate=vcp["is_breakout_candidate"] if vcp else False,
        quality_score=vcp["quality_score"] if vcp else 0,
        has_earnings_warning=has_earn,
        has_macro_warning=has_macro,
        failed_filters=failed_filters,
        vcp_details=vcp,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan_daily(
    session: Session,
    as_of_date: date | None = None,
    backtest_run_id: int | None = None,
    macro_event_dates: list[date] | None = None,
    preloaded_dfs: dict[int, pd.DataFrame] | None = None,
    earnings_dates: dict[str, list[date]] | None = None,
) -> dict:
    """
    Run the full daily scan pipeline.

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session.
    as_of_date : date | None
        Scan date.  Defaults to today (live mode).
    backtest_run_id : int | None
        Set when called from backtester so results can be isolated.
    macro_event_dates : list[date] | None
        Dates of known macro events (FOMC, CPI, etc.).  Pass None to skip check.
    preloaded_dfs : dict[int, pd.DataFrame] | None
        Pre-loaded price data keyed by ticker_id.  Supplied by backtester to
        avoid per-ticker DB reads during the simulation loop.

    Returns
    -------
    dict with keys:
        market_healthy  bool
        as_of_date      date
        candidates      list[ScanResult]   — passed filters AND have VCP
        all_results     list[ScanResult]   — all tickers (for debugging/logging)
    """
    settings = get_settings()
    as_of_date = as_of_date or date.today()

    # ------------------------------------------------------------------
    # Step 1: Market filter
    # ------------------------------------------------------------------
    if backtest_run_id is not None or as_of_date < date.today():
        market_status = _get_market_status_asof(session, as_of_date)
    else:
        market_status = check_market(session)

    if not market_status["is_healthy"]:
        logger.info("scan_daily(%s): market unhealthy — skipping stock scan", as_of_date)
        return {
            "market_healthy": False,
            "as_of_date": as_of_date,
            "candidates": [],
            "all_results": [],
        }

    # ------------------------------------------------------------------
    # Step 2: Load active tickers
    # ------------------------------------------------------------------
    ticker_rows = session.execute(
        select(
            Ticker.ticker_id,
            Ticker.symbol,
            Ticker.sector,
            Ticker.exchange,
            Ticker.index_membership,
        ).where(Ticker.is_active == True)  # noqa: E712
    ).all()

    # ------------------------------------------------------------------
    # Step 3: Scan each ticker
    # ------------------------------------------------------------------
    all_results: list[ScanResult] = []
    for ticker_row in ticker_rows:
        try:
            result = _scan_ticker(
                session, ticker_row, as_of_date, macro_event_dates, settings,
                preloaded_dfs, earnings_dates,
            )
            all_results.append(result)
        except Exception as exc:
            logger.error("scan_daily: error scanning %s: %s", ticker_row.symbol, exc)

    # ------------------------------------------------------------------
    # Step 4: Collect candidates (passed all filters AND have VCP)
    # ------------------------------------------------------------------
    # detect_vcp already rejects below-threshold setups; this guard is an
    # extra safety net in case detect_vcp is called outside the scanner.
    candidates = [
        r for r in all_results
        if not r.failed_filters
        and r.vcp_details is not None
        and r.quality_score >= settings.min_setup_quality_score
    ]

    # ------------------------------------------------------------------
    # Step 5: Rank candidates
    #   Priority: breakout_candidate + preferred_quality →
    #             quality_score desc → rs_score desc → distance asc
    # ------------------------------------------------------------------
    def _rank_key(r: ScanResult):
        breakout_pts = 2 if (r.is_breakout_candidate and r.preferred_quality) else (
            1 if (r.is_breakout_candidate or r.preferred_quality) else 0
        )
        rs = r.rs_score if r.rs_score is not None else 0.0
        dist = r.distance_from_high_pct if r.distance_from_high_pct is not None else 1.0
        return (-breakout_pts, -r.quality_score, -rs, dist)

    candidates.sort(key=_rank_key)

    logger.info(
        "scan_daily(%s): %d candidates from %d tickers (market healthy)",
        as_of_date,
        len(candidates),
        len(all_results),
    )
    return {
        "market_healthy": True,
        "as_of_date": as_of_date,
        "candidates": candidates,
        "all_results": all_results,
    }
