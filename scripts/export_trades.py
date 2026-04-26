"""
scripts/export_trades.py
Export all trades for a specific backtest run to CSV.

Output
------
  data/exports/trades_{run_id}_{YYYY-MM-DD}.csv

Columns
-------
  TradeID, Symbol, Sector, PatternType,
  EntryDate, ExitDate, EntryPrice, ExitPrice, Shares,
  InitialStopLoss,
  PartialExitDate, PartialExitPrice, PartialShares,
  PnL, PnLPct, ExitReason, RiskAmount, BacktestRunID

Note: PnLPct is stored as a decimal ratio in the DB (e.g. 0.0312 = 3.12%).

Usage
-----
    python scripts/export_trades.py --run_id 45
    python scripts/export_trades.py --run_id 45 --out-dir C:/some/other/folder
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select, text

from backend.config.settings import get_settings
from backend.db.database import get_db, get_engine
from backend.db.models import BacktestRun, Trade

_CSV_FIELDS = [
    "TradeID",
    "Symbol",
    "Sector",
    "PatternType",
    "EntryDate",
    "ExitDate",
    "EntryPrice",
    "ExitPrice",
    "Shares",
    "InitialStopLoss",
    "PartialExitDate",
    "PartialExitPrice",
    "PartialShares",
    "PnL",
    "PnLPct",
    "ExitReason",
    "RiskAmount",
    "BacktestRunID",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Export trades for a backtest run to CSV."
    )
    p.add_argument(
        "--run_id",
        type=int,
        required=True,
        metavar="ID",
        help="BacktestRunID to export (required).",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        metavar="PATH",
        help="Output directory (default: data/exports from settings.json).",
    )
    return p.parse_args()


def _trade_to_row(t: Trade) -> dict:
    return {
        "TradeID":          t.trade_id,
        "Symbol":           t.symbol,
        "Sector":           t.sector,
        "PatternType":      t.pattern_type,
        "EntryDate":        t.entry_date,
        "ExitDate":         t.exit_date or "",
        "EntryPrice":       float(t.entry_price),
        "ExitPrice":        float(t.exit_price) if t.exit_price is not None else "",
        "Shares":           t.shares,
        "InitialStopLoss":  float(t.initial_stop_loss),
        "PartialExitDate":  t.partial_exit_date or "",
        "PartialExitPrice": float(t.partial_exit_price) if t.partial_exit_price is not None else "",
        "PartialShares":    t.partial_shares or "",
        "PnL":              float(t.pnl) if t.pnl is not None else "",
        "PnLPct":           float(t.pnl_pct) if t.pnl_pct is not None else "",
        "ExitReason":       t.exit_reason or "",
        "RiskAmount":       float(t.risk_amount),
        "BacktestRunID":    t.backtest_run_id,
    }


def main() -> None:
    args = _parse_args()
    run_id: int = args.run_id

    # Resolve output directory
    if args.out_dir is not None:
        out_dir = Path(args.out_dir)
    else:
        out_dir = get_settings().export_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    with get_db() as session:
        # Verify the run exists
        run = session.get(BacktestRun, run_id)
        if run is None:
            print(f"ERROR: BacktestRunID {run_id} not found.", file=sys.stderr)
            sys.exit(1)

        # Fetch trades
        trades = session.execute(
            select(Trade)
            .where(Trade.backtest_run_id == run_id)
            .order_by(Trade.entry_date, Trade.symbol)
        ).scalars().all()

    if not trades:
        print(f"No trades found for BacktestRunID {run_id}.")
        sys.exit(0)

    # Build output path
    today_str = date.today().isoformat()
    out_path = out_dir / f"trades_{run_id}_{today_str}.csv"

    # Write CSV
    rows = [_trade_to_row(t) for t in trades]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    pnl_values = [float(t.pnl) for t in trades if t.pnl is not None]
    winners = [v for v in pnl_values if v > 0]
    losers  = [v for v in pnl_values if v <= 0]

    print(f"\nBacktest Run #{run_id}: {run.run_name}")
    print(f"  Period      : {run.start_date} → {run.end_date}")
    print(f"  Total trades: {len(trades)}")
    print(f"  Winners     : {len(winners)}  /  Losers: {len(losers)}")
    if pnl_values:
        total_pnl = sum(pnl_values)
        print(f"  Total PnL   : ${total_pnl:,.2f}")
    print(f"\n  Saved → {out_path}")


if __name__ == "__main__":
    main()
