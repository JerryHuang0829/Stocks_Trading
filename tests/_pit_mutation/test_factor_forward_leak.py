"""Factor-module PIT cutoff mutation tests — "passthrough panel" coverage.

`_DataSlicer` PIT-truncates OHLCV / institutional / month_revenue / market_value
directly (see `test_pit_forward_leak.py`). The remaining panels —
``quarterly_eps`` / ``margin_short`` / ``three_institutional`` — reach the
feature modules **un-truncated** via ``_DataSlicer.__getattr__`` passthrough;
the as_of cutoff then happens *inside* the feature module
(``compute_*_universe(..., as_of=...)``). These tests verify that internal
cutoff actually drops future-dated rows.

Closes the follow-up gap noted in:
  - ``reports/diagnosis/architecture_audit_2026_05_02.md`` §B.2
  - ``reports/sprint_pro_validation/J_multi_perspective_audit.md`` §P6.1

Mutation logic: if someone removed the ``<= cutoff`` filter inside the feature
module, these tests would FAIL — a planted future-dated outlier would leak into
the factor value.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.features.margin_short_ratio import _normalise_margin_frame, score_margin_short
from src.features.pead_eps import compute_pead_eps


# Quarter-end dates 2021-Q1 .. 2024-Q1 (13 rows). FinMind feeds quarterly EPS
# stamped at quarter-end; it becomes public +45d (Q1-Q3) / +90d (Q4) later.
_EPS_QUARTER_ENDS = pd.to_datetime([
    "2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31",
    "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
    "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    "2024-03-31",  # ← Q1-2024: public on 2024-05-15, NOT before
])
# First 12 quarters wobble around ~1.0; the 13th (unfiled at our as_of) is an
# extreme 99.0 — if it leaks in, surprise_z explodes.
_EPS_VALUES = [1.0, 1.1, 0.95, 1.05, 1.0, 1.1, 0.95, 1.05, 1.0, 1.1, 0.95, 1.05, 99.0]


def _eps_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "date": _EPS_QUARTER_ENDS,
        "type": ["EPS"] * len(_EPS_QUARTER_ENDS),
        "value": _EPS_VALUES,
    })


def test_pead_eps_cutoff_drops_unfiled_quarter():
    """Q1-2024 EPS (quarter-end 2024-03-31) is public on 2024-05-15 (+45d).

    At as_of=2024-04-15 it is NOT yet filed → must be excluded from the base
    rate. If the cutoff were removed, the planted 99.0 would become "latest"
    and the surprise z-score would blow up.
    """
    df = _eps_frame()
    out = compute_pead_eps(df, as_of=datetime(2024, 4, 15))
    assert out["n_quarters"] == 12, (
        f"FORWARD LEAK: unfiled 2024-Q1 EPS leaked into the base rate "
        f"(n_quarters={out['n_quarters']}, expected 12)"
    )
    assert out["surprise_z"] is not None
    assert abs(out["surprise_z"]) < 5.0, (
        f"FORWARD LEAK: surprise_z={out['surprise_z']} reflects the planted "
        f"99.0 outlier from an unfiled quarter"
    )


def test_pead_eps_cutoff_inclusive_after_filing_deadline():
    """Boundary: once the +45d window has elapsed (as_of=2024-05-20), the
    2024-Q1 row IS in-universe — verifies the cutoff is ``<=`` not over-strict.
    """
    df = _eps_frame()
    out = compute_pead_eps(df, as_of=datetime(2024, 5, 20))
    assert out["n_quarters"] == 13
    # The 99.0 jump is now legitimately visible → surprise is large.
    assert out["surprise_z"] is not None and out["surprise_z"] > 5.0


def test_margin_short_cutoff_drops_future_balance():
    """Margin/short panel: a future-dated balance spike (999999 lots dated after
    the as_of-lag cutoff) must not reach ``_compute_raw_signals`` — otherwise it
    becomes the "latest" row and ``margin_change_20d`` explodes.
    """
    past = pd.DataFrame({
        "date": pd.bdate_range("2024-04-01", periods=50),
        "MarginPurchaseTodayBalance": [1000.0] * 50,
        "ShortSaleTodayBalance": [0.0] * 50,
    })
    future = pd.DataFrame({
        "date": pd.to_datetime(["2024-07-01", "2024-07-02"]),
        "MarginPurchaseTodayBalance": [999999.0, 999999.0],
        "ShortSaleTodayBalance": [0.0, 0.0],
    })
    df = pd.concat([past, future], ignore_index=True)

    as_of = pd.Timestamp("2024-06-30")
    frame = _normalise_margin_frame(df, as_of=as_of, lag_days=2)  # cutoff = 2024-06-28
    assert frame is not None
    assert frame["MarginPurchaseTodayBalance"].max() == 1000.0, (
        "FORWARD LEAK: future margin-balance spike (999999) leaked past the "
        "as_of-lag cutoff"
    )
    assert frame["date"].max() <= pd.Timestamp("2024-06-28")

    # The score-level wrapper sees only the stable balance → no explosive change.
    res = score_margin_short(df, issued_shares=1e9, as_of=as_of, lag_days=2)
    assert res["score"] is not None
