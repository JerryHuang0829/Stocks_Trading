"""Tests for src/backtest/lot_sizing.py — v8.1 whole-lot position sizing.

Pure synthetic; every case carries its hand-computed expected value.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.backtest.lot_sizing import (  # noqa: E402
    LOT_SIZE,
    compute_gross_return,
    size_whole_lots,
)


def test_lot_size_constant():
    """台股 1 張 = 1000 股."""
    assert LOT_SIZE == 1000


def test_exact_division_no_residual():
    """nav 1e6, weight 0.5, price 250 → slice 5e5 / (250×1000) = 2 lots exactly."""
    r = size_whole_lots({"A": 0.5}, {"A": 250.0}, 1_000_000.0)
    pos = r.positions[0]
    assert pos.lots == 2
    assert pos.invested_capital == 500_000.0
    assert pos.residual_cash == 0.0
    assert pos.actual_weight == 0.5
    assert pos.feasible is True


def test_partial_fill_leaves_residual_cash():
    """price 333 → slice 5e5 / 333_000 = 1.50.. → floor 1 lot; residual 167_000."""
    r = size_whole_lots({"A": 0.5}, {"A": 333.0}, 1_000_000.0)
    pos = r.positions[0]
    assert pos.lots == 1
    assert pos.invested_capital == 333_000.0
    assert pos.residual_cash == pytest.approx(167_000.0)
    assert pos.actual_weight == pytest.approx(0.333)
    assert pos.feasible is True


def test_unaffordable_stock_zero_lots():
    """top_n=16 slice 62_500; price 1000 → one lot costs 1e6 → 0 lots, infeasible."""
    r = size_whole_lots({"A": 1 / 16}, {"A": 1000.0}, 1_000_000.0)
    pos = r.positions[0]
    assert pos.lots == 0
    assert pos.feasible is False
    assert pos.invested_capital == 0.0
    assert pos.residual_cash == pytest.approx(62_500.0)
    assert pos.actual_weight == 0.0


def test_mixed_high_low_price_aggregate():
    """A (250) fully fills its 0.5 slice; B (price 1e6) cannot afford one lot.

    Covers: infeasible + mixed + invested_ratio + n_feasible + weight_deviation_l1.
    """
    r = size_whole_lots(
        {"A": 0.5, "B": 0.5}, {"A": 250.0, "B": 1_000_000.0}, 1_000_000.0,
    )
    assert r.n_feasible == 1
    assert r.n_target == 2
    assert r.total_invested == 500_000.0
    assert r.invested_ratio == 0.5
    # A: |0.5-0.5|=0 ; B: |0-0.5|=0.5
    assert r.weight_deviation_l1 == pytest.approx(0.5)


def test_invested_ratio_upper_bound_one():
    """When every slice fills exactly, invested_ratio = 1.0 (no cash drag)."""
    r = size_whole_lots(
        {"A": 0.5, "B": 0.5}, {"A": 250.0, "B": 500.0}, 1_000_000.0,
    )
    assert r.invested_ratio == pytest.approx(1.0)
    assert r.total_residual_cash == pytest.approx(0.0)


def test_price_zero_infeasible():
    r = size_whole_lots({"A": 0.5}, {"A": 0.0}, 1_000_000.0)
    pos = r.positions[0]
    assert pos.lots == 0 and pos.feasible is False
    assert pos.residual_cash == pytest.approx(500_000.0)


def test_price_nan_infeasible():
    r = size_whole_lots({"A": 0.5}, {"A": float("nan")}, 1_000_000.0)
    pos = r.positions[0]
    assert pos.lots == 0 and pos.feasible is False


def test_nav_non_positive_raises():
    with pytest.raises(ValueError, match="nav must be > 0"):
        size_whole_lots({"A": 1.0}, {"A": 100.0}, 0.0)
    with pytest.raises(ValueError, match="nav must be > 0"):
        size_whole_lots({"A": 1.0}, {"A": 100.0}, -5.0)


def test_key_mismatch_raises():
    with pytest.raises(ValueError, match="identical keys"):
        size_whole_lots({"A": 0.5, "B": 0.5}, {"A": 100.0}, 1_000_000.0)


def test_dynamic_capital_larger_nav_more_lots():
    """Same price; doubling NAV roughly doubles the lots affordable."""
    small = size_whole_lots({"A": 1.0}, {"A": 300.0}, 1_000_000.0)
    large = size_whole_lots({"A": 1.0}, {"A": 300.0}, 2_000_000.0)
    # nav 1e6: floor(1e6 / 3e5) = 3 ; nav 2e6: floor(2e6 / 3e5) = 6
    assert small.positions[0].lots == 3
    assert large.positions[0].lots == 6


def test_floor_not_round_boundary():
    """Mutation guard: lots must use floor, not round.

    slice / (price×lot_size) = 2.9999999 → floor → 2. If the code used round()
    it would give 3 and this test would fail.
    """
    r = size_whole_lots({"A": 1.0}, {"A": 1.0}, 2.9999999, lot_size=1)
    assert r.positions[0].lots == 2
    assert math.floor(2.9999999) == 2  # documents the expected primitive


def test_lot_size_one_recovers_exact_weight():
    """lot_size=1 with an evenly-dividing price reproduces the target weight."""
    r = size_whole_lots({"A": 0.5}, {"A": 1.0}, 1000.0, lot_size=1)
    # slice 500 / (1×1) = 500 shares; invested 500; actual_weight 0.5 exact
    assert r.positions[0].lots == 500
    assert r.positions[0].actual_weight == 0.5


def test_compute_gross_return_cash_drag():
    """actual weights sum 0.8, every stock +10% → gross 0.08 (uninvested 0.2 = 0%)."""
    g = compute_gross_return({"A": 0.4, "B": 0.4}, {"A": 0.10, "B": 0.10})
    assert g == pytest.approx(0.08)


def test_compute_gross_return_missing_return_contributes_zero():
    """A symbol with no return available contributes 0 (held flat)."""
    g = compute_gross_return({"A": 0.5, "B": 0.5}, {"A": 0.20})  # B missing
    assert g == pytest.approx(0.10)  # 0.5×0.20 + 0.5×0


def test_compute_gross_return_empty():
    assert compute_gross_return({}, {}) == 0.0
