"""
Backtest routes.

POST /api/backtest/run                    — Run a new backtest (synchronous).
GET  /api/backtest                        — List all backtest runs.
GET  /api/backtest/{run_id}               — Single run + performance report.
GET  /api/backtest/{run_id}/trades        — Trades for a specific run.
GET  /api/backtest/{run_id}/snapshots     — Portfolio equity curve for a run.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.backtester import run_backtest
from backend.db.database import get_db
from backend.db.models import BacktestRun, PerformanceReport, PortfolioSnapshot, Trade

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class BacktestRunRequest(BaseModel):
    run_name: str = "Backtest"
    start_date: date
    end_date: date


class BacktestRunSummary(BaseModel):
    backtest_run_id: int
    run_name: str
    start_date: date
    end_date: date
    initial_capital: float
    final_capital: Optional[float]
    status: str
    created_at: Optional[str]
    completed_at: Optional[str]

    model_config = {"from_attributes": True}


class BacktestDetailResponse(BaseModel):
    run: BacktestRunSummary
    performance: Optional[dict]


class TradeResponse(BaseModel):
    trade_id: int
    symbol: str
    sector: str
    entry_date: date
    exit_date: Optional[date]
    entry_price: float
    exit_price: Optional[float]
    shares: int
    pnl: Optional[float]
    pnl_pct: Optional[float]
    exit_reason: Optional[str]

    model_config = {"from_attributes": True}


class SnapshotResponse(BaseModel):
    snapshot_id: int
    snapshot_date: date
    total_value: float
    cash_balance: float
    open_positions_value: float
    open_positions_count: int

    model_config = {"from_attributes": True}


def _run_summary(run: BacktestRun) -> BacktestRunSummary:
    return BacktestRunSummary(
        backtest_run_id=run.backtest_run_id,
        run_name=run.run_name,
        start_date=run.start_date,
        end_date=run.end_date,
        initial_capital=float(run.initial_capital),
        final_capital=float(run.final_capital) if run.final_capital is not None else None,
        status=run.status,
        created_at=run.created_at.isoformat() if run.created_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/run", response_model=BacktestRunSummary)
def run_backtest_endpoint(body: BacktestRunRequest):
    """
    Run a backtest synchronously.  The client should set a long timeout (≥300s).
    Returns the completed BacktestRun record when finished.
    """
    if body.end_date <= body.start_date:
        raise HTTPException(status_code=422, detail="end_date must be after start_date")

    with get_db() as session:
        backtest_run = run_backtest(
            session,
            start_date=body.start_date,
            end_date=body.end_date,
            run_name=body.run_name,
        )

    return _run_summary(backtest_run)


@router.get("", response_model=list[BacktestRunSummary])
def list_backtest_runs():
    """Return all backtest runs, most recent first."""
    with get_db() as session:
        runs = session.execute(
            select(BacktestRun).order_by(BacktestRun.created_at.desc())
        ).scalars().all()
        return [_run_summary(r) for r in runs]


@router.get("/{run_id}/trades", response_model=list[TradeResponse])
def get_backtest_trades(run_id: int):
    """Return all trades for a specific backtest run."""
    with get_db() as session:
        trades = session.execute(
            select(Trade)
            .where(Trade.backtest_run_id == run_id)
            .order_by(Trade.entry_date)
        ).scalars().all()
        return [TradeResponse.model_validate(t) for t in trades]


@router.get("/{run_id}/snapshots", response_model=list[SnapshotResponse])
def get_backtest_snapshots(run_id: int):
    """Return the portfolio equity curve for a specific backtest run."""
    with get_db() as session:
        snapshots = session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.backtest_run_id == run_id)
            .order_by(PortfolioSnapshot.snapshot_date)
        ).scalars().all()
        return [SnapshotResponse.model_validate(s) for s in snapshots]


@router.get("/{run_id}", response_model=BacktestDetailResponse)
def get_backtest_run(run_id: int):
    """Return a single backtest run with its performance report."""
    with get_db() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found")

        report = session.execute(
            select(PerformanceReport)
            .where(PerformanceReport.backtest_run_id == run_id)
            .order_by(PerformanceReport.created_at.desc())
        ).scalars().first()

        perf = None
        if report is not None:
            perf = {
                "total_trades": report.total_trades,
                "winning_trades": report.winning_trades,
                "losing_trades": report.losing_trades,
                "win_rate": report.win_rate,
                "avg_win_pct": report.avg_win_pct,
                "avg_loss_pct": report.avg_loss_pct,
                "profit_factor": report.profit_factor,
                "max_drawdown_pct": report.max_drawdown_pct,
                "sharpe_ratio": report.sharpe_ratio,
                "total_return_pct": report.total_return_pct,
            }

        return BacktestDetailResponse(run=_run_summary(run), performance=perf)
