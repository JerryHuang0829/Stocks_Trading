"""Tests for src/utils/returns.py — gap-aware return computation (v8 prep, 2026-05-22 audit).

Verifies the single-source-of-truth gap detection primitive:
- detect_index_gaps: consecutive / gapped / non-datetime / <2-row / boundary
- gap_aware_returns: value-preservation (flag-only policy) + GapReport correctness
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.utils.returns import (  # noqa: E402
    MAX_RETURN_GAP_DAYS,
    GapReport,
    detect_index_gaps,
    gap_aware_returns,
)


# --- constant ---------------------------------------------------------------

def test_max_return_gap_days_default():
    """Sanity: shared threshold is 10 calendar days."""
    assert MAX_RETURN_GAP_DAYS == 10


# --- detect_index_gaps ------------------------------------------------------

def test_detect_no_gap_consecutive_dates():
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    report = detect_index_gaps(idx)
    assert not report.has_gap
    assert report.n_gaps == 0
    assert report.max_gap_days == 0


def test_detect_single_gap():
    """One >10-day gap is detected with the correct span."""
    idx = pd.DatetimeIndex([
        "2024-01-01", "2024-01-02", "2024-01-03",
        "2024-02-15", "2024-02-16",  # ~43-day jump
    ])
    report = detect_index_gaps(idx)
    assert report.has_gap
    assert report.n_gaps == 1
    assert report.max_gap_days == 43


def test_detect_multiple_gaps():
    idx = pd.DatetimeIndex([
        "2024-01-01", "2024-02-01",  # 31-day gap
        "2024-02-02", "2024-04-01",  # ~59-day gap
    ])
    report = detect_index_gaps(idx)
    assert report.n_gaps == 2
    assert report.max_gap_days == 59


def test_detect_boundary_exactly_threshold_not_a_gap():
    """A gap of exactly MAX_RETURN_GAP_DAYS is NOT flagged (strict >)."""
    idx = pd.DatetimeIndex(["2024-01-01", "2024-01-11"])  # 10 days
    assert not detect_index_gaps(idx).has_gap


def test_detect_boundary_one_over_threshold_is_a_gap():
    idx = pd.DatetimeIndex(["2024-01-01", "2024-01-12"])  # 11 days
    assert detect_index_gaps(idx).has_gap


def test_detect_non_datetime_index_returns_empty():
    """A non-DatetimeIndex cannot be gap-checked → empty report, no crash."""
    report = detect_index_gaps(pd.RangeIndex(5))
    assert not report.has_gap
    assert report.n_gaps == 0


def test_detect_single_row_returns_empty():
    report = detect_index_gaps(pd.DatetimeIndex(["2024-01-01"]))
    assert not report.has_gap


def test_detect_first_row_nat_not_false_gap():
    """The first row's diff is NaT; it must not be counted as a gap."""
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    report = detect_index_gaps(idx)
    assert report.n_gaps == 0


# --- gap_aware_returns ------------------------------------------------------

def test_gap_aware_returns_pct_value_preserving():
    """method='pct' is byte-identical to pandas pct_change() (flag-only policy)."""
    prices = pd.Series(
        [100.0, 110.0, 99.0, 99.0],
        index=pd.date_range("2024-01-01", periods=4, freq="B"),
    )
    rets, report = gap_aware_returns(prices, method="pct")
    pd.testing.assert_series_equal(rets, prices.pct_change())
    assert not report.has_gap


def test_gap_aware_returns_log_value_preserving():
    """method='log' is byte-identical to np.log(prices).diff()."""
    prices = pd.Series(
        [100.0, 110.0, 105.0],
        index=pd.date_range("2024-01-01", periods=3, freq="B"),
    )
    rets, _ = gap_aware_returns(prices, method="log")
    pd.testing.assert_series_equal(rets, np.log(prices).diff())


def test_gap_aware_returns_values_unaltered_even_with_gap():
    """Flag-only policy: a gap-spanning return is REPORTED but NOT altered."""
    prices = pd.Series(
        [100.0, 50.0, 51.0],  # 100->50 = -50% across a long gap
        index=pd.DatetimeIndex(["2024-01-01", "2024-03-01", "2024-03-02"]),
    )
    rets, report = gap_aware_returns(prices, method="pct")
    # gap detected ...
    assert report.has_gap
    # ... but the -50% return value is left exactly as pandas computed it
    pd.testing.assert_series_equal(rets, prices.pct_change())
    assert abs(rets.iloc[1] - (-0.5)) < 1e-12


def test_gap_aware_returns_reports_gap():
    prices = pd.Series(
        [10.0, 11.0, 12.0, 30.0],
        index=pd.DatetimeIndex([
            "2024-01-01", "2024-01-02", "2024-01-03", "2024-02-20",
        ]),
    )
    _, report = gap_aware_returns(prices, method="pct")
    assert report.has_gap
    assert report.n_gaps == 1


def test_gap_aware_returns_invalid_method_raises():
    prices = pd.Series([1.0, 2.0], index=pd.date_range("2024-01-01", periods=2))
    with pytest.raises(ValueError, match="method must be"):
        gap_aware_returns(prices, method="diff")


def test_gap_report_has_gap_property():
    assert GapReport().has_gap is False
    assert GapReport(n_gaps=1).has_gap is True
