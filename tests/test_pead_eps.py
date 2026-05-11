"""Unit tests for src.features.pead_eps."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.pead_eps import (
    compute_pead_eps,
    compute_pead_eps_universe,
)


def _make_eps_frame(
    start_quarter: str = "2019-03-31",
    n_quarters: int = 16,
    values: list[float] | None = None,
) -> pd.DataFrame:
    """Build quarterly EPS frame (date = quarter-end, type='EPS', value=EPS)."""
    dates = pd.date_range(start=start_quarter, periods=n_quarters, freq="QE")
    if values is None:
        values = [2.0 + 0.1 * i for i in range(n_quarters)]  # gently rising
    return pd.DataFrame({
        "date": dates,
        "stock_id": ["9999"] * n_quarters,
        "type": ["EPS"] * n_quarters,
        "value": values,
    })


def test_pit_filters_recent_quarters():
    """Cutoff = as_of - 60 days should drop quarters within that window."""
    df = _make_eps_frame(n_quarters=16)
    # as_of shortly after the last quarter-end → that quarter's row must drop
    last_date = df["date"].iloc[-1]
    out = compute_pead_eps(df, as_of=last_date + pd.Timedelta(days=30), lag_days=60)
    # Only 15 quarters fall before cutoff; baseline needs 12 → still computable
    assert out["n_quarters"] == 15


def test_insufficient_quarters_returns_none():
    df = _make_eps_frame(n_quarters=8)  # < min_quarters=12
    out = compute_pead_eps(df, as_of=df["date"].iloc[-1] + pd.Timedelta(days=100))
    assert out["score"] is None


def test_surprise_positive_for_earnings_beat():
    """Latest EPS way above baseline mean should give strongly positive z."""
    rng = np.random.default_rng(0)
    values = list(rng.normal(2.0, 0.05, 12))
    values.append(5.0)
    df = _make_eps_frame(n_quarters=13, values=values)
    out = compute_pead_eps(
        df, as_of=df["date"].iloc[-1] + pd.Timedelta(days=100), baseline_quarters=8,
    )
    assert out["surprise_z"] is not None
    assert out["surprise_z"] > 3.0  # decisive beat


def test_surprise_negative_for_earnings_miss():
    rng = np.random.default_rng(1)
    values = list(rng.normal(2.0, 0.05, 12))
    values.append(-1.0)
    df = _make_eps_frame(n_quarters=13, values=values)
    out = compute_pead_eps(
        df, as_of=df["date"].iloc[-1] + pd.Timedelta(days=100), baseline_quarters=8,
    )
    assert out["surprise_z"] is not None
    assert out["surprise_z"] < -3.0


def test_zero_std_baseline_returns_none():
    """If prior 8 quarters are all identical, std=0 → cannot z-score."""
    values = [2.0] * 12 + [3.0]
    df = _make_eps_frame(n_quarters=13, values=values)
    out = compute_pead_eps(df, as_of=df["date"].iloc[-1] + pd.Timedelta(days=100))
    assert out["score"] is None


def test_non_eps_rows_filtered():
    """Mixed type rows — only EPS should be kept."""
    base = _make_eps_frame(n_quarters=13)
    # Inject some Revenue rows that should be ignored
    rev_rows = pd.DataFrame({
        "date": base["date"],
        "stock_id": ["9999"] * 13,
        "type": ["Revenue"] * 13,
        "value": [1e9] * 13,
    })
    mixed = pd.concat([base, rev_rows]).sort_values("date").reset_index(drop=True)
    out = compute_pead_eps(
        mixed, as_of=mixed["date"].iloc[-1] + pd.Timedelta(days=100),
    )
    # Should filter to 13 EPS rows (not 26)
    assert out["n_quarters"] in {12, 13}  # depends on PIT cutoff


def test_universe_batch_drops_insufficient_and_ranks_correct():
    np.random.seed(42)
    # Three symbols: BEAT (big beat), MISS (big miss), SHORT (insufficient)
    vals_beat = list(np.random.normal(2.0, 0.05, 12)) + [5.0]
    vals_miss = list(np.random.normal(2.0, 0.05, 12)) + [-1.0]

    df_beat = pd.DataFrame({
        "date": pd.date_range("2020-03-31", periods=13, freq="QE"),
        "stock_id": ["A"] * 13, "type": ["EPS"] * 13, "value": vals_beat,
    })
    df_miss = pd.DataFrame({
        "date": pd.date_range("2020-03-31", periods=13, freq="QE"),
        "stock_id": ["B"] * 13, "type": ["EPS"] * 13, "value": vals_miss,
    })
    df_short = pd.DataFrame({
        "date": pd.date_range("2023-03-31", periods=5, freq="QE"),
        "stock_id": ["C"] * 5, "type": ["EPS"] * 5, "value": [2.0] * 5,
    })
    out = compute_pead_eps_universe(
        {"BEAT": df_beat, "MISS": df_miss, "SHORT": df_short},
        as_of=pd.Timestamp("2024-03-31") + pd.Timedelta(days=100),
    )
    assert "BEAT" in out and "MISS" in out
    assert "SHORT" not in out
    assert out["BEAT"] > out["MISS"]


def test_aux_panel_ignored_without_error():
    """CLI passes aux_panel for parity; PEAD doesn't need it but must not crash."""
    df = _make_eps_frame(n_quarters=13)
    out = compute_pead_eps_universe(
        {"X": df}, aux_panel={"X": "unused"},
        as_of=df["date"].iloc[-1] + pd.Timedelta(days=100),
    )
    assert "X" in out


