"""
scripts/morning_routine.py
Master script — runs the full daily morning pipeline in order.

Steps (run sequentially; stops on first failure)
-------------------------------------------------
  1. update_prices.py    — pull missing OHLCV from yfinance
  2. update_earnings.py  — refresh earnings dates from yfinance
  3. run_scanner.py      — daily VCP scan, save signals CSV
  4. db_health_check.py  — verify DB state, flag stale data

Usage
-----
    python scripts/morning_routine.py
    python scripts/morning_routine.py --no-fail-fast   # continue even if a step fails

Prerequisites
-------------
    # Reminder: Run sql/03_daily_maintenance.sql in SSMS every morning
    # (updates statistics, rebuilds fragmented indexes, cleans up stale backtest data)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

_STEPS: list[tuple[str, Path]] = [
    ("Update prices",   SCRIPTS_DIR / "update_prices.py"),
    ("Update earnings", SCRIPTS_DIR / "update_earnings.py"),
    ("Run scanner",     SCRIPTS_DIR / "run_scanner.py"),
    ("DB health check", SCRIPTS_DIR / "db_health_check.py"),
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="QualMaggie morning routine — runs all daily maintenance scripts."
    )
    p.add_argument(
        "--no-fail-fast",
        action="store_true",
        help="Continue running remaining steps even if one fails (default: stop on first failure).",
    )
    return p.parse_args()


def _run_step(name: str, script: Path) -> tuple[bool, float, str]:
    """
    Run one script as a subprocess.

    Returns
    -------
    (success, elapsed_seconds, combined_output)
    """
    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        elapsed = time.monotonic() - start
        output = (result.stdout or "") + (result.stderr or "")
        success = result.returncode == 0
        return success, elapsed, output
    except Exception as exc:
        elapsed = time.monotonic() - start
        return False, elapsed, str(exc)


def main() -> None:
    args = _parse_args()
    fail_fast = not args.no_fail_fast

    started_at = datetime.now()
    print("=" * 60)
    print(f"  QualMaggie Morning Routine — {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results: list[tuple[str, bool, float]] = []
    any_failed = False

    for step_num, (name, script) in enumerate(_STEPS, start=1):
        print(f"\n[{step_num}/{len(_STEPS)}] {name} ...")
        print("-" * 60)

        if not script.exists():
            print(f"  ERROR: Script not found: {script}")
            results.append((name, False, 0.0))
            any_failed = True
            if fail_fast:
                print("\n  Stopping (use --no-fail-fast to continue on errors).")
                break
            continue

        success, elapsed, output = _run_step(name, script)
        results.append((name, success, elapsed))

        # Stream the output (already captured, print it now)
        for line in output.splitlines():
            print(f"  {line}")

        status = "OK" if success else "FAILED"
        print(f"\n  [{status}]  {elapsed:.1f}s")

        if not success:
            any_failed = True
            if fail_fast:
                print("\n  Stopping (use --no-fail-fast to continue on errors).")
                break

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    total_elapsed = (datetime.now() - started_at).total_seconds()
    print()
    print("=" * 60)
    print("  SUMMARY")
    print("  " + "-" * 40)
    for name, ok, elapsed in results:
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}]  {elapsed:6.1f}s  {name}")

    skipped = len(_STEPS) - len(results)
    if skipped:
        for _, (name, _) in enumerate(_STEPS[len(results):]):
            print(f"  [SKIP]         {name}")

    print("  " + "-" * 40)
    print(f"  Total time: {total_elapsed:.1f}s")
    print("=" * 60)

    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
