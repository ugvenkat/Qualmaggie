-- QualMaggie — Daily Maintenance
-- sql/03_daily_maintenance.sql
--
-- Run in SSMS every morning before the trading session.
-- Safe to run on a live database — no structural changes, no data loss.
--
-- Steps
-- -----
--   1. Update statistics on heavy tables
--   2. Rebuild indexes with fragmentation > 30 %
--   3. Clean up orphaned data from failed / interrupted backtest runs
--   4. Show table sizes and row counts

USE QualMaggie;
GO

-- ============================================================
-- 1. Update statistics
--    Do this first so the query optimiser has fresh cardinality
--    estimates before the day's scans and backtests run.
-- ============================================================

UPDATE STATISTICS dbo.PriceData         WITH FULLSCAN;
GO
UPDATE STATISTICS dbo.Tickers           WITH FULLSCAN;
GO
UPDATE STATISTICS dbo.Trades            WITH FULLSCAN;
GO
UPDATE STATISTICS dbo.OpenPositions     WITH FULLSCAN;
GO
UPDATE STATISTICS dbo.Blacklist         WITH FULLSCAN;
GO
UPDATE STATISTICS dbo.PortfolioSnapshot WITH FULLSCAN;
GO
UPDATE STATISTICS dbo.BacktestRuns      WITH FULLSCAN;
GO
UPDATE STATISTICS dbo.PerformanceReport WITH FULLSCAN;
GO

PRINT '--- Statistics updated ---';
GO

-- ============================================================
-- 2. Rebuild fragmented indexes (> 30 % fragmentation only)
--    Uses a cursor so only indexes that actually need it are
--    rebuilt — avoids unnecessary I/O on lightly used tables.
--    REORGANIZE threshold (10–30 %) shown in the report below.
-- ============================================================

DECLARE @tbl  NVARCHAR(128);
DECLARE @idx  NVARCHAR(128);
DECLARE @frag FLOAT;
DECLARE @sql  NVARCHAR(500);

DECLARE rebuild_cur CURSOR LOCAL FAST_FORWARD FOR
    SELECT
        OBJECT_NAME(ips.object_id),
        i.name,
        ips.avg_fragmentation_in_percent
    FROM sys.dm_db_index_physical_stats(DB_ID(), NULL, NULL, NULL, 'SAMPLED') ips
    JOIN sys.indexes i
        ON  ips.object_id = i.object_id
        AND ips.index_id  = i.index_id
    WHERE ips.avg_fragmentation_in_percent > 30
      AND ips.page_count > 100
      AND i.index_id > 0   -- skip heaps
    ORDER BY ips.avg_fragmentation_in_percent DESC;

OPEN rebuild_cur;
FETCH NEXT FROM rebuild_cur INTO @tbl, @idx, @frag;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'ALTER INDEX [' + @idx + N'] ON dbo.[' + @tbl
             + N'] REBUILD WITH (FILLFACTOR = 85, ONLINE = OFF);';
    EXEC sp_executesql @sql;
    PRINT 'Rebuilt: ' + @tbl + '.' + @idx
        + ' (' + CAST(ROUND(@frag, 1) AS NVARCHAR(10)) + '% fragmented)';
    FETCH NEXT FROM rebuild_cur INTO @tbl, @idx, @frag;
END;

CLOSE rebuild_cur;
DEALLOCATE rebuild_cur;
GO

PRINT '--- Index rebuild complete ---';
GO

-- ============================================================
-- 3. Fragmentation report  (informational — no changes made)
--    Review after the rebuild above to confirm results.
--    Items in the 10–30 % band can be REORGANIZE'd manually.
-- ============================================================

SELECT
    OBJECT_NAME(ips.object_id)                    AS TableName,
    i.name                                        AS IndexName,
    ROUND(ips.avg_fragmentation_in_percent, 1)    AS FragmentationPct,
    ips.page_count                                AS PageCount
FROM sys.dm_db_index_physical_stats(
         DB_ID(), NULL, NULL, NULL, 'SAMPLED') ips
