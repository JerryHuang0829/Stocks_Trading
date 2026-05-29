"""Gross Profitability (Novy-Marx 2013) — Quality factor.

Theory
------
Novy-Marx (2013, JFE "The Other Side of Value"): cross-sectional gross
profitability has roughly the same predictive power as book-to-market
but with the opposite sign correlation to value. Combining the two
captures both "cheap" (value) and "productive" (quality) dimensions
without redundancy. The signal proxies a clean "productivity per
dollar of assets" metric that is harder to fake than bottom-line
earnings.

Formula
-------
    TTM_GP(t)  = sum( GrossProfit[last 4 quarters disclosed before t] )
    Assets(t)  = TotalAssets[latest balance sheet disclosed before t]
    Score(t)   = TTM_GP / Assets                       # higher = better

PIT discipline
--------------
- Both GrossProfit (income statement) and TotalAssets (balance sheet)
  use the same quarter-aware lag as pead_eps:
    Q1-Q3 quarter-end + 45 days (legal deadline)
    Q4 annual report + 90 days
- TotalAssets is a point-in-time snapshot per balance-sheet date;
  use the most recent one whose disclosure deadline ≤ as_of.

Filters
-------
- Require ≥ 4 quarters of GrossProfit available at as_of (for TTM).
- Drop TTM_GP ≤ 0 (loss-making at the gross level — usually data issues
  or distressed firm; Novy-Marx explicitly drops these).
- Drop Assets ≤ 0 or missing.
- Drop NaN in any required quarter.

Output
------
pd.Series indexed by symbol, value = GP/Assets ratio.
Sign convention: higher score = higher gross profitability per asset
                = expected positive alpha (Novy-Marx convention).
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from src.utils.constants import (
    QUARTERLY_EPS_LAG_DAYS_OTHER,
    QUARTERLY_EPS_LAG_DAYS_Q4,
)

DEFAULT_MIN_QUARTERS = 4   # for TTM GP


def _earliest_asof_for_row(row_date: pd.Timestamp) -> pd.Timestamp:
    """Quarter-aware disclosure deadline: Q4 = +90d, Q1-Q3 = +45d.

    Mirrors pead_eps._earliest_asof_for_row exactly so the GP / EPS / Value
    factors all use a single consistent PIT rule across the v5.0 feature set.
    """
    quarter = int(row_date.quarter)
    lag = QUARTERLY_EPS_LAG_DAYS_Q4 if quarter == 4 else QUARTERLY_EPS_LAG_DAYS_OTHER
    return row_date + pd.Timedelta(days=lag)


def _normalise_quarterly_frame(
    df: pd.DataFrame | None,
    type_value: str,
    as_of: pd.Timestamp,
) -> pd.DataFrame | None:
    """Filter ``df`` to rows matching ``type == type_value``, PIT-truncate.

    Generalised helper covering both GrossProfit (quarterly_financial_full)
    and TotalAssets (balance_sheet) — both have the same long-form schema
    (date / stock_id / type / value).
    """
    if df is None or df.empty or "date" not in df.columns:
        return None
    if "type" not in df.columns or "value" not in df.columns:
        return None
    working = df[df["type"] == type_value].copy()
    if working.empty:
        return None
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working["value"] = pd.to_numeric(working["value"], errors="coerce")
    working = working.dropna(subset=["date", "value"])
    if working.empty:
        return None

    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_convert(None)
    earliest = working["date"].apply(_earliest_asof_for_row)
    working = working[earliest <= as_of_ts]
    if working.empty:
        return None

    return working.sort_values("date").reset_index(drop=True)


def _ttm_gp(frame: pd.DataFrame, min_quarters: int) -> float | None:
    """Sum of last ``min_quarters`` GrossProfit quarters; None if NaN or short."""
    if len(frame) < min_quarters:
        return None
    last_n = frame["value"].iloc[-min_quarters:]
    if last_n.isna().any():
        return None
    s = float(last_n.sum())
    if not np.isfinite(s):
        return None
    return s


def _latest_assets(frame: pd.DataFrame) -> float | None:
    """Most recent TotalAssets disclosed before as_of (last row of PIT frame)."""
    if frame is None or frame.empty:
        return None
    val = float(frame["value"].iloc[-1])
    if not np.isfinite(val) or val <= 0:
        return None
    return val


def compute_gross_profitability(
    qf_df: pd.DataFrame | None,
    bs_df: pd.DataFrame | None,
    as_of: pd.Timestamp,
    *,
    min_quarters: int = DEFAULT_MIN_QUARTERS,
) -> dict:
    """Per-symbol Gross Profitability. Returns dict with score + diagnostics."""
    gp_frame = _normalise_quarterly_frame(qf_df, "GrossProfit", as_of)
    if gp_frame is None or len(gp_frame) < min_quarters:
        return {
            "score": None, "ttm_gp": None, "assets": None,
            "n_quarters": 0 if gp_frame is None else len(gp_frame),
            "reason": "insufficient_gp_history",
        }
    ttm = _ttm_gp(gp_frame, min_quarters=min_quarters)
    if ttm is None:
        return {
            "score": None, "ttm_gp": None, "assets": None,
            "n_quarters": len(gp_frame), "reason": "ttm_unavailable",
        }
    if ttm <= 0:
        return {
            "score": None, "ttm_gp": ttm, "assets": None,
            "n_quarters": len(gp_frame), "reason": "non_positive_gp",
        }
    assets_frame = _normalise_quarterly_frame(bs_df, "TotalAssets", as_of)
    assets = _latest_assets(assets_frame)
    if assets is None:
        return {
            "score": None, "ttm_gp": ttm, "assets": None,
            "n_quarters": len(gp_frame), "reason": "assets_unavailable",
        }
    score = ttm / assets
    return {
        "score": float(score),
        "ttm_gp": float(ttm),
        "assets": float(assets),
        "n_quarters": len(gp_frame),
        "reason": "ok",
    }


def compute_gross_profitability_universe(
    qf_by_symbol: Mapping[str, pd.DataFrame | None],
    bs_by_symbol: Mapping[str, pd.DataFrame | None] | None = None,
    as_of: pd.Timestamp | None = None,
    min_history: int = DEFAULT_MIN_QUARTERS,
) -> pd.Series:
    """Batch compute GP/Assets across the universe.

    Parameters
    ----------
    qf_by_symbol : {symbol: quarterly_financial_full DataFrame}
        FinMind TaiwanStockFinancialStatements long-form. Must contain
        rows with type='GrossProfit'.
    bs_by_symbol : {symbol: balance_sheet DataFrame}
        FinMind TaiwanStockBalanceSheet long-form. Must contain rows
        with type='TotalAssets'.
    as_of : timestamp
        Reference date (rebalance day).
    min_history : int
        Minimum quarters required for TTM (default 4 = 1 year).

    Returns
    -------
    pd.Series indexed by symbol, value = GP / Assets ratio. Symbols failing
    any filter (insufficient GP history, non-positive GP, missing assets)
    are excluded.
    """
    if as_of is None:
        raise ValueError("as_of is required")
    if bs_by_symbol is None:
        return pd.Series(dtype=float)

    scores: dict[str, float] = {}
    for symbol, qf_df in qf_by_symbol.items():
        bs_df = bs_by_symbol.get(symbol)
        result = compute_gross_profitability(
            qf_df, bs_df, as_of=as_of, min_quarters=min_history,
        )
        if result["score"] is not None:
            scores[symbol] = result["score"]
    return pd.Series(scores, dtype=float)
