"""
Positions and trades routes.

GET /api/positions              — All open live positions.
GET /api/positions/history      — All closed live trades (alias for /api/trades).
GET /api/trades                 — All closed live trades.
GET /api/trades/{trade_id}      — Single trade detail.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import OpenPosition, Ticker, Trade

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class OpenPositionResponse(BaseModel):
    position_id: int
    symbol: str
    sector: str
    entry_date: date
    entry_price: float
    shares: int
    initial_stop_loss: float
    current_stop: float
    partial_sold_shares: Optional[int]
    partial_sold_price: Optional[float]
    partial_sold_date: Optional[date]
    pattern_type: str
    risk_amount: float

    model_config = {"from_attributes": True}


class TradeResponse(BaseModel):
    trade_id: int
    symbol: str
    sector: str
    entry_date: date
    exit_date: Optional[date]
    entry_price: float
    exit_price: Optional[float]
    shares: int
    initial_stop_loss: float
    partial_exit_date: Optional[date]
    partial_exit_price: Optional[float]
    partial_shares: Optional[int]
    pattern_type: str
    pnl: Optional[float]
    pnl_pct: Optional[float]
    exit_reason: Optional[str]
    backtest_run_id: Optional[int]

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/positions/history", response_model=list[TradeResponse])
def get_positions_history():
    """Return backtest trades for active tickers only, most recent exit first.

    Joins Tickers so that deactivated symbols (e.g. SOXL) are excluded —
    their historical trades would otherwise pollute the history tab.
    """
    with get_db() as session:
        trades = session.execute(
            select(Trade)
            .join(Ticker, Trade.ticker_id == Ticker.ticker_id)
            .where(Trade.is_live == False)   # noqa: E712
            .where(Ticker.is_active == True) # noqa: E712
            .order_by(Trade.exit_date.desc())
        ).scalars().all()
        return [TradeResponse.model_validate(t) for t in trades]


@router.get("/positions", response_model=list[OpenPositionResponse])
def get_open_positions():
    """Return all currently open live positions."""
    with get_db() as session:
        positions = session.execute(
            select(OpenPosition)
            .where(OpenPosition.is_live == True)  # noqa: E712
            .where(OpenPosition.backtest_run_id == None)  # noqa: E711
            .order_by(OpenPosition.entry_date.desc())
        ).scalars().all()
        return [OpenPositionResponse.model_validate(p) for p in positions]


@router.get("/trades", response_model=list[TradeResponse])
def get_trades():
    """Return all closed live trades, most recent first."""
    with get_db() as session:
        trades = session.execute(
            select(Trade)
            .where(Trade.is_live == True)  # noqa: E712
            .where(Trade.backtest_run_id == None)  # noqa: E711
            .where(Trade.exit_date != None)  # noqa: E711
            .order_by(Trade.exit_date.desc())
        ).scalars().all()
        return [TradeResponse.model_validate(t) for t in trades]


@router.get("/trades/{trade_id}", response_model=TradeResponse)
def get_trade(trade_id: int):
    """Return a single trade by ID."""
    with get_db() as session:
        trade = session.get(Trade, trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
        return TradeResponse.model_validate(trade)
