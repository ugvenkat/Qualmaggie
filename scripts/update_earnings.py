"""
scripts/update_earnings.py
Fetch earnings dates from yfinance for all active tickers and update EarningsDates table.

Uses the existing earnings_updater service which:
  - Replaces all stored dates for each ticker on each call
  - Commits per ticker so partial runs survive errors
  - Skips ETFs that have no earnings (SPY, QQQ, SOXL, etc.)

Output
------
  console   — progress per ticker + final summary
  scripts/logs/update_earnings_YYYY-MM-DD.log

Usage
-----
    python scripts/update_earnings.py
    python scripts/update_earnings.py --symbol AAPL   # single ticker
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import Ticker
from backend.services.earnings_updater import update_earnings_dates

LOG_DIR = Path(__file__).resolve().parent / "logs"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch earnings dates from yfinance and update EarningsDates table."
    )
    p.add_argument(
        "--symbol",
        metavar="SYM",
        help="Update a single ticker instead of the full active universe.",
    )
    return p.parse_args()


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"update_earnings_{date.today().isoformat()}.log"

    fmt = "%(asctime)s  %(levelname)-8s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("update_earnings")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(fmt, datefmt))

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


def main() -> None:
    args = _parse_args()
    log = _setup_logging()
    log.info("=" * 56)
    log.info("update_earnings.py started")

    if args.symbol:
        # Single-ticker mode: temporarily narrow the active set by patching
        # the session query used inside update_earnings_dates.
        # Simpler: call the service and filter the result ourselves.
        symbol = args.symbol.upper()
        log.info("Single-ticker mode: %s", symbol)

        with get_db() as session:
            # Verify the ticker exists and is active
            row = session.execute(
                select(Ticker.symbol, Ticker.is_active).where(Ticker.symbol == symbol)
            ).fetchone()
            if row is None:
                log.error("Symbol '%s' not found in Tickers table.", symbol)
                sys.exit(1)
            if not row[1]:
                log.warning("Symbol '%s' is inactive (IsActive=0) — proceeding anyway.", symbol)

        # The service iterates all active tickers; for a single-ticker run we
        # temporarily deactivate all others in-memory by querying and filtering.
        # Easier: just call the service for all and read back the one result.
        # For single-ticker efficiency, open a session and call the low-level path.
        import yfinance as yf
        from sqlalchemy import delete
        from backend.db.models import EarningsDate
        from backend.services.earnings_updater import _NO_EARNINGS  # type: ignore[attr-defined]

        if symbol in _NO_EARNINGS:
            log.info("%s is an ETF / no-earnings instrument — skipping.", symbol)
            sys.exit(0)

        with get_db() as session:
            ticker = yf.Ticker(symbol)
            df = ticker.earnings_dates
            if df is None or df.empty:
                log.warning("%s: yfinance returned no earnings dates.", symbol)
                results = {symbol: 0}
            else:
                session.execute(delete(EarningsDate).where(EarningsDate.symbol == symbol))
                seen: set = set()
                count = 0
                for idx in df.index:
                    ed = idx.date() if hasattr(idx, "date") else idx
                    if ed in seen:
                        continue
                    seen.add(ed)
                    session.add(EarningsDate(symbol=symbol, earnings_date=ed))
                    count += 1
                results = {symbol: count}
                log.info("%s: stored %d earnings dates", symbol, count)
    else:
        log.info("Updating earnings for all active tickers...")
        with get_db() as session:
            results = update_earnings_dates(session)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_dates = sum(results.values())
    log.info("=" * 56)
    log.info("SUMMARY")
    log.info("  Tickers processed : %d", len(results))
    log.info("  Total dates stored: %d", total_dates)
    if results:
        log.info("  Per-ticker breakdown:")
        for sym, cnt in sorted(results.items()):
            log.info("    %-10s  %d dates", sym, cnt)
    log.info("=" * 56)


if __name__ == "__main__":
    main()
