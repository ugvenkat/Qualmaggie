"""
Barchart CSV importer for QualMaggie.

Supported Barchart daily export format
---------------------------------------
Columns:  Symbol, Time, Open, High, Low, Latest, Change, %Change, Volume
- Symbol   = ticker (groups rows when file contains multiple tickers)
- Latest   = close price  (older exports may use 'Last' — both accepted)
- Change / %Change = dropped
- Volume   may contain commas  (e.g. "1,234,567")
- Rows     are newest-first    (reversed before insert)
- Date fmt "%m/%d/%Y"
- Footer   "Downloaded from Barchart.com…" — non-parseable date; stripped
            automatically by errors="coerce" + dropna

Multi-ticker files
------------------
If the CSV contains a 'Symbol' column each unique symbol is imported
separately.  Tickers not present in the Tickers table are auto-created
with placeholder sector/exchange values and logged as warnings.

Public API
----------
    import_csv(file_path, session, symbol=None)   -> ImportStats
    import_folder(folder, session)                -> ImportStats
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models import PriceData, Ticker

logger = logging.getLogger(__name__)

_DROP_COLS = ["Change", "%Change"]
_BATCH_SIZE = 1000          # rows per executemany call
_PROGRESS_INTERVAL = 5000   # log progress every N rows


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ImportStats:
    """Aggregated results from a CSV import operation."""
    rows_by_symbol: dict[str, int] = field(default_factory=dict)
    symbols_found: int = 0
    symbols_already_existed: int = 0
    symbols_auto_created: int = 0
    total_rows_inserted: int = 0
    rows_skipped_duplicates: int = 0
    import_time_seconds: float = 0.0

    def merge(self, other: ImportStats) -> None:
        """Add another ImportStats into this one (used by import_folder)."""
        for sym, count in other.rows_by_symbol.items():
            self.rows_by_symbol[sym] = self.rows_by_symbol.get(sym, 0) + count
        self.symbols_found += other.symbols_found
        self.symbols_already_existed += other.symbols_already_existed
        self.symbols_auto_created += other.symbols_auto_created
        self.total_rows_inserted += other.total_rows_inserted
        self.rows_skipped_duplicates += other.rows_skipped_duplicates
        # import_time_seconds is set by the caller after the loop


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_csv(file_path: Path) -> pd.DataFrame:
    """
    Read and clean a Barchart CSV.

    Expected header (no spaces after commas):
        Symbol,Time,Open,High,Low,Latest,Change,%Change,Volume
    Date format: YYYY-MM-DD  (e.g. 2010-03-11)
    Footer:      "Downloaded from Barchart.com as of ..."  — stripped explicitly.

    Returns a DataFrame with columns:
        Symbol (str), TradeDate (date), Open, High, Low, Close (float), Volume (int)
    """
    print(f"[import] Reading file: {file_path}")

    df = pd.read_csv(file_path, encoding="utf-8", skipinitialspace=True)
    df.columns = df.columns.str.strip()

    print(f"[import] Columns found: {list(df.columns)}")
    print(f"[import] Raw row count: {len(df)}")

    # Strip footer rows ("Downloaded from Barchart.com ...")
    first_col = df.columns[0]
    footer_mask = df[first_col].astype(str).str.contains("Downloaded from Barchart", na=False)
    if footer_mask.any():
        print(f"[import] Stripping {footer_mask.sum()} footer row(s)")
        df = df[~footer_mask].copy()

    df.drop(columns=_DROP_COLS, errors="ignore", inplace=True)

    df.rename(columns={"Time": "TradeDate"}, inplace=True)

    if "Latest" in df.columns:
        df.rename(columns={"Latest": "Close"}, inplace=True)
    elif "Last" in df.columns:
        df.rename(columns={"Last": "Close"}, inplace=True)
    else:
        raise ValueError(
            f"{file_path.name}: no close price column ('Latest' or 'Last') found. "
            f"Columns present: {list(df.columns)}"
        )

    if "Symbol" not in df.columns:
        inferred = file_path.stem.upper()
        df["Symbol"] = inferred
        print(f"[import] No Symbol column — using filename stem: '{inferred}'")
    else:
        df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()

    required = {"Symbol", "TradeDate", "Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"{file_path.name}: missing columns after normalisation: {missing}. "
            f"Columns present: {list(df.columns)}"
        )

    df["TradeDate"] = pd.to_datetime(df["TradeDate"], errors="coerce")
    before = len(df)
    df.dropna(subset=["TradeDate"], inplace=True)
    if len(df) < before:
        print(f"[import] Dropped {before - len(df)} row(s) with unparseable dates")
    df["TradeDate"] = df["TradeDate"].dt.date

    df["Volume"] = (
        df["Volume"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )
    before = len(df)
    df.dropna(subset=["Volume"], inplace=True)
    if len(df) < before:
        print(f"[import] Dropped {before - len(df)} row(s) with unparseable Volume")
    df["Volume"] = df["Volume"].astype("int64")

    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    before = len(df)
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    if len(df) < before:
        print(f"[import] Dropped {before - len(df)} row(s) with non-numeric OHLC")

    symbols_found = sorted(df["Symbol"].unique().tolist())
    print(f"[import] Valid rows after cleaning: {len(df)} | symbols: {symbols_found}")

    return df[["Symbol", "TradeDate", "Open", "High", "Low", "Close", "Volume"]]


def _get_or_create_ticker(symbol: str, session: Session) -> tuple[int, bool]:
    """
    Return (ticker_id, was_created) for symbol.

    Creates the Ticker row with placeholder values if it does not exist.
    Auto-created tickers use IsActive=True and IndexMembership='SP500' so
    they pass the scanner's index filter; update via the seed SQL once confirmed.
    """
    ticker_id: int | None = session.execute(
        select(Ticker.ticker_id).where(Ticker.symbol == symbol)
    ).scalar_one_or_none()

    if ticker_id is not None:
        return ticker_id, False

    ticker = Ticker(
        symbol=symbol,
        company_name=symbol,
        sector="Unknown",
        exchange="Unknown",
        index_membership="SP500",
        is_active=True,
    )
    session.add(ticker)
    session.flush()
    logger.warning(
        "Auto-created Ticker for '%s' with placeholder sector/exchange. "
        "Update via 02_seed_tickers.sql or directly in the DB.",
        symbol,
    )
    return ticker.ticker_id, True


def _import_symbol_df(
    df: pd.DataFrame,
    symbol: str,
    ticker_id: int,
    session: Session,
) -> tuple[int, int]:
    """
    Bulk-insert new rows for one symbol.

    Uses executemany (fast_executemany via engine event) in batches of
    _BATCH_SIZE rows.  Skips dates that already exist.  Does NOT commit.

    Returns
    -------
    (inserted, skipped)
        inserted : rows actually written to the DB
        skipped  : rows present in the CSV but already in PriceData
    """
    if df.empty:
        return 0, 0

    # One query to fetch all existing dates for this ticker
    existing_dates: set = set(
        session.execute(
            select(PriceData.trade_date).where(PriceData.ticker_id == ticker_id)
        ).scalars().all()
    )

    new_df = df[~df["TradeDate"].isin(existing_dates)]
    skipped = len(df) - len(new_df)

    if new_df.empty:
        logger.info("%s: all %d rows already present — nothing to insert", symbol, skipped)
        return 0, skipped

    # Build list of dicts for bulk insert — avoids ORM overhead per row
    rows = [
        {
            "ticker_id": ticker_id,
            "trade_date": row.TradeDate,
            "open_price": float(row.Open),
            "high_price": float(row.High),
            "low_price": float(row.Low),
            "close_price": float(row.Close),
            "volume": int(row.Volume),
            "sma50": None,
            "sma200": None,
            "ema10": None,
            "ema20": None,
            "atr_pct": None,
            "adr": None,
            "rs_score": None,
        }
        for row in new_df.itertuples(index=False)
    ]

    inserted = 0
    total = len(rows)

    for start in range(0, total, _BATCH_SIZE):
        batch = rows[start : start + _BATCH_SIZE]

        # Progress log every _PROGRESS_INTERVAL rows
        if start % _PROGRESS_INTERVAL == 0 and total > _PROGRESS_INTERVAL:
            print(
                f"[import]   {symbol}: inserting rows "
                f"{start + 1}–{min(start + _BATCH_SIZE, total)} / {total}"
            )

        try:
            session.execute(insert(PriceData), batch)
            session.flush()
            inserted += len(batch)
        except IntegrityError:
            session.rollback()
            logger.warning(
                "%s: IntegrityError on batch at row %d — skipping batch "
                "(likely a concurrent import or duplicate date).",
                symbol,
                start,
            )

    logger.info("%s: inserted %d, skipped %d (already existed)", symbol, inserted, skipped)
    return inserted, skipped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_csv(
    file_path: Path,
    session: Session,
    symbol: str | None = None,
) -> ImportStats:
    """
    Parse a Barchart CSV file and import all symbols found in it.

    If the file has a Symbol column each unique ticker is imported separately.
    If symbol is provided it overrides the Symbol column.

    Does NOT commit — the caller owns the transaction boundary.

    Returns
    -------
    ImportStats
        Counts of rows inserted, skipped, symbols created, and elapsed time.
    """
    t0 = time.monotonic()
    print(f"[import] import_csv: {file_path.name}")
    logger.info("Importing from %s (symbol override=%s)", file_path, symbol)

    df = _parse_csv(file_path)

    if symbol is not None:
        df["Symbol"] = symbol.upper()

    stats = ImportStats()

    for sym, sym_df in df.groupby("Symbol", sort=False):
        sym = str(sym).upper()
        # Barchart exports newest-first; reverse to oldest-first for indicators
        sym_df = sym_df.iloc[::-1].reset_index(drop=True)
        stats.symbols_found += 1
        print(f"[import]   {sym}: {len(sym_df)} rows to process")

        ticker_id, was_created = _get_or_create_ticker(sym, session)
        if was_created:
            stats.symbols_auto_created += 1
        else:
            stats.symbols_already_existed += 1

        inserted, skipped = _import_symbol_df(sym_df, sym, ticker_id, session)
        stats.rows_by_symbol[sym] = inserted
        stats.rows_skipped_duplicates += skipped
        print(f"[import]   {sym}: inserted {inserted}, skipped {skipped}")

    stats.total_rows_inserted = sum(stats.rows_by_symbol.values())
    stats.import_time_seconds = round(time.monotonic() - t0, 2)
    return stats


def import_folder(folder: Path, session: Session) -> ImportStats:
    """
    Import all *.csv files found in folder.

    Each file is committed independently — a bad file does not roll back
    files already imported.

    Returns
    -------
    ImportStats
        Aggregated counts across all files.
    """
    csv_files = sorted(folder.glob("*.csv"))
    if not csv_files:
        logger.warning("import_folder: no *.csv files found in %s", folder)
        return ImportStats()

    logger.info("import_folder: found %d file(s) in %s", len(csv_files), folder)

    t0 = time.monotonic()
    totals = ImportStats()

    for file_path in csv_files:
        logger.info("import_folder: processing %s ...", file_path.name)
        try:
            file_stats = import_csv(file_path, session)
            session.commit()
            totals.merge(file_stats)
            logger.info(
                "import_folder: %s → committed. inserted=%d skipped=%d",
                file_path.name,
                file_stats.total_rows_inserted,
                file_stats.rows_skipped_duplicates,
            )
        except Exception as exc:
            session.rollback()
            logger.error(
                "import_folder: FAILED on %s — %s: %s",
                file_path.name,
                type(exc).__name__,
                exc,
                exc_info=True,
            )

    totals.import_time_seconds = round(time.monotonic() - t0, 2)
    logger.info(
        "import_folder: done. total_inserted=%d skipped=%d auto_created=%d time=%.1fs",
        totals.total_rows_inserted,
        totals.rows_skipped_duplicates,
        totals.symbols_auto_created,
        totals.import_time_seconds,
    )
    return totals