def test_as_of_required_raises():
    with pytest.raises(ValueError):
        compute_pead_eps_universe({}, as_of=None)


def test_q4_annual_report_requires_90d_lag():
    """P1-1: Q4 EPS must not be usable until as_of >= quarter_end + 90 days.

    Construct a frame whose latest row is Q4 (2024-12-31) with 12 prior baseline
    quarters, and query at as_of 75 days after Q4 end. Under the old blanket
    60-day lag this would pass; under the new quarter-aware lag the Q4 row is
    still future — so we should fall back to the Q3 latest and the surprise
    score either shifts or becomes None when the baseline falls short.
    """
    # 13 quarters ending in Q4 2024 (2021-Q4 through 2024-Q4)
    dates = pd.date_range("2021-12-31", periods=13, freq="QE")
    # Give Q4 a dramatic outlier so "is it used?" is unambiguous
    values = [2.0] * 12 + [100.0]
    df = pd.DataFrame({
        "date": dates, "stock_id": ["Q"] * 13, "type": ["EPS"] * 13, "value": values,
    })
    # 75 days after 2024-12-31 → 2025-03-16, still before 90-day deadline
    as_of = pd.Timestamp("2025-03-16")
    out = compute_pead_eps(df, as_of=as_of)
    # With quarter-aware lag the Q4 outlier (value=100) must be filtered out.
    # n_quarters reflects only pre-Q4 rows → 12 quarters retained.
    assert out["n_quarters"] == 12
    # Latest visible row is Q3 2024 (value 2.0, identical to baseline) — not
    # the 100.0 outlier.
    assert out["latest_eps"] == pytest.approx(2.0, abs=1e-9)


def test_q1_quarterly_report_uses_45d_lag():
    """P1-1 positive case: Q1 EPS should be visible 50 days after quarter end."""
    # 13 quarters ending at Q1 2024 (2021-Q1 through 2024-Q1)
    dates = pd.date_range("2021-03-31", periods=13, freq="QE")
    values = [2.0] * 12 + [5.0]
    df = pd.DataFrame({
        "date": dates, "stock_id": ["Q"] * 13, "type": ["EPS"] * 13, "value": values,
    })
    # Q1 2024 + 50 days = 2024-05-20 (past 45-day deadline)
    as_of = pd.Timestamp("2024-05-20")
    out = compute_pead_eps(df, as_of=as_of)
    assert out["n_quarters"] == 13
    assert out["latest_eps"] == pytest.approx(5.0, abs=1e-9)


def test_q1_before_45d_lag_excluded():
    """P1-1: Q1 EPS must be dropped before 45 days elapse."""
    dates = pd.date_range("2021-03-31", periods=13, freq="QE")
    values = [2.0] * 12 + [5.0]
    df = pd.DataFrame({
        "date": dates, "stock_id": ["Q"] * 13, "type": ["EPS"] * 13, "value": values,
    })
    # 30 days after Q1 2024 end → not yet legally published
    as_of = pd.Timestamp("2024-04-30")
    out = compute_pead_eps(df, as_of=as_of)
    assert out["n_quarters"] == 12
    assert out["latest_eps"] == pytest.approx(2.0, abs=1e-9)


# R6-3 companion tests -----------------------------------------------


def test_pead_surprise_z_handles_near_constant_baseline():
    """R6-3: baseline with float-noise but ideologically zero variance must
    yield None, not a pathological z-score ~ 4e12.

    Without the fix (sd == 0 exact compare), `[2.0, 2.0, 2.0, ..., 2.0001]`
    baseline produces sd ~ 1e-14 rather than 0, bypassing the guard.
    """
    # 13 quarters: 12 near-constant baseline + 1 measurable latest
    values = [2.0 + i * 1e-15 for i in range(12)] + [2.5]  # baseline sd ~ 1e-15
    dates = pd.date_range("2020-03-31", periods=13, freq="QE")
    df = pd.DataFrame({
        "date": dates,
        "stock_id": ["Q"] * 13,
        "type": ["EPS"] * 13,
        "value": values,
    })
    # Run well past all quarter-ends so baseline is full 12
    out = compute_pead_eps(df, as_of=pd.Timestamp("2024-01-01"), lag_days=60)
    # Guard must fire → surprise_z is None, not a float
    assert out["surprise_z"] is None, (
        f"near-constant baseline produced surprise_z={out['surprise_z']} "
        f"instead of None; sd < 1e-12 guard not catching float noise"
    )
