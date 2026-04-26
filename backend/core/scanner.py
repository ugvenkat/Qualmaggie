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
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import Settings, get_settings
from backend.core.market_filter import check_market, get_market_status
from backend.core.pattern_detector import detect_vcp
from backend.core.position_manager import calculate_initial_stop, calculate_position_size
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
    # ── Required fields (no defaults) ────────────────────────────────────────
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
    pivot_high: Optional[float]           # entry trigger from VCP
    distance_from_high_pct: Optional[float]
    contractions_count: Optional[int]
    preferred_quality: bool               # VCP has >= vcp_preferred_contractions
    is_breakout_candidate: bool           # price within 2% of pivot high
    has_earnings_warning: bool
    has_macro_warning: bool

    # ── Fields with defaults ─────────────────────────────────────────────────
    quality_score: int = 0                # 0–10 composite setup quality
    failed_filters: list[str] = field(default_factory=list)
    vcp_details: Optional[dict] = None

    # ── Enhanced signal fields (Task 1) ──────────────────────────────────────

    # Basic info
    signal_date: Optional[date] = None
    pattern_description: str = ""        # e.g. "VCP (3 contractions)"
    signal_strength: str = ""            # "Fresh" (within 2% of pivot) | "Extended"

    # Entry info
    adr: Optional[float] = None          # raw ADR in dollars (for stop calculation)
    entry_price: Optional[float] = None  # close * 1.002 estimate (live scan approx)
    pivot_point: Optional[float] = None  # alias of pivot_high for API clarity
    distance_from_pivot_pct: Optional[float] = None  # alias of distance_from_high_pct

    # Stop & risk
    stop_loss_price: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    max_stop_as_adr_fraction: Optional[float] = None

    # Risk/reward
    risk_reward_ratio: float = 3.0
    recommended_shares: Optional[int] = None
    recommended_position_size_usd: Optional[float] = None
    max_loss_usd: Optional[float] = None

    # Setup quality detail
    prior_move_pct: Optional[float] = None      # % move before base (e.g. 45.2)
    base_length_days: Optional[int] = None
    num_contractions: Optional[int] = None
    tightest_contraction_pct: Optional[float] = None
    volume_dry_up_pct: Optional[float] = None

    # Relative strength
    rs_rank: Optional[int] = None               # percentile vs universe 1–100
    rs_vs_spy_6m: Optional[float] = None        # % outperformance vs SPY
    distance_from_52w_high_pct: Optional[float] = None
    distance_from_200sma_pct: Optional[float] = None

    # Warnings (with API-friendly names)
    earnings_date_next: Optional[date] = None
    days_to_earnings: Optional[int] = None
    earnings_warning: bool = False
    macro_event_warning: bool = False

    # Market context
    market_status_label: str = ""        # "Healthy" | "Neutral" | "Weak"
    spy_above_50sma: bool = False
    spy_10ema_above_20ema: bool = False


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
    for ev in event_dates:
        if abs((ev - as_of_date).days) <= settings.macro_event_warning_days:
            return True
    return False


def _market_label(ms: dict | None) -> tuple[str, bool, bool]:
    """Return (label, spy_above_50sma, spy_10ema_above_20ema) from a market_status dict."""
    if not ms:
        return "", False, False
    close_above = bool(ms.get("close_above_sma50", False))
    ema_above = bool(ms.get("ema10_above_ema20", False))
    if close_above and ema_above:
        label = "Healthy"
    elif close_above or ema_above:
        label = "Neutral"
    else:
        label = "Weak"
    return label, close_above, ema_above


