"""
Market status route.

GET /api/market/status  — Returns current SPY market filter status.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.market_filter import check_market
from backend.db.database import get_db

router = APIRouter()


class MarketStatusResponse(BaseModel):
    is_healthy: bool
    as_of_date: Optional[date]
    close: Optional[float]
    sma50: Optional[float]
    ema10: Optional[float]
    ema20: Optional[float]
    close_above_sma50: bool
    ema10_above_ema20: bool


@router.get("/status", response_model=MarketStatusResponse)
def get_market_status():
    """Return the current market filter status based on SPY indicators."""
    with get_db() as session:
        status = check_market(session)
    return MarketStatusResponse(**status)
