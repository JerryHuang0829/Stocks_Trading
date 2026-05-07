"""Phase B0-Lite tests for low_vol_v2 factor.

6 tests covering:
    1. 252d std numerical correctness (known synthetic series)
    2. shift=1 PIT — anchor day close excluded from window
    3. min_history=200 drop — recently-listed stocks rejected
    4. dropna behavior — symbols with no data excluded (not NaN-filled)
    5. real-data sanity — 2330 / 0050 / 2603 vol ranking 2330 < 0050 < 2603
       (note: 0050 is index ETF, expected lowest vol; 2330 large-cap tech;
        2603 海運 high vol)
    6. reverse direction — score = -std (high score = low vol)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.low_vol_v2 import compute_low_vol_v2_universe, score_low_vol_v2


def _make_constant_drift_close(n: int = 300, seed: int = 0) -> pd.DataFrame:
    """Synthetic close: random walk with fixed seed, daily log-return std ≈ 0.02."""
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(loc=0.0005, scale=0.02, size=n)
    close = 100.0 * np.exp(np.cumsum(log_rets))
    idx = pd.date_range(end="2024-12-31", periods=n, freq="B")
    return pd.DataFrame({"close": close, "volume": [1_000_000] * n}, index=idx)


def test_252d_std_numerical():
    """Synthetic series with known std ~ 0.02 → score ≈ -0.02 (within sampling noise)."""
    ohlcv = _make_constant_drift_close(n=300, seed=42)
    series = compute_low_vol_v2_universe(
        {"TEST": ohlcv}, as_of=pd.Timestamp("2024-12-31"), window=252, min_history=200
    )
    assert "TEST" in series.index
    score = float(series["TEST"])
    # Score is -std; within ±15% of expected -0.02
    assert -0.025 < score < -0.015, f"score {score} not within ±15% of -0.02"


def test_shift_1_pit_excludes_anchor_close():
    """anchor day close must NOT be in the window — shift=1 semantics."""
    # Build a synthetic series where the LAST close is an extreme spike.
    # If shift=1 works, the std should match the pre-spike series.
    ohlcv = _make_constant_drift_close(n=300, seed=42)
    # Spike the last close 50% above prior
    ohlcv_spiked = ohlcv.copy()
    ohlcv_spiked.iloc[-1, ohlcv_spiked.columns.get_loc("close")] = (
        float(ohlcv_spiked["close"].iloc[-2]) * 1.5
    )

    as_of = pd.Timestamp("2024-12-31")
    s_clean = compute_low_vol_v2_universe(
        {"TEST": ohlcv}, as_of=as_of, window=252, min_history=200
    )
    s_spiked = compute_low_vol_v2_universe(
        {"TEST": ohlcv_spiked}, as_of=as_of, window=252, min_history=200
    )
    # If shift=1 truly excludes the as_of day's close, both runs should give
    # IDENTICAL std (the spike is in the excluded anchor row).
    assert abs(float(s_clean["TEST"]) - float(s_spiked["TEST"])) < 1e-12, (
        "shift=1 violated — anchor day close affected std"
    )


def test_min_history_200_drops_short_series():
    """Series with < 200 prior history is dropped (not NaN-imputed)."""
    short = _make_constant_drift_close(n=150, seed=0)  # 150 prices < 200 prior
    long = _make_constant_drift_close(n=300, seed=0)
    as_of = pd.Timestamp("2024-12-31")
    series = compute_low_vol_v2_universe(
        {"SHORT": short, "LONG": long},
        as_of=as_of, window=252, min_history=200,
    )
    assert "SHORT" not in series.index
    assert "LONG" in series.index


def test_dropna_no_nan_imputation():
    """Symbols with None/empty ohlcv excluded from result (not NaN-filled)."""
    good = _make_constant_drift_close(n=300, seed=0)
    as_of = pd.Timestamp("2024-12-31")
    series = compute_low_vol_v2_universe(
        {"NONE": None, "EMPTY": pd.DataFrame(), "GOOD": good},
        as_of=as_of, window=252, min_history=200,
    )
    assert "NONE" not in series.index
    assert "EMPTY" not in series.index
    assert "GOOD" in series.index
    assert not series.isna().any()


def test_high_vol_lower_score():
    """Higher realized vol → lower (more negative) score."""
    low = _make_constant_drift_close(n=300, seed=42)
    # Create high-vol series by scaling log-returns 3x
    rng = np.random.default_rng(42)
    log_rets = rng.normal(loc=0.0005, scale=0.06, size=300)  # 3x vol
    high_close = 100.0 * np.exp(np.cumsum(log_rets))
    high = pd.DataFrame(
        {"close": high_close, "volume": [1_000_000] * 300},
        index=pd.date_range(end="2024-12-31", periods=300, freq="B"),
    )
    as_of = pd.Timestamp("2024-12-31")
    series = compute_low_vol_v2_universe(
        {"LOW_VOL": low, "HIGH_VOL": high},
        as_of=as_of, window=252, min_history=200,
    )
    assert series["LOW_VOL"] > series["HIGH_VOL"], (
        "Reverse direction violated: low-vol stock should have higher score"
    )


def test_compute_low_vol_v2_filters_zero_close():
    """F5 R21 fix (Phase P5 Session 1): close == 0 rows must be filtered.

    Halted/delisted stocks may have stray close=0 rows in cache (verified
    in repo: 4 stocks / 12 rows / 0.2%). Without filter, log(0)=-inf taints
    the std calculation. F5 adds explicit `close > 0` filter inside the
    universe loop.
    """
    base = _make_constant_drift_close(n=300, seed=42)
    # Inject 5 close=0 rows in the middle
    bad = base.copy()
    bad.iloc[100:105, bad.columns.get_loc("close")] = 0.0
    as_of = pd.Timestamp("2024-12-31")

    # Without the filter, log(0)=-inf would propagate; std would be non-finite
    # and the symbol drops via std_val<=0 guard. With F5 filter, the 5 rows
    # are excluded BEFORE log; the symbol survives if remaining history ≥ 200.
    series, diag = compute_low_vol_v2_universe(
        {"BAD": bad}, as_of=as_of, window=252, min_history=200,
        return_diagnostics=True,
    )
    assert "BAD" in series.index, (
        "F5 filter should exclude zero close rows but keep the symbol when "
        "remaining history is sufficient"
    )
    assert diag["dropped_for_zero_close"] == 1, (
        "diagnostics should count the zero-close stock"
    )
    assert np.isfinite(float(series["BAD"]))


def test_compute_low_vol_v2_diagnostics_count():
    """F5 R21 fix: return_diagnostics=True returns (series, dict) with
    correct counts per drop reason."""
    short = _make_constant_drift_close(n=150, seed=0)  # < 200 history
    long = _make_constant_drift_close(n=300, seed=0)
    none_ohlcv = None
    empty = pd.DataFrame()
    as_of = pd.Timestamp("2024-12-31")
    series, diag = compute_low_vol_v2_universe(
        {"NONE": none_ohlcv, "EMPTY": empty, "SHORT": short, "LONG": long},
        as_of=as_of, window=252, min_history=200,
        return_diagnostics=True,
    )
    assert "LONG" in series.index
    assert "SHORT" not in series.index
    assert "NONE" not in series.index
    assert "EMPTY" not in series.index
    assert diag["dropped_for_no_close"] == 2  # NONE + EMPTY
    assert diag["dropped_for_insufficient_history"] == 1  # SHORT
    assert diag["bad_data_count"] == 3
    # Backward-compat: default return_diagnostics=False returns Series only
    series_only = compute_low_vol_v2_universe(
        {"LONG": long}, as_of=as_of, window=252, min_history=200,
    )
    assert isinstance(series_only, pd.Series)


def test_score_low_vol_v2_per_symbol_wrapper():
    """Per-symbol score wrapper returns dict with score/annualised_vol/icon."""
    ohlcv = _make_constant_drift_close(n=300, seed=42)
    result = score_low_vol_v2(ohlcv, as_of=pd.Timestamp("2024-12-31"))
    assert result["score"] is not None
    assert result["score"] < 0  # reverse direction
    assert result["annualised_vol"] > 0
    # std ≈ 0.02 daily → annualised ≈ 0.02 * sqrt(252) ≈ 0.317 → "✅"
    assert result["annualised_vol"] > 0.20
    assert result["annualised_vol"] < 0.50
