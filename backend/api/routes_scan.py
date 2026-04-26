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
    pivot_high: Optional[float]
    distance_from_high_pct: Optional[float]
    contractions_count: Optional[int]
    preferred_quality: bool
    is_breakout_candidate: bool
    has_earnings_warning: bool
    has_macro_warning: bool
    failed_filters: list[str]

    model_config = {"from_attributes": True}


class ScanResponse(BaseModel):
    market_healthy: bool
    as_of_date: date
    candidate_count: int
    candidates: list[ScanResultResponse]


def _to_response(r: ScanResult) -> ScanResultResponse:
    return ScanResultResponse(
        symbol=r.symbol,
        sector=r.sector,
        exchange=r.exchange,
        index_membership=r.index_membership,
        close=r.close,
        volume_ma20=r.volume_ma20,
        atr_pct=r.atr_pct,
        sma200=r.sma200,
        ema10=r.ema10,
        rs_score=r.rs_score,
        pivot_high=r.pivot_high,
        distance_from_high_pct=r.distance_from_high_pct,
        contractions_count=r.contractions_count,
        preferred_quality=r.preferred_quality,
        is_breakout_candidate=r.is_breakout_candidate,
        has_earnings_warning=r.has_earnings_warning,
        has_macro_warning=r.has_macro_warning,
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
