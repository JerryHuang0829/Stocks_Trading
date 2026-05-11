"""Revenue Momentum v2 — multi-signal composite for monthly revenue.

Four sub-signals combined into one cross-sectional score:

    1. YoY growth      (0.50) — latest_month / same_month_last_year - 1
    2. 3M/3M accel     (0.20) — last_3m_avg / prev_3m_avg - 1
    3. 24M percentile  (0.15) — last_3m_avg rank vs every 3m-rolling-window in last 24m
    4. Seasonal z      (0.15) — for each of last 3 months, z-score vs the same
                                calendar month across preceding 24 months, averaged.

Point-in-time:
    Rows with ``date > as_of - REVENUE_LAG_DAYS`` are dropped before computation.
    The lag models the "legal publication deadline + publication buffer" for
    TWSE monthly revenue (10th of next month + 5-day buffer).

Data source: TWSE monthly revenue OpenData (``src.data.twse_scraper``) with
FinMind fallback. Cached in ``data/cache/revenue/<symbol>.pkl``.

Edge cases:
    * < 15 months of history → returned as None (dropped from universe Series)
    * NaN months → dropped via dropna
    * Zero-revenue base periods → YoY / Accel return None for that symbol
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from src.utils.constants import REVENUE_LAG_DAYS


DEFAULT_MIN_MONTHS = 15  # need ≥ 13 for YoY + 6 for accel; 15 is a safe floor
DEFAULT_PERCENTILE_LOOKBACK_MONTHS = 24
DEFAULT_SEASONAL_LOOKBACK_MONTHS = 24

# 2026-05-11 R32 finding fix: SUBSIGNAL_WEIGHTS is now a FALLBACK DEFAULT.
# The live source of truth is `config/factor_thresholds.yaml ::
# factor_specific.revenue_momentum_v2.weights`, read at call time via
# `_subsignal_weights()` (same pattern as foreign_investor_v2 R31-4 fix).
# Editing the yaml changes behaviour without touching code. The constant is
# kept (a) as a fallback if the yaml lookup fails validation, and (b) as a
# stable import target for tests/test_revenue_momentum_v2.py.
SUBSIGNAL_WEIGHTS = {
    "yoy": 0.50,
    "accel": 0.20,
    "percentile": 0.15,
    "seasonal_z": 0.15,
}


def _subsignal_weights() -> dict[str, float]:
    """Resolve sub-signal weights from yaml (fallback to SUBSIGNAL_WEIGHTS).

    2026-05-11 R32 finding fix: was hardcoded SUBSIGNAL_WEIGHTS only and
    `config/factor_thresholds.yaml :: factor_specific.revenue_momentum_v2.weights`
    used mismatched keys (accel_3m3m / pct_24m vs module accel / percentile). The
    yaml keys are now renamed to match; this helper reads them with the module
    constant as fallback.

    Falls back to the module constant if the yaml section is missing or fails
    validation: (a) must be a dict with exactly the 4 known sub-signal keys,
    (b) total weight must sum to ≈ 1.0 (within float tolerance), (c) all weights
    non-negative — so a malformed yaml can't silently de-normalise the composite.
    """
    try:
        from src.utils.thresholds import get_threshold
        weights = get_threshold(
            "factor_specific", "revenue_momentum_v2", "weights",
            default=None,
        )
        if isinstance(weights, dict) and set(weights) == set(SUBSIGNAL_WEIGHTS):
            vals = {k: float(v) for k, v in weights.items()}
            if abs(sum(vals.values()) - 1.0) < 1e-6 and all(v >= 0 for v in vals.values()):
                return vals
    except Exception:
        pass
    return dict(SUBSIGNAL_WEIGHTS)


def _normalise_revenue_frame(
    df: pd.DataFrame | None,
    as_of: pd.Timestamp,
    lag_days: int,
) -> pd.DataFrame | None:
    """Clean + PIT-truncate the monthly revenue frame. Returns sorted by date or None."""
    if df is None or df.empty or "date" not in df.columns:
        return None
    revenue_col = next(
        (col for col in ("revenue", "Revenue", "monthly_revenue") if col in df.columns),
        None,
    )
    if revenue_col is None:
        return None

    working = df[["date", revenue_col]].copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working["revenue"] = pd.to_numeric(working[revenue_col], errors="coerce")
    working = working.dropna(subset=["date", "revenue"])
    if working.empty:
        return None

    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_convert(None)
    cutoff = as_of_ts - pd.Timedelta(days=lag_days)

    working = working[working["date"] <= cutoff]
    if working.empty:
        return None

    working = working.sort_values("date").reset_index(drop=True)
    return working[["date", "revenue"]]


def _yoy_growth(frame: pd.DataFrame) -> float | None:
    """Latest revenue vs same calendar month one year earlier.

    P1-新6: strict year/month match. The previous ±45-day tolerance could silently
    substitute an adjacent month when the exact prior-year observation was
    missing, contaminating the YoY signal with seasonal drift. Fallback is None,
    so the symbol is dropped that period rather than given a misleading score.
    """
    if len(frame) < 13:
        return None
    latest = frame.iloc[-1]
    latest_date = pd.Timestamp(latest["date"])
    target_year = latest_date.year - 1
    target_month = latest_date.month
    mask = (frame["date"].dt.year == target_year) & (
        frame["date"].dt.month == target_month
    )
    matched = frame[mask]
    if matched.empty:
        return None
    base = float(matched.iloc[0]["revenue"])
    if base <= 0:
        return None
    return float(latest["revenue"]) / base - 1.0


def _acceleration(frame: pd.DataFrame) -> float | None:
    """3M vs prior 3M revenue average."""
    if len(frame) < 6:
        return None
    recent = float(frame["revenue"].iloc[-3:].mean())
    prev = float(frame["revenue"].iloc[-6:-3].mean())
    if prev == 0:
        return None
    return recent / prev - 1.0


def _percentile_rank(frame: pd.DataFrame, lookback_months: int) -> float | None:
    """Percentile rank of latest 3M avg among every 3M-rolling avg in the
    preceding ``lookback_months`` months.
    """
    if len(frame) < lookback_months + 3:
        return None
    window_tail = frame["revenue"].iloc[-(lookback_months + 3):]
    rolling_3m = window_tail.rolling(window=3).mean().dropna()
    if len(rolling_3m) < 4:
        return None
    latest = float(rolling_3m.iloc[-1])
    history = rolling_3m.iloc[:-1]  # exclude the latest point from the comparison pool
    if history.empty:
        return None
    # Rank in [0, 1]: proportion of history < latest
    rank = float((history < latest).sum()) / len(history)
    # Center to [-1, 1] so it matches the sign conventions of YoY / Accel
    return 2 * rank - 1


def _seasonal_zscore(frame: pd.DataFrame, lookback_months: int) -> float | None:
    """Mean z-score of the last 3 months against their same-calendar-month
    peers within the previous ``lookback_months`` months.

    Guards: each of the 3 recent months must have ≥ 2 same-month peers
    with non-zero stdev. Otherwise returns None.
    """
    if len(frame) < lookback_months + 3:
        return None

    zs: list[float] = []
    for offset in range(3):
        target_idx = len(frame) - 1 - offset
        if target_idx < 0:
            return None
        target_row = frame.iloc[target_idx]
        target_month = int(target_row["date"].month)
        # Peers: same calendar month in earlier positions (exclude target itself)
        earlier = frame.iloc[:target_idx]
        # Keep only entries within lookback_months months prior to target
        window_start = target_row["date"] - pd.DateOffset(months=lookback_months)
        peers = earlier[
            (earlier["date"] >= window_start)
            & (earlier["date"].dt.month == target_month)
        ]
        if len(peers) < 2:
            return None
        mu = float(peers["revenue"].mean())
        sd = float(peers["revenue"].std(ddof=1))
        # R6-3: mirror ic_analysis.py R5-2 — `sd == 0` exact compare is
        # brittle against float noise; near-constant revenue series can yield
        # sd ~ 1e-14 and produce seasonal_z values like 7e13. Use tolerance.
        if sd < 1e-12 or pd.isna(sd):
            return None
        zs.append((float(target_row["revenue"]) - mu) / sd)

    if not zs:
        return None
    return float(np.mean(zs))


def _composite_score(subsignals: dict[str, float | None]) -> float | None:
    """Weighted average over non-None sub-signals. Weights renormalized.

    2026-05-11 R32 finding fix: weights now resolved from yaml via
    `_subsignal_weights()` (was `SUBSIGNAL_WEIGHTS[name]` hardcoded).
    """
    weights = _subsignal_weights()
    paired = [
        (value, weights[name])
        for name, value in subsignals.items()
        if value is not None and not pd.isna(value)
    ]
    if not paired:
        return None
    total_weight = sum(w for _, w in paired)
    if total_weight == 0:
        return None
    return sum(v * w for v, w in paired) / total_weight


def compute_revenue_momentum_v2(
    revenue_df: pd.DataFrame | None,
    as_of: pd.Timestamp,
    lag_days: int = REVENUE_LAG_DAYS,
    min_months: int = DEFAULT_MIN_MONTHS,
    percentile_lookback: int = DEFAULT_PERCENTILE_LOOKBACK_MONTHS,
    seasonal_lookback: int = DEFAULT_SEASONAL_LOOKBACK_MONTHS,
) -> dict:
    """Single-symbol computation. Returns dict with subsignals + composite.

    Returns:
        {
            "score": float | None,       # composite (weighted avg of non-None parts)
            "yoy": float | None,
            "accel": float | None,
            "percentile": float | None,  # centered to [-1, 1]
            "seasonal_z": float | None,
            "n_months": int,
        }
    """
    frame = _normalise_revenue_frame(revenue_df, as_of, lag_days)
    if frame is None or len(frame) < min_months:
        return {
            "score": None, "yoy": None, "accel": None,
            "percentile": None, "seasonal_z": None,
            "n_months": 0 if frame is None else len(frame),
        }

    subsignals = {
        "yoy": _yoy_growth(frame),
        "accel": _acceleration(frame),
        "percentile": _percentile_rank(frame, percentile_lookback),
        "seasonal_z": _seasonal_zscore(frame, seasonal_lookback),
    }
    return {
        "score": _composite_score(subsignals),
        **subsignals,
        "n_months": len(frame),
    }


def compute_revenue_momentum_v2_universe(
    revenue_by_symbol: Mapping[str, pd.DataFrame | None],
    as_of: pd.Timestamp,
    lag_days: int = REVENUE_LAG_DAYS,
    min_history: int = DEFAULT_MIN_MONTHS,
    percentile_lookback: int = DEFAULT_PERCENTILE_LOOKBACK_MONTHS,
    seasonal_lookback: int = DEFAULT_SEASONAL_LOOKBACK_MONTHS,
) -> pd.Series:
    """Batch version — returns Series(index=symbol, value=composite_score).

    Symbols with insufficient history or no computable composite are dropped.
    ``min_history`` matches ``compute_high_proximity_universe`` naming for
    CLI parity (it is the ``min_months`` floor for this factor).
    """
    results: dict[str, float] = {}
    for symbol, df in revenue_by_symbol.items():
        out = compute_revenue_momentum_v2(
            df, as_of=as_of,
            lag_days=lag_days,
            min_months=min_history,
            percentile_lookback=percentile_lookback,
            seasonal_lookback=seasonal_lookback,
        )
        if out["score"] is not None:
            results[symbol] = float(out["score"])
    return pd.Series(results, dtype=float)
