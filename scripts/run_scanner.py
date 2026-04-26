"""
scripts/run_scanner.py
Run the daily VCP scanner via the FastAPI backend and save signals to CSV.

The scanner calls POST /api/scan/run which orchestrates:
  market filter → stock filters → VCP pattern detection → ranked candidates.

Output
------
  scripts/logs/signals_YYYY-MM-DD.csv   — full candidate list (all signals)
  console                               — market status + top 5 signals

Usage
-----
    python scripts/run_scanner.py
    python scripts/run_scanner.py --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000"
LOG_DIR = Path(__file__).resolve().parent / "logs"

# CSV column order for signals file
_CSV_FIELDS = [
    "symbol",
    "sector",
    "exchange",
    "index_membership",
    "close",
    "volume_ma20",
    "atr_pct",
    "sma200",
    "ema10",
    "rs_score",
    "pivot_high",
    "distance_from_high_pct",
    "contractions_count",
    "preferred_quality",
    "is_breakout_candidate",
    "has_earnings_warning",
    "has_macro_warning",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the QualMaggie VCP scanner.")
    p.add_argument(
        "--url",
        default=BASE_URL,
        metavar="URL",
        help=f"Backend base URL (default: {BASE_URL})",
    )
    return p.parse_args()


def _run_scan(base_url: str) -> dict:
    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)) as client:
        resp = client.post(f"{base_url}/api/scan/run")
    resp.raise_for_status()
    return resp.json()


def _sort_key(c: dict) -> tuple:
    # Primary: breakout candidates first, then preferred quality, then RS score desc
    return (
        0 if c.get("is_breakout_candidate") else 1,
        0 if c.get("preferred_quality") else 1,
        -(c.get("rs_score") or 0.0),
    )


def _save_csv(candidates: list[dict], today: date) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOG_DIR / f"signals_{today.isoformat()}.csv"
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(candidates)
    return out_path


def _fmt(val, precision: int = 2) -> str:
    if val is None:
        return "  —  "
    try:
        return f"{float(val):.{precision}f}"
    except (TypeError, ValueError):
        return str(val)


def _print_top5(candidates: list[dict], market_healthy: bool, as_of_date: str) -> None:
    status = "HEALTHY" if market_healthy else "UNHEALTHY"
    print()
    print(f"  Market ({as_of_date}): {status}")
    print(f"  Total signals: {len(candidates)}")
    print()

    if not candidates:
        print("  No VCP signals found today.")
        return

    top = candidates[:5]
    print(f"  {'#':<3} {'Symbol':<8} {'Close':>7} {'RS':>6} {'Dist%':>6} "
          f"{'Ctrs':>4} {'Breakout':<10} {'Earnings?':<10}")
    print("  " + "-" * 62)
    for i, c in enumerate(top, start=1):
        breakout = "YES" if c.get("is_breakout_candidate") else "no"
        earnings = "WARN" if c.get("has_earnings_warning") else ""
        print(
            f"  {i:<3} {c['symbol']:<8} {_fmt(c.get('close')):>7} "
            f"{_fmt(c.get('rs_score'), 3):>6} "
            f"{_fmt(c.get('distance_from_high_pct')):>6} "
            f"{str(c.get('contractions_count') or ''):>4} "
            f"{breakout:<10} {earnings:<10}"
        )
    print()


def main() -> None:
    args = _parse_args()

    print(f"Running scanner against {args.url} ...")
    try:
        result = _run_scan(args.url)
    except httpx.ConnectError:
        print(
            f"ERROR: Cannot connect to backend at {args.url}\n"
            "Is uvicorn running?",
            file=sys.stderr,
        )
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"ERROR: Backend returned HTTP {exc.response.status_code}", file=sys.stderr)
        sys.exit(1)

    as_of = result.get("as_of_date", date.today().isoformat())
    candidates: list[dict] = result.get("candidates", [])

    # Sort: breakout candidates first, then preferred quality, then RS desc
    candidates.sort(key=_sort_key)

    # Save CSV
    today = date.today()
    out_path = _save_csv(candidates, today)
    print(f"  Signals saved → {out_path}")

    # Print top 5
    _print_top5(candidates, result.get("market_healthy", False), as_of)


if __name__ == "__main__":
    main()
