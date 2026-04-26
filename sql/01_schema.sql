-- QualMaggie Momentum Swing Trading System
-- sql/01_schema.sql  —  Single source of truth for schema, indexes, and type upgrades.
--
-- USAGE
-- -----
--   Fresh install  : run top-to-bottom once in SSMS — creates DB, tables, indexes.
--   Re-run (safe)  : every CREATE is guarded with IF NOT EXISTS; ALTER TABLE
--                    statements widen columns idempotently (SQL Server metadata-only).
--   Full reset     : uncomment the DROP block below, run, recomment it.
--
-- DATA TYPE RULES (applied uniformly)
-- ------------------------------------
--   Price / indicator columns  →  DECIMAL(18,4)   (up to 99 trillion, 4 dp)
--   Dollar amount columns      →  DECIMAL(18,2)   (up to 99 trillion, 2 dp)
--   Percentage / ratio columns →  DECIMAL(10,6)   (up to 9999, 6 dp)
--   PriceDataID                →  BIGINT          (3.5 M rows today; INT cap = 2.1 B)
--
-- NOTE: Existing databases with INT PriceDataID are not migrated automatically —
-- INT holds up to 2.1 B rows, which is safe for the foreseeable horizon.
-- Newly created databases use BIGINT from the start.

IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = N'QualMaggie')
    CREATE DATABASE QualMaggie;
GO

USE QualMaggie;
GO

-- ============================================================
-- FULL RESET (DANGER — destroys all data)
-- Uncomment only when you need a clean slate.
-- ============================================================
/*
IF OBJECT_ID('dbo.PerformanceReport',  'U') IS NOT NULL DROP TABLE dbo.PerformanceReport;
IF OBJECT_ID('dbo.PortfolioSnapshot',  'U') IS NOT NULL DROP TABLE dbo.PortfolioSnapshot;
IF OBJECT_ID('dbo.Blacklist',          'U') IS NOT NULL DROP TABLE dbo.Blacklist;
IF OBJECT_ID('dbo.OpenPositions',      'U') IS NOT NULL DROP TABLE dbo.OpenPositions;
IF OBJECT_ID('dbo.Trades',             'U') IS NOT NULL DROP TABLE dbo.Trades;
IF OBJECT_ID('dbo.BacktestRuns',       'U') IS NOT NULL DROP TABLE dbo.BacktestRuns;
IF OBJECT_ID('dbo.PriceData',          'U') IS NOT NULL DROP TABLE dbo.PriceData;
IF OBJECT_ID('dbo.Tickers',            'U') IS NOT NULL DROP TABLE dbo.Tickers;
IF OBJECT_ID('dbo.EarningsDates',      'U') IS NOT NULL DROP TABLE dbo.EarningsDates;
IF OBJECT_ID('dbo.MacroEventDates',    'U') IS NOT NULL DROP TABLE dbo.MacroEventDates;
GO
*/


-- ============================================================
-- TABLES
-- ============================================================

-- ------------------------------------------------------------
-- 1. Tickers  —  universe of tracked symbols
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.Tickers', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Tickers (
        TickerID        INT           IDENTITY(1,1) PRIMARY KEY,
        Symbol          VARCHAR(10)   NOT NULL UNIQUE,
        CompanyName     VARCHAR(100)  NOT NULL,
        Sector          VARCHAR(50)   NOT NULL,
        Exchange        VARCHAR(10)   NOT NULL,         -- 'Nasdaq' | 'NYSE'
        IndexMembership VARCHAR(20)   NOT NULL,         -- 'SP500' | 'Nasdaq100' | 'Both'
        IsActive        BIT           NOT NULL DEFAULT 1,
        CreatedAt       DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
    );
    PRINT 'Created table: Tickers';
END;
GO

-- ------------------------------------------------------------
-- 2. PriceData  —  daily OHLCV + computed indicators
--    BIGINT PK: 3.5 M rows today, grows with ticker universe
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.PriceData', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.PriceData (
        PriceDataID BIGINT        IDENTITY(1,1) PRIMARY KEY,
        TickerID    INT           NOT NULL REFERENCES dbo.Tickers(TickerID),
        TradeDate   DATE          NOT NULL,
        OpenPrice   DECIMAL(18,4) NOT NULL,
        HighPrice   DECIMAL(18,4) NOT NULL,
        LowPrice    DECIMAL(18,4) NOT NULL,
        ClosePrice  DECIMAL(18,4) NOT NULL,
        Volume      BIGINT        NOT NULL,
        -- Computed indicators — NULL until calculate_and_store() populates them
        SMA50       DECIMAL(18,4) NULL,
        SMA200      DECIMAL(18,4) NULL,
        EMA10       DECIMAL(18,4) NULL,
        EMA20       DECIMAL(18,4) NULL,
        ATRPct      DECIMAL(10,6) NULL,    -- ATR as % of close price
        ADR         DECIMAL(18,4) NULL,    -- Average Daily Range (absolute $)
        RSScore     DECIMAL(10,6) NULL,    -- Relative Strength vs SPY over RSLookbackDays
        CreatedAt   DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_PriceData_TickerDate UNIQUE (TickerID, TradeDate)
    );
    PRINT 'Created table: PriceData';
