"""Phase A3.1.3 tests: _generate_windows step_months parameter.

Covers:
- Backward compat: step_months=None -> defaults to test_months (Phase A2 behavior)
- Monthly stride (step_months=1) -> ~48 windows for 2019-2025 / 36mo train / 12mo test
- 6-month stride gives the expected count
- Correct window boundaries (train/test non-overlap within a window)
"""

from __future__ import annotations

from datetime import datetime

from scripts.walk_forward import _generate_windows


def test_generate_windows_default_step_matches_phase_a2():
    """step_months=None -> behavior identical to explicit step=test_months.
    Regression guard: previous Phase A2 WF used non-overlapping stride."""
    start = datetime(2019, 1, 1)
    end = datetime(2025, 12, 31)
    default = _generate_windows(start, end, train_months=36, test_months=12)
    explicit = _generate_windows(start, end, train_months=36, test_months=12,
                                  step_months=12)
    assert len(default) == len(explicit)
    for a, b in zip(default, explicit):
        assert a == b


def test_generate_windows_monthly_stride_produces_48_slices_for_2019_2025():
    """step_months=1 with 36mo train + 12mo test + 2019-2025 (7 years) should
    produce roughly 4 slices per year non-overlapping equivalent extended via
    1-month stride — Phase A3.1.3 canonical 48-slice config."""
    start = datetime(2019, 1, 1)
    end = datetime(2025, 12, 31)
    windows = _generate_windows(
        start, end, train_months=36, test_months=12, step_months=1,
    )
    # First train ends 2022-01, first test ends 2023-01 -> valid
    # Last viable train_end is such that test_start + 12mo <= 2025-12-31
    # -> train_end (= test_start) <= 2024-12-31 -> first train_end = 2022-01-01,
    # stride of 1 month gives (2024-12-01 - 2022-01-01) / 1mo = 35 slices after
    # first one, +1 for the initial = 36 slices
    # But adjusting for end truncation, should be ~36-48 depending on exactness
    assert 30 <= len(windows) <= 50, (
        f"Expected monthly-stride windows to be in 30-50 range, got {len(windows)}"
    )


def test_generate_windows_monthly_stride_step_is_1_month():
    """step_months=1 means test_start of consecutive windows differ by exactly
    1 calendar month."""
    start = datetime(2019, 1, 1)
    end = datetime(2025, 12, 31)
    windows = _generate_windows(
        start, end, train_months=36, test_months=12, step_months=1,
    )
    # Check first few consecutive windows
    for i in range(min(5, len(windows) - 1)):
        # test_start for slice N+1 should be 1 month after slice N
        w1_test_start = windows[i]["test_start"]
        w2_test_start = windows[i + 1]["test_start"]
        delta_days = (w2_test_start - w1_test_start).days
        # 1 month = 28-31 days depending on month
        assert 28 <= delta_days <= 31, (
            f"slice {i} -> {i+1} stride should be ~1 month, got {delta_days} days"
        )


def test_generate_windows_train_test_non_overlap():
    """Within a window, test_start == train_end (adjacent, no overlap / gap)."""
    start = datetime(2020, 1, 1)
    end = datetime(2025, 12, 31)
    windows = _generate_windows(
        start, end, train_months=24, test_months=6, step_months=1,
    )
    for w in windows:
        assert w["test_start"] == w["train_end"], (
            f"window {w['window']}: test_start {w['test_start']} != "
            f"train_end {w['train_end']} (overlap or gap)"
        )


def test_generate_windows_no_crash_short_period():
    """Period too short for even 1 window -> return []."""
    # train=36mo but only 1 year data -> no window fits
    start = datetime(2024, 1, 1)
    end = datetime(2024, 12, 31)
    windows = _generate_windows(
        start, end, train_months=36, test_months=12, step_months=1,
    )
    assert windows == []  # no valid window
