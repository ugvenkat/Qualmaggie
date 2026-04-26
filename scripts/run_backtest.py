"""
scripts/run_backtest.py
Run a QualMaggie backtest via the FastAPI backend and save full results to JSON.

The POST /api/backtest/run endpoint is synchronous (blocks until done).
This script submits it in a background thread so the main thread can print a
progress line every 30 seconds while waiting.

If --capital or --risk are supplied they are applied to settings.json via
POST /api/settings *before* the backtest starts, then restored afterwards.

Usage
-----
    python scripts/run_backtest.py
    python scripts/run_backtest.py --start 2020-01-01 --end 2024-12-31
    python scripts/run_backtest.py --capital 50000 --risk 1.0 --name "V10 test"
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"
POLL_INTERVAL_SECS = 30

LOG_DIR = Path(__file__).resolve().parent / "logs"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run a QualMaggie backtest and save results to JSON."
    )
    p.add_argument(
        "--start",
        default="2024-01-01",
        metavar="YYYY-MM-DD",
        help="Backtest start date (default: 2024-01-01)",
    )
    p.add_argument(
        "--end",
        default="2024-12-31",
        metavar="YYYY-MM-DD",
        help="Backtest end date (default: 2024-12-31)",
    )
    p.add_argument(
        "--capital",
        type=float,
        default=None,
        metavar="AMOUNT",
        help="Initial capital in settings.json (e.g. 100000). "
             "Only updates settings if explicitly supplied.",
    )
    p.add_argument(
        "--risk",
        type=float,
        default=None,
        metavar="PCT",
        help="RiskPerTrade %% in settings.json (e.g. 1.5). "
             "Only updates settings if explicitly supplied.",
    )
    p.add_argument(
        "--name",
        default="My Backtest",
        metavar="TEXT",
        help="Label stored with this run (default: 'My Backtest')",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=1800.0,
        metavar="SECS",
        help="HTTP read timeout in seconds (default: 1800)",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Settings patch / restore
# ---------------------------------------------------------------------------

def _get_settings(client: httpx.Client) -> dict:
    resp = client.get(f"{BASE_URL}/api/settings", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _save_settings(client: httpx.Client, patch: dict) -> dict:
    resp = client.post(f"{BASE_URL}/api/settings", json=patch, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Backtest POST — runs in a background thread
# ---------------------------------------------------------------------------

class _BacktestResult:
    """Shared state between the worker thread and the main thread."""

    def __init__(self) -> None:
        self.response: httpx.Response | None = None
        self.error: Exception | None = None


def _post_backtest(
    client: httpx.Client,
    payload: dict,
    result: _BacktestResult,
    timeout: httpx.Timeout,
) -> None:
    try:
        resp = client.post(
            f"{BASE_URL}/api/backtest/run",
            json=payload,
            timeout=timeout,
        )
        result.response = resp
    except Exception as exc:
        result.error = exc


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _get_detail(client: httpx.Client, run_id: int) -> dict:
    resp = client.get(f"{BASE_URL}/api/backtest/{run_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get_trades(client: httpx.Client, run_id: int) -> list[dict]:
    resp = client.get(f"{BASE_URL}/api/backtest/{run_id}/trades", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get_snapshots(client: httpx.Client, run_id: int) -> list[dict]:
    resp = client.get(f"{BASE_URL}/api/backtest/{run_id}/snapshots", timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _save_results(data: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = LOG_DIR / f"backtest_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return out_path


def _fmt_pct(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_float(value, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _print_summary(run: dict, perf: dict | None, trades: list[dict]) -> None:
    sep = "-" * 52
    print()
    print("=" * 52)
    print(f"  BACKTEST SUMMARY — {run.get('run_name', '')}")
    print("=" * 52)
    print(f"  Run ID     : {run.get('backtest_run_id')}")
    print(f"  Period     : {run.get('start_date')} → {run.get('end_date')}")
    print(f"  Capital    : ${float(run.get('initial_capital', 0)):,.2f}")
    final = run.get("final_capital")
    if final is not None:
        print(f"  Final Cap  : ${float(final):,.2f}")
    print(f"  Status     : {run.get('status')}")
    print(sep)
    if perf:
        total_ret = perf.get("total_return_pct")
        win_rate  = perf.get("win_rate")
        pf        = perf.get("profit_factor")
        drawdown  = perf.get("max_drawdown_pct")
        total_tr  = perf.get("total_trades")
        win_tr    = perf.get("winning_trades")
        lose_tr   = perf.get("losing_trades")
        avg_win   = perf.get("avg_win_pct")
        avg_loss  = perf.get("avg_loss_pct")
        sharpe    = perf.get("sharpe_ratio")

        print(f"  Total Return : {_fmt_pct(total_ret)}")
        print(f"  Win Rate     : {_fmt_pct(win_rate)}  ({win_tr}W / {lose_tr}L / {total_tr} total)")
        print(f"  Profit Factor: {_fmt_float(pf, 3)}")
        print(f"  Max Drawdown : {_fmt_pct(drawdown)}")
        print(f"  Avg Win      : {_fmt_pct(avg_win)}")
        print(f"  Avg Loss     : {_fmt_pct(avg_loss)}")
        print(f"  Sharpe       : {_fmt_float(sharpe, 3)}")
    else:
        print("  (No performance report generated)")
    print(sep)
    if trades:
        print(f"  Trades ({len(trades)} total):")
        header = f"  {'Symbol':<7} {'Entry':<12} {'Exit':<12} {'PnL%':>7} {'Reason':<12}"
        print(header)
        print("  " + "-" * 48)
        for t in trades[:20]:  # cap console output at 20 rows
            pnl_pct = t.get("pnl_pct")
            pnl_str = f"{float(pnl_pct) * 100:+.2f}%" if pnl_pct is not None else "  open"
            print(
                f"  {t.get('symbol',''):<7} "
                f"{str(t.get('entry_date','')):<12} "
                f"{str(t.get('exit_date') or ''):<12} "
                f"{pnl_str:>7}  "
                f"{t.get('exit_reason') or '':<12}"
            )
        if len(trades) > 20:
            print(f"  ... and {len(trades) - 20} more (see JSON output)")
    print("=" * 52)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    # Validate dates
    try:
        datetime.strptime(args.start, "%Y-%m-%d")
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as exc:
        print(f"ERROR: Invalid date format — {exc}", file=sys.stderr)
        sys.exit(1)

    if args.end <= args.start:
        print("ERROR: --end must be after --start", file=sys.stderr)
        sys.exit(1)

    request_timeout = httpx.Timeout(connect=10.0, read=args.timeout, write=10.0, pool=10.0)
    client = httpx.Client()
    original_settings: dict | None = None

    try:
        # ------------------------------------------------------------------
        # 1. Optionally patch settings (capital / risk)
        # ------------------------------------------------------------------
        settings_patch: dict = {}
        if args.capital is not None:
            settings_patch["InitialCapital"] = args.capital
        if args.risk is not None:
            settings_patch["RiskPerTrade"] = args.risk

        if settings_patch:
            print("Updating settings before backtest...")
            try:
                original_settings = _get_settings(client)
                updated = _save_settings(client, settings_patch)
                for k, v in settings_patch.items():
                    print(f"  {k}: {original_settings.get(k)} → {v}")
            except httpx.HTTPError as exc:
                print(f"ERROR: Could not update settings: {exc}", file=sys.stderr)
                print("Is the backend running at http://localhost:8000?", file=sys.stderr)
                sys.exit(1)

        # ------------------------------------------------------------------
        # 2. Submit backtest in a background thread
        # ------------------------------------------------------------------
        payload = {
            "run_name": args.name,
            "start_date": args.start,
            "end_date": args.end,
        }

        result = _BacktestResult()
        thread = threading.Thread(
            target=_post_backtest,
            args=(client, payload, result, request_timeout),
            daemon=True,
        )

        print(
            f"\nStarting backtest: {args.name!r}  "
            f"({args.start} → {args.end})"
        )
        print("Waiting for completion (press Ctrl+C to abort)...\n")

        start_ts = time.monotonic()
        thread.start()

        # ------------------------------------------------------------------
        # 3. Show progress every POLL_INTERVAL_SECS while thread is alive
        # ------------------------------------------------------------------
        while thread.is_alive():
            thread.join(timeout=POLL_INTERVAL_SECS)
            if thread.is_alive():
                elapsed = int(time.monotonic() - start_ts)
                print(f"  Still running... {elapsed}s elapsed", flush=True)

        elapsed_total = time.monotonic() - start_ts

        # ------------------------------------------------------------------
        # 4. Handle thread result
        # ------------------------------------------------------------------
        if result.error is not None:
            exc = result.error
            if isinstance(exc, httpx.ConnectError):
                print(
                    "\nERROR: Could not connect to backend. "
                    "Is uvicorn running at http://localhost:8000?",
                    file=sys.stderr,
                )
            else:
                print(f"\nERROR: Request failed — {exc}", file=sys.stderr)
            sys.exit(1)

        resp = result.response
        if resp.status_code != 200:
            print(
                f"\nERROR: Backend returned HTTP {resp.status_code}:\n{resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        run_summary: dict = resp.json()
        run_id: int = run_summary["backtest_run_id"]
        print(f"\nBacktest complete in {elapsed_total:.1f}s  (run_id={run_id})")

        # ------------------------------------------------------------------
        # 5. Fetch detail, trades, snapshots
        # ------------------------------------------------------------------
        print("Fetching results...")
        detail = _get_detail(client, run_id)
        trades = _get_trades(client, run_id)
        snapshots = _get_snapshots(client, run_id)

        # ------------------------------------------------------------------
        # 6. Save full JSON output
        # ------------------------------------------------------------------
        output = {
            "run": detail.get("run", run_summary),
            "performance": detail.get("performance"),
            "trades": trades,
            "snapshots": snapshots,
            "meta": {
                "generated_at": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed_total, 1),
            },
        }

        out_path = _save_results(output)
        print(f"Results saved → {out_path}")

        # ------------------------------------------------------------------
        # 7. Console summary
        # ------------------------------------------------------------------
        _print_summary(
            run=detail.get("run", run_summary),
            perf=detail.get("performance"),
            trades=trades,
        )

    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        sys.exit(1)

    finally:
        # ------------------------------------------------------------------
        # Restore original settings if we patched them
        # ------------------------------------------------------------------
        if original_settings is not None and settings_patch:
            restore = {k: original_settings[k] for k in settings_patch if k in original_settings}
            if restore:
                try:
                    _save_settings(client, restore)
                    print(
                        f"Settings restored: "
                        + ", ".join(f"{k}={v}" for k, v in restore.items())
                    )
                except Exception as exc:
                    print(f"WARNING: Could not restore settings: {exc}", file=sys.stderr)

        client.close()


if __name__ == "__main__":
    main()
