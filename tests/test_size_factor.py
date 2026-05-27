"""Tests for src/features/size_factor.py — Size factor (-log market cap).

Covers:
- Happy path: smaller cap → higher score (SMB sign convention)
- PIT shift=1 price
- Filters: missing shares / missing price / non-positive shares or mcap
- Batch universe wrapper
- Empty inputs → empty Series
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.size_factor import (  # noqa: E402
    _price_asof,
    compute_size_universe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_ohlcv(start: str, end: str, close_val: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, end, freq="B")
    n = len(idx)
    return pd.DataFrame({"close": [close_val] * n}, index=idx)


# ---------------------------------------------------------------------------
# _price_asof
# ---------------------------------------------------------------------------
def test_price_asof_shift_1_excludes_as_of():
    ohlcv = pd.DataFrame(
        {"close": [100.0, 110.0, 120.0]},
        index=pd.date_range("2024-01-01", periods=3, freq="B"),
    )
    p = _price_asof(ohlcv, pd.Timestamp("2024-01-03"))
    assert p == 110.0  # 2024-01-02 close


def test_price_asof_none_for_empty():
    assert _price_asof(None, pd.Timestamp("2024-01-01")) is None
    assert _price_asof(pd.DataFrame(), pd.Timestamp("2024-01-01")) is None


def test_price_asof_none_for_zero_price():
    ohlcv = pd.DataFrame({"close": [0.0]}, index=[pd.Timestamp("2024-01-01")])
    assert _price_asof(ohlcv, pd.Timestamp("2024-01-02")) is None


# ---------------------------------------------------------------------------
# compute_size_universe — happy path + sign convention
# ---------------------------------------------------------------------------
def test_smaller_cap_gets_higher_score():
    """SMB convention: smaller market cap → higher score."""
    ohlcv = {
        "SMALL": _make_ohlcv("2024-01-01", "2024-01-31", close_val=10.0),
        "BIG": _make_ohlcv("2024-01-01", "2024-01-31", close_val=1000.0),
    }
    issued = {"SMALL": 1_000_000, "BIG": 1_000_000_000}  # SMALL: 10M, BIG: 1T
    result = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"),
    )
    assert result["SMALL"] > result["BIG"]
    # Both scores are -log(mcap), so finite negatives:
    assert np.isfinite(result["SMALL"])
    assert np.isfinite(result["BIG"])


def test_score_is_negative_log_mcap():
    """Sanity: score == -log(price × shares)."""
    ohlcv = {"A": _make_ohlcv("2024-01-01", "2024-01-31", close_val=100.0)}
    issued = {"A": 10_000_000}
    result = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"),
    )
    expected = -np.log(100.0 * 10_000_000)
    assert result["A"] == pytest.approx(expected)


def test_pit_shift_one_day_price():
    """Score must use close on (as_of - 1d), not as_of itself."""
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    # last 2 days different prices to detect mis-shift
    ohlcv = {"A": pd.DataFrame(
        {"close": [50.0, 60.0, 70.0, 80.0, 999.0]},  # last is "future" price
        index=idx,
    )}
    issued = {"A": 1_000_000}
    # as_of = the LAST day; shift=1 → use second-to-last (80, not 999)
    as_of = idx[-1]
    result = compute_size_universe(ohlcv, aux_panel=issued, as_of=as_of)
    expected = -np.log(80.0 * 1_000_000)
    assert result["A"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def test_drop_missing_shares():
    ohlcv = {
        "A": _make_ohlcv("2024-01-01", "2024-01-31"),
        "B": _make_ohlcv("2024-01-01", "2024-01-31"),
    }
    issued = {"A": 1_000_000}  # B missing
    result = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"),
    )
    assert "A" in result.index
    assert "B" not in result.index


def test_drop_missing_ohlcv():
    ohlcv = {"A": None, "B": _make_ohlcv("2024-01-01", "2024-01-31")}
    issued = {"A": 1_000_000, "B": 1_000_000}
    result = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"),
    )
    assert "A" not in result.index
    assert "B" in result.index


def test_drop_zero_or_negative_shares():
    ohlcv = {
        "ZERO": _make_ohlcv("2024-01-01", "2024-01-31"),
        "NEG": _make_ohlcv("2024-01-01", "2024-01-31"),
        "OK": _make_ohlcv("2024-01-01", "2024-01-31"),
    }
    issued = {"ZERO": 0.0, "NEG": -100.0, "OK": 1_000_000}
    result = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"),
    )
    assert "ZERO" not in result.index
    assert "NEG" not in result.index
    assert "OK" in result.index


def test_drop_nan_shares():
    ohlcv = {"A": _make_ohlcv("2024-01-01", "2024-01-31")}
    issued = {"A": float("nan")}
    result = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"),
    )
    assert result.empty


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------
def test_empty_aux_panel_returns_empty():
    ohlcv = {"A": _make_ohlcv("2024-01-01", "2024-01-31")}
    result = compute_size_universe(
        ohlcv, aux_panel={}, as_of=pd.Timestamp("2024-01-15"),
    )
    assert result.empty


def test_none_aux_panel_returns_empty():
    ohlcv = {"A": _make_ohlcv("2024-01-01", "2024-01-31")}
    result = compute_size_universe(
        ohlcv, aux_panel=None, as_of=pd.Timestamp("2024-01-15"),
    )
    assert result.empty


def test_empty_ohlcv_dict_returns_empty():
    result = compute_size_universe(
        {}, aux_panel={"A": 1_000_000}, as_of=pd.Timestamp("2024-01-15"),
    )
    assert result.empty


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def test_as_of_required():
    with pytest.raises(ValueError, match="as_of is required"):
        compute_size_universe({}, aux_panel={"A": 1.0})


def test_min_history_ignored():
    """min_history is kept for signature parity but should not affect result."""
    ohlcv = {"A": _make_ohlcv("2024-01-01", "2024-01-31")}
    issued = {"A": 1_000_000}
    r1 = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"), min_history=0,
    )
    r2 = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"), min_history=999,
    )
    assert r1["A"] == r2["A"]


def test_ranking_consistent_across_caps():
    """Three caps differing by 10x each → strict score ordering."""
    ohlcv = {
        "TINY":  _make_ohlcv("2024-01-01", "2024-01-31", close_val=10.0),
        "MID":   _make_ohlcv("2024-01-01", "2024-01-31", close_val=10.0),
        "MEGA":  _make_ohlcv("2024-01-01", "2024-01-31", close_val=10.0),
    }
    issued = {"TINY": 1e6, "MID": 1e8, "MEGA": 1e10}
    result = compute_size_universe(
        ohlcv, aux_panel=issued, as_of=pd.Timestamp("2024-01-15"),
    )
    assert result["TINY"] > result["MID"] > result["MEGA"]
