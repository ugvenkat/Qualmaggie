-- =============================================================================
-- reset_backtests.sql  —  SOFT RESET
-- Deletes all backtest/trading data. Keeps price data and tickers intact.
-- Safe to re-run any time.  Run in SSMS or via POST /api/data/reset-backtests.
-- =============================================================================

PRINT 'Starting soft reset...';
PRINT '';

DECLARE @n INT;
SELECT @n = COUNT(*) FROM dbo.Blacklist;         PRINT '  Blacklist:          ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.OpenPositions;     PRINT '  OpenPositions:      ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.PortfolioSnapshot; PRINT '  PortfolioSnapshot:  ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.PerformanceReport; PRINT '  PerformanceReport:  ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.Trades;            PRINT '  Trades:             ' + CAST(@n AS NVARCHAR(20)) + ' rows';
SELECT @n = COUNT(*) FROM dbo.BacktestRuns;      PRINT '  BacktestRuns:       ' + CAST(@n AS NVARCHAR(20)) + ' rows';
PRINT '';

-- Delete in FK-safe order (children before parents)
DELETE FROM dbo.Blacklist;
DELETE FROM dbo.PortfolioSnapshot;
DELETE FROM dbo.OpenPositions;
DELETE FROM dbo.PerformanceReport;
DELETE FROM dbo.Trades;
DELETE FROM dbo.BacktestRuns;

-- Reset identity seeds so next run starts at ID 1
DBCC CHECKIDENT ('dbo.Blacklist',         RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.OpenPositions',     RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.PortfolioSnapshot', RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.PerformanceReport', RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.Trades',            RESEED, 0) WITH NO_INFOMSGS;
DBCC CHECKIDENT ('dbo.BacktestRuns',      RESEED, 0) WITH NO_INFOMSGS;

PRINT 'Soft reset complete.';
PRINT 'Price data and tickers are preserved.';
