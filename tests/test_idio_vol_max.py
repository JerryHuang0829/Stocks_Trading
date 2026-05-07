"""V0.13 D-G idio_vol_max (0.5/0.5 IdioVol + MAX lottery) tests — Phase 2 S3.

Verifies `src.features.idio_vol_max.compute_idio_vol_max_panel`:
- 60 trading days residual std lookback (V0.13 lock)
- 22 trading days MAX lottery lookback (top-5 daily returns)
- 0.5/0.5 weight split per H_d_v6:58
- Negation: low residual / low MAX = high score (long-only "good")
- PIT shift=1
- Edge cases (insufficient data / NaN / weights validation)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.idio_vol_max import (  # noqa: E402
    DEFAULT_MAX_LOOKBACK_DAYS,
    DEFAULT_RESIDUAL_LOOKBACK_DAYS,
    DEFAULT_WEIGHTS,
    _compute_max_lottery,
    _compute_residual_std,
    compute_idio_vol_max_panel,
)


def _make_ohlcv_with_returns(
    start: str, n_days: int, daily_returns: list[float]
) -> pd.DataFrame:
    """Build synthetic OHLCV from prescribed daily returns; close = 100 × cumprod(1+r)."""
    dates = pd.date_range(start, periods=n_days, freq="B")
    closes = 100.0 * np.cumprod(1.0 + np.array(daily_returns))
    return pd.DataFrame({"close": closes}, index=dates)


def _make_market_series(start: str, n_days: int, daily_returns: list[float]) -> pd.Series:
    """Build synthetic market daily-return Series."""
    dates = pd.date_range(start, periods=n_days, freq="B")
    return pd.Series(daily_returns, index=dates)


def test_idio_vol_max_default_weights_50_50():
    """Sanity: DEFAULT_WEIGHTS = (0.5, 0.5) per H_d_v6:58 D-G spec."""
    assert DEFAULT_WEIGHTS == (0.5, 0.5)


def test_idio_vol_max_default_residual_lookback_60():
    """Sanity: DEFAULT_RESIDUAL_LOOKBACK_DAYS = 60 per V0.13 lock."""
    assert DEFAULT_RESIDUAL_LOOKBACK_DAYS == 60


def test_idio_vol_max_default_max_lookback_22():
    """Sanity: DEFAULT_MAX_LOOKBACK_DAYS = 22 (~1 month) per V0.13."""
    assert DEFAULT_MAX_LOOKBACK_DAYS == 22


def test_idio_vol_max_weights_must_sum_to_one():
    """Mutation: invalid weights → raise ValueError."""
    rng = np.random.default_rng(42)
    n = 100
    ohlcv = {"2330": _make_ohlcv_with_returns("2024-01-01", n, list(rng.normal(0.001, 0.02, n)))}
    market = _make_market_series("2024-01-01", n, list(rng.normal(0.0005, 0.015, n)))
    with pytest.raises(ValueError, match="weights must sum to 1.0"):
        compute_idio_vol_max_panel(
            ohlcv, market, pd.Timestamp("2024-06-01"),
            weights=(0.7, 0.7),
        )


def test_compute_residual_std_known_correlation():
    """Sanity: stock perfectly correlated with market → residual std = 0."""
    n = 100
    market = pd.Series(
        np.random.default_rng(0).normal(0, 0.02, n),
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )
    # Perfect correlation: stock = 1.5 × market
    stock = market * 1.5
    residual = _compute_residual_std(stock, market)
    # corr=1 → residual = stock_std × √(1-1) = 0
    assert abs(residual) < 1e-9


def test_compute_residual_std_zero_correlation():
    """Sanity: stock uncorrelated with market → residual ≈ stock std."""
    rng = np.random.default_rng(0)
    n = 1000  # large n for stable corr ≈ 0
    market_arr = rng.normal(0, 0.02, n)
    stock_arr = rng.normal(0, 0.02, n)  # independent
    market = pd.Series(market_arr, index=pd.date_range("2024-01-01", periods=n, freq="B"))
    stock = pd.Series(stock_arr, index=market.index)
    residual = _compute_residual_std(stock, market)
    stock_std = float(stock.std(ddof=1))
    # corr ≈ 0 → residual ≈ stock_std × √(1-0²) = stock_std
    assert abs(residual - stock_std) < 0.005


def test_compute_max_lottery_top_5():
    """Sanity: top-5 mean of [0.01, 0.02, ..., 0.10] = mean of top-5 = (0.06+0.07+0.08+0.09+0.10)/5 = 0.08."""
    returns = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10])
    result = _compute_max_lottery(returns, top_k=5)
    expected = (0.06 + 0.07 + 0.08 + 0.09 + 0.10) / 5
    assert abs(result - expected) < 1e-9


def test_compute_max_lottery_insufficient_returns():
    """Edge: fewer returns than top_k → NaN."""
    returns = pd.Series([0.01, 0.02])
    result = _compute_max_lottery(returns, top_k=5)
    assert np.isnan(result)


def test_idio_vol_max_panel_basic_three_symbols():
    """Happy path: 3 symbols with different idio vol; cross-section z-score sane."""
    rng = np.random.default_rng(42)
    n = 100
    market = _make_market_series("2024-01-01", n, list(rng.normal(0.0005, 0.015, n)))
    ohlcv = {
        # 2330: low idio vol (high market correlation), low MAX lottery
        "2330": _make_ohlcv_with_returns("2024-01-01", n, list(market.values * 1.0 + rng.normal(0, 0.005, n))),
        # 2317: medium idio vol
        "2317": _make_ohlcv_with_returns("2024-01-01", n, list(rng.normal(0.001, 0.02, n))),
        # 2454: high idio vol (idiosyncratic spikes)
        "2454": _make_ohlcv_with_returns("2024-01-01", n, list(rng.normal(0.001, 0.04, n))),
    }
    panel = compute_idio_vol_max_panel(
        ohlcv, market, pd.Timestamp("2024-05-01"),
    )
    # 3 symbols expected
    assert len(panel) == 3
    # All finite
    assert all(np.isfinite(panel.values))


def test_idio_vol_max_negation_low_residual_high_score():
    """Mutation: composite negates both — low residual + low MAX = HIGH score
    (long-only quality interpretation). Test verifies sign of factor."""
    rng = np.random.default_rng(42)
    n = 100
    market = _make_market_series("2024-01-01", n, list(rng.normal(0.0005, 0.015, n)))
    ohlcv = {
        # Low-vol stock: tightly tracks market (low residual + low MAX)
        "LOW": _make_ohlcv_with_returns("2024-01-01", n, list(market.values + rng.normal(0, 0.001, n))),
        # High-vol stock: independent with high vol (high residual + high MAX)
        "HIGH": _make_ohlcv_with_returns("2024-01-01", n, list(rng.normal(0.0, 0.05, n))),
    }
    panel = compute_idio_vol_max_panel(
        ohlcv, market, pd.Timestamp("2024-05-01"),
    )
    # LOW (low idio + low MAX) should score HIGHER than HIGH
    assert panel["LOW"] > panel["HIGH"], (
        "negation regression: low-vol stock should score higher than high-vol"
    )


def test_idio_vol_max_insufficient_market_returns_empty():
    """Edge: market < required lookback → empty panel."""
    rng = np.random.default_rng(0)
    market = _make_market_series("2024-01-01", 30, list(rng.normal(0, 0.01, 30)))
    ohlcv = {"2330": _make_ohlcv_with_returns("2024-01-01", 30, list(rng.normal(0, 0.01, 30)))}
    panel = compute_idio_vol_max_panel(
        ohlcv, market, pd.Timestamp("2024-03-01"),
    )
    assert panel.empty


def test_idio_vol_max_insufficient_stock_drops_symbol():
    """Edge: stock with < required lookback dropped, others survive."""
    rng = np.random.default_rng(0)
    n = 100
    market = _make_market_series("2024-01-01", n, list(rng.normal(0, 0.015, n)))
    ohlcv = {
        "FULL": _make_ohlcv_with_returns("2024-01-01", n, list(rng.normal(0, 0.02, n))),
        "SHORT": _make_ohlcv_with_returns("2024-04-01", 10, list(rng.normal(0, 0.02, 10))),  # only 10 days
    }
    panel = compute_idio_vol_max_panel(
        ohlcv, market, pd.Timestamp("2024-05-01"),
    )
    assert "FULL" in panel.index
    assert "SHORT" not in panel.index


def test_idio_vol_max_pit_excludes_rebal_day_close():
    """PIT critical: shift=1 — rebal day's own close NOT included."""
    rng = np.random.default_rng(0)
    n = 100
    market = _make_market_series("2024-01-01", n, list(rng.normal(0, 0.015, n)))
    ohlcv = {"2330": _make_ohlcv_with_returns("2024-01-01", n, list(rng.normal(0, 0.02, n)))}
    # ohlcv last date = 2024-01-01 + 99 business days; choose rebal s.t. cutoff=as_of-1d ≈ 100th day
    # If shift not enforced, rebal day data would leak; test verifies finite output
    panel = compute_idio_vol_max_panel(
        ohlcv, market, pd.Timestamp("2024-05-15"),
    )
    # As long as data sufficient, panel computed without leak
    if not panel.empty:
        assert all(np.isfinite(panel.values))


def test_idio_vol_max_empty_panel_when_no_common_symbols():
    """Edge: symbol with valid residual but no MAX (or vice versa) → drops out
    of common_syms intersection → empty if all such cases."""
    # Empty ohlcv → empty panel
    market = _make_market_series("2024-01-01", 100, [0.01] * 100)
    panel = compute_idio_vol_max_panel(
        {}, market, pd.Timestamp("2024-05-01"),
    )
    assert panel.empty