END;
GO

-- ------------------------------------------------------------
-- 3. BacktestRuns  —  run metadata + settings snapshot
--    Must exist before Trades / Positions (FK target)
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.BacktestRuns', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.BacktestRuns (
        BacktestRunID  INT           IDENTITY(1,1) PRIMARY KEY,
        RunName        VARCHAR(100)  NOT NULL,
        StartDate      DATE          NOT NULL,
        EndDate        DATE          NOT NULL,
        InitialCapital DECIMAL(18,2) NOT NULL,
        FinalCapital   DECIMAL(18,2) NULL,
        -- Running | Completed | CompletedWithErrors | Partial | Failed
        Status         VARCHAR(25)   NOT NULL DEFAULT 'Running',
        SettingsJson   NVARCHAR(MAX) NOT NULL,
        CreatedAt      DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        CompletedAt    DATETIME2     NULL
    );
    PRINT 'Created table: BacktestRuns';
END;
GO

-- ------------------------------------------------------------
-- 4. Trades  —  completed / closed trades
--    Actual column names verified against backend/db/models.py:
--      EntryPrice, ExitPrice, InitialStopLoss,
--      PartialExitDate, PartialExitPrice, PartialShares
-- ------------------------------------------------------------
-- Diagnostic (run interactively to verify existing column names):
--   SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE
--   FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'Trades'
--   ORDER BY ORDINAL_POSITION;

IF OBJECT_ID('dbo.Trades', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Trades (
        TradeID          INT           IDENTITY(1,1) PRIMARY KEY,
        TickerID         INT           NOT NULL REFERENCES dbo.Tickers(TickerID),
        Symbol           VARCHAR(10)   NOT NULL,
        EntryDate        DATE          NOT NULL,
        ExitDate         DATE          NULL,
        EntryPrice       DECIMAL(18,4) NOT NULL,
        ExitPrice        DECIMAL(18,4) NULL,
        Shares           INT           NOT NULL,
        InitialStopLoss  DECIMAL(18,4) NOT NULL,
        -- Partial exit — 50 % of shares sold when unrealised gain >= PartialSellMinProfitPct
        PartialExitDate  DATE          NULL,
        PartialExitPrice DECIMAL(18,4) NULL,
        PartialShares    INT           NULL,
        -- Metadata
        PatternType      VARCHAR(30)   NOT NULL,   -- VCP | Flag | CupHandle | FlatBase
        Sector           VARCHAR(50)   NOT NULL,
        RiskAmount       DECIMAL(18,2) NOT NULL,
        PnL              DECIMAL(18,2) NULL,
        PnLPct           DECIMAL(10,6) NULL,
        ExitReason       VARCHAR(30)   NULL,        -- StopLoss | TrailStop | MaxHold | Manual
        IsLive           BIT           NOT NULL DEFAULT 1,
        BacktestRunID    INT           NULL REFERENCES dbo.BacktestRuns(BacktestRunID),
        CreatedAt        DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
    );
    PRINT 'Created table: Trades';
END;
GO

-- ------------------------------------------------------------
-- 5. OpenPositions  —  live / in-flight positions
--    Actual column names verified against backend/db/models.py:
--      CurrentStop (trailing stop), InitialStopLoss,
--      PartialSoldPrice, PartialSoldDate, PartialSoldShares
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.OpenPositions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.OpenPositions (
        PositionID        INT           IDENTITY(1,1) PRIMARY KEY,
        TickerID          INT           NOT NULL REFERENCES dbo.Tickers(TickerID),
        Symbol            VARCHAR(10)   NOT NULL,
        EntryDate         DATE          NOT NULL,
        EntryPrice        DECIMAL(18,4) NOT NULL,
        Shares            INT           NOT NULL,
        InitialStopLoss   DECIMAL(18,4) NOT NULL,
        CurrentStop       DECIMAL(18,4) NOT NULL,   -- Trailing stop; starts == InitialStopLoss
        PartialSoldShares INT           NOT NULL DEFAULT 0,
        PartialSoldPrice  DECIMAL(18,4) NULL,
        PartialSoldDate   DATE          NULL,
        PatternType       VARCHAR(30)   NOT NULL,
        Sector            VARCHAR(50)   NOT NULL,
        RiskAmount        DECIMAL(18,2) NOT NULL,
        IsLive            BIT           NOT NULL DEFAULT 1,
        BacktestRunID     INT           NULL REFERENCES dbo.BacktestRuns(BacktestRunID),
        LastUpdated       DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
    );
    PRINT 'Created table: OpenPositions';
END;
GO

-- ------------------------------------------------------------
-- 6. Blacklist  —  10-day re-entry ban after stop-out
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.Blacklist', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Blacklist (
        BlacklistID   INT          IDENTITY(1,1) PRIMARY KEY,
        TickerID      INT          NOT NULL REFERENCES dbo.Tickers(TickerID),
        Symbol        VARCHAR(10)  NOT NULL,
        BlacklistDate DATE         NOT NULL,
        ExpiryDate    DATE         NOT NULL,    -- BlacklistDate + BlacklistDays from settings
        Reason        VARCHAR(50)  NOT NULL DEFAULT 'StopOut',  -- StopOut | Manual
        IsActive      BIT          NOT NULL DEFAULT 1,
        BacktestRunID INT          NULL REFERENCES dbo.BacktestRuns(BacktestRunID),
        CreatedAt     DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME()
    );
    PRINT 'Created table: Blacklist';
