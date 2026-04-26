"""
QualMaggie FastAPI application entry point.

Run with:
    uvicorn backend.main:app --reload

API docs available at:
    http://localhost:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import (
    routes_backtest,
    routes_data,
    routes_market,
    routes_performance,
    routes_positions,
    routes_scan,
    routes_settings,
)
from backend.config.settings import get_settings
from backend.db.database import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure data folders exist and warm up the DB connection pool."""
    settings = get_settings()
    settings.ensure_folders_exist()
    get_engine()   # initialises pool; any connection error surfaces here
    yield
    # Shutdown: SQLAlchemy disposes pool automatically


app = FastAPI(
    title="QualMaggie",
    description="Momentum swing trading system — VCP pattern scanner and backtester",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_market.router,      prefix="/api/market",      tags=["Market"])
app.include_router(routes_scan.router,        prefix="/api/scan",        tags=["Scanner"])
app.include_router(routes_positions.router,   prefix="/api",             tags=["Positions"])
app.include_router(routes_data.router,        prefix="/api/data",        tags=["Data"])
app.include_router(routes_performance.router, prefix="/api/performance", tags=["Performance"])
app.include_router(routes_backtest.router,    prefix="/api/backtest",    tags=["Backtest"])
app.include_router(routes_settings.router,    prefix="/api/settings",    tags=["Settings"])
