"""Value (E/P) — Earnings-to-Price ratio (classical Fama-French Value).

Theory
------
Low E/P (expensive) stocks tend to under-perform; high E/P (cheap) stocks
out-perform.  Original Fama-French HML (1992/1993) ranks on book-to-market
but the closely-related earnings-yield E/P captures the same value premium
and is more directly observable in Taiwan (no consistent BV data feed).
Adding Value to the v3.3 factor set closes the single biggest theoretical
gap (v3.3 had momentum / quality / liquidity proxies but no value lever).

Formula
-------
    TTM_EPS(t) = sum( EPS[last 4 quarters whose disclosure deadline ≤ t] )
    Price(t)   = close at (t - 1 trading day)          # shift=1 PIT
    Value(t,s) = TTM_EPS(t,s) / Price(t,s)             # higher = cheaper

PIT discipline
--------------
- Quarterly EPS: reuses `pead_eps._normalise_eps_frame` which applies the
  P1-1 quarter-aware deadline (Q1-Q3 = +45 d, Q4 annual report = +90 d).
- Price: shift=1 — at rebalance ``as_of`` use ``close`` on or before
  (as_of - 1 day). Mirrors high_proximity / low_vol_v2 semantics.

Filters
-------
- Need ≥ 4 quarters available at ``as_of`` for a valid TTM (else drop).
- Drop TTM_EPS ≤ 0 (loss-makers): F-F HML convention; "cheap money-loser"
  effect contaminates a pure value signal.
- Drop Price ≤ 0 or NaN.

Output
------
``pd.Series`` indexed by symbol, value = E/P ratio.  Caller convention is
"higher score = better" (here higher E/P = cheaper = expected positive alpha),
matching the project's existing factor sign convention (no negation needed).
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from src.features.pead_eps import _normalise_eps_frame
from src.utils.constants import (
    QUARTERLY_EPS_LAG_DAYS,
)

DEFAULT_MIN_QUARTERS = 4   # for TTM EPS


def _price_asof(
    ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
) -> float | None:
    """PIT-safe close on or before (as_of - 1 day).  Shift=1 semantics."""
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
    cutoff = cutoff - pd.Timedelta(days=1)
    valid = close[close.index <= cutoff].dropna()
    if valid.empty:
        return None
    price = float(valid.iloc[-1])
    if not np.isfinite(price) or price <= 0:
        return None
    return price


def _ttm_eps(eps_frame: pd.DataFrame, min_quarters: int) -> float | None:
    """Sum of the latest ``min_quarters`` quarters in the PIT-truncated frame.

    Returns None if fewer quarters available, or if any quarter's value is NaN.
    """
    if len(eps_frame) < min_quarters:
        return None
    last_n = eps_frame["value"].iloc[-min_quarters:]
    if last_n.isna().any():
        return None
    s = float(last_n.sum())
    if not np.isfinite(s):
        return None
    return s


def compute_value_ep(
    eps_df: pd.DataFrame | None,
    ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
    *,
    min_quarters: int = DEFAULT_MIN_QUARTERS,
) -> dict:
    """Per-symbol Value (E/P).  Returns dict with score + diagnostics."""
    frame = _normalise_eps_frame(
        eps_df,
        as_of=as_of,
        lag_days=QUARTERLY_EPS_LAG_DAYS,
    )
    if frame is None or len(frame) < min_quarters:
        return {
            "score": None,
            "ttm_eps": None,
            "price": None,
            "n_quarters": 0 if frame is None else len(frame),
            "reason": "insufficient_eps_history",
        }
    ttm = _ttm_eps(frame, min_quarters=min_quarters)
    if ttm is None:
        return {
            "score": None, "ttm_eps": None, "price": None,
            "n_quarters": len(frame), "reason": "ttm_unavailable",
        }
    if ttm <= 0:
        return {
            "score": None, "ttm_eps": ttm, "price": None,
            "n_quarters": len(frame), "reason": "negative_or_zero_ttm",
        }
    price = _price_asof(ohlcv, as_of)
    if price is None:
        return {
            "score": None, "ttm_eps": ttm, "price": None,
            "n_quarters": len(frame), "reason": "price_unavailable",
        }
    ep = ttm / price
    return {
        "score": float(ep),
        "ttm_eps": float(ttm),
        "price": float(price),
        "n_quarters": len(frame),
        "reason": "ok",
    }


def compute_value_ep_universe(
    eps_by_symbol: Mapping[str, pd.DataFrame | None],
    ohlcv_by_symbol: Mapping[str, pd.DataFrame | None] | None = None,
    aux_panel: Mapping | None = None,         # CLI parity placeholder
    as_of: pd.Timestamp | None = None,
    min_history: int = DEFAULT_MIN_QUARTERS,
    *,
    close_by_symbol: Mapping[str, pd.Series] | None = None,
) -> pd.Series:
    """Batch compute Value (E/P) score across the universe.

    Parameters
    ----------
    eps_by_symbol : {symbol: quarterly EPS DataFrame}
        Same schema as ``pead_eps`` uses (FinMind TaiwanStockFinancialStatements
        long form with ``type='EPS'`` rows).
    ohlcv_by_symbol : {symbol: OHLCV DataFrame} | None
        Per-symbol OHLCV with at least ``close`` column.  Either this or
        ``close_by_symbol`` must be supplied so prices are PIT-resolvable.
    aux_panel : unused — kept so the factor signature mirrors the CLI dispatcher
        contract of the existing ``run_factor_ic.py`` registry.
    as_of : timestamp
        Reference date (rebalance day).
    min_history : int
        Minimum quarters required for TTM (default 4 = 1 year).
    close_by_symbol : {symbol: close pd.Series} | None
        Alternative price input (matches the ``foreign_investor_v2`` plumbing).

    Returns
    -------
    pd.Series indexed by symbol, value = E/P ratio.  Symbols failing any
    filter (insufficient EPS, negative TTM, missing price) are excluded.
    """
    if as_of is None:
        raise ValueError("as_of is required")

    # Allow either ohlcv_by_symbol (DataFrame per symbol) or close_by_symbol (Series).
    if ohlcv_by_symbol is None and close_by_symbol is None:
        raise ValueError(
            "Provide either ohlcv_by_symbol (DataFrame) or close_by_symbol (Series)"
        )

    scores: dict[str, float] = {}
    for symbol, eps_df in eps_by_symbol.items():
        ohlcv = None
        if ohlcv_by_symbol is not None:
            ohlcv = ohlcv_by_symbol.get(symbol)
        elif close_by_symbol is not None:
            s = close_by_symbol.get(symbol)
            if s is not None:
                ohlcv = pd.DataFrame({"close": s})
        result = compute_value_ep(
            eps_df, ohlcv, as_of=as_of, min_quarters=min_history,
        )
        if result["score"] is not None:
            scores[symbol] = result["score"]

    return pd.Series(scores, dtype=float)
