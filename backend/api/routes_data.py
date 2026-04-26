"""
Data management routes.

POST /api/data/import-csv       — Upload a Barchart CSV file and import it.
POST /api/data/import-folder    — Scan the dataInput folder and import all CSVs.
POST /api/data/update           — Pull latest prices from yfinance for all tickers.
POST /api/data/update-earnings  — Fetch earnings dates from yfinance for all active tickers.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select, text

from backend.config.settings import get_settings
from backend.core.indicators import calculate_all, calculate_and_store
from backend.db.database import get_db
from backend.db.models import PriceData, Ticker
from backend.services.data_importer import import_csv, import_folder
from backend.services.data_updater import update_all_tickers
from backend.services.earnings_updater import update_earnings_dates

router = APIRouter()


class ImportResponse(BaseModel):
    file: str
    symbols_found: int
    symbols_already_existed: int
    symbols_auto_created: int
    rows_by_symbol: dict[str, int]
    total_rows_inserted: int
    rows_skipped_duplicates: int
    import_time_seconds: float


class FolderImportResponse(BaseModel):
    folder: str
    symbols_found: int
    symbols_already_existed: int
    symbols_auto_created: int
    rows_by_symbol: dict[str, int]
    total_rows_inserted: int
    rows_skipped_duplicates: int
    import_time_seconds: float


class UpdateResponse(BaseModel):
    symbols_updated: int
    total_rows_inserted: int
    results: dict[str, int]


class EarningsUpdateResponse(BaseModel):
    symbols_processed: int
    dates_by_symbol: dict[str, int]


class TickerStatusResponse(BaseModel):
    symbol: str
    sector: str
    is_active: bool
    row_count: int
    earliest_date: Optional[str]
    last_date: Optional[str]


class ResetResponse(BaseModel):
    rows_deleted: dict[str, int]
    message: str


class HardResetRequest(BaseModel):
    confirmation: str


@router.post("/import-csv", response_model=ImportResponse)
async def import_csv_file(file: UploadFile):
    """
    Upload a Barchart CSV file (multi-ticker format) and import all symbols found.

    After import, indicators are recalculated for every affected ticker.
    """
    settings = get_settings()
    settings.ensure_folders_exist()

    safe_name = Path(file.filename).name if file.filename else "upload.csv"
    dest_path = settings.data_input_folder / safe_name

    try:
        with open(dest_path, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save file: {exc}") from exc

    with get_db() as session:
        stats = import_csv(dest_path, session)

    # Recalculate indicators for each imported symbol
    with get_db() as session:
        for symbol in stats.rows_by_symbol:
            try:
                calculate_and_store(symbol, session)
                session.commit()
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Indicator calculation failed for {symbol}: {exc}",
                ) from exc

    return ImportResponse(
        file=str(dest_path),
        symbols_found=stats.symbols_found,
        symbols_already_existed=stats.symbols_already_existed,
        symbols_auto_created=stats.symbols_auto_created,
        rows_by_symbol=stats.rows_by_symbol,
        total_rows_inserted=stats.total_rows_inserted,
        rows_skipped_duplicates=stats.rows_skipped_duplicates,
        import_time_seconds=stats.import_time_seconds,
    )


@router.post("/import-folder", response_model=FolderImportResponse)
def import_folder_endpoint():
    """
    Scan the configured dataInput folder, import all CSV files found,
    then recalculate indicators for every symbol that received new rows.
    """
    settings = get_settings()
    settings.ensure_folders_exist()
    folder = settings.data_input_folder

    with get_db() as session:
        stats = import_folder(folder, session)

    # Recalculate indicators for symbols that received new rows
    symbols_with_data = [s for s, c in stats.rows_by_symbol.items() if c > 0]
    with get_db() as session:
        for symbol in symbols_with_data:
            try:
                calculate_and_store(symbol, session)
                session.commit()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error(
                    "Indicator calculation failed for %s: %s", symbol, exc
                )

    return FolderImportResponse(
        folder=str(folder),
        symbols_found=stats.symbols_found,
        symbols_already_existed=stats.symbols_already_existed,
        symbols_auto_created=stats.symbols_auto_created,
        rows_by_symbol=stats.rows_by_symbol,
        total_rows_inserted=stats.total_rows_inserted,
        rows_skipped_duplicates=stats.rows_skipped_duplicates,
        import_time_seconds=stats.import_time_seconds,
    )


@router.post("/update", response_model=UpdateResponse)
def update_all():
    """
    Pull the latest price data from yfinance for all active tickers, then
    recalculate all technical indicators.
    """
    with get_db() as session:
        update_results = update_all_tickers(session)

    with get_db() as session:
        calculate_all(session)

    total = sum(update_results.values())
    return UpdateResponse(
        symbols_updated=len(update_results),
        total_rows_inserted=total,
        results=update_results,
    )


@router.post("/update-earnings", response_model=EarningsUpdateResponse)
def update_earnings():
    """
    Fetch upcoming and historical earnings dates from yfinance for all active
    tickers and store them in the EarningsDates table.  Safe to re-run; existing
    dates for each symbol are replaced on each call.
    """
    with get_db() as session:
        results = update_earnings_dates(session)

    return EarningsUpdateResponse(
        symbols_processed=len(results),
        dates_by_symbol=results,
    )


@router.post("/reset-backtests", response_model=ResetResponse)
def reset_backtests():
    """
    Soft reset: delete all backtest history (trades, positions, snapshots,
    performance reports, blacklist, backtest runs).  Price data and tickers
    are preserved.  Identity columns are reseeded to 0.
    """
    # Delete in FK-safe order (children before parents)
    _SOFT_TABLES = [
        "Blacklist",
        "PortfolioSnapshot",
        "OpenPositions",
        "PerformanceReport",
        "Trades",
        "BacktestRuns",
    ]
    rows_deleted: dict[str, int] = {}
    with get_db() as session:
        for table in _SOFT_TABLES:
            result = session.execute(text(f"DELETE FROM dbo.{table}"))
            rows_deleted[table] = result.rowcount
        for table in _SOFT_TABLES:
            session.execute(text(f"DBCC CHECKIDENT ('dbo.{table}', RESEED, 0)"))
    return ResetResponse(
        rows_deleted=rows_deleted,
        message="Soft reset complete. Price data and tickers preserved.",
    )


@router.post("/hard-reset", response_model=ResetResponse)
def hard_reset(body: HardResetRequest):
    """
    Hard reset: deletes ALL data including price history and tickers.
    Requires body: {"confirmation": "CONFIRM"}.
    Identity columns for all tables are reseeded to 0.
    """
    if body.confirmation != "CONFIRM":
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Send {\"confirmation\": \"CONFIRM\"}.",
        )

    _ALL_TABLES_ORDERED = [
        "Blacklist",
        "OpenPositions",
        "PortfolioSnapshot",
        "PerformanceReport",
        "Trades",
        "BacktestRuns",
        "EarningsDates",
        "MacroEventDates",
        "PriceData",
        "Tickers",
    ]
    rows_deleted: dict[str, int] = {}
    with get_db() as session:
        for table in _ALL_TABLES_ORDERED:
            result = session.execute(text(f"DELETE FROM dbo.{table}"))
            rows_deleted[table] = result.rowcount
        for table in _ALL_TABLES_ORDERED:
            session.execute(text(f"DBCC CHECKIDENT ('dbo.{table}', RESEED, 0)"))
    return ResetResponse(
        rows_deleted=rows_deleted,
        message="Hard reset complete. All tables are empty.",
    )


@router.get("/status", response_model=list[TickerStatusResponse])
def get_data_status():
    """Return row count and most recent trade date for every ticker."""
    with get_db() as session:
        rows = session.execute(
            select(
                Ticker.symbol,
                Ticker.sector,
                Ticker.is_active,
                func.count(PriceData.price_data_id).label("row_count"),
                func.min(PriceData.trade_date).label("earliest_date"),
                func.max(PriceData.trade_date).label("last_date"),
            )
            .outerjoin(PriceData, PriceData.ticker_id == Ticker.ticker_id)
            .group_by(Ticker.ticker_id, Ticker.symbol, Ticker.sector, Ticker.is_active)
            .order_by(Ticker.symbol)
        ).all()

    return [
        TickerStatusResponse(
            symbol=r.symbol,
            sector=r.sector or "",
            is_active=bool(r.is_active),
            row_count=r.row_count or 0,
            earliest_date=r.earliest_date.isoformat() if r.earliest_date else None,
            last_date=r.last_date.isoformat() if r.last_date else None,
        )
        for r in rows
    ]
