"""
Chart pattern detection for QualMaggie.

Currently implemented
---------------------
    VCP — Volatility Contraction Pattern

Planned (not yet implemented)
------------------------------
    Flag, Cup & Handle, Flat Base

VCP definition
--------------
A VCP is a series of 3+ price contractions where each successive
contraction has:
  - A narrower price range than the previous  (volatility contraction)
  - Lower average volume                      (drying up of sellers)
  - A strictly higher swing low               (Fix 5: trend structure intact)

At the end of the pattern, price is near the last pivot high and volume
is quiet — setting up for a breakout on expanding volume.

Detection parameters
--------------------
    min_contractions       = 3    (at minimum 3 high→low swings)
    pivot_window           = 5    (bars on each side for pivot detection)
    max_last_range_pct     = 0.15 (last contraction must be < 15% wide)
    max_distance_pct       = 0.03 (price within 3% of pivot high = in base)
    breakout_distance      = 0.02 (price within 2% = breakout candidate)
    pattern_lookback       = 120  (bars of history used for detection)
    min_history            = 60   (minimum bars required)

Entry quality filters (applied inside detect_vcp)
--------------------------------------------------
    Fix 2 — prior move    : base preceded by ≥MinPriorMovePct run-up
    Fix 4 — quality score : 0–10 composite score; reject below MinSetupQualityScore
    Fix 5 — higher lows   : every swing low strictly above the previous

Public API
----------
    detect_vcp(df, settings)                     -> dict | None
    scan_vcp(symbols, session, settings)         -> dict[str, dict]
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import Settings, get_settings
from backend.db.database import get_engine
from backend.db.models import PriceData, Ticker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection constants  (pivot/lookback params stay fixed; tightness/count
# moved to Settings so they can be tuned without touching code)
# ---------------------------------------------------------------------------
_PIVOT_WINDOW: int = 5
_MAX_DISTANCE_PCT: float = 0.03      # 3%  — price within 3% of pivot high (base)
_BREAKOUT_DISTANCE_PCT: float = 0.02  # 2%  — active breakout candidate
_PATTERN_LOOKBACK: int = 120         # bars of history used for VCP detection
_MIN_HISTORY: int = 60               # minimum bars required to attempt detection


# ---------------------------------------------------------------------------
# Private pivot helpers
# ---------------------------------------------------------------------------

def _find_pivot_highs(high: pd.Series, window: int = _PIVOT_WINDOW) -> pd.Series:
    """
    Identify local pivot highs.

    A bar is a pivot high if its High equals the rolling maximum in a
    (2 × window + 1) window centered on that bar.

    Returns a Series with the pivot high value at each pivot date, NaN elsewhere.
    """
    span = 2 * window + 1
    rolling_max = high.rolling(window=span, center=True, min_periods=window + 1).max()
    is_pivot = high == rolling_max
    return high.where(is_pivot)


def _find_pivot_lows(low: pd.Series, window: int = _PIVOT_WINDOW) -> pd.Series:
    """
    Identify local pivot lows (symmetric to pivot highs).
    """
    span = 2 * window + 1
    rolling_min = low.rolling(window=span, center=True, min_periods=window + 1).min()
    is_pivot = low == rolling_min
    return low.where(is_pivot)


def _build_contractions(df: pd.DataFrame, pivot_window: int = _PIVOT_WINDOW) -> list[dict]:
    """
    Build a list of price contractions from alternating pivot highs and lows.

    A contraction is a high→low move:
        { high, low, range_pct, avg_volume, high_date, low_date }

    Only contractions where the high precedes the low are included.
    Contractions are returned in chronological order.

    Parameters
    ----------
    df : pd.DataFrame
        Price data with columns: high_price, low_price, volume, trade_date.
        Must be sorted oldest-first.
    pivot_window : int
        Half-window for pivot detection.

    Returns
    -------
    list[dict]
        List of contraction dicts, oldest first.
    """
    pivot_highs = _find_pivot_highs(df["high_price"], pivot_window).dropna()
    pivot_lows = _find_pivot_lows(df["low_price"], pivot_window).dropna()

    if pivot_highs.empty or pivot_lows.empty:
        return []

    # Build alternating high → low pairs
    contractions: list[dict] = []

    # Iterate pivot highs; for each find the next pivot low that follows it
    low_indices = pivot_lows.index.tolist()
    high_indices = pivot_highs.index.tolist()

    used_low_pos = 0
    for h_idx in high_indices:
        # Find first low that comes AFTER this high
        while used_low_pos < len(low_indices) and low_indices[used_low_pos] <= h_idx:
            used_low_pos += 1
        if used_low_pos >= len(low_indices):
            break

        l_idx = low_indices[used_low_pos]
        h_price = float(pivot_highs.loc[h_idx])
        l_price = float(pivot_lows.loc[l_idx])

        if h_price <= 0:
            continue

        range_pct = (h_price - l_price) / h_price

        # Average volume between the high and low dates (inclusive)
        mask = (df.index >= h_idx) & (df.index <= l_idx)
        avg_vol = float(df.loc[mask, "volume"].mean()) if mask.any() else 0.0

        # Trade dates (for reference)
        high_date = df.loc[h_idx, "trade_date"] if "trade_date" in df.columns else None
        low_date = df.loc[l_idx, "trade_date"] if "trade_date" in df.columns else None

        contractions.append(
            {
                "high": h_price,
                "low": l_price,
                "range_pct": round(range_pct, 4),
                "avg_volume": round(avg_vol, 0),
                "high_date": high_date,
                "low_date": low_date,
            }
        )

        # Advance so the next high must be after the current low
        used_low_pos += 1

    return contractions


# ---------------------------------------------------------------------------
# Fix 5 — Strict higher lows
# ---------------------------------------------------------------------------

def _check_higher_lows(contractions: list[dict]) -> bool:
    """
    Return True when every swing low is strictly higher than the previous.

    This enforces the trend-structure requirement: buyers are stepping in at
    progressively higher prices, confirming the base is constructive.
    A single lower low rejects the pattern regardless of range/volume.
    """
    lows = [c["low"] for c in contractions]
    return all(lows[i + 1] > lows[i] for i in range(len(lows) - 1))


# ---------------------------------------------------------------------------
# Fix 2 — Prior move check
# ---------------------------------------------------------------------------

def _check_prior_move(
    df: pd.DataFrame,
    base_start_date,
    settings: Settings,
) -> float:
    """
    Return (max_high_in_prior_period / price_at_base_start) - 1.

    Examines the PriorMoveLookbackDays of data immediately before
    base_start_date.  A result >= MinPriorMovePct / 100 means the stock
    previously traded that percentage above the base-start price — evidence
    that the stock had a meaningful prior run-up before forming the base.

    Returns 0.0 if there is insufficient history.
    """
    if base_start_date is None:
        return 0.0

    if hasattr(base_start_date, "date"):
        base_start_date = base_start_date.date()

    base_start_ts = pd.Timestamp(base_start_date)
    prior_start_ts = base_start_ts - pd.Timedelta(days=settings.prior_move_lookback_days)

    prior_mask = (df["trade_date"] < base_start_ts) & (df["trade_date"] >= prior_start_ts)
    prior_data = df.loc[prior_mask]

    if prior_data.empty:
        return 0.0

    # Price at the start of the base — close on or just before base_start_date
    at_base = df.loc[df["trade_date"] <= base_start_ts].tail(1)
    if at_base.empty:
        return 0.0

    price_at_base_start = float(at_base.iloc[0]["close_price"])
    if price_at_base_start <= 0:
        return 0.0

    max_high = float(prior_data["high_price"].max())
    return round((max_high / price_at_base_start) - 1.0, 4)


# ---------------------------------------------------------------------------
# Fix 4 — Setup quality score (0–10)
# ---------------------------------------------------------------------------

def _compute_quality_score(
    contractions: list[dict],
    prior_move_pct: float,
    has_higher_lows: bool,
    rs_score: float | None,
) -> int:
    """
    Score a VCP setup on five dimensions (0–2 pts each) → total 0–10.

    Dimensions
    ----------
    1. Prior move strength  : how large was the run-up into the base
    2. Contractions count   : more contractions = more refined pattern
    3. Volume dry-up        : last contraction's avg volume vs first
    4. Higher lows          : strict higher lows = uptrend structure intact
    5. RS vs SPY            : relative strength vs the market benchmark
    """
    score = 0

    # 1. Prior move strength
    if prior_move_pct >= 0.50:
        score += 2
    elif prior_move_pct >= 0.30:
        score += 1

    # 2. Contractions count
    count = len(contractions)
    if count >= 4:
        score += 2
    elif count >= 3:
        score += 1

    # 3. Volume dry-up — last contraction volume vs first contraction volume
    if len(contractions) >= 2:
        first_vol = contractions[0]["avg_volume"]
        last_vol = contractions[-1]["avg_volume"]
        if first_vol > 0:
            vol_ratio = last_vol / first_vol
            if vol_ratio < 0.40:   # >60% reduction — strong dry-up
                score += 2
            elif vol_ratio < 0.65: # >35% reduction — moderate dry-up
                score += 1

    # 4. Higher lows (always True after Fix 5 hard-rejects non-strict patterns)
    if has_higher_lows:
        score += 2

    # 5. RS vs SPY — rs_score > 0 means outperforming the market
    if rs_score is not None:
        if rs_score > 0.10:
            score += 2
        elif rs_score >= 0.0:
            score += 1

    return score


# ---------------------------------------------------------------------------
# Main detection function
# ---------------------------------------------------------------------------

def detect_vcp(
    df: pd.DataFrame,
    settings: Settings | None = None,
) -> dict | None:
    """
    Detect a Volatility Contraction Pattern in the given price DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Full price history with columns: high_price, low_price, close_price,
        volume, trade_date.  Must be sorted oldest-first.

        Pass as much history as available.  The function uses only the last
        _PATTERN_LOOKBACK (120) bars for VCP detection, but accesses the full
        df for the prior-move lookback (Fix 2).  The scanner passes ≥252 bars
        so the prior-move check has enough context.

        Minimum required: _MIN_HISTORY (60) bars.

    settings : Settings | None
        Loaded settings.  If None, get_settings() is used.

    Returns
    -------
    dict | None
        None if no VCP detected or any quality check fails.
        Detail dict if VCP found:

        {
            "pattern":               "VCP",
            "contractions_count":    int,
            "ranges_pct":            list[float],
            "volumes":               list[float],
            "pivot_high":            float,      # last pivot high = entry trigger
            "current_close":         float,
            "distance_from_high_pct": float,
            "is_breakout_candidate": bool,       # True if within 2% of pivot high
            "tightest_range_pct":    float,      # last (tightest) contraction
            "preferred_quality":     bool,       # >= vcp_preferred_contractions
            "prior_move_pct":        float,      # Fix 2: prior run-up fraction
            "has_higher_lows":       bool,       # Fix 5: strict higher lows
            "quality_score":         int,        # Fix 4: 0–10 composite score
        }
    """
    if settings is None:
        settings = get_settings()

    if len(df) < _MIN_HISTORY:
        return None

    # Work on the most recent _PATTERN_LOOKBACK bars for VCP detection.
    # df itself (full history) is passed to prior-move helpers below.
    window = df.sort_values("trade_date").tail(_PATTERN_LOOKBACK).copy()
    window = window.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Build contractions
    # ------------------------------------------------------------------
    all_contractions = _build_contractions(window)

    if len(all_contractions) < settings.vcp_min_contractions:
        return None

    # ------------------------------------------------------------------
    # Find the longest RECENT suffix of contractions that satisfies
    # both monotone-decreasing ranges AND monotone-decreasing volumes.
    #
    # Why suffix, not all?: A market correction (e.g. COVID crash) can
    # widen a contraction in the middle of the lookback window.  The
    # actionable VCP setup lives in the most recent tightening sequence.
    # We require at least vcp_min_contractions in the qualifying suffix.
    # ------------------------------------------------------------------
    max_depth = settings.vcp_max_depth_pct / 100.0

    contractions = []
    for start in range(len(all_contractions)):
        candidate = all_contractions[start:]
        if len(candidate) < settings.vcp_min_contractions:
            break
        ranges_c = [c["range_pct"] for c in candidate]
        volumes_c = [c["avg_volume"] for c in candidate]
        ranges_ok = all(ranges_c[i] > ranges_c[i + 1] for i in range(len(ranges_c) - 1))
        volumes_ok = all(volumes_c[i] > volumes_c[i + 1] for i in range(len(volumes_c) - 1))
        depth_ok = all(r <= max_depth for r in ranges_c)
        if ranges_ok and volumes_ok and depth_ok:
            contractions = candidate
            break   # longest valid suffix found (we iterate oldest→newest)

    if len(contractions) < settings.vcp_min_contractions:
        return None

    # ------------------------------------------------------------------
    # Fix 5: Strict higher lows — each swing low must be strictly above
    # the previous.  A single lower low rejects the pattern.
    # ------------------------------------------------------------------
    has_higher_lows = _check_higher_lows(contractions)
    if not has_higher_lows:
        return None

    # ------------------------------------------------------------------
    # Validation: each contraction within vcp_max_depth_pct (already
    # checked in suffix search above, kept for clarity)
    # ------------------------------------------------------------------
    ranges = [c["range_pct"] for c in contractions]

    # ------------------------------------------------------------------
    # Validation: last contraction must be tight (< vcp_tightness_factor_pct)
    # ------------------------------------------------------------------
    last_range = contractions[-1]["range_pct"]
    max_last_range = settings.vcp_tightness_factor_pct / 100.0
    if last_range > max_last_range:
        return None

    # ------------------------------------------------------------------
    # Validation: base duration must be within [min, max] days
    # ------------------------------------------------------------------
    first_high_date = contractions[0]["high_date"]
    last_low_date = contractions[-1]["low_date"]
    base_days: int | None = None
    if first_high_date is not None and last_low_date is not None:
        # Normalize to date objects for subtraction
        fhd = first_high_date.date() if hasattr(first_high_date, "date") else first_high_date
        lld = last_low_date.date() if hasattr(last_low_date, "date") else last_low_date
        base_days = (lld - fhd).days
        if not (settings.vcp_min_days_in_base <= base_days <= settings.vcp_max_days_in_base):
            return None

    # ------------------------------------------------------------------
    # Fix 2: Prior move — the stock must have traded MinPriorMovePct%
    # above the base-start price in the PriorMoveLookbackDays before
    # the base started.  Uses the full df (not just the 120-bar window).
    # ------------------------------------------------------------------
    prior_move_pct = _check_prior_move(df, contractions[0]["high_date"], settings)
    if prior_move_pct < settings.min_prior_move_pct / 100.0:
        logger.debug(
            "VCP rejected: prior_move=%.1f%% < min=%.1f%%",
            prior_move_pct * 100, settings.min_prior_move_pct,
        )
        return None

    # ------------------------------------------------------------------
    # Validation: current price must be near the last pivot high
    # ------------------------------------------------------------------
    last_pivot_high = contractions[-1]["high"]
    current_close = float(window["close_price"].iloc[-1])

    if last_pivot_high <= 0:
        return None

    distance = (last_pivot_high - current_close) / last_pivot_high
    # distance > 0  → price below pivot high (approaching)
    # distance ≈ 0  → price at pivot high (breakout imminent)
    # distance < 0  → price above pivot high (already broken out)

    # Too far below the pivot — not in the base yet
    if distance > _MAX_DISTANCE_PCT:
        return None

    # Already broken out significantly — no longer a setup candidate
    if distance < -_BREAKOUT_DISTANCE_PCT:
        return None

    # ------------------------------------------------------------------
    # Fix 4: Quality score — reject setups below MinSetupQualityScore
    # ------------------------------------------------------------------
    rs_score_val: float | None = None
    if "rs_score" in window.columns:
        raw_rs = window["rs_score"].iloc[-1]
        if raw_rs is not None and not pd.isna(raw_rs):
            rs_score_val = float(raw_rs)

    quality_score = _compute_quality_score(
        contractions, prior_move_pct, has_higher_lows, rs_score_val
    )
    if quality_score < settings.min_setup_quality_score:
        logger.debug(
            "VCP rejected: quality_score=%d < min=%d",
            quality_score, settings.min_setup_quality_score,
        )
        return None

    # ------------------------------------------------------------------
    # VCP confirmed
    # ------------------------------------------------------------------
    is_breakout_candidate = abs(distance) <= _BREAKOUT_DISTANCE_PCT
    preferred_quality = len(contractions) >= settings.vcp_preferred_contractions

    result = {
        "pattern": "VCP",
        "contractions_count": len(contractions),
        "ranges_pct": [c["range_pct"] for c in contractions],
        "volumes": [c["avg_volume"] for c in contractions],
        "pivot_high": round(last_pivot_high, 4),
        "current_close": round(current_close, 4),
        "distance_from_high_pct": round(distance, 4),
        "is_breakout_candidate": is_breakout_candidate,
        "tightest_range_pct": round(last_range, 4),
        "preferred_quality": preferred_quality,
        "prior_move_pct": round(prior_move_pct, 4),
        "has_higher_lows": has_higher_lows,
        "quality_score": quality_score,
        "base_length_days": base_days,
    }

    logger.debug(
        "VCP detected: contractions=%d, tightest=%.1f%%, distance=%.1f%%, "
        "prior_move=%.1f%%, quality=%d, breakout=%s",
        len(contractions),
        last_range * 100,
        distance * 100,
        prior_move_pct * 100,
        quality_score,
        is_breakout_candidate,
    )
    return result


# ---------------------------------------------------------------------------
# Scanner (DB integration)
# ---------------------------------------------------------------------------

def scan_vcp(
    symbols: list[str],
    session: Session,
    settings: Settings | None = None,
) -> dict[str, dict]:
    """
    Scan a list of tickers for VCP setups using data from the database.

    Loads the last _PATTERN_LOOKBACK price rows per ticker.  Tickers with
    no VCP return no entry in the result dict.

    Parameters
    ----------
    symbols : list[str]
        Ticker symbols to scan.
    session : Session
        Open SQLAlchemy session.
    settings : Settings | None
        Loaded settings; auto-loaded if None.

    Returns
    -------
    dict[str, dict]
        Mapping of symbol → VCP detail dict for tickers where a VCP was found.
        Symbols with no VCP are omitted.
    """
    if settings is None:
        settings = get_settings()

    engine = get_engine()
    results: dict[str, dict] = {}

    for symbol in symbols:
        symbol = symbol.upper()
        try:
            ticker_id: int | None = session.execute(
                select(Ticker.ticker_id).where(Ticker.symbol == symbol)
            ).scalar_one_or_none()

            if ticker_id is None:
                logger.warning("scan_vcp: '%s' not found in Tickers table — skipping", symbol)
                continue

            # Load enough bars for both VCP detection (_PATTERN_LOOKBACK)
            # and prior-move lookback (PriorMoveLookbackDays).
            bar_limit = _PATTERN_LOOKBACK + settings.prior_move_lookback_days + 20

            query = (
                select(
                    PriceData.trade_date.label("trade_date"),
                    PriceData.high_price.label("high_price"),
                    PriceData.low_price.label("low_price"),
                    PriceData.close_price.label("close_price"),
                    PriceData.volume.label("volume"),
                    PriceData.rs_score.label("rs_score"),
                )
                .where(PriceData.ticker_id == ticker_id)
                .order_by(PriceData.trade_date.desc())
                .limit(bar_limit)
            )
            with engine.connect() as conn:
                df = pd.read_sql(query, conn)

            if df.empty:
                continue

            # re-sort oldest-first after the DESC query
            df = df.sort_values("trade_date").reset_index(drop=True)

            vcp = detect_vcp(df, settings)
            if vcp is not None:
                results[symbol] = vcp
                logger.info(
                    "VCP found: %s | contractions=%d | pivot_high=%.2f | "
                    "prior_move=%.1f%% | quality=%d | breakout=%s",
                    symbol,
                    vcp["contractions_count"],
                    vcp["pivot_high"],
                    vcp["prior_move_pct"] * 100,
                    vcp["quality_score"],
                    vcp["is_breakout_candidate"],
                )

        except Exception as exc:
            logger.error("scan_vcp: error processing %s: %s", symbol, exc)

    logger.info(
        "VCP scan complete: %d / %d symbols show a VCP setup",
        len(results),
        len(symbols),
    )
    return results
