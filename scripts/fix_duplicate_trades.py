"""
Fix duplicate trades in the Trades table.

Duplicates are defined as rows sharing the same (BacktestRunID, Symbol, EntryDate).
Strategy: keep the row with the lowest TradeID, delete the rest.
After cleanup, add a UNIQUE constraint on those three columns.

Run with: python scripts/fix_duplicate_trades.py
Add --dry-run to preview without making changes.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pyodbc
from backend.config.settings import get_settings


def get_connection() -> pyodbc.Connection:
    s = get_settings()
    conn_str = str(s.db_connection_string)
    # pyodbc expects semicolon-separated DSN string, not SQLAlchemy URL.
    # Parse the SQLAlchemy URL into a pyodbc connection string.
    # Expected format: mssql+pyodbc://server/db?driver=...&trusted_connection=yes
    import re
    m = re.search(r"//([^/]+)/([^?]+)\?(.+)", conn_str)
    if not m:
        raise ValueError(f"Cannot parse DB_CONNECTION_STRING: {conn_str}")
    server, database, params_str = m.group(1), m.group(2), m.group(3)
    params = dict(p.split("=", 1) for p in params_str.split("&") if "=" in p)
    driver = params.get("driver", "ODBC+Driver+17+for+SQL+Server").replace("+", " ")
    trusted = params.get("trusted_connection", "yes")
    dsn = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"Trusted_Connection={trusted};"
    )
    return pyodbc.connect(dsn)


def find_duplicates(cursor: pyodbc.Cursor) -> list[dict]:
    cursor.execute("""
        SELECT
            BacktestRunID,
            Symbol,
            EntryDate,
            COUNT(*)           AS DupeCount,
            MIN(TradeID)       AS KeepID,
            STRING_AGG(CAST(TradeID AS VARCHAR), ', ')
                WITHIN GROUP (ORDER BY TradeID) AS AllIDs
        FROM Trades
        GROUP BY BacktestRunID, Symbol, EntryDate
        HAVING COUNT(*) > 1
        ORDER BY BacktestRunID, Symbol, EntryDate
    """)
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def delete_duplicates(cursor: pyodbc.Cursor, dry_run: bool) -> int:
    """Delete all but the lowest TradeID for each duplicate group. Returns row count deleted."""
    sql = """
        DELETE t
        FROM Trades t
        INNER JOIN (
            SELECT
                BacktestRunID,
                Symbol,
                EntryDate,
                MIN(TradeID) AS KeepID
            FROM Trades
            GROUP BY BacktestRunID, Symbol, EntryDate
            HAVING COUNT(*) > 1
        ) dupes
            ON  t.BacktestRunID = dupes.BacktestRunID
            AND t.Symbol        = dupes.Symbol
            AND t.EntryDate     = dupes.EntryDate
            AND t.TradeID      <> dupes.KeepID
    """
    if dry_run:
        # Count rows that would be deleted without actually deleting.
        count_sql = """
            SELECT COUNT(*) FROM Trades t
            INNER JOIN (
                SELECT BacktestRunID, Symbol, EntryDate, MIN(TradeID) AS KeepID
                FROM Trades
                GROUP BY BacktestRunID, Symbol, EntryDate
                HAVING COUNT(*) > 1
            ) dupes
                ON  t.BacktestRunID = dupes.BacktestRunID
                AND t.Symbol        = dupes.Symbol
                AND t.EntryDate     = dupes.EntryDate
                AND t.TradeID      <> dupes.KeepID
        """
        cursor.execute(count_sql)
        return cursor.fetchone()[0]
    cursor.execute(sql)
    return cursor.rowcount


def constraint_exists(cursor: pyodbc.Cursor) -> bool:
    cursor.execute("""
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID('dbo.Trades')
          AND name = 'UQ_Trades_RunSymbolEntry'
    """)
    return cursor.fetchone() is not None


def add_unique_constraint(cursor: pyodbc.Cursor, dry_run: bool) -> None:
    if constraint_exists(cursor):
        print("  UNIQUE constraint UQ_Trades_RunSymbolEntry already exists — skipping.")
        return
    ddl = """
        ALTER TABLE Trades
        ADD CONSTRAINT UQ_Trades_RunSymbolEntry
        UNIQUE (BacktestRunID, Symbol, EntryDate)
    """
    if dry_run:
        print("  [DRY RUN] Would execute:")
        print("  " + ddl.strip())
        return
    cursor.execute(ddl)
    print("  UNIQUE constraint UQ_Trades_RunSymbolEntry added.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix duplicate trades in the Trades table.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making any changes.",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    if dry_run:
        print("=== DRY RUN MODE — no changes will be made ===\n")

    conn = get_connection()
    conn.autocommit = False
    cursor = conn.cursor()

    try:
        # 1. Report duplicates.
        dupes = find_duplicates(cursor)
        if not dupes:
            print("No duplicate trades found.")
        else:
            total_extra = sum(d["DupeCount"] - 1 for d in dupes)
            print(f"Found {len(dupes)} duplicate group(s), {total_extra} extra row(s) to delete:\n")
            print(f"  {'BacktestRunID':<15} {'Symbol':<8} {'EntryDate':<12} {'Count':<6} {'Keep':<8} All IDs")
            print("  " + "-" * 70)
            for d in dupes:
                print(
                    f"  {str(d['BacktestRunID']):<15} "
                    f"{d['Symbol']:<8} "
                    f"{str(d['EntryDate']):<12} "
                    f"{d['DupeCount']:<6} "
                    f"{d['KeepID']:<8} "
                    f"{d['AllIDs']}"
                )
            print()

            # 2. Delete duplicates.
            deleted = delete_duplicates(cursor, dry_run)
            if dry_run:
                print(f"[DRY RUN] Would delete {deleted} row(s).")
            else:
                print(f"Deleted {deleted} duplicate row(s).")

        # 3. Add UNIQUE constraint.
        print("\nChecking UNIQUE constraint...")
        add_unique_constraint(cursor, dry_run)

        if not dry_run:
            conn.commit()
            print("\nAll changes committed.")
        else:
            conn.rollback()
            print("\n[DRY RUN] No changes committed.")

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
