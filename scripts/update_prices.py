"""
scripts/update_prices.py
Pull missing OHLCV data from yfinance for all active tickers.

Flow
----
1. Add MaxDate / TotalRecords / LastUpdated columns to Tickers (idempotent DDL).
2. Back-fill NULL MaxDate / TotalRecords from existing PriceData.
3. Fetch active tickers where MaxDate < today (or NULL).
4. Process in batches of 50 with a 2-second delay between batches.
5. After each ticker: commit price rows, refresh Tickers metadata.
6. Log progress to console AND scripts/logs/update_prices_YYYY-MM-DD.log.
7. Print final summary.

Usage
-----
    python scripts/update_prices.py
    python scripts/update_prices.py --dry-run       # show plan only, no downloads
    python scripts/update_prices.py --symbol AAPL   # single ticker
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from backend.db.database import get_db, get_engine
from backend.services.data_updater import update_ticker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE: int = 50
BATCH_DELAY_SECS: float = 2.0

LOG_DIR = PROJECT_ROOT / "scripts" / "logs"


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"update_prices_{date.today().isoformat()}.log"

    fmt = "%(asctime)s  %(levelname)-8s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("update_prices")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Console handler — INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(fmt, datefmt))

    # File handler — DEBUG and above (verbose record per ticker)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _ensure_ticker_columns(log: logging.Logger) -> None:
    """
    Add MaxDate, TotalRecords, LastUpdated to Tickers if they don't exist.
    Safe to call on every run — each ALTER is guarded by a sys.columns check.
    """
    new_columns = [
        ("MaxDate",      "DATE     NULL"),
        ("TotalRecords", "INT      NULL DEFAULT 0"),
        ("LastUpdated",  "DATETIME2 NULL"),
    ]
    engine = get_engine()
    with engine.connect() as conn:
        for col_name, col_def in new_columns:
            conn.execute(text(
                f"IF NOT EXISTS ("
                f"  SELECT 1 FROM sys.columns"
                f"  WHERE object_id = OBJECT_ID('dbo.Tickers') AND name = '{col_name}'"
                f") ALTER TABLE dbo.Tickers ADD {col_name} {col_def}"
            ))
            log.debug("Ensured column Tickers.%s exists", col_name)
        conn.commit()


def _backfill_ticker_metadata(log: logging.Logger) -> None:
    """
    Populate MaxDate and TotalRecords from PriceData for any Tickers rows
    where they are still NULL (first run after column creation).
    """
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            UPDATE t
            SET
                t.MaxDate      = sub.MaxDate,
                t.TotalRecords = sub.TotalRecords,
                t.LastUpdated  = GETDATE()
            FROM dbo.Tickers t
            JOIN (
                SELECT TickerID, MAX(TradeDate) AS MaxDate, COUNT(*) AS TotalRecords
                FROM dbo.PriceData
                GROUP BY TickerID
            ) sub ON sub.TickerID = t.TickerID
            WHERE t.MaxDate IS NULL
        """))
        conn.commit()
    rows_updated = result.rowcount
    if rows_updated:
        log.info("Back-filled metadata for %d ticker(s) with no MaxDate", rows_updated)


# ---------------------------------------------------------------------------
# Ticker queries
# ---------------------------------------------------------------------------

def _get_active_tickers(symbol_filter: str | None) -> list[dict]:
    """
    Return active tickers that need updating.
    Each dict: {symbol, ticker_id, max_date}

    Tickers with MaxDate IS NULL are included (never fetched).
    Tickers already current (MaxDate == today) are excluded.
    """
    engine = get_engine()
    today = date.today()

    where_extra = ""
    params: dict = {"today": today}

    if symbol_filter:
        where_extra = " AND t.Symbol = :symbol"
        params["symbol"] = symbol_filter.upper()

    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT t.Symbol, t.TickerID, t.MaxDate
            FROM dbo.Tickers t
            WHERE t.IsActive = 1
              AND (t.MaxDate IS NULL OR t.MaxDate < :today)
              {where_extra}
            ORDER BY t.Symbol
        """), params).fetchall()

    return [{"symbol": r[0], "ticker_id": r[1], "max_date": r[2]} for r in rows]


def _count_already_current(symbol_filter: str | None) -> int:
    """Count active tickers that are already up to date (MaxDate = today)."""
    engine = get_engine()
    today = date.today()
    params: dict = {"today": today}
    where_extra = ""
    if symbol_filter:
        where_extra = " AND Symbol = :symbol"
        params["symbol"] = symbol_filter.upper()
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            SELECT COUNT(*)
            FROM dbo.Tickers
            WHERE IsActive = 1 AND MaxDate = :today {where_extra}
        """), params).fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Per-ticker metadata refresh
# ---------------------------------------------------------------------------

