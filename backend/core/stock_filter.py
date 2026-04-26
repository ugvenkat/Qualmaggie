"""
Stock universe filters for QualMaggie.

Strategy filter rules
---------------------
1.  Price > $20                     (settings.min_stock_price)
2.  Avg Volume > 2M                 (settings.min_avg_volume) — 20-day volume MA
3.  ATR% between 2% and 20%         (settings.min_atr_pct / max_atr_pct)
4.  Above 200-day SMA               (close_price > sma200)
5.  RS > SPY over 6 months          (rs_score > 1.0)
6.  S&P 500 or Nasdaq only          (index_membership in SP500 | Nasdaq100 | Both)
7.  Positive EPS                    (eps > 0)  — skipped if None (no data)
8.  Institutional ownership > 30%   (settings.min_institutional_ownership_pct)
                                     — skipped if None (no data)

EPS and institutional ownership are NOT stored in PriceData.  Pass None to
skip those checks.  A warning is logged the first time each is skipped so
the caller is aware that the check was bypassed.

Volume MA note
--------------
    volume_ma20 is NOT a PriceData column.  Callers must compute it:
        volume_ma20 = df["volume"].rolling(20).mean().iloc[-1]
    before calling apply_stock_filters().

Public API
----------
    passes_price_filter(close, settings)                 -> bool
    passes_volume_filter(volume_ma20, settings)          -> bool
    passes_atr_filter(atr_pct, settings)                 -> bool
    passes_sma200_filter(close, sma200)                  -> bool
    passes_rs_filter(rs_score)                           -> bool
    passes_index_filter(index_membership)                -> bool
    passes_eps_filter(eps)                               -> bool
    passes_institutional_filter(inst_pct, settings)      -> bool

    apply_stock_filters(symbol, latest_row, settings,
                        eps, inst_pct)       -> tuple[bool, list[str]]
    filter_universe(candidates, settings)   -> list[dict]
"""

from __future__ import annotations

import logging
import math

from backend.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Index membership values that pass the index filter
_VALID_INDEX_MEMBERSHIPS: frozenset[str] = frozenset({"SP500", "Nasdaq100", "Both"})


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _is_valid(value: float | None) -> bool:
    """Return True if value is a real, finite number."""
    if value is None:
        return False
    try:
        return not (math.isnan(value) or math.isinf(value))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Individual filter functions
# ---------------------------------------------------------------------------

def passes_price_filter(close: float, settings: Settings) -> bool:
    """Close price must be above the minimum threshold ($20 default)."""
    return _is_valid(close) and close > settings.min_stock_price


def passes_volume_filter(volume_ma20: float, settings: Settings) -> bool:
    """
    20-day average volume must exceed the minimum (2M shares default).

    volume_ma20 must be computed by the caller from:
        df["volume"].rolling(20).mean().iloc[-1]
    """
    return _is_valid(volume_ma20) and volume_ma20 >= settings.min_avg_volume


def passes_atr_filter(atr_pct: float | None, settings: Settings) -> bool:
    """ATR% must be between min_atr_pct and max_atr_pct (2%–20% default)."""
    if not _is_valid(atr_pct):
        return False
    return settings.min_atr_pct <= atr_pct <= settings.max_atr_pct


def passes_sma200_filter(close: float, sma200: float | None) -> bool:
    """
    Close price must be above the 200-day SMA.
    Returns False if sma200 is None (warmup period not complete).
    """
    if not _is_valid(sma200):
        return False
    return _is_valid(close) and close > sma200


def passes_rs_filter(rs_score: float | None) -> bool:
    """
    Relative Strength vs SPY must be > 1.0 (outperforming SPY over the
    rs_lookback_days window, default 126 trading days ≈ 6 months).
    Returns False if rs_score is None (not yet computed).
    """
    if not _is_valid(rs_score):
        return False
    return rs_score > 1.0


def passes_index_filter(index_membership: str) -> bool:
    """
    Stock must be in S&P 500 and/or Nasdaq 100.
    Valid values: 'SP500', 'Nasdaq100', 'Both'.
    """
    return str(index_membership).strip() in _VALID_INDEX_MEMBERSHIPS


