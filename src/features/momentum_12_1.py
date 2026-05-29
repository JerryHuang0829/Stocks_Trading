"""Momentum 12-1 (Carhart 1997) — intermediate-term momentum factor.

Theory
------
Jegadeesh-Titman (1993) "Returns to Buying Winners and Selling Losers";
Carhart (1997, JoF) added it as the 4th factor to the F-F 3-factor model.
The standard construction is **past 12-month return excluding the most
recent 1 month**, because the latest month exhibits short-term reversal
(DeBondt-Thaler) that contaminates the momentum signal. Subtracting
that month gives a cleaner "winners persist" effect.

Sibling factors in this codebase:
  - high_proximity: 52-week-high proximity (related but different —
    distance to recent peak, not cumulative return)
  - reversal_1m: short-term reversal — uses exactly the 1m return
    that Mom12-1 deliberately excludes
  - idio_vol_max: residual volatility (different family entirely)

Formula
-------
    anchor_t1 = close at (t - 21 trading days)        # skip recent month
    anchor_t12 = close at (t - 252 trading days)      # 12 months prior
    Score(t)   = anchor_t1 / anchor_t12 - 1           # raw return

(Using simple return rather than log return for consistency with
reversal_1m / high_proximity sign conventions in the codebase.)

PIT discipline
--------------
- All anchors are PIT-safe: each is the close on or before its target
  trading day, which is strictly before as_of. No look-ahead.
- Equivalent to shift=1 in the spirit of high_proximity / value_ep:
  the FIRST anchor (t-21d) is already 21 trading days before as_of,
  so shift is automatic and explicit.

Filters
-------
- Need ≥ 252 + 1 trading days of history (252 for 12m anchor + 1 buffer).
- Drop if either anchor close is None / NaN / non-positive.

Output
------
pd.Series indexed by symbol, value = Mom12-1 raw return.
Sign convention: higher = stronger past winner = expected positive alpha
                (Carhart momentum convention).
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

DEFAULT_SKIP_DAYS = 21       # 1 trading month skip
DEFAULT_LOOKBACK_DAYS = 252  # 12 trading months
DEFAULT_MIN_HISTORY = 253    # need both anchors


def _close_at_offset(
    ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
    days_offset: int,
) -> float | None:
    """Close on or before (as_of - days_offset trading days).

    Strictly speaking ``days_offset`` is **calendar** trading-row offset:
    we take the last close at index position (len - 1 - days_offset).
    Falls back to None if insufficient history.
    """
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return None
    idx = pd.to_datetime(ohlcv.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close = ohlcv["close"].copy()
    close.index = idx
    cutoff = pd.Timestamp(as_of)
    if cutoff.tz is not None:
        cutoff = cutoff.tz_convert(None)
    # All trading days strictly before as_of (PIT)
    eligible = close[close.index < cutoff].dropna()
    if len(eligible) <= days_offset:
        return None
    val = float(eligible.iloc[-1 - days_offset])
    if not np.isfinite(val) or val <= 0:
        return None
    return val


def compute_momentum_12_1(
    ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
    *,
    skip_days: int = DEFAULT_SKIP_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict:
    """Per-symbol Momentum 12-1. Returns dict with score + diagnostics."""
    p_recent = _close_at_offset(ohlcv, as_of, skip_days)
    if p_recent is None:
        return {"score": None, "reason": "missing_t-1m_anchor"}
    p_far = _close_at_offset(ohlcv, as_of, lookback_days)
    if p_far is None:
        return {"score": None, "reason": "missing_t-12m_anchor"}
    ret = p_recent / p_far - 1.0
    if not np.isfinite(ret):
        return {"score": None, "reason": "non_finite_return"}
    return {
        "score": float(ret),
        "p_recent": p_recent,
        "p_far": p_far,
        "reason": "ok",
    }


def compute_momentum_12_1_universe(
    ohlcv_by_symbol: Mapping[str, pd.DataFrame | None],
    aux_panel: Mapping | None = None,  # CLI parity placeholder (unused)
    as_of: pd.Timestamp | None = None,
    min_history: int = DEFAULT_MIN_HISTORY,
    *,
    skip_days: int = DEFAULT_SKIP_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> pd.Series:
    """Batch compute Mom 12-1 across the universe.

    Parameters
    ----------
    ohlcv_by_symbol : {symbol: OHLCV DataFrame}
        Per-symbol OHLCV with at least ``close`` column.
    aux_panel : unused — kept for FACTOR_REGISTRY signature parity.
    as_of : timestamp
        Reference date (rebalance day).
    min_history : int
        Override for ``lookback_days + 1`` minimum (used by IC pipeline
        to skip symbols too young to have a 12m return).
    skip_days, lookback_days : int
        Trading-day offsets for the two anchors.

    Returns
    -------
    pd.Series indexed by symbol, value = past-12m-ex-1m raw return.
    Symbols with insufficient history are excluded.
    """
    if as_of is None:
        raise ValueError("as_of is required")
    # min_history is consulted as a soft floor — even if the caller passes
    # a smaller value, we still need at least lookback_days+1 rows to compute.
    required = max(min_history, lookback_days + 1)

    scores: dict[str, float] = {}
    for symbol, ohlcv in ohlcv_by_symbol.items():
        if ohlcv is None or ohlcv.empty:
            continue
        if len(ohlcv) < required:
            continue
        result = compute_momentum_12_1(
            ohlcv, as_of, skip_days=skip_days, lookback_days=lookback_days,
        )
        if result["score"] is not None:
            scores[symbol] = result["score"]
    return pd.Series(scores, dtype=float)
