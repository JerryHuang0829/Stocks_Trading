"""V0.13 P1 #10 / Phase 2 Session 1 cross-frequency alignment tests.

Verifies `src.utils.cross_frequency.align_factor_to_rebalance_date` correctly
handles daily / monthly / quarterly factor publication frequencies with PIT
discipline; mutation tests catch shift=1 violation + look-ahead leak.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.utils.cross_frequency import align_factor_to_rebalance_date  # noqa: E402


def _make_panel(dates: list[str], symbols: list[str]) -> pd.DataFrame:
    """Build a synthetic factor panel: row i × col j = i*10 + j."""
    idx = pd.to_datetime(dates)
    data = {sym: [i * 10.0 + j for i in range(len(dates))] for j, sym in enumerate(symbols)}
    return pd.DataFrame(data, index=idx)


def test_align_daily_factor_to_rebalance():
    """Daily factor: shift=1 PIT — t-1d close strictly before rebalance."""
    panel = _make_panel(
        ["2024-01-29", "2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"],
        ["2330", "2317"],
    )
    rebal = pd.Timestamp("2024-02-02")
    aligned = align_factor_to_rebalance_date(panel, "daily", rebal, pit_lag_days=1)
    # eff_cutoff = 2024-02-01; latest STRICTLY BEFORE → 2024-01-31 (i=2)
    expected_2330 = 2 * 10.0 + 0  # i=2 row 2330=col 0 → 20.0
    expected_2317 = 2 * 10.0 + 1  # i=2 row 2317=col 1 → 21.0
    assert aligned["2330"] == expected_2330
    assert aligned["2317"] == expected_2317


def test_align_daily_pit_lag_zero_minimum_one_enforced():
    """Mutation: caller passes pit_lag_days=0 → MUST enforce shift=1 minimum."""
    panel = _make_panel(["2024-01-30", "2024-01-31", "2024-02-01"], ["2330"])
    rebal = pd.Timestamp("2024-02-01")
    aligned = align_factor_to_rebalance_date(panel, "daily", rebal, pit_lag_days=0)
    # eff_lag = max(1, 0) = 1; eff_cutoff = 2024-01-31; latest strictly before → 2024-01-30
    assert aligned["2330"] == 0 * 10.0 + 0  # i=0 (2024-01-30) → 0.0
    # Must NOT include 2024-02-01 (look-ahead) or 2024-01-31 (same-day)


def test_align_monthly_factor_to_rebalance():
    """Monthly factor: latest month-end strictly before (t - pit_lag_days)."""
    panel = _make_panel(
        ["2023-10-31", "2023-11-30", "2023-12-31"],
        ["2330"],
    )
    rebal = pd.Timestamp("2024-02-15")
    # pit_lag_days=45; cutoff = 2024-01-01; latest month-end strictly before → 2023-12-31
    aligned = align_factor_to_rebalance_date(panel, "monthly", rebal, pit_lag_days=45)
    assert aligned["2330"] == 2 * 10.0 + 0  # i=2 row 2330 → 20.0


def test_align_quarterly_factor_to_rebalance():
    """Quarterly factor: latest quarter-end with (qend + lag <= rebal)."""
    panel = _make_panel(
        ["2023-09-30", "2023-12-31", "2024-03-31"],
        ["2330"],
    )
    rebal = pd.Timestamp("2024-03-15")
    # Q4 2023 EPS lag 90d; cutoff = 2023-12-16; latest qend <= cutoff → 2023-09-30
    # (2023-12-31 + 90d = 2024-03-31 > 2024-03-15, so 2023-12-31 NOT yet published)
    aligned = align_factor_to_rebalance_date(panel, "quarterly", rebal, pit_lag_days=90)
    assert aligned["2330"] == 0 * 10.0 + 0  # i=0 (2023-09-30) → 0.0


def test_align_quarterly_after_lag_publication_threshold():
    """Quarterly: rebalance after Q4 lag passes → Q4 EPS available."""
    panel = _make_panel(
        ["2023-09-30", "2023-12-31"],
        ["2330"],
    )
    # Q4 2023 + 90d = 2024-03-31; rebalance 2024-04-01 → Q4 EPS available
    rebal = pd.Timestamp("2024-04-01")
    aligned = align_factor_to_rebalance_date(panel, "quarterly", rebal, pit_lag_days=90)
    # cutoff = 2024-04-01 - 90d = 2024-01-02; 2023-12-31 <= cutoff → use Q4 2023
    assert aligned["2330"] == 1 * 10.0 + 0  # i=1 (2023-12-31) → 10.0


def test_align_invalid_freq_raises():
    """Mutation: caller passes invalid freq → raise ValueError."""
    panel = _make_panel(["2024-01-31"], ["2330"])
    rebal = pd.Timestamp("2024-02-01")
    with pytest.raises(ValueError, match="Unknown factor_freq"):
        align_factor_to_rebalance_date(panel, "weekly", rebal, pit_lag_days=1)  # type: ignore[arg-type]


def test_align_empty_panel_raises():
    """Mutation: empty factor_panel → raise ValueError (caller bug detection)."""
    panel = pd.DataFrame()
    rebal = pd.Timestamp("2024-02-01")
    with pytest.raises(ValueError, match="factor_panel is empty"):
        align_factor_to_rebalance_date(panel, "daily", rebal, pit_lag_days=1)


def test_align_returns_empty_series_when_no_valid_date():
    """Edge: no factor date before cutoff → return empty Series, NOT raise."""
    panel = _make_panel(["2024-03-15", "2024-04-15"], ["2330"])
    rebal = pd.Timestamp("2024-01-01")  # earlier than all factor dates
    aligned = align_factor_to_rebalance_date(panel, "monthly", rebal, pit_lag_days=45)
    assert aligned.empty


def test_align_daily_does_not_include_rebalance_day_close():
    """PIT critical: even with pit_lag_days=1, MUST NOT include rebalance day's
    own close (look-ahead). Caller intent: t-1 close used to score t open positions."""
    panel = _make_panel(
        ["2024-01-30", "2024-01-31", "2024-02-01"],
        ["2330"],
    )
    rebal = pd.Timestamp("2024-02-01")
    aligned = align_factor_to_rebalance_date(panel, "daily", rebal, pit_lag_days=1)
    # eff_cutoff = 2024-01-31; STRICTLY BEFORE → 2024-01-30 (NOT 2024-01-31, NOT 2024-02-01)
    assert aligned["2330"] == 0 * 10.0 + 0