END;
GO

-- ------------------------------------------------------------
-- 7. PortfolioSnapshot  —  end-of-day portfolio state
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.PortfolioSnapshot', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.PortfolioSnapshot (
        SnapshotID         INT           IDENTITY(1,1) PRIMARY KEY,
        SnapshotDate       DATE          NOT NULL,
        TotalValue         DECIMAL(18,2) NOT NULL,
        CashBalance        DECIMAL(18,2) NOT NULL,
        OpenPositionsValue DECIMAL(18,2) NOT NULL,
        OpenPositionsCount INT           NOT NULL,
        DailyPnL           DECIMAL(18,2) NOT NULL DEFAULT 0,
        TotalPnL           DECIMAL(18,2) NOT NULL DEFAULT 0,
        TotalPnLPct        DECIMAL(10,6) NOT NULL DEFAULT 0,
        IsLive             BIT           NOT NULL DEFAULT 1,
        BacktestRunID      INT           NULL REFERENCES dbo.BacktestRuns(BacktestRunID),
        CreatedAt          DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_PortfolioSnapshot_DateRun UNIQUE (SnapshotDate, BacktestRunID)
    );
    PRINT 'Created table: PortfolioSnapshot';
END;
GO

-- ------------------------------------------------------------
-- 8. PerformanceReport  —  aggregated stats per run / period
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.PerformanceReport', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.PerformanceReport (
        ReportID       INT           IDENTITY(1,1) PRIMARY KEY,
        ReportDate     DATE          NOT NULL,
        TotalTrades    INT           NOT NULL DEFAULT 0,
        WinningTrades  INT           NOT NULL DEFAULT 0,
        LosingTrades   INT           NOT NULL DEFAULT 0,
        WinRate        DECIMAL(6,4)  NOT NULL DEFAULT 0,   -- 0.0 – 1.0
        AvgWinPct      DECIMAL(8,4)  NOT NULL DEFAULT 0,
        AvgLossPct     DECIMAL(8,4)  NOT NULL DEFAULT 0,
        ProfitFactor   DECIMAL(8,4)  NOT NULL DEFAULT 0,
        MaxDrawdownPct DECIMAL(8,4)  NOT NULL DEFAULT 0,
        SharpeRatio    DECIMAL(8,4)  NULL,
        TotalReturnPct DECIMAL(8,4)  NOT NULL DEFAULT 0,
        IsLive         BIT           NOT NULL DEFAULT 1,
        BacktestRunID  INT           NULL REFERENCES dbo.BacktestRuns(BacktestRunID),
        CreatedAt      DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
    );
    PRINT 'Created table: PerformanceReport';
END;
GO

