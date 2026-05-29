"""Tests for src/features/momentum_12_1.py — Carhart momentum factor.

Covers:
- Anchor offsets: skip_days (1m) vs lookback_days (12m)
- PIT: all anchors strictly before as_of
- Score formula: p_recent / p_far - 1
- Sign convention: higher = stronger past winner
- Filters: insufficient history / NaN / non-positive close
- Batch universe wrapper
- Edge: ties, monotonic ranking
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.momentum_12_1 import (  # noqa: E402
    _close_at_offset,
    compute_momentum_12_1,
    compute_momentum_12_1_universe,
)


def _make_ohlcv(n_days: int, prices: list[float] | None = None) -> pd.DataFrame:
    """Build an OHLCV DataFrame of length n_days ending today.

    If ``prices`` is None defaults to a flat 100. If shorter than n_days,
    front-fills with the first value.
    """
    idx = pd.date_range(end="2024-12-31", periods=n_days, freq="B")
    if prices is None:
        prices = [100.0] * n_days
    elif len(prices) < n_days:
        prices = [prices[0]] * (n_days - len(prices)) + prices
    return pd.DataFrame({"close": prices[:n_days]}, index=idx)


# ---------------------------------------------------------------------------
# _close_at_offset
# ---------------------------------------------------------------------------
def test_close_at_offset_returns_correct_row():
    """offset=0 → last row before as_of."""
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    ohlcv = pd.DataFrame({"close": list(range(10, 20))}, index=idx)
    # as_of = first day after end → all 10 rows eligible
    val = _close_at_offset(ohlcv, pd.Timestamp("2024-01-15"), 0)
    assert val == 19.0  # last close


def test_close_at_offset_skips_n_rows():
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    ohlcv = pd.DataFrame({"close": list(range(10, 20))}, index=idx)
    val = _close_at_offset(ohlcv, pd.Timestamp("2024-01-15"), 5)
    # 10 eligible rows, last is index 9 (val 19), offset 5 → index 4 (val 14)
    assert val == 14.0


def test_close_at_offset_strict_pit():
    """Anchor must be strictly before as_of, not <= ."""
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    ohlcv = pd.DataFrame({"close": [1, 2, 3, 4, 999]}, index=idx)
    # as_of = the last index → strict < drops 999
    val = _close_at_offset(ohlcv, idx[-1], 0)
    assert val == 4.0  # second-to-last


def test_close_at_offset_none_when_insufficient():
    idx = pd.date_range("2024-01-01", periods=3, freq="B")
    ohlcv = pd.DataFrame({"close": [100.0] * 3}, index=idx)
    # asking for 5 rows back when only 3 exist
    val = _close_at_offset(ohlcv, pd.Timestamp("2024-01-10"), 5)
    assert val is None


def test_close_at_offset_none_for_empty():
    assert _close_at_offset(None, pd.Timestamp("2024-01-01"), 0) is None
    assert _close_at_offset(pd.DataFrame(), pd.Timestamp("2024-01-01"), 0) is None


def test_close_at_offset_none_for_non_positive():
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    ohlcv = pd.DataFrame({"close": [100, 100, 0, 100, 100]}, index=idx)
    # offset 2 → index 2 = 0 → None
    val = _close_at_offset(ohlcv, pd.Timestamp("2024-01-10"), 2)
    assert val is None


# ---------------------------------------------------------------------------
# compute_momentum_12_1 — formula
# ---------------------------------------------------------------------------
def test_score_formula_simple():
    """Construct with explicit values at known offsets."""
    n = 260
    prices = list(np.linspace(100.0, 200.0, n))  # ramp 100 → 200
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    ohlcv = pd.DataFrame({"close": prices}, index=idx)
    as_of = idx[-1] + pd.Timedelta(days=1)  # strict < eligible all 260
    result = compute_momentum_12_1(ohlcv, as_of)
    # eligible last = index 259 → offset 21 → index 238
    # eligible last = index 259 → offset 252 → index 7
    p_recent = prices[259 - 21]
    p_far = prices[259 - 252]
    expected = p_recent / p_far - 1
    assert result["score"] == pytest.approx(expected, rel=1e-9)


def test_score_negative_when_loser():
    """Past loser: p_recent < p_far → negative score."""
    n = 260
    # First half higher, second half lower
    prices = [200.0] * 100 + [100.0] * 160
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    ohlcv = pd.DataFrame({"close": prices}, index=idx)
    as_of = idx[-1] + pd.Timedelta(days=1)
    result = compute_momentum_12_1(ohlcv, as_of)
    # p_recent at offset 21 → index 238 = 100, p_far at offset 252 → index 7 = 200
    assert result["score"] == pytest.approx(100.0 / 200.0 - 1.0)
    assert result["score"] < 0


def test_filter_insufficient_history_returns_none():
    ohlcv = pd.DataFrame(
        {"close": [100.0] * 50},
        index=pd.date_range("2024-01-01", periods=50, freq="B"),
    )
    as_of = ohlcv.index[-1] + pd.Timedelta(days=1)
    result = compute_momentum_12_1(ohlcv, as_of)
    assert result["score"] is None
    assert "anchor" in result["reason"]


def test_filter_empty():
    result = compute_momentum_12_1(None, pd.Timestamp("2024-01-01"))
    assert result["score"] is None


# ---------------------------------------------------------------------------
# compute_momentum_12_1_universe
# ---------------------------------------------------------------------------
def test_universe_batch_skips_short_history():
    """Symbols with <253 rows must be excluded."""
    n_full = 260
    full = pd.DataFrame(
        {"close": list(np.linspace(100, 200, n_full))},
        index=pd.date_range("2023-01-01", periods=n_full, freq="B"),
    )
    short = pd.DataFrame(
        {"close": list(np.linspace(100, 200, 100))},
        index=pd.date_range("2023-01-01", periods=100, freq="B"),
    )
    ohlcv = {"A": full, "B": short}
    as_of = full.index[-1] + pd.Timedelta(days=1)
    result = compute_momentum_12_1_universe(ohlcv, as_of=as_of)
    assert "A" in result.index
    assert "B" not in result.index


def test_universe_ranking_monotonic():
    """Larger past return → larger score, ranking matches."""
    n = 260
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    ohlcv = {
        "LOSER":  pd.DataFrame(
            {"close": [200.0] * 100 + [100.0] * (n - 100)}, index=idx,
        ),
        "FLAT":   pd.DataFrame({"close": [100.0] * n}, index=idx),
        "WINNER": pd.DataFrame(
            {"close": [100.0] * 100 + [200.0] * (n - 100)}, index=idx,
        ),
    }
    as_of = idx[-1] + pd.Timedelta(days=1)
    result = compute_momentum_12_1_universe(ohlcv, as_of=as_of)
    assert result["WINNER"] > result["FLAT"] > result["LOSER"]


def test_universe_as_of_required():
    with pytest.raises(ValueError, match="as_of is required"):
        compute_momentum_12_1_universe({}, as_of=None)


def test_universe_empty_returns_empty():
    result = compute_momentum_12_1_universe(
        {}, as_of=pd.Timestamp("2024-01-01"),
    )
    assert result.empty


def test_universe_min_history_floor():
    """min_history can't go below lookback_days + 1; the larger wins."""
    n = 260
    ohlcv = {"A": pd.DataFrame(
        {"close": list(np.linspace(100, 200, n))},
        index=pd.date_range("2023-01-01", periods=n, freq="B"),
    )}
    as_of = ohlcv["A"].index[-1] + pd.Timedelta(days=1)
    # Pass min_history=10 (much less than 253) — should still compute (260 > 253)
    result = compute_momentum_12_1_universe(ohlcv, as_of=as_of, min_history=10)
    assert "A" in result.index
