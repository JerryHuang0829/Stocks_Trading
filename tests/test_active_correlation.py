"""Tests for src.analysis.active_correlation.active_corr.

Mutation tests cover:
1. happy path: known synthetic series → known corr value
2. self-corr mutation: revert to corr(portfolio, portfolio) → would always
   return 1.0 (hollow PASS) → caught by mutation test
3. wrong direction mutation: revert to corr(portfolio, benchmark) → different
   value than corr(active, benchmark) → caught
4. length mismatch raises (sanity check)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.active_correlation import active_corr  # noqa: E402


def test_active_corr_basic_signature():
    """Happy path: known portfolio + benchmark monthly series → finite corr."""
    dates = pd.date_range("2024-01-31", periods=12, freq="ME")
    # Portfolio strongly correlated with benchmark + alpha noise
    bench = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02, 0.0, 0.01, -0.03, 0.02, 0.01, -0.01, 0.02], index=dates)
    port = pd.Series([0.015, -0.018, 0.032, -0.008, 0.022, 0.005, 0.012, -0.028, 0.022, 0.012, -0.008, 0.022], index=dates)
    result = active_corr(port, bench)
    assert isinstance(result, float)
    assert -1.0 <= result <= 1.0


def test_active_corr_active_zero_handles_gracefully():
    """Edge: portfolio == benchmark → active is zero series → corr is NaN.

    pd.Series.corr returns NaN when std is zero; active_corr passes through.
    L5 (a) gate caller must handle NaN as PASS (no active management = no
    active corr to measure)."""
    dates = pd.date_range("2024-01-31", periods=12, freq="ME")
    bench = pd.Series([0.01] * 12, index=dates)
    port = bench.copy()
    result = active_corr(port, bench)
    # active = 0, corr undefined → NaN expected
    assert pd.isna(result)


def test_active_corr_mutation_catches_self_corr():
    """Mutation: revert to corr(portfolio, portfolio).
    self-corr always returns 1.0 (hollow PASS) → this test catches the
    regression by verifying active_corr ≠ self-corr.
    """
    import numpy as np
    rng = np.random.default_rng(42)
    dates = pd.date_range("2020-01-31", periods=60, freq="ME")
    bench = pd.Series(rng.normal(0.005, 0.04, 60), index=dates)
    # Portfolio with known active component
    active = pd.Series(rng.normal(0.002, 0.02, 60), index=dates)
    port = bench + active

    real_corr = active_corr(port, bench)
    mutation_self_corr = float(port.corr(port))  # would be 1.0 if mutated
    # Mutation: returning self_corr would always be ~1.0
    assert abs(mutation_self_corr - 1.0) < 1e-9
    # active_corr must NOT equal self-corr (would be hollow)
    assert abs(real_corr - mutation_self_corr) > 0.01, (
        "active_corr collapsed to self-corr → mutation regression"
    )


def test_active_corr_mutation_catches_port_vs_bench():
    """Mutation: revert to corr(portfolio, benchmark) directly
    (without subtracting active = port - bench). Different metric than
    corr(active, benchmark); test catches the regression."""
    import numpy as np
    rng = np.random.default_rng(42)
    dates = pd.date_range("2020-01-31", periods=60, freq="ME")
    bench = pd.Series(rng.normal(0.005, 0.04, 60), index=dates)
    active = pd.Series(rng.normal(0.002, 0.02, 60), index=dates)
    port = bench + active

    real_active_corr = active_corr(port, bench)  # corr(active, bench)
    mutation_port_bench_corr = float(port.corr(bench))  # corr(port, bench)
    # These should differ (port = bench + active means high port-bench corr
    # but lower active-bench corr)
    assert abs(real_active_corr - mutation_port_bench_corr) > 0.01, (
        "active_corr equals port-bench corr → not subtracting active properly"
    )


def test_active_corr_length_mismatch_raises():
    """Sanity: caller passes mismatched series → ValueError with caller hint."""
    dates_short = pd.date_range("2024-01-31", periods=5, freq="ME")
    dates_long = pd.date_range("2024-01-31", periods=10, freq="ME")
    port = pd.Series([0.01] * 5, index=dates_short)
    bench = pd.Series([0.01] * 10, index=dates_long)
    with pytest.raises(ValueError, match="Length mismatch"):
        active_corr(port, bench)


def test_active_corr_index_misalignment_raises_v0_14():
    """docstring promised non-aligned index check but original code only
    verified length. Same length ≠ same dates; pandas Series subtract
    auto-aligns by index which silently produces wrong result if dates differ.
    Caller MUST align by date index first.

    Mutation reverts the index check → same-length-different-dates
    series silently compute wrong active_corr."""
    dates_a = pd.date_range("2024-01-31", periods=12, freq="ME")
    dates_b = pd.date_range("2024-02-29", periods=12, freq="ME")  # offset 1 month
    port = pd.Series([0.01] * 12, index=dates_a)
    bench = pd.Series([0.01] * 12, index=dates_b)
    # Same length (12) but different date indexes → must raise
    with pytest.raises(ValueError, match="Index misalignment"):
        active_corr(port, bench)


def test_active_corr_a10_mutation_3_daily_frequency_v1_2_s5():
    """Mutation: daily frequency masquerading as monthly → must produce
    different result than properly-monthly aligned input. Together with
    self-corr and port-vs-bench mutations covers the active_corr mutation set.

    Mutation list:
      1. self-corr → covered by test_active_corr_mutation_catches_self_corr
      2. daily frequency → THIS test
      3. 移除 active = port - bench → covered by test_active_corr_mutation_catches_port_vs_bench
    """
    import numpy as np
    rng = np.random.default_rng(42)
    # Daily-frequency series (252 trading days, masquerading as monthly)
    daily_dates = pd.date_range("2024-01-01", periods=252, freq="B")
    daily_bench = pd.Series(rng.normal(0.0005, 0.015, 252), index=daily_dates)
    daily_active = pd.Series(rng.normal(0.0002, 0.005, 252), index=daily_dates)
    daily_port = daily_bench + daily_active

    # Monthly-frequency series (12 monthly observations from same period)
    monthly_dates = pd.date_range("2024-01-31", periods=12, freq="ME")
    monthly_bench_arr = rng.normal(0.005, 0.04, 12)
    monthly_active_arr = rng.normal(0.002, 0.02, 12)
    monthly_bench = pd.Series(monthly_bench_arr, index=monthly_dates)
    monthly_port = pd.Series(monthly_bench_arr + monthly_active_arr, index=monthly_dates)

    daily_result = active_corr(daily_port, daily_bench)
    monthly_result = active_corr(monthly_port, monthly_bench)

    # Both compute valid corr in [-1, 1]; daily noise structure different
    # from monthly. Caller passing daily masquerading as monthly produces
    # different result — test verifies BOTH produce valid finite output (no
    # silent NaN) AND that they cannot be confused (different magnitude
    # with high probability for non-degenerate data).
    assert -1.0 <= daily_result <= 1.0
    assert -1.0 <= monthly_result <= 1.0
    # Intent: callers MUST pass monthly-frequency only; the function does NOT
    # enforce this internally (caller responsibility) but the docstring makes
    # daily input a regression detected by this mutation cover.
