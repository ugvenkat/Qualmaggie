-- =============================================================================
-- hard_reset.sql  —  HARD RESET  (DESTRUCTIVE)
-- Deletes ALL data: backtests, trades, positions, earnings, price history,
-- and tickers.  Requires a full re-import of CSV data afterward.
--
-- WARNING: This is completely irreversible.
-- Run via POST /api/data/hard-reset with {"confirmation": "CONFIRM"}
-- =============================================================================

PRINT 'WARNING: Starting HARD RESET — ALL data will be deleted.';
PRINT '';

DECLARE @n INT;
SELECT @n = COUNT(*) FROM dbo.Blacklist;         PRINT '  Blacklist:          ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.OpenPositions;     PRINT '  OpenPositions:      ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.PortfolioSnapshot; PRINT '  PortfolioSnapshot:  ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.PerformanceReport; PRINT '  PerformanceReport:  ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.Trades;            PRINT '  Trades:             ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.BacktestRuns;      PRINT '  BacktestRuns:       ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.EarningsDates;     PRINT '  EarningsDates:      ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.MacroEventDates;   PRINT '  MacroEventDates:    ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.PriceData;         PRINT '  PriceData:          ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.Tickers;           PRINT '  Tickers:            ' + CAST(@n AS NVARCHAR(20)) + ' rows';
PRINT '';

-- Delete in FK-safe order (children before parents)
DELETE FROM dbo.Blacklist;
DELETE FROM dbo.OpenPositions;
DELETE FROM dbo.PortfolioSnapshot;
DELETE FROM dbo.PerformanceReport;
DELETE FROM dbo.Trades;
DELETE FROM dbo.BacktestRuns;
DELETE FROM dbo.EarningsDates;
DELETE FROM dbo.MacroEventDates;
DELETE FROM dbo.PriceData;
DELETE FROM dbo.Tickers;

-- Reset identity seeds
DBCC CHECKIDENT ('dbo.Blacklist',         RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.OpenPositions',     RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.PortfolioSnapshot', RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.PerformanceReport', RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.Trades',            RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.BacktestRuns',      RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.EarningsDates',     RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.MacroEventDates',   RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.PriceData',         RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.Tickers',           RESEED, 0) WITH NO_INFOMSGS;

PRINT 'Hard reset complete.';
PRINT 'All tables are empty. Re-import price data using the Data Management page.';