def _refresh_ticker_metadata(symbol: str) -> tuple[date | None, int]:
    """
    Re-query MAX(TradeDate) and COUNT(*) from PriceData for one ticker
    and write the result back to Tickers.MaxDate / TotalRecords / LastUpdated.

    Returns (max_date, total_records).
    """
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT MAX(pd.TradeDate), COUNT(*)
            FROM dbo.PriceData pd
            JOIN dbo.Tickers t ON t.TickerID = pd.TickerID
            WHERE t.Symbol = :symbol
        """), {"symbol": symbol}).fetchone()

        max_date: date | None = row[0] if row else None
        total: int = row[1] if row else 0

        conn.execute(text("""
            UPDATE dbo.Tickers
            SET
                MaxDate      = :max_date,
                TotalRecords = :total,
                LastUpdated  = GETDATE()
            WHERE Symbol = :symbol
        """), {"max_date": max_date, "total": total, "symbol": symbol})
        conn.commit()

    return max_date, total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull missing OHLCV price data from yfinance for active tickers."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which tickers would be updated without downloading anything.",
    )
    parser.add_argument(
        "--symbol",
        metavar="SYM",
        help="Update a single ticker instead of the full active universe.",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run
    symbol_filter: str | None = args.symbol

    log = _setup_logging()
    log.info("=" * 60)
    log.info("update_prices.py started%s", "  [DRY RUN]" if dry_run else "")

    # ------------------------------------------------------------------
    # 1. Ensure schema columns exist
    # ------------------------------------------------------------------
    log.info("Ensuring Tickers columns (MaxDate, TotalRecords, LastUpdated)...")
    _ensure_ticker_columns(log)

    # ------------------------------------------------------------------
    # 2. Back-fill metadata for any tickers with NULL MaxDate
    # ------------------------------------------------------------------
    _backfill_ticker_metadata(log)

    # ------------------------------------------------------------------
    # 3. Fetch tickers that need updating
    # ------------------------------------------------------------------
    tickers = _get_active_tickers(symbol_filter)
    already_current = _count_already_current(symbol_filter)

    log.info(
        "Tickers to update: %d  |  Already current: %d",
        len(tickers),
        already_current,
    )

    if not tickers:
        log.info("Nothing to do — all active tickers are current.")
        _print_summary(log, updated=0, inserted=0, current=already_current, failed=[])
        return

    if dry_run:
        log.info("[DRY RUN] Tickers that would be updated:")
        for t in tickers:
            log.info("  %-10s  MaxDate=%s", t["symbol"], t["max_date"] or "never fetched")
        _print_summary(log, updated=0, inserted=0, current=already_current, failed=[])
        return

    # ------------------------------------------------------------------
    # 4. Process in batches of BATCH_SIZE
    # ------------------------------------------------------------------
    total_updated = 0
    total_inserted = 0
    failed: list[str] = []

    batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    log.info("Processing %d ticker(s) in %d batch(es) of up to %d", len(tickers), len(batches), BATCH_SIZE)

    for batch_num, batch in enumerate(batches, start=1):
        log.info("--- Batch %d / %d ---", batch_num, len(batches))

        for item in batch:
            sym = item["symbol"]
            old_max = item["max_date"]

            log.info("%-10s  last=%s  fetching...", sym, old_max or "none")

            try:
                # Pull missing price rows and commit.
                with get_db() as session:
                    inserted = update_ticker(sym, session)

                # Refresh Tickers metadata in a separate lightweight query.
                new_max, total_records = _refresh_ticker_metadata(sym)

                if inserted > 0:
                    total_updated += 1
                    total_inserted += inserted
                    log.info(
                        "%-10s  inserted=%d  new MaxDate=%s  total=%d",
                        sym, inserted, new_max, total_records,
                    )
                else:
                    log.debug("%-10s  already current (0 new rows)", sym)

            except Exception as exc:
                log.error("%-10s  FAILED: %s", sym, exc)
                failed.append(sym)

        # Delay between batches (not after the last one)
        if batch_num < len(batches):
            log.debug("Batch %d done — sleeping %.1fs before next batch", batch_num, BATCH_DELAY_SECS)
            time.sleep(BATCH_DELAY_SECS)

    # ------------------------------------------------------------------
    # 5. Summary
    # ------------------------------------------------------------------
    _print_summary(
        log,
        updated=total_updated,
        inserted=total_inserted,
        current=already_current,
        failed=failed,
    )


def _print_summary(
    log: logging.Logger,
    updated: int,
    inserted: int,
    current: int,
    failed: list[str],
) -> None:
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("  Tickers updated:   %d", updated)
    log.info("  New rows inserted: %d", inserted)
    log.info("  Already current:   %d", current)
    log.info("  Failed:            %d", len(failed))
    if failed:
        log.info("  Failed tickers:    %s", ", ".join(failed))
    log.info("=" * 60)


if __name__ == "__main__":
    main()