def passes_eps_filter(eps: float | None) -> bool:
    """
    EPS must be positive.  Returns True if eps is None (data unavailable
    — check is bypassed with a warning logged once per session).

    To enable: provide eps from a fundamentals data source.
    """
    if eps is None:
        logger.debug("EPS data unavailable — bypassing EPS filter")
        return True
    if not _is_valid(eps):
        return False
    return eps > 0


def passes_institutional_filter(
    inst_pct: float | None,
    settings: Settings,
) -> bool:
    """
    Institutional ownership must be >= min_institutional_ownership_pct (30% default).
    Returns True if inst_pct is None (data unavailable — check is bypassed).

    To enable: provide inst_pct from a fundamentals data source.
    """
    if inst_pct is None:
        logger.debug("Institutional ownership data unavailable — bypassing ownership filter")
        return True
    if not _is_valid(inst_pct):
        return False
    return inst_pct >= settings.min_institutional_ownership_pct


# ---------------------------------------------------------------------------
# Combined filter
# ---------------------------------------------------------------------------

def apply_stock_filters(
    symbol: str,
    latest_row: dict,
    settings: Settings,
    eps: float | None = None,
    inst_pct: float | None = None,
) -> tuple[bool, list[str]]:
    """
    Run all 8 stock filters against the latest data row for one ticker.

    Parameters
    ----------
    symbol : str
        Ticker symbol (used for logging only).
    latest_row : dict
        Most recent PriceData row as a plain dict.  Expected keys:
            close_price, atr_pct, sma200, rs_score,
            volume_ma20, index_membership
        volume_ma20 must be computed by the caller before passing.
    settings : Settings
        Loaded settings (from get_settings()).
    eps : float | None
        Earnings per share (positive = profitable).  Pass None to skip.
    inst_pct : float | None
        Institutional ownership percentage.  Pass None to skip.

    Returns
    -------
    tuple[bool, list[str]]
        (True, [])                  all filters passed
        (False, ["ATR", "RS", ...]) list of failed filter names
    """
    close = latest_row.get("close_price")
    atr_pct = latest_row.get("atr_pct")
    sma200 = latest_row.get("sma200")
    rs_score = latest_row.get("rs_score")
    volume_ma20 = latest_row.get("volume_ma20")
    index_membership = latest_row.get("index_membership", "")

    failed: list[str] = []

    if not passes_price_filter(close, settings):
        failed.append("Price")
    if not passes_volume_filter(volume_ma20, settings):
        failed.append("Volume")
    if not passes_atr_filter(atr_pct, settings):
        failed.append("ATR")
    if not passes_sma200_filter(close, sma200):
        failed.append("SMA200")
    if not passes_rs_filter(rs_score):
        failed.append("RS")
    if not passes_index_filter(index_membership):
        failed.append("Index")
    if not passes_eps_filter(eps):
        failed.append("EPS")
    if not passes_institutional_filter(inst_pct, settings):
        failed.append("Institutional")

    passed = len(failed) == 0
    if not passed:
        logger.debug("%s failed filters: %s", symbol, failed)

    return passed, failed


# ---------------------------------------------------------------------------
# Universe scan
# ---------------------------------------------------------------------------

def filter_universe(
    candidates: list[dict],
    settings: Settings | None = None,
) -> list[dict]:
    """
    Apply stock filters to a list of ticker snapshots.

    Parameters
    ----------
    candidates : list[dict]
        List of latest-row dicts.  Each must include a 'symbol' key plus
        all keys expected by apply_stock_filters().
    settings : Settings | None
        If None, get_settings() is called automatically.

    Returns
    -------
    list[dict]
        Subset of candidates that passed all filters.
    """
    if settings is None:
        settings = get_settings()

    passed_list: list[dict] = []
    total = len(candidates)

    for row in candidates:
        symbol = row.get("symbol", "UNKNOWN")
        eps = row.get("eps")           # None if not provided
        inst_pct = row.get("inst_pct") # None if not provided
        passed, _ = apply_stock_filters(symbol, row, settings, eps=eps, inst_pct=inst_pct)
        if passed:
            passed_list.append(row)

    logger.info(
        "Universe filter: %d / %d tickers passed all stock filters",
        len(passed_list),
        total,
    )
    return passed_list
