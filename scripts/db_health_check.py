"""
scripts/db_health_check.py
Database health check for QualMaggie.

Checks
------
  1. Row counts for every table.
  2. PriceData date range (min, max, total rows) per active ticker.
  3. Flags tickers whose MaxDate is more than STALE_DAYS old.

Output
------
  console                                    — formatted report
  scripts/logs/db_health_YYYY-MM-DD.txt      — same report saved to file

Usage
-----
    python scripts/db_health_check.py
    python scripts/db_health_check.py --stale-days 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from backend.db.database import get_engine

LOG_DIR = Path(__file__).resolve().parent / "logs"
STALE_DAYS_DEFAULT = 7

# All tables in schema order
_TABLES = [
    "Tickers",
    "PriceData",
    "BacktestRuns",
    "Trades",
    "OpenPositions",
    "Blacklist",
    "PortfolioSnapshot",
    "PerformanceReport",
    "EarningsDates",
    "MacroEventDates",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QualMaggie database health check.")
    p.add_argument(
        "--stale-days",
        type=int,
        default=STALE_DAYS_DEFAULT,
        metavar="N",
        help=f"Flag tickers with MaxDate older than N days (default: {STALE_DAYS_DEFAULT})",
    )
    return p.parse_args()


def _table_counts(engine) -> list[tuple[str, int]]:
    rows = []
    with engine.connect() as conn:
        for tbl in _TABLES:
            # Check the table exists before counting (handles optional tables)
            exists = conn.execute(text(
                "SELECT 1 FROM sys.tables WHERE name = :n AND schema_id = SCHEMA_ID('dbo')"
            ), {"n": tbl}).fetchone()
            if exists:
                count = conn.execute(text(f"SELECT COUNT(*) FROM dbo.[{tbl}]")).scalar()
                rows.append((tbl, count))
            else:
                rows.append((tbl, -1))  # -1 = table missing
    return rows


def _price_data_ranges(engine) -> list[dict]:
    with engine.connect() as conn:
        raw = conn.execute(text("""
            SELECT
                t.Symbol,
                t.IsActive,
                MIN(pd.TradeDate)  AS MinDate,
                MAX(pd.TradeDate)  AS MaxDate,
                COUNT(*)           AS TotalRows
            FROM dbo.Tickers t
            LEFT JOIN dbo.PriceData pd ON pd.TickerID = t.TickerID
            GROUP BY t.Symbol, t.IsActive
            ORDER BY t.Symbol
        """)).fetchall()
    return [
        {
            "symbol":    r[0],
            "is_active": bool(r[1]),
            "min_date":  r[2],
            "max_date":  r[3],
            "total":     r[4],
        }
        for r in raw
    ]


def _build_report(table_counts: list, price_ranges: list, stale_days: int) -> list[str]:
    today = date.today()
    stale_cutoff = today - timedelta(days=stale_days)
    lines: list[str] = []

    def add(line: str = "") -> None:
        lines.append(line)

    add("=" * 60)
    add(f"  QualMaggie DB Health Check — {today.isoformat()}")
    add("=" * 60)
    add()

    # ------------------------------------------------------------------ table counts
    add("  TABLE ROW COUNTS")
    add("  " + "-" * 40)
    for tbl, count in table_counts:
        if count == -1:
            add(f"  {tbl:<28}  MISSING")
        else:
            add(f"  {tbl:<28}  {count:>8,}")
    add()

    # ------------------------------------------------------------------ price data ranges
    active = [r for r in price_ranges if r["is_active"]]
    inactive = [r for r in price_ranges if not r["is_active"]]

    stale: list[str] = []

    add("  PRICE DATA — ACTIVE TICKERS")
    add(f"  {'Symbol':<8} {'MinDate':<12} {'MaxDate':<12} {'Rows':>7}  {'Status'}")
    add("  " + "-" * 55)

    for r in active:
        symbol   = r["symbol"]
        min_date = str(r["min_date"]) if r["min_date"] else "none"
        max_date = str(r["max_date"]) if r["max_date"] else "none"
        total    = r["total"] or 0

        if r["max_date"] is None:
            status = "NO DATA"
        elif r["max_date"] < stale_cutoff:
            days_old = (today - r["max_date"]).days
            status = f"STALE ({days_old}d old)"
            stale.append(symbol)
        else:
            status = "OK"

        add(f"  {symbol:<8} {min_date:<12} {max_date:<12} {total:>7,}  {status}")

    add()

    if inactive:
        add(f"  INACTIVE TICKERS ({len(inactive)}):")
        add("  " + ", ".join(r["symbol"] for r in inactive))
        add()

    # ------------------------------------------------------------------ stale summary
    if stale:
        add(f"  STALE TICKERS (MaxDate > {stale_days} days old): {len(stale)}")
        add("  " + ", ".join(stale))
        add(f"  → Run: python scripts/update_prices.py")
    else:
        add(f"  All active tickers have data within the last {stale_days} days.")

    add()
    add("=" * 60)
    return lines


def main() -> None:
    args = _parse_args()

    print("Connecting to database...")
    engine = get_engine()

    try:
        table_counts = _table_counts(engine)
        price_ranges = _price_data_ranges(engine)
    except Exception as exc:
        print(f"ERROR: Database query failed — {exc}", file=sys.stderr)
        sys.exit(1)

    lines = _build_report(table_counts, price_ranges, args.stale_days)
    report = "\n".join(lines)

    # Print to console
    print(report)

    # Save to file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOG_DIR / f"db_health_{date.today().isoformat()}.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
        f.write("\n")
    print(f"\n  Report saved → {out_path}")


if __name__ == "__main__":
    main()
