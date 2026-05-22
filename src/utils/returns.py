"""Gap-aware return computation — single source of truth (v8 prep, 2026-05-22 audit).

Problem
-------
``pandas`` ``pct_change()`` / ``diff()`` compare consecutive *rows*, blind to
how many calendar days separate them.  A suspended / illiquid stock — or a
cache gap — produces a single "return" that actually spans weeks, silently
corrupting any risk metric (vol / Sharpe / MDD / TE) derived from it.

Before this module, five call sites each rolled their own raw ``pct_change`` /
``diff`` with no gap awareness (audit forensic-sweep 2026-05-22):

  - src/backtest/engine.py   ``_compute_daily_returns``  (per-stock daily returns)
  - src/backtest/engine.py   benchmark daily returns
  - src/features/idio_vol_max.py   residual + MAX lookback windows
  - src/features/low_vol_v2.py     log-return window
  - src/portfolio/tw_stock.py      ``volatility_20d``

This module centralises the gap *detection* primitive so every site shares one
threshold and one definition.

Policy (2026-05-22 audit decision: FLAG-ONLY)
---------------------------------------------
Return values are **never altered** here — no winsorise, no drop, no spread.
``gap_aware_returns`` hands back the plain ``pct_change`` / log-diff series plus
a :class:`GapReport`; the caller decides how to surface it (warn, mark a period
``data_degraded``, add a diagnostics counter).  This keeps cumulative NAV exact
and never fabricates an intra-suspension price path — consistent with the
project's guard-not-silent-fallback discipline.

Note: ``metrics._SPLIT_MAX_GAP_DAYS`` encodes the same "a normal trading-row
adjacency is at most ~10 calendar days" idea for *split* detection.  The two are
deliberately kept as independent constants (different purpose) but share the
value 10; change them together if the trading-calendar assumption changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Calendar-day span beyond which two adjacent rows are treated as gap-separated.
# 10 days covers normal long weekends / consecutive holidays; a held stock that
# resumes after a multi-week suspension exceeds it.
MAX_RETURN_GAP_DAYS = 10


@dataclass
class GapReport:
    """Calendar-gap detection result for a price/return index.

    Attributes
    ----------
    n_gaps : int
        Number of consecutive-row pairs separated by more than the threshold.
    max_gap_days : int
        Largest gap (calendar days) found; 0 when no gap.
    gaps : list[tuple[pd.Timestamp, int]]
        ``(row_timestamp, gap_days)`` for each gap-separated row.
    """

    n_gaps: int = 0
    max_gap_days: int = 0
    gaps: list[tuple[pd.Timestamp, int]] = field(default_factory=list)

    @property
    def has_gap(self) -> bool:
        return self.n_gaps > 0


def detect_index_gaps(
    index: pd.Index,
    max_gap_days: int = MAX_RETURN_GAP_DAYS,
) -> GapReport:
    """Detect consecutive-row pairs in a DatetimeIndex spanning > ``max_gap_days``.

    Pure detection, no side effects.  Returns an empty :class:`GapReport` when
    the index is not a ``DatetimeIndex`` or has fewer than 2 rows (gaps are then
    indeterminable / undefined).
    """
    report = GapReport()
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return report
    gap_days = index.to_series().diff().dt.days
    for ts, g in zip(index, gap_days):
        # First row's diff is NaT -> g is NaN; NaN > threshold is False.
        if pd.notna(g) and g > max_gap_days:
            report.n_gaps += 1
            report.gaps.append((ts, int(g)))
    if report.gaps:
        report.max_gap_days = max(g for _, g in report.gaps)
    return report


def gap_aware_returns(
    prices: pd.Series,
    *,
    method: str = "pct",
    max_gap_days: int = MAX_RETURN_GAP_DAYS,
) -> tuple[pd.Series, GapReport]:
    """Compute a return series and report calendar gaps WITHOUT altering values.

    Parameters
    ----------
    prices : pd.Series
        Price series indexed by date (ideally a ``DatetimeIndex``).
    method : {"pct", "log"}
        ``"pct"`` -> ``prices.pct_change()`` ; ``"log"`` -> ``np.log(prices).diff()``.
    max_gap_days : int
        Calendar-day threshold for gap detection.

    Returns
    -------
    (returns, report) : tuple[pd.Series, GapReport]
        ``returns`` is byte-identical to the corresponding plain pandas call
        (NOT dropna'd — the caller drops NaN as before).  ``report`` flags
        whether the series spans calendar gaps; per the flag-only policy the
        caller decides how to surface it.
    """
    if method == "pct":
        returns = prices.pct_change()
    elif method == "log":
        returns = np.log(prices).diff()
    else:
        raise ValueError(f"method must be 'pct' or 'log', got {method!r}")
    report = detect_index_gaps(prices.index, max_gap_days=max_gap_days)
    return returns, report
