"""Cross-frequency factor alignment for monthly cell sweep (V0.13 P1 #10).

Phase 2 Session 1 落地 (2026-05-05): provide wrapper to align factor panel
(any frequency) to monthly rebalance t-point. Existing 5 factors each handle
their own PIT lag internally per `src/utils/constants.py`; this module
provides the cross-freq alignment infra for the v7 18-cell sweep.

PIT discipline:
    - daily: shift=1 minimum (strictly-before-rebalance close)
    - monthly: latest month-end strictly before (t - pit_lag_days)
    - quarterly: latest quarter-end with (factor_date + pit_lag_days <= t)

Caller wires (Phase 2 S4 composite_d_v7 generic engine):
    from src.utils.cross_frequency import align_factor_to_rebalance_date
    aligned = align_factor_to_rebalance_date(
        factor_panel=high_proximity_panel,
        factor_freq="daily",
        rebalance_date=month_end_t,
        pit_lag_days=1,  # shift=1 semantics
    )

Per H_d_v6 V0.13 §"3 New factor PIT lag spec" + Phase 2 Session 1 「跨頻 infra」
spec: 18-cell sweep 各 cell 在月初 t 點對齊各 factor PIT-lagged value，避免
look-ahead bias。
"""
from __future__ import annotations

from typing import Literal

import pandas as pd


FactorFreq = Literal["daily", "monthly", "quarterly"]


def align_factor_to_rebalance_date(
    factor_panel: pd.DataFrame,
    factor_freq: FactorFreq,
    rebalance_date: pd.Timestamp,
    pit_lag_days: int,
) -> pd.Series:
    """Align a factor panel (any frequency) to a monthly rebalance t-point.

    Args:
        factor_panel: DataFrame with DatetimeIndex (rows = publication dates,
                      columns = symbols). Each row is a factor snapshot at the
                      respective publication date.
        factor_freq: "daily" | "monthly" | "quarterly".
        rebalance_date: Phase 2 cell sweep rebalance day (typically month-end).
        pit_lag_days: PIT publication lag (e.g. 1d for daily shift=1, 45d for
                      monthly revenue, 60d/90d for quarterly EPS).

    Returns:
        Series indexed by symbol with the latest valid factor value at
        (rebalance_date - pit_lag_days). Empty Series if no valid date exists.

    Raises:
        ValueError: if factor_freq invalid or factor_panel empty.

    PIT semantics:
        - daily: take latest factor row STRICTLY BEFORE
          (rebalance_date - max(1, pit_lag_days)). The max(1, ...) enforces
          shift=1 even when caller passes pit_lag_days=0 by mistake.
        - monthly: take latest factor row STRICTLY BEFORE
          (rebalance_date - pit_lag_days).
        - quarterly: take latest factor row with
          (factor_date + pit_lag_days <= rebalance_date), i.e.
          factor_date <= rebalance_date - pit_lag_days.
    """
    if factor_freq not in ("daily", "monthly", "quarterly"):
        raise ValueError(
            f"Unknown factor_freq: {factor_freq!r}. "
            f"Must be 'daily' | 'monthly' | 'quarterly'."
        )
    if factor_panel.empty:
        raise ValueError("factor_panel is empty")

    cutoff = rebalance_date - pd.Timedelta(days=pit_lag_days)

    if factor_freq == "daily":
        eff_lag = max(1, pit_lag_days)
        eff_cutoff = rebalance_date - pd.Timedelta(days=eff_lag)
        valid_dates = factor_panel.index[factor_panel.index < eff_cutoff]
    elif factor_freq == "monthly":
        valid_dates = factor_panel.index[factor_panel.index < cutoff]
    else:
        valid_dates = factor_panel.index[factor_panel.index <= cutoff]

    if len(valid_dates) == 0:
        return pd.Series(dtype=float)

    last_date = valid_dates.max()
    return factor_panel.loc[last_date]
