"""PEAD — Post-Earnings Announcement Drift via quarterly EPS surprise.

Theory: stocks that beat consensus EPS outperform over the following weeks
(Bernard & Thomas 1989 classic anomaly). Taiwan has no FactSet-consensus
so we proxy with a **historical base-rate surprise**: compare latest EPS
against a rolling mean of prior quarters, scaled by historical std.

Data: FinMind TaiwanStockFinancialStatements, row per (date, type, value)
where ``type == 'EPS'`` gives quarterly EPS in NTD.

Formula:

    surprise_z = (eps_latest - mean(prior_n_quarters)) / std(prior_n_quarters)

    score = surprise_z  (continuous cross-sectional factor)

- ``prior_n_quarters`` default 8 (2 years)
- Requires ≥ 12 quarters (3 years) of history to stabilise the base rate.
- PIT: rows with ``date > as_of - QUARTERLY_EPS_LAG_DAYS`` are dropped.
  Note: ``date`` in the FinMind feed is the quarter-end (e.g. 2024-03-31
  for Q1-2024). Q1/Q2/Q3 legal deadline = +45 days; Q4 (annual) = +90 days.
  We use 60 days as a conservative blanket cutoff; symbols with Q4 reports
  that slip into the look-ahead window are filtered at the surprise level
  (latest value still anchored to the cutoff).
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from src.utils.constants import (
    QUARTERLY_EPS_LAG_DAYS,
    QUARTERLY_EPS_LAG_DAYS_OTHER,
    QUARTERLY_EPS_LAG_DAYS_Q4,
)


DEFAULT_MIN_HISTORY = 12    # quarters
DEFAULT_BASELINE_QUARTERS = 8


def _earliest_asof_for_row(row_date: pd.Timestamp) -> pd.Timestamp:
    """Earliest as_of timestamp at which a given quarter-end row becomes public.

    P1-1: Q4 annual report has a 90-day legal deadline (March 31 next year),
    Q1-Q3 quarterlies have 45 days. A flat 60-day blanket lag admits unfiled
    Q4 EPS into the factor universe for early-year as_ofs.
    """
    quarter = int(row_date.quarter)
    lag = QUARTERLY_EPS_LAG_DAYS_Q4 if quarter == 4 else QUARTERLY_EPS_LAG_DAYS_OTHER
    return row_date + pd.Timedelta(days=lag)


def _normalise_eps_frame(
    df: pd.DataFrame | None,
    as_of: pd.Timestamp,
    lag_days: int,
    *,
    quarter_aware: bool = True,
) -> pd.DataFrame | None:
    """Clean + PIT-truncate the quarterly EPS frame.

    When ``quarter_aware`` is True (default) P1-1 per-quarter deadlines apply:
    Q4 rows need 90 days, Q1-Q3 rows need 45 days. Passing ``quarter_aware=False``
    falls back to the blanket ``lag_days`` parameter for backward compat.
    """
    if df is None or df.empty or "date" not in df.columns:
        return None
    working = df.copy()
    if "type" in working.columns:
        working = working[working["type"] == "EPS"]
    if working.empty:
        return None
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    if "value" in working.columns:
        working["value"] = pd.to_numeric(working["value"], errors="coerce")
    working = working.dropna(subset=["date", "value"])
    if working.empty:
        return None

    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_convert(None)

    if quarter_aware:
        earliest_asof = working["date"].apply(_earliest_asof_for_row)
        working = working[earliest_asof <= as_of_ts]
    else:
        cutoff = as_of_ts - pd.Timedelta(days=lag_days)
        working = working[working["date"] <= cutoff]
    if working.empty:
        return None

    return working.sort_values("date").reset_index(drop=True)


def _compute_surprise_z(
    frame: pd.DataFrame,
    baseline_quarters: int,
) -> float | None:
    if len(frame) < baseline_quarters + 1:
        return None
    latest = float(frame["value"].iloc[-1])
    baseline = frame["value"].iloc[-(baseline_quarters + 1):-1]
    if len(baseline) < 3:
        return None
    mu = float(baseline.mean())
    sd = float(baseline.std(ddof=1))
    # Codex R6-3: `sd == 0` exact compare misses near-constant baselines that
    # yield sd ~ 1e-14 via float accumulation, producing pathological
    # pead_surprise_z (~4e12). Mirror the ic_analysis.py R5-2 tolerance.
    if sd < 1e-12 or pd.isna(sd):
        return None
    return (latest - mu) / sd


def compute_pead_eps(
    eps_df: pd.DataFrame | None,
    as_of: pd.Timestamp,
    lag_days: int = QUARTERLY_EPS_LAG_DAYS,
    min_quarters: int = DEFAULT_MIN_HISTORY,
    baseline_quarters: int = DEFAULT_BASELINE_QUARTERS,
) -> dict:
    """Single-symbol computation. Returns dict with surprise_z + diagnostics."""
    frame = _normalise_eps_frame(eps_df, as_of=as_of, lag_days=lag_days)
    if frame is None or len(frame) < min_quarters:
        return {
            "score": None,
            "surprise_z": None,
            "latest_eps": None,
            "n_quarters": 0 if frame is None else len(frame),
        }
    surprise = _compute_surprise_z(frame, baseline_quarters=baseline_quarters)
    return {
        "score": surprise,
        "surprise_z": surprise,
        "latest_eps": float(frame["value"].iloc[-1]),
        "n_quarters": len(frame),
    }


def compute_pead_eps_universe(
    eps_by_symbol: Mapping[str, pd.DataFrame | None],
    aux_panel: Mapping | None = None,   # unused (kept for CLI parity)
    as_of: pd.Timestamp | None = None,
    lag_days: int = QUARTERLY_EPS_LAG_DAYS,
    min_history: int = DEFAULT_MIN_HISTORY,
    baseline_quarters: int = DEFAULT_BASELINE_QUARTERS,
) -> pd.Series:
    """Batch compute EPS surprise z-score across the universe.

    Returns Series indexed by symbol with the raw surprise z (NOT re-z-scored
    cross-sectionally — downstream rank IC is order-invariant to monotonic
    transforms so cross-sectional z-score is unnecessary).
    """
    if as_of is None:
        raise ValueError("as_of is required")

    results: dict[str, float] = {}
    for symbol, df in eps_by_symbol.items():
        out = compute_pead_eps(
            df,
            as_of=as_of,
            lag_days=lag_days,
            min_quarters=min_history,
            baseline_quarters=baseline_quarters,
        )
        if out["score"] is not None:
            results[symbol] = float(out["score"])
    return pd.Series(results, dtype=float)
