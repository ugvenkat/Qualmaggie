"""
SQLAlchemy 2.x ORM models for QualMaggie.

Each class maps 1-to-1 with a SQL Server table from 01_schema.sql.
Column names (DB) are preserved via the first positional arg to mapped_column();
Python attribute names are snake_case.

Live trades/positions use IsLive=True and BacktestRunID=NULL.
Backtest trades/positions use IsLive=False and a valid BacktestRunID.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# SQL Server expression used as server-side default for all CreatedAt / LastUpdated columns.
# Declaring it here means SQLAlchemy fetches the generated value after INSERT and
# ORM objects are fully populated without requiring the caller to set created_at manually.
_SYSUTC = text("SYSUTCDATETIME()")


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Tickers
# ---------------------------------------------------------------------------

class Ticker(Base):
    __tablename__ = "Tickers"

    ticker_id: Mapped[int] = mapped_column("TickerID", Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column("Symbol", String(10), nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column("CompanyName", String(100), nullable=False)
    sector: Mapped[str] = mapped_column("Sector", String(50), nullable=False)
    exchange: Mapped[str] = mapped_column("Exchange", String(10), nullable=False)
    index_membership: Mapped[str] = mapped_column("IndexMembership", String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False, server_default=_SYSUTC)

    # Relationships
    price_data: Mapped[list[PriceData]] = relationship(back_populates="ticker")
    trades: Mapped[list[Trade]] = relationship(back_populates="ticker")
    open_positions: Mapped[list[OpenPosition]] = relationship(back_populates="ticker")
    blacklist_entries: Mapped[list[Blacklist]] = relationship(back_populates="ticker")


# ---------------------------------------------------------------------------
# PriceData
# ---------------------------------------------------------------------------

class PriceData(Base):
    __tablename__ = "PriceData"
    __table_args__ = (
        UniqueConstraint("TickerID", "TradeDate", name="UQ_PriceData_TickerDate"),
    )

    price_data_id: Mapped[int] = mapped_column("PriceDataID", Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        "TickerID", Integer, ForeignKey("Tickers.TickerID"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column("TradeDate", Date, nullable=False)
    open_price: Mapped[float] = mapped_column("OpenPrice", Numeric(18, 4), nullable=False)
    high_price: Mapped[float] = mapped_column("HighPrice", Numeric(18, 4), nullable=False)
    low_price: Mapped[float] = mapped_column("LowPrice", Numeric(18, 4), nullable=False)
    close_price: Mapped[float] = mapped_column("ClosePrice", Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column("Volume", BigInteger, nullable=False)
    # Computed indicators — NULL until calculate_indicators() populates them
    sma50: Mapped[Optional[float]] = mapped_column("SMA50", Numeric(18, 4), nullable=True)
    sma200: Mapped[Optional[float]] = mapped_column("SMA200", Numeric(18, 4), nullable=True)
    ema10: Mapped[Optional[float]] = mapped_column("EMA10", Numeric(18, 4), nullable=True)
    ema20: Mapped[Optional[float]] = mapped_column("EMA20", Numeric(18, 4), nullable=True)
    atr_pct: Mapped[Optional[float]] = mapped_column("ATRPct", Numeric(10, 6), nullable=True)
    adr: Mapped[Optional[float]] = mapped_column("ADR", Numeric(18, 4), nullable=True)
    rs_score: Mapped[Optional[float]] = mapped_column("RSScore", Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False, server_default=_SYSUTC)

    ticker: Mapped[Ticker] = relationship(back_populates="price_data")


# ---------------------------------------------------------------------------
# BacktestRuns
# ---------------------------------------------------------------------------

class BacktestRun(Base):
    __tablename__ = "BacktestRuns"

    backtest_run_id: Mapped[int] = mapped_column("BacktestRunID", Integer, primary_key=True)
    run_name: Mapped[str] = mapped_column("RunName", String(100), nullable=False)
    start_date: Mapped[date] = mapped_column("StartDate", Date, nullable=False)
    end_date: Mapped[date] = mapped_column("EndDate", Date, nullable=False)
    initial_capital: Mapped[float] = mapped_column(
        "InitialCapital", Numeric(15, 2), nullable=False
    )
    final_capital: Mapped[Optional[float]] = mapped_column(
        "FinalCapital", Numeric(15, 2), nullable=True
    )
    # Running | Completed | Failed
    status: Mapped[str] = mapped_column(
        "Status", String(20), nullable=False, default="Running"
    )
    settings_json: Mapped[str] = mapped_column("SettingsJson", Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False, server_default=_SYSUTC)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        "CompletedAt", DateTime, nullable=True
    )

    trades: Mapped[list[Trade]] = relationship(back_populates="backtest_run")
    open_positions: Mapped[list[OpenPosition]] = relationship(back_populates="backtest_run")
    blacklist_entries: Mapped[list[Blacklist]] = relationship(back_populates="backtest_run")
    snapshots: Mapped[list[PortfolioSnapshot]] = relationship(back_populates="backtest_run")
    performance_reports: Mapped[list[PerformanceReport]] = relationship(
        back_populates="backtest_run"
    )


# ---------------------------------------------------------------------------
# Trades  (closed positions)
# ---------------------------------------------------------------------------

class Trade(Base):
    __tablename__ = "Trades"

    trade_id: Mapped[int] = mapped_column("TradeID", Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        "TickerID", Integer, ForeignKey("Tickers.TickerID"), nullable=False
    )
    symbol: Mapped[str] = mapped_column("Symbol", String(10), nullable=False)
    entry_date: Mapped[date] = mapped_column("EntryDate", Date, nullable=False)
    exit_date: Mapped[Optional[date]] = mapped_column("ExitDate", Date, nullable=True)
    entry_price: Mapped[float] = mapped_column("EntryPrice", Numeric(18, 4), nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(
        "ExitPrice", Numeric(18, 4), nullable=True
    )
    shares: Mapped[int] = mapped_column("Shares", Integer, nullable=False)
    initial_stop_loss: Mapped[float] = mapped_column(
        "InitialStopLoss", Numeric(18, 4), nullable=False
    )
    # Partial exit — 50% of shares sold at day PartialSellDays
    partial_exit_date: Mapped[Optional[date]] = mapped_column(
        "PartialExitDate", Date, nullable=True
    )
    partial_exit_price: Mapped[Optional[float]] = mapped_column(
        "PartialExitPrice", Numeric(18, 4), nullable=True
    )
    partial_shares: Mapped[Optional[int]] = mapped_column(
        "PartialShares", Integer, nullable=True
    )
    # VCP | Flag | CupHandle | FlatBase
    pattern_type: Mapped[str] = mapped_column("PatternType", String(30), nullable=False)
    sector: Mapped[str] = mapped_column("Sector", String(50), nullable=False)
    risk_amount: Mapped[float] = mapped_column("RiskAmount", Numeric(18, 2), nullable=False)
    pnl: Mapped[Optional[float]] = mapped_column("PnL", Numeric(18, 2), nullable=True)
    pnl_pct: Mapped[Optional[float]] = mapped_column("PnLPct", Numeric(10, 6), nullable=True)
    # StopLoss | TrailStop | MaxHold | Manual
    exit_reason: Mapped[Optional[str]] = mapped_column("ExitReason", String(30), nullable=True)
    is_live: Mapped[bool] = mapped_column("IsLive", Boolean, nullable=False, default=True)
    backtest_run_id: Mapped[Optional[int]] = mapped_column(
        "BacktestRunID",
        Integer,
        ForeignKey("BacktestRuns.BacktestRunID"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False, server_default=_SYSUTC)

    ticker: Mapped[Ticker] = relationship(back_populates="trades")
    backtest_run: Mapped[Optional[BacktestRun]] = relationship(back_populates="trades")


# ---------------------------------------------------------------------------
# OpenPositions  (live / in-flight)
# ---------------------------------------------------------------------------

class OpenPosition(Base):
    __tablename__ = "OpenPositions"

    position_id: Mapped[int] = mapped_column("PositionID", Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        "TickerID", Integer, ForeignKey("Tickers.TickerID"), nullable=False
    )
    symbol: Mapped[str] = mapped_column("Symbol", String(10), nullable=False)
    entry_date: Mapped[date] = mapped_column("EntryDate", Date, nullable=False)
    entry_price: Mapped[float] = mapped_column("EntryPrice", Numeric(18, 4), nullable=False)
    shares: Mapped[int] = mapped_column("Shares", Integer, nullable=False)
    initial_stop_loss: Mapped[float] = mapped_column(
        "InitialStopLoss", Numeric(18, 4), nullable=False
    )
    # Trailing stop — updated daily to follow 10 EMA after partial exit
    current_stop: Mapped[float] = mapped_column("CurrentStop", Numeric(18, 4), nullable=False)
    partial_sold_shares: Mapped[int] = mapped_column(
        "PartialSoldShares", Integer, nullable=False, default=0
    )
    partial_sold_price: Mapped[Optional[float]] = mapped_column(
        "PartialSoldPrice", Numeric(18, 4), nullable=True
    )
    partial_sold_date: Mapped[Optional[date]] = mapped_column(
        "PartialSoldDate", Date, nullable=True
    )
    pattern_type: Mapped[str] = mapped_column("PatternType", String(30), nullable=False)
    sector: Mapped[str] = mapped_column("Sector", String(50), nullable=False)
    risk_amount: Mapped[float] = mapped_column("RiskAmount", Numeric(18, 2), nullable=False)
    is_live: Mapped[bool] = mapped_column("IsLive", Boolean, nullable=False, default=True)
    backtest_run_id: Mapped[Optional[int]] = mapped_column(
        "BacktestRunID",
        Integer,
        ForeignKey("BacktestRuns.BacktestRunID"),
        nullable=True,
    )
    last_updated: Mapped[datetime] = mapped_column("LastUpdated", DateTime, nullable=False, server_default=_SYSUTC)

    ticker: Mapped[Ticker] = relationship(back_populates="open_positions")
    backtest_run: Mapped[Optional[BacktestRun]] = relationship(back_populates="open_positions")

    def days_held(self, as_of: date) -> int:
        entry = self.entry_date.date() if hasattr(self.entry_date, "date") else self.entry_date
        return (as_of - entry).days

    def partial_exit_due(self, partial_sell_days: int, as_of: date | None = None) -> bool:
        """True when the partial-exit window has arrived and not yet executed."""
        if as_of is None:
            as_of = date.today()
        return self.days_held(as_of) >= partial_sell_days and self.partial_sold_shares == 0


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------

class Blacklist(Base):
    __tablename__ = "Blacklist"

    blacklist_id: Mapped[int] = mapped_column("BlacklistID", Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        "TickerID", Integer, ForeignKey("Tickers.TickerID"), nullable=False
    )
    symbol: Mapped[str] = mapped_column("Symbol", String(10), nullable=False)
    blacklist_date: Mapped[date] = mapped_column("BlacklistDate", Date, nullable=False)
    expiry_date: Mapped[date] = mapped_column("ExpiryDate", Date, nullable=False)
    # StopOut | Manual
    reason: Mapped[str] = mapped_column(
        "Reason", String(50), nullable=False, default="StopOut"
    )
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean, nullable=False, default=True)
    backtest_run_id: Mapped[Optional[int]] = mapped_column(
        "BacktestRunID",
        Integer,
        ForeignKey("BacktestRuns.BacktestRunID"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False, server_default=_SYSUTC)

    ticker: Mapped[Ticker] = relationship(back_populates="blacklist_entries")
    backtest_run: Mapped[Optional[BacktestRun]] = relationship(
        back_populates="blacklist_entries"
    )


# ---------------------------------------------------------------------------
# PortfolioSnapshot
# ---------------------------------------------------------------------------

class PortfolioSnapshot(Base):
    __tablename__ = "PortfolioSnapshot"
    __table_args__ = (
        UniqueConstraint(
            "SnapshotDate", "BacktestRunID", name="UQ_PortfolioSnapshot_DateRun"
        ),
    )

    snapshot_id: Mapped[int] = mapped_column("SnapshotID", Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column("SnapshotDate", Date, nullable=False)
    total_value: Mapped[float] = mapped_column("TotalValue", Numeric(18, 2), nullable=False)
    cash_balance: Mapped[float] = mapped_column("CashBalance", Numeric(18, 2), nullable=False)
    open_positions_value: Mapped[float] = mapped_column(
        "OpenPositionsValue", Numeric(18, 2), nullable=False
    )
    open_positions_count: Mapped[int] = mapped_column(
        "OpenPositionsCount", Integer, nullable=False
    )
    daily_pnl: Mapped[float] = mapped_column(
        "DailyPnL", Numeric(18, 2), nullable=False, default=0
    )
    total_pnl: Mapped[float] = mapped_column(
        "TotalPnL", Numeric(18, 2), nullable=False, default=0
    )
    total_pnl_pct: Mapped[float] = mapped_column(
        "TotalPnLPct", Numeric(10, 6), nullable=False, default=0
    )
    is_live: Mapped[bool] = mapped_column("IsLive", Boolean, nullable=False, default=True)
    backtest_run_id: Mapped[Optional[int]] = mapped_column(
        "BacktestRunID",
        Integer,
        ForeignKey("BacktestRuns.BacktestRunID"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False, server_default=_SYSUTC)

    backtest_run: Mapped[Optional[BacktestRun]] = relationship(back_populates="snapshots")


# ---------------------------------------------------------------------------
# PerformanceReport
# ---------------------------------------------------------------------------

class PerformanceReport(Base):
    __tablename__ = "PerformanceReport"

    report_id: Mapped[int] = mapped_column("ReportID", Integer, primary_key=True)
    report_date: Mapped[date] = mapped_column("ReportDate", Date, nullable=False)
    total_trades: Mapped[int] = mapped_column(
        "TotalTrades", Integer, nullable=False, default=0
    )
    winning_trades: Mapped[int] = mapped_column(
        "WinningTrades", Integer, nullable=False, default=0
    )
    losing_trades: Mapped[int] = mapped_column(
        "LosingTrades", Integer, nullable=False, default=0
    )
    win_rate: Mapped[float] = mapped_column(
        "WinRate", Numeric(6, 4), nullable=False, default=0
    )
    avg_win_pct: Mapped[float] = mapped_column(
        "AvgWinPct", Numeric(8, 4), nullable=False, default=0
    )
    avg_loss_pct: Mapped[float] = mapped_column(
        "AvgLossPct", Numeric(8, 4), nullable=False, default=0
    )
    profit_factor: Mapped[float] = mapped_column(
        "ProfitFactor", Numeric(8, 4), nullable=False, default=0
    )
    max_drawdown_pct: Mapped[float] = mapped_column(
        "MaxDrawdownPct", Numeric(8, 4), nullable=False, default=0
    )
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(
        "SharpeRatio", Numeric(8, 4), nullable=True
    )
    total_return_pct: Mapped[float] = mapped_column(
        "TotalReturnPct", Numeric(8, 4), nullable=False, default=0
    )
    is_live: Mapped[bool] = mapped_column("IsLive", Boolean, nullable=False, default=True)
    backtest_run_id: Mapped[Optional[int]] = mapped_column(
        "BacktestRunID",
        Integer,
        ForeignKey("BacktestRuns.BacktestRunID"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime, nullable=False, server_default=_SYSUTC)

    backtest_run: Mapped[Optional[BacktestRun]] = relationship(
        back_populates="performance_reports"
    )


# ---------------------------------------------------------------------------
# EarningsDate
# ---------------------------------------------------------------------------

class EarningsDate(Base):
    __tablename__ = "EarningsDates"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column("Symbol", String(10), nullable=False)
    earnings_date: Mapped[date] = mapped_column("EarningsDate", Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime, nullable=False, server_default=text("GETDATE()")
    )


# ---------------------------------------------------------------------------
# MacroEventDate
# ---------------------------------------------------------------------------

class MacroEventDate(Base):
    __tablename__ = "MacroEventDates"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    event_date: Mapped[date] = mapped_column("EventDate", Date, nullable=False)
    event_name: Mapped[str] = mapped_column("EventName", String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime, nullable=False, server_default=text("GETDATE()")
    )