def _scan_ticker(
    session: Session,
    ticker_row,
    as_of_date: date,
    macro_event_dates: list[date] | None,
    settings: Settings,
    preloaded_dfs: dict[int, pd.DataFrame] | None = None,
    earnings_dates: dict[str, list[date]] | None = None,
    market_status: dict | None = None,
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
    adr_raw = latest.get("adr")
    adr_val = float(adr_raw) if adr_raw is not None else None
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
    # Earnings check
    # ------------------------------------------------------------------
    ticker_earnings: list[date] = (earnings_dates or {}).get(symbol, [])
    has_earn = any(
        0 <= (ed - as_of_date).days <= settings.earnings_warning_days
        for ed in ticker_earnings
    )
    if has_earn and settings.earnings_hard_block:
        failed_filters = list(failed_filters) + ["EarningsBlock"]

    # ------------------------------------------------------------------
    # VCP detection (always run; not gated on filter pass)
    # ------------------------------------------------------------------
    vcp = detect_vcp(df, settings)

    has_macro = _check_macro_warning(macro_event_dates, as_of_date, settings)

    # ------------------------------------------------------------------
    # Task 1 + Task 5: Compute enhanced signal fields
    # ------------------------------------------------------------------

    # Entry price estimate (close * 1.002; backtester uses actual next-day open)
    entry_price_est = round(close * 1.002, 4) if close > 0 else None

    # Stop loss calculation and ADR rejection (mirrors backtester Fix 3)
    stop_loss_price: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    if adr_val is not None and entry_price_est is not None and entry_price_est > 0:
        stop_loss_price = calculate_initial_stop(entry_price_est, adr_val, settings)
        stop_loss_pct = round((entry_price_est - stop_loss_price) / entry_price_est * 100, 2)

        # Task 5: Reject if stop too wide relative to ADR
        stop_dist_pct = (entry_price_est - stop_loss_price) / entry_price_est
        adr_pct_of_entry = adr_val / entry_price_est
        if stop_dist_pct > adr_pct_of_entry * settings.max_stop_as_adr_fraction:
            failed_filters = list(failed_filters) + ["StopTooWide"]
            logger.debug(
                "_scan_ticker(%s): rejected — stop %.2f%% > %.2f× ADR (%.2f%%)",
                symbol, stop_dist_pct * 100,
                settings.max_stop_as_adr_fraction, adr_pct_of_entry * 100,
            )

    # Position sizing (hypothetical, uses settings.portfolio_size)
    recommended_shares: Optional[int] = None
    recommended_position_size_usd: Optional[float] = None
    max_loss_usd: Optional[float] = None
    if (
        stop_loss_price is not None
        and entry_price_est is not None
        and stop_loss_price < entry_price_est
    ):
        recommended_shares = calculate_position_size(
            entry_price_est, stop_loss_price,
            settings.portfolio_size, 0.0, settings,
        )
        if recommended_shares > 0:
            recommended_position_size_usd = round(recommended_shares * entry_price_est, 2)
            max_loss_usd = round(recommended_shares * (entry_price_est - stop_loss_price), 2)

    # VCP detail fields
    pattern_description = ""
    prior_move_pct: Optional[float] = None
    base_length_days: Optional[int] = None
    num_contractions: Optional[int] = None
    tightest_contraction_pct: Optional[float] = None
    volume_dry_up_pct: Optional[float] = None
    if vcp:
        n_c = vcp["contractions_count"]
        pattern_description = f"VCP ({n_c} contraction{'s' if n_c != 1 else ''})"
        prior_move_pct = round(vcp["prior_move_pct"] * 100, 1)
        base_length_days = vcp.get("base_length_days")
        num_contractions = n_c
        tightest_contraction_pct = round(vcp["tightest_range_pct"] * 100, 2)
        vols = vcp.get("volumes", [])
        if len(vols) >= 2 and vols[0] > 0:
            volume_dry_up_pct = round((1.0 - vols[-1] / vols[0]) * 100, 1)

    # Signal strength
    signal_strength = ""
    if vcp:
        signal_strength = "Fresh" if vcp["is_breakout_candidate"] else "Extended"

    # Relative strength fields
    rs_vs_spy_6m: Optional[float] = None
    if rs_score is not None:
        rs_vs_spy_6m = round((rs_score - 1.0) * 100, 2)

    # Distance from 52-week high
    distance_from_52w_high_pct: Optional[float] = None
    try:
        cutoff_ts = pd.Timestamp(as_of_date) - pd.Timedelta(days=365)
        df_52w = df[df["trade_date"] >= cutoff_ts]
        if not df_52w.empty and close > 0:
            high_52w = float(df_52w["high_price"].max())
            if high_52w > 0:
                distance_from_52w_high_pct = round((close - high_52w) / high_52w * 100, 2)
    except Exception:
        pass

    # Distance from 200 SMA
    distance_from_200sma_pct: Optional[float] = None
    if sma200 is not None and sma200 > 0:
        distance_from_200sma_pct = round((close - sma200) / sma200 * 100, 2)

    # Next earnings date and days-to-earnings
    earnings_date_next: Optional[date] = None
    days_to_earnings: Optional[int] = None
    if ticker_earnings:
        upcoming = [ed for ed in ticker_earnings if (ed - as_of_date).days >= 0]
        if upcoming:
            earnings_date_next = min(upcoming)
            days_to_earnings = (earnings_date_next - as_of_date).days

    # Market context
    market_status_label, spy_above_50sma, spy_10ema_above_20ema = _market_label(market_status)

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
        # Enhanced fields
        signal_date=as_of_date,
        pattern_description=pattern_description,
        signal_strength=signal_strength,
        adr=adr_val,
        entry_price=entry_price_est,
        pivot_point=vcp["pivot_high"] if vcp else None,
        distance_from_pivot_pct=vcp["distance_from_high_pct"] if vcp else None,
        stop_loss_price=stop_loss_price,
        stop_loss_pct=stop_loss_pct,
        max_stop_as_adr_fraction=settings.max_stop_as_adr_fraction,
        risk_reward_ratio=3.0,
        recommended_shares=recommended_shares,
        recommended_position_size_usd=recommended_position_size_usd,
        max_loss_usd=max_loss_usd,
        prior_move_pct=prior_move_pct,
        base_length_days=base_length_days,
        num_contractions=num_contractions,
        tightest_contraction_pct=tightest_contraction_pct,
        volume_dry_up_pct=volume_dry_up_pct,
        rs_vs_spy_6m=rs_vs_spy_6m,
        distance_from_52w_high_pct=distance_from_52w_high_pct,
        distance_from_200sma_pct=distance_from_200sma_pct,
        earnings_date_next=earnings_date_next,
        days_to_earnings=days_to_earnings,
        earnings_warning=has_earn,
        macro_event_warning=has_macro,
        market_status_label=market_status_label,
        spy_above_50sma=spy_above_50sma,
        spy_10ema_above_20ema=spy_10ema_above_20ema,
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
        market_status   dict               — raw market status dict
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
            "market_status": market_status,
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
                preloaded_dfs, earnings_dates, market_status,
            )
            all_results.append(result)
        except Exception as exc:
            logger.error("scan_daily: error scanning %s: %s", ticker_row.symbol, exc)

    # ------------------------------------------------------------------
    # Step 4: Collect candidates (passed all filters AND have VCP)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Step 6: Compute rs_rank (percentile vs full universe)
    # ------------------------------------------------------------------
    rs_for_rank = [
        (r.rs_score, r.symbol)
        for r in all_results
        if r.rs_score is not None
    ]
    rs_for_rank.sort(key=lambda x: x[0])
    n_ranked = len(rs_for_rank)
    rs_rank_by_symbol: dict[str, int] = {}
    if n_ranked > 0:
        for rank_idx, (_, sym) in enumerate(rs_for_rank):
            rs_rank_by_symbol[sym] = max(1, round((rank_idx + 1) / n_ranked * 100))

    for candidate in candidates:
        candidate.rs_rank = rs_rank_by_symbol.get(candidate.symbol)

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
        "market_status": market_status,
    }