-- ------------------------------------------------------------
-- 9. EarningsDates  —  upcoming/historical earnings per symbol
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.EarningsDates', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.EarningsDates (
        ID           INT          IDENTITY(1,1) PRIMARY KEY,
        Symbol       NVARCHAR(10) NOT NULL,
        EarningsDate DATE         NOT NULL,
        CreatedAt    DATETIME     NOT NULL DEFAULT GETDATE()
    );
    PRINT 'Created table: EarningsDates';
END;
GO

-- ------------------------------------------------------------
-- 10. MacroEventDates  —  FOMC, CPI, NFP, etc.
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.MacroEventDates', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.MacroEventDates (
        ID        INT          IDENTITY(1,1) PRIMARY KEY,
        EventDate DATE         NOT NULL,
        EventName NVARCHAR(50) NOT NULL,
        CreatedAt DATETIME     NOT NULL DEFAULT GETDATE()
    );
    PRINT 'Created table: MacroEventDates';
END;
GO


-- ============================================================
-- COLUMN TYPE UPGRADES
-- Widens existing columns to match the correct precision rules.
-- Safe to run on a live database — SQL Server treats DECIMAL
-- widening as a metadata-only change; no rows are rewritten.
-- No-op if the column is already at the target precision.
-- ============================================================

-- ------------------------------------------------------------
-- PriceData  —  OHLC + computed indicator columns
-- ------------------------------------------------------------
ALTER TABLE dbo.PriceData ALTER COLUMN OpenPrice  DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN HighPrice  DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN LowPrice   DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN ClosePrice DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN SMA50      DECIMAL(18,4) NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN SMA200     DECIMAL(18,4) NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN EMA10      DECIMAL(18,4) NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN EMA20      DECIMAL(18,4) NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN ADR        DECIMAL(18,4) NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN ATRPct     DECIMAL(10,6) NULL;
ALTER TABLE dbo.PriceData ALTER COLUMN RSScore    DECIMAL(10,6) NULL;
GO

-- ------------------------------------------------------------
-- OpenPositions
--   CurrentStop      — trailing stop, updated daily to EMA
--   InitialStopLoss  — hard stop set at entry
--   EntryPrice
--   PartialSoldPrice — price at 50 % partial exit
--   RiskAmount       — dollar risk at open (18,2 — dollar amount)
-- ------------------------------------------------------------
ALTER TABLE dbo.OpenPositions ALTER COLUMN EntryPrice       DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.OpenPositions ALTER COLUMN InitialStopLoss  DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.OpenPositions ALTER COLUMN CurrentStop      DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.OpenPositions ALTER COLUMN PartialSoldPrice DECIMAL(18,4) NULL;
ALTER TABLE dbo.OpenPositions ALTER COLUMN RiskAmount       DECIMAL(18,2) NOT NULL;
GO

-- ------------------------------------------------------------
-- Trades
--   EntryPrice, ExitPrice, InitialStopLoss, PartialExitPrice
--   RiskAmount, PnL — dollar amounts (18,2)
--   PnLPct          — percentage (10,6)
-- ------------------------------------------------------------
ALTER TABLE dbo.Trades ALTER COLUMN EntryPrice        DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.Trades ALTER COLUMN ExitPrice         DECIMAL(18,4) NULL;
ALTER TABLE dbo.Trades ALTER COLUMN InitialStopLoss   DECIMAL(18,4) NOT NULL;
ALTER TABLE dbo.Trades ALTER COLUMN PartialExitPrice  DECIMAL(18,4) NULL;
ALTER TABLE dbo.Trades ALTER COLUMN RiskAmount        DECIMAL(18,2) NOT NULL;
ALTER TABLE dbo.Trades ALTER COLUMN PnL               DECIMAL(18,2) NULL;
ALTER TABLE dbo.Trades ALTER COLUMN PnLPct            DECIMAL(10,6) NULL;
GO

-- ------------------------------------------------------------
-- PortfolioSnapshot  —  dollar amounts + percentage
-- ------------------------------------------------------------
ALTER TABLE dbo.PortfolioSnapshot ALTER COLUMN TotalValue         DECIMAL(18,2) NOT NULL;
ALTER TABLE dbo.PortfolioSnapshot ALTER COLUMN CashBalance        DECIMAL(18,2) NOT NULL;
ALTER TABLE dbo.PortfolioSnapshot ALTER COLUMN OpenPositionsValue DECIMAL(18,2) NOT NULL;
ALTER TABLE dbo.PortfolioSnapshot ALTER COLUMN DailyPnL           DECIMAL(18,2) NOT NULL;
ALTER TABLE dbo.PortfolioSnapshot ALTER COLUMN TotalPnL           DECIMAL(18,2) NOT NULL;
ALTER TABLE dbo.PortfolioSnapshot ALTER COLUMN TotalPnLPct        DECIMAL(10,6) NOT NULL;
GO

