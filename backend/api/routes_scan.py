"""
Scanner routes.

POST /api/scan/run    — Run a full daily scan and return candidates.
GET  /api/scan/latest — Re-run the scan for today (same as POST /run).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.core.scanner import ScanResult, scan_daily
from backend.db.database import get_db

router = APIRouter()


class ScanResultResponse(BaseModel):
    # ── Basic info ────────────────────────────────────────────────────────────
    symbol: str
    sector: str
    exchange: str
    index_membership: str
    signal_date: Optional[date]
    pattern_description: str
    signal_strength: str                    # "Fresh" | "Extended"

    # ── Entry info ────────────────────────────────────────────────────────────
    close: float
    entry_price: Optional[float]            # close * 1.002 estimate
    pivot_point: Optional[float]            # exact breakout trigger price
    distance_from_pivot_pct: Optional[float]

    # ── Stop & risk ───────────────────────────────────────────────────────────
    stop_loss_price: Optional[float]
    stop_loss_pct: Optional[float]
    adr_pct: Optional[float]               # ATR as % of price
    adr: Optional[float]                   # raw ADR in dollars
    max_stop_as_adr_fraction: Optional[float]

    # ── Risk/reward ───────────────────────────────────────────────────────────
    risk_reward_ratio: float
    recommended_shares: Optional[int]
    recommended_position_size_usd: Optional[float]
    max_loss_usd: Optional[float]

    # ── Setup quality ─────────────────────────────────────────────────────────
    setup_quality_score: int               # 0–10
    prior_move_pct: Optional[float]        # % move before base
    base_length_days: Optional[int]
    num_contractions: Optional[int]
    tightest_contraction_pct: Optional[float]
    volume_dry_up_pct: Optional[float]

    # ── Relative strength ─────────────────────────────────────────────────────
    rs_rank: Optional[int]                 # percentile vs universe 1–100
    rs_score: Optional[float]             # raw RS score vs SPY
    rs_vs_spy_6m: Optional[float]         # % outperformance
    distance_from_52w_high_pct: Optional[float]
    distance_from_200sma_pct: Optional[float]
    sma200: Optional[float]
    ema10: Optional[float]

    # ── Warnings ──────────────────────────────────────────────────────────────
    earnings_date: Optional[date]          # next upcoming earnings date
    days_to_earnings: Optional[int]
    earnings_warning: bool
    macro_event_warning: bool

    # ── Market context ────────────────────────────────────────────────────────
    market_status: str                     # "Healthy" | "Neutral" | "Weak"
    spy_above_50sma: bool
    spy_10ema_above_20ema: bool

    # ── Legacy / internal ─────────────────────────────────────────────────────
    volume_ma20: float
    preferred_quality: bool
    is_breakout_candidate: bool
    failed_filters: list[str]

    model_config = {"from_attributes": True}


class ScanResponse(BaseModel):
    market_healthy: bool
    as_of_date: date
    candidate_count: int
    candidates: list[ScanResultResponse]


def _to_response(r: ScanResult) -> ScanResultResponse:
    return ScanResultResponse(
        # Basic info
        symbol=r.symbol,
        sector=r.sector,
        exchange=r.exchange,
        index_membership=r.index_membership,
        signal_date=r.signal_date,
        pattern_description=r.pattern_description,
        signal_strength=r.signal_strength,
        # Entry
        close=r.close,
        entry_price=r.entry_price,
        pivot_point=r.pivot_point,
        distance_from_pivot_pct=r.distance_from_pivot_pct,
        # Stop & risk
        stop_loss_price=r.stop_loss_price,
        stop_loss_pct=r.stop_loss_pct,
        adr_pct=r.atr_pct,
        adr=r.adr,
        max_stop_as_adr_fraction=r.max_stop_as_adr_fraction,
        # Risk/reward
        risk_reward_ratio=r.risk_reward_ratio,
        recommended_shares=r.recommended_shares,
        recommended_position_size_usd=r.recommended_position_size_usd,
        max_loss_usd=r.max_loss_usd,
        # Setup quality
        setup_quality_score=r.quality_score,
        prior_move_pct=r.prior_move_pct,
        base_length_days=r.base_length_days,
        num_contractions=r.num_contractions,
        tightest_contraction_pct=r.tightest_contraction_pct,
        volume_dry_up_pct=r.volume_dry_up_pct,
        # Relative strength
        rs_rank=r.rs_rank,
        rs_score=r.rs_score,
        rs_vs_spy_6m=r.rs_vs_spy_6m,
        distance_from_52w_high_pct=r.distance_from_52w_high_pct,
        distance_from_200sma_pct=r.distance_from_200sma_pct,
        sma200=r.sma200,
        ema10=r.ema10,
        # Warnings
        earnings_date=r.earnings_date_next,
        days_to_earnings=r.days_to_earnings,
        earnings_warning=r.earnings_warning,
        macro_event_warning=r.macro_event_warning,
        # Market context
        market_status=r.market_status_label,
        spy_above_50sma=r.spy_above_50sma,
        spy_10ema_above_20ema=r.spy_10ema_above_20ema,
        # Legacy
        volume_ma20=r.volume_ma20,
        preferred_quality=r.preferred_quality,
        is_breakout_candidate=r.is_breakout_candidate,
        failed_filters=r.failed_filters,
    )


def _run_scan() -> ScanResponse:
    with get_db() as session:
        result = scan_daily(session)
    candidates = [_to_response(c) for c in result["candidates"]]
    return ScanResponse(
        market_healthy=result["market_healthy"],
        as_of_date=result["as_of_date"],
        candidate_count=len(candidates),
        candidates=candidates,
    )


@router.post("/run", response_model=ScanResponse)
def run_scan():
    """Run the full daily scan pipeline and return VCP candidates."""
    return _run_scan()


@router.get("/latest", response_model=ScanResponse)
def get_latest_scan():
    """Return today's scan results (re-runs the scanner)."""
    return _run_scan()
