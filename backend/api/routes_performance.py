"""
Performance routes.

GET /api/performance             — Latest live PerformanceReport.
GET /api/performance/snapshots   — Live PortfolioSnapshot history.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import PerformanceReport, PortfolioSnapshot

router = APIRouter()


class PerformanceResponse(BaseModel):
    report_id: int
    report_date: date
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_return_pct: float

    model_config = {"from_attributes": True}


class SnapshotResponse(BaseModel):
    snapshot_id: int
    snapshot_date: date
    total_value: float
    cash_balance: float
    open_positions_value: float
    open_positions_count: int

    model_config = {"from_attributes": True}


@router.get("", response_model=PerformanceResponse)
def get_performance():
    """Return the latest live performance report."""
    with get_db() as session:
        report = session.execute(
            select(PerformanceReport)
            .where(PerformanceReport.is_live == True)  # noqa: E712
            .where(PerformanceReport.backtest_run_id == None)  # noqa: E711
            .order_by(PerformanceReport.created_at.desc())
        ).scalars().first()

        if report is None:
            raise HTTPException(status_code=404, detail="No performance report available yet")
        return PerformanceResponse.model_validate(report)


@router.get("/snapshots", response_model=list[SnapshotResponse])
def get_snapshots():
    """Return the live portfolio equity curve (all snapshots)."""
    with get_db() as session:
        snapshots = session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.is_live == True)  # noqa: E712
            .where(PortfolioSnapshot.backtest_run_id == None)  # noqa: E711
            .order_by(PortfolioSnapshot.snapshot_date)
        ).scalars().all()
        return [SnapshotResponse.model_validate(s) for s in snapshots]