PRINT 'Column type upgrades complete — 29 columns widened across 4 tables.';
GO


-- ============================================================
-- INDEXES
-- All guarded with IF NOT EXISTS — safe to re-run.
-- ============================================================

-- ------------------------------------------------------------
-- PriceData  (most critical — grows to 20 M+ rows)
-- ------------------------------------------------------------

-- Primary read pattern: full ticker history for scanner / backtester
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_PriceData_TickerID_Date'
      AND object_id = OBJECT_ID('dbo.PriceData')
)
    CREATE INDEX IX_PriceData_TickerID_Date
        ON dbo.PriceData (TickerID, TradeDate DESC)
        INCLUDE (OpenPrice, HighPrice, LowPrice, ClosePrice, Volume);
GO

-- Cross-ticker date scan (backtester iterates every unique TradeDate)
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_PriceData_Date'
      AND object_id = OBJECT_ID('dbo.PriceData')
)
    CREATE INDEX IX_PriceData_Date
        ON dbo.PriceData (TradeDate DESC)
        INCLUDE (TickerID, ClosePrice, Volume);
GO

-- ------------------------------------------------------------
-- Tickers
-- ------------------------------------------------------------

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Tickers_Symbol'
      AND object_id = OBJECT_ID('dbo.Tickers')
)
    CREATE INDEX IX_Tickers_Symbol
        ON dbo.Tickers (Symbol)
        INCLUDE (TickerID, IsActive, Sector);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Tickers_Active'
      AND object_id = OBJECT_ID('dbo.Tickers')
)
    CREATE INDEX IX_Tickers_Active
        ON dbo.Tickers (IsActive)
        INCLUDE (Symbol, TickerID, Sector);
GO

-- ------------------------------------------------------------
-- Trades
-- ------------------------------------------------------------

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Trades_BacktestRunID'
      AND object_id = OBJECT_ID('dbo.Trades')
)
    CREATE INDEX IX_Trades_BacktestRunID
        ON dbo.Trades (BacktestRunID)
        INCLUDE (Symbol, EntryDate, ExitDate, PnL, PnLPct);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Trades_Symbol_Date'
      AND object_id = OBJECT_ID('dbo.Trades')
)
    CREATE INDEX IX_Trades_Symbol_Date
        ON dbo.Trades (Symbol, EntryDate DESC);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Trades_IsLive'
      AND object_id = OBJECT_ID('dbo.Trades')
)
    CREATE INDEX IX_Trades_IsLive
        ON dbo.Trades (IsLive)
        INCLUDE (Symbol, EntryDate, ExitDate, PnL);
GO

-- Filtered unique index prevents duplicate trades within a backtest run.
-- FILTERED because BacktestRunID is NULLable (live trades have BacktestRunID = NULL,
-- and NULL != NULL under standard UNIQUE constraints).
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_Trades_Backtest_Symbol_Entry'
      AND object_id = OBJECT_ID('dbo.Trades')
)
    CREATE UNIQUE INDEX UQ_Trades_Backtest_Symbol_Entry
        ON dbo.Trades (BacktestRunID, Symbol, EntryDate, ExitDate, Shares)
        WHERE BacktestRunID IS NOT NULL;
GO

-- ------------------------------------------------------------
-- Blacklist
-- ------------------------------------------------------------

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Blacklist_Symbol'
      AND object_id = OBJECT_ID('dbo.Blacklist')
)
    CREATE INDEX IX_Blacklist_Symbol
        ON dbo.Blacklist (Symbol, ExpiryDate)
        INCLUDE (IsActive, BacktestRunID);
GO

-- ------------------------------------------------------------
-- EarningsDates
-- ------------------------------------------------------------

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EarningsDates_Symbol_Date'
      AND object_id = OBJECT_ID('dbo.EarningsDates')
)
    CREATE INDEX IX_EarningsDates_Symbol_Date
        ON dbo.EarningsDates (Symbol, EarningsDate);
GO

-- ------------------------------------------------------------
-- MacroEventDates
-- ------------------------------------------------------------

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_MacroEventDates_Date'
      AND object_id = OBJECT_ID('dbo.MacroEventDates')
)
    CREATE INDEX IX_MacroEventDates_Date
        ON dbo.MacroEventDates (EventDate);
GO

PRINT 'sql/01_schema.sql complete — 10 tables, 29 column upgrades, 11 indexes.';
