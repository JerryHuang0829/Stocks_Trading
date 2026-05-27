"""Tests for src/features/value_ep.py — Value (E/P) factor.

Covers:
- Happy path: TTM EPS / price → E/P
- PIT: Q1-3 (+45 d) / Q4 (+90 d) quarter-aware lag
- Shift=1 price PIT
- Filters: < 4 quarters / negative TTM / missing or zero price
- Batch universe wrapper
- TTM logic with NaN quarter
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.value_ep import (  # noqa: E402
    _price_asof,
    _ttm_eps,
    compute_value_ep,
    compute_value_ep_universe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_eps_frame(quarter_ends: list[str], values: list[float]) -> pd.DataFrame:
    """Build a FinMind-style EPS long DataFrame."""
    return pd.DataFrame({
        "date": pd.to_datetime(quarter_ends),
        "type": ["EPS"] * len(quarter_ends),
        "value": values,
    })


def _make_ohlcv(start: str, end: str, close_val: float = 100.0) -> pd.DataFrame:
    """Build an OHLCV DataFrame with a flat close series."""
    idx = pd.date_range(start, end, freq="B")
    n = len(idx)
    return pd.DataFrame(
        {"close": [close_val] * n},
        index=idx,
    )


# ---------------------------------------------------------------------------
# _price_asof
# ---------------------------------------------------------------------------
def test_price_asof_shift_1_excludes_as_of():
    """Price should be from on/before (as_of - 1d)."""
    ohlcv = pd.DataFrame(
        {"close": [100.0, 110.0, 120.0]},
        index=pd.date_range("2024-01-01", periods=3, freq="B"),
    )
    # as_of = day 3 (index 2) → shift=1 means look at day 2 or before
    p = _price_asof(ohlcv, pd.Timestamp("2024-01-03"))
    # day-3 is Wed 2024-01-03; (as_of - 1d) = 2024-01-02 → close 110
    assert p == 110.0


def test_price_asof_none_for_empty_or_bad():
    assert _price_asof(None, pd.Timestamp("2024-01-01")) is None
    assert _price_asof(pd.DataFrame(), pd.Timestamp("2024-01-01")) is None
    # No data before as_of - 1d
    ohlcv = pd.DataFrame({"close": [100.0]}, index=[pd.Timestamp("2024-12-31")])
    assert _price_asof(ohlcv, pd.Timestamp("2024-01-01")) is None
    # zero price
    ohlcv = pd.DataFrame({"close": [0.0]}, index=[pd.Timestamp("2024-01-01")])
    assert _price_asof(ohlcv, pd.Timestamp("2024-01-02")) is None


# ---------------------------------------------------------------------------
# _ttm_eps
# ---------------------------------------------------------------------------
def test_ttm_eps_sums_last_four_quarters():
    frame = pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0, 5.0]})  # 5 quarters
    # latest 4 = [2,3,4,5] → sum 14
    assert _ttm_eps(frame, min_quarters=4) == 14.0


def test_ttm_eps_returns_none_when_too_few_quarters():
    frame = pd.DataFrame({"value": [1.0, 2.0, 3.0]})
    assert _ttm_eps(frame, min_quarters=4) is None


def test_ttm_eps_returns_none_when_nan_quarter():
    frame = pd.DataFrame({"value": [1.0, 2.0, np.nan, 4.0]})
    assert _ttm_eps(frame, min_quarters=4) is None


# ---------------------------------------------------------------------------
# compute_value_ep happy path + filters
# ---------------------------------------------------------------------------
def test_value_ep_happy_path():
    """4 quarters of EPS=2 each, TTM=8, price=100 → E/P=0.08."""
    # Quarter-ends well before as_of so all 4 are publicly available
    eps = _make_eps_frame(
        ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
        [2.0, 2.0, 2.0, 2.0],
    )
    ohlcv = _make_ohlcv("2023-06-01", "2023-12-31", close_val=100.0)
    # as_of = 2023-12-31; Q4-2022 needs +90d → 2023-03-31, well before, OK
    result = compute_value_ep(eps, ohlcv, pd.Timestamp("2023-12-31"))
    assert result["score"] == pytest.approx(0.08)
    assert result["ttm_eps"] == pytest.approx(8.0)
    assert result["price"] == 100.0
    assert result["reason"] == "ok"


def test_value_ep_drops_negative_ttm():
    """Loss-makers (negative TTM EPS) are dropped (F-F HML convention)."""
    eps = _make_eps_frame(
        ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
        [-1.0, -1.0, -1.0, -1.0],   # TTM = -4
    )
    ohlcv = _make_ohlcv("2023-06-01", "2023-12-31")
    result = compute_value_ep(eps, ohlcv, pd.Timestamp("2023-12-31"))
    assert result["score"] is None
    assert result["reason"] == "negative_or_zero_ttm"


def test_value_ep_drops_zero_ttm():
    """TTM exactly 0 → dropped (division would be 0, not meaningful)."""
    eps = _make_eps_frame(
        ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
        [1.0, -1.0, 1.0, -1.0],     # TTM = 0
    )
    ohlcv = _make_ohlcv("2023-06-01", "2023-12-31")
    result = compute_value_ep(eps, ohlcv, pd.Timestamp("2023-12-31"))
    assert result["score"] is None
    assert result["reason"] == "negative_or_zero_ttm"


def test_value_ep_drops_insufficient_history():
    """< 4 quarters → no TTM available → dropped."""
    eps = _make_eps_frame(
        ["2022-09-30", "2022-12-31"],   # only 2 quarters
        [2.0, 2.0],
    )
    ohlcv = _make_ohlcv("2023-06-01", "2023-12-31")
    result = compute_value_ep(eps, ohlcv, pd.Timestamp("2023-12-31"))
    assert result["score"] is None
    assert result["reason"] == "insufficient_eps_history"


def test_value_ep_drops_missing_price():
    """Valid TTM but no OHLCV → dropped."""
    eps = _make_eps_frame(
        ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
        [2.0, 2.0, 2.0, 2.0],
    )
    result = compute_value_ep(eps, None, pd.Timestamp("2023-12-31"))
    assert result["score"] is None
    assert result["reason"] == "price_unavailable"


def test_value_ep_quarter_aware_pit_drops_unfiled_q4():
    """Q4-2023 ends 2023-12-31; legal deadline +90 d = 2024-03-31.
    On as_of 2024-02-15 (still within Q4 window) Q4-2023 is NOT yet public →
    TTM falls back to Q4-2022+Q1-Q3-2023; demonstrates quarter-aware filter.
    """
    eps = _make_eps_frame(
        ["2022-12-31", "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
        [1.0, 2.0, 3.0, 4.0, 99.0],   # Q4-2023 has anomalous 99 — should be excluded
    )
    ohlcv = _make_ohlcv("2024-01-01", "2024-03-01", close_val=100.0)
    result = compute_value_ep(eps, ohlcv, pd.Timestamp("2024-02-15"))
    # Q4-2023 (anomalous 99) excluded → TTM = 1+2+3+4 = 10
    assert result["score"] == pytest.approx(0.10)
    assert result["ttm_eps"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Universe batch
# ---------------------------------------------------------------------------
def test_compute_value_ep_universe_basic():
    eps_by_sym = {
        "2330": _make_eps_frame(
            ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
            [10.0, 10.0, 10.0, 10.0],   # TTM 40
        ),
        "2454": _make_eps_frame(
            ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
            [5.0, 5.0, 5.0, 5.0],       # TTM 20
        ),
        "9999": _make_eps_frame(
            ["2022-03-31"], [1.0],       # only 1 quarter → drop
        ),
    }
    ohlcv_by_sym = {
        "2330": _make_ohlcv("2023-06-01", "2023-12-31", close_val=1000.0),
        "2454": _make_ohlcv("2023-06-01", "2023-12-31", close_val=200.0),
        "9999": _make_ohlcv("2023-06-01", "2023-12-31", close_val=50.0),
    }
    panel = compute_value_ep_universe(
        eps_by_sym, ohlcv_by_sym, as_of=pd.Timestamp("2023-12-31"),
    )
    assert "2330" in panel.index
    assert "2454" in panel.index
    assert "9999" not in panel.index   # insufficient history
    # E/P 2330 = 40/1000 = 0.04 ; 2454 = 20/200 = 0.10 → 2454 cheaper
    assert panel["2330"] == pytest.approx(0.04)
    assert panel["2454"] == pytest.approx(0.10)
    assert panel["2454"] > panel["2330"]


def test_compute_value_ep_universe_requires_as_of():
    with pytest.raises(ValueError, match="as_of is required"):
        compute_value_ep_universe({}, {}, as_of=None)


def test_compute_value_ep_universe_requires_price_source():
    eps = {"2330": _make_eps_frame(
        ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
        [1.0, 1.0, 1.0, 1.0],
    )}
    with pytest.raises(ValueError, match="ohlcv_by_symbol .* close_by_symbol"):
        compute_value_ep_universe(eps, None, as_of=pd.Timestamp("2023-12-31"))


def test_compute_value_ep_universe_close_by_symbol_path():
    """Alternative price input (close Series instead of OHLCV DataFrame)."""
    eps = {"2330": _make_eps_frame(
        ["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
        [2.0, 2.0, 2.0, 2.0],
    )}
    close_s = pd.Series(
        [100.0] * 100,
        index=pd.date_range("2023-06-01", periods=100, freq="B"),
    )
    panel = compute_value_ep_universe(
        eps, ohlcv_by_symbol=None, close_by_symbol={"2330": close_s},
        as_of=pd.Timestamp("2023-12-31"),
    )
    assert panel["2330"] == pytest.approx(0.08)   # TTM 8 / 100
