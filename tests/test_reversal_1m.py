"""Tests for src/features/reversal_1m.py — Short-term Reversal factor."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.reversal_1m import (  # noqa: E402
    DEFAULT_LOOKBACK_DAYS,
    compute_reversal_1m_universe,
)


def _make_ohlcv(closes: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Build OHLCV with a given close trajectory (business days)."""
    idx = pd.date_range(start, periods=len(closes), freq="B")
    return pd.DataFrame({"close": closes}, index=idx)


# ---------------------------------------------------------------------------
# Sign convention + happy path
# ---------------------------------------------------------------------------
def test_reversal_negates_past_return():
    """Past return +10% → score = -0.10 (loser-bouncer interpretation flipped)."""
    closes = [100.0] * 22
    closes[-1] = 110.0   # last bar (which is BEFORE as_of) is +10% vs first
    ohlcv = _make_ohlcv(closes)
    # as_of = day after last close
    as_of = pd.Timestamp(ohlcv.index[-1]) + pd.Timedelta(days=1)
    panel = compute_reversal_1m_universe({"X": ohlcv}, as_of=as_of)
    # past_ret = 110/100 - 1 = +0.10 → score = -0.10
    assert panel["X"] == pytest.approx(-0.10)


def test_reversal_negative_past_return_high_score():
    """Past return -10% → score = +0.10 (loser earns positive expected alpha)."""
    closes = [100.0] * 22
    closes[-1] = 90.0
    ohlcv = _make_ohlcv(closes)
    as_of = pd.Timestamp(ohlcv.index[-1]) + pd.Timedelta(days=1)
    panel = compute_reversal_1m_universe({"X": ohlcv}, as_of=as_of)
    # past_ret = 90/100 - 1 = -0.10 → score = +0.10
    assert panel["X"] == pytest.approx(0.10)


def test_reversal_flat_zero_score():
    closes = [100.0] * 25
    ohlcv = _make_ohlcv(closes)
    as_of = pd.Timestamp(ohlcv.index[-1]) + pd.Timedelta(days=1)
    panel = compute_reversal_1m_universe({"X": ohlcv}, as_of=as_of)
    assert panel["X"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# PIT (shift=1)
# ---------------------------------------------------------------------------
def test_reversal_shift_1_excludes_as_of_close():
    """The close on as_of itself MUST NOT enter the calculation."""
    # 22 bars + 1 extreme bar exactly at as_of date — extreme bar must be excluded
    closes_pre = [100.0] * 22
    closes_pre[-1] = 105.0       # anchor (shifted to position -1)
    # Add an extra bar on as_of with a wild move
    closes = closes_pre + [9999.0]
    ohlcv = _make_ohlcv(closes)
    as_of = pd.Timestamp(ohlcv.index[-1])    # equal to the wild bar's date
    panel = compute_reversal_1m_universe({"X": ohlcv}, as_of=as_of)
    # Should use anchor=105 and lookback=100; past_ret=0.05; score=-0.05
    assert panel["X"] == pytest.approx(-0.05)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def test_reversal_drops_insufficient_history():
    """Need ≥ lookback_days+1 valid bars."""
    closes = [100.0] * 10   # < 22
    ohlcv = _make_ohlcv(closes)
    as_of = pd.Timestamp(ohlcv.index[-1]) + pd.Timedelta(days=1)
    panel = compute_reversal_1m_universe({"X": ohlcv}, as_of=as_of)
    assert "X" not in panel.index


def test_reversal_drops_zero_close_after_filter():
    """Stocks with zero closes are filtered;
    if remaining length < min_history → drop."""
    # 22 closes, but 12 are zero (halted) → 10 valid < 22 → drop
    closes = [0.0] * 12 + [100.0] * 10
    ohlcv = _make_ohlcv(closes)
    as_of = pd.Timestamp(ohlcv.index[-1]) + pd.Timedelta(days=1)
    panel = compute_reversal_1m_universe({"X": ohlcv}, as_of=as_of)
    assert "X" not in panel.index


def test_reversal_drops_missing_ohlcv():
    panel = compute_reversal_1m_universe(
        {"X": None, "Y": pd.DataFrame()}, as_of=pd.Timestamp("2024-12-31"),
    )
    assert panel.empty


# ---------------------------------------------------------------------------
# Universe batch
# ---------------------------------------------------------------------------
def test_reversal_universe_ranks_losers_highest():
    """Cross-section: stock B fell most → should have the highest reversal score."""
    base = [100.0] * 22
    A = base.copy(); A[-1] = 110.0    # +10% → score -0.10
    B = base.copy(); B[-1] = 85.0     # -15% → score +0.15
    C = base.copy(); C[-1] = 95.0     # -5% → score +0.05
    ohlcv_panel = {"A": _make_ohlcv(A), "B": _make_ohlcv(B), "C": _make_ohlcv(C)}
    as_of = pd.Timestamp(ohlcv_panel["A"].index[-1]) + pd.Timedelta(days=1)
    panel = compute_reversal_1m_universe(ohlcv_panel, as_of=as_of)
    assert panel["B"] > panel["C"] > panel["A"]
    assert panel["A"] == pytest.approx(-0.10)
    assert panel["B"] == pytest.approx(0.15)
    assert panel["C"] == pytest.approx(0.05)


def test_reversal_requires_as_of():
    with pytest.raises(ValueError, match="as_of is required"):
        compute_reversal_1m_universe({"X": _make_ohlcv([100.0] * 25)})


def test_reversal_rejects_invalid_lookback():
    with pytest.raises(ValueError, match="lookback_days"):
        compute_reversal_1m_universe(
            {"X": _make_ohlcv([100.0] * 25)},
            as_of=pd.Timestamp("2024-12-31"),
            lookback_days=0,
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_default_lookback_is_21():
    """1 month ≈ 21 trading days (TW)."""
    assert DEFAULT_LOOKBACK_DAYS == 21