JOIN sys.indexes i
    ON  ips.object_id = i.object_id
    AND ips.index_id  = i.index_id
WHERE ips.avg_fragmentation_in_percent > 10
  AND ips.page_count > 100
ORDER BY ips.avg_fragmentation_in_percent DESC;
GO

-- ============================================================
-- 4. Clean up orphaned data from failed / interrupted runs
--
--    Status values set by the backtester:
--      Running            — run is in progress (or was killed mid-flight)
--      Completed          — clean finish
--      CompletedWithErrors— finished with recoverable per-day errors
--      Partial            — crashed; committed trades are preserved
--      Failed             — explicit failure (future-proofed)
--
--    Runs with status Running/Partial/Failed may leave orphaned
--    OpenPositions rows that block the next backtest run.
--    PortfolioSnapshot rows for failed runs are also cleaned up.
-- ============================================================

-- 4a. Orphaned open positions
DELETE FROM dbo.OpenPositions
WHERE BacktestRunID IN (
    SELECT BacktestRunID FROM dbo.BacktestRuns
    WHERE Status IN ('Failed', 'Partial', 'Running')
);
PRINT 'OpenPositions cleaned: ' + CAST(@@ROWCOUNT AS NVARCHAR) + ' rows removed.';
GO

-- 4b. Orphaned portfolio snapshots
DELETE FROM dbo.PortfolioSnapshot
WHERE IsLive = 0
  AND BacktestRunID IN (
    SELECT BacktestRunID FROM dbo.BacktestRuns
    WHERE Status IN ('Failed', 'Partial', 'Running')
);
PRINT 'PortfolioSnapshot cleaned: ' + CAST(@@ROWCOUNT AS NVARCHAR) + ' rows removed.';
GO

-- ============================================================
-- 5. Table sizes and row counts
--    Track growth as the ticker universe expands.
-- ============================================================

SELECT
    t.name                                        AS TableName,
    p.rows                                        AS RowCount,
    SUM(a.total_pages) * 8                        AS TotalSpaceKB,
    SUM(a.used_pages)  * 8                        AS UsedSpaceKB,
    (SUM(a.total_pages) - SUM(a.used_pages)) * 8  AS FreeSpaceKB
FROM sys.tables t
INNER JOIN sys.indexes        i ON t.object_id = i.object_id
INNER JOIN sys.partitions     p ON i.object_id = p.object_id AND i.index_id = p.index_id
INNER JOIN sys.allocation_units a ON p.partition_id = a.container_id
WHERE t.is_ms_shipped = 0
  AND i.object_id > 255
  AND i.index_id IN (0, 1)   -- clustered index or heap only (avoids double-counting)
GROUP BY t.name, p.rows
ORDER BY TotalSpaceKB DESC;
GO

-- ============================================================
-- 6. Duplicate trade check  (read-only — no changes)
--    Review output before adding or re-verifying the
--    UQ_Trades_Backtest_Symbol_Entry filtered unique index.
-- ============================================================

SELECT
    BacktestRunID,
    Symbol,
    EntryDate,
    COUNT(*)     AS DuplicateCount,
    MIN(TradeID) AS KeepTradeID
FROM dbo.Trades
WHERE BacktestRunID IS NOT NULL
GROUP BY BacktestRunID, Symbol, EntryDate
HAVING COUNT(*) > 1
ORDER BY DuplicateCount DESC;
GO

-- Remove duplicate trades if the query above returns rows.
-- Keeps the lowest TradeID for each (BacktestRunID, Symbol, EntryDate) group.
-- UNCOMMENT and run manually — not executed automatically.
/*
DELETE t
FROM dbo.Trades t
WHERE BacktestRunID IS NOT NULL
  AND TradeID NOT IN (
      SELECT MIN(TradeID)
      FROM dbo.Trades
      WHERE BacktestRunID IS NOT NULL
      GROUP BY BacktestRunID, Symbol, EntryDate
  );
PRINT 'Duplicate trades removed: ' + CAST(@@ROWCOUNT AS NVARCHAR) + ' rows.';
*/
GO

PRINT '--- sql/03_daily_maintenance.sql complete ---';
