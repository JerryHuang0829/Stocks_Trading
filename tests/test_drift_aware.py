"""Tests for P4.6 drift-aware daily returns in _compute_daily_returns().

Verifies that portfolio returns correctly reflect weight drift within
a holding period, rather than using fixed rebalance-day weights.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine


# ---------------------------------------------------------------------------
# Minimal fake source for unit-testing _compute_daily_returns
# ---------------------------------------------------------------------------

class _MinimalSource:
    """Source that returns pre-built OHLCV DataFrames for specific symbols."""

    def __init__(self, ohlcv_map: dict[str, pd.DataFrame]):
        self._ohlcv = ohlcv_map

    def fetch_ohlcv(self, symbol, timeframe="D", limit=2000):
        return self._ohlcv.get(symbol)

    def fetch_stock_info(self):
        return None

    def fetch_institutional(self, symbol, days=500):
        return None

    def fetch_month_revenue(self, symbol, months=24):
        return None

    def fetch_market_value(self, days=2500):
        return None

    def fetch_delisting(self):
        return None

    def is_market_open(self):
        return False


def _make_price_series(prices: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Build a simple OHLCV DataFrame from a list of closing prices."""
    dates = pd.bdate_range(start, periods=len(prices), tz="UTC")
    arr = np.array(prices, dtype=float)
    return pd.DataFrame({
        "open": arr,
        "high": arr,
        "low": arr,
        "close": arr,
        "volume": [1_000_000] * len(prices),
    }, index=dates)


def _make_engine(ohlcv_map: dict[str, pd.DataFrame]) -> BacktestEngine:
    """Create a BacktestEngine with minimal config and a fake source."""
    source = _MinimalSource(ohlcv_map)
    config = {
        "system": {"mode": "tw_stock_portfolio"},
        "portfolio": {"top_n": 4},
    }
    return BacktestEngine(source, config)


def _run_daily_returns(engine, holdings, period_start, period_end):
    """Helper: call _compute_daily_returns via the engine's _DataSlicer."""
    from src.backtest.engine import _DataSlicer
    slicer = _DataSlicer(
        engine._source,
        as_of=period_end,
        backtest_start=period_start,
        reference_now=period_end,
    )
    return engine._compute_daily_returns(holdings, period_start, period_end, slicer)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDriftAwareReturns:
    """Verify drift-aware weight tracking in _compute_daily_returns."""

    def test_drift_diverges_from_fixed_weight(self):
        """When stocks have different returns, drift-aware != fixed-weight.

        Setup: Stock A returns +10% day 1, then +10% day 2.
               Stock B returns 0% both days.
               Initial weights: A=0.5, B=0.5.

        Fixed-weight day 2 return: 0.5 * 0.10 + 0.5 * 0.0 = 5.00%
        Drift-aware day 2 return:
          After day 1: A=0.55, B=0.50, total=1.05
          Day 2: A goes +10% -> 0.605, B stays 0.50, total=1.105
          Return = 1.105/1.05 - 1 = 5.238%
        """
        # 3 prices: day0 (baseline), day1 (+10%), day2 (+10%)
        prices_a = [100.0, 110.0, 121.0]
        prices_b = [100.0, 100.0, 100.0]

        ohlcv_map = {
            "A": _make_price_series(prices_a),
            "B": _make_price_series(prices_b),
        }
        engine = _make_engine(ohlcv_map)
        holdings = {"A": 0.5, "B": 0.5}

        # period_start < first date, period_end >= last date
        start = datetime(2023, 12, 31)
        end = datetime(2024, 1, 5)
        results = _run_daily_returns(engine, holdings, start, end)

        assert len(results) == 2  # day1 and day2 returns

        day1_ret = results[0][1]
        day2_ret = results[1][1]

        # Day 1: both methods agree: 0.5 * 0.10 + 0.5 * 0.0 = 0.05
        assert abs(day1_ret - 0.05) < 1e-10

        # Day 2: drift-aware should be ~5.238%, NOT 5.00%
        expected_drift = 1.105 / 1.05 - 1.0  # 0.05238...
        assert abs(day2_ret - expected_drift) < 1e-10
        # Confirm it's NOT the fixed-weight answer
        assert abs(day2_ret - 0.05) > 0.001

    def test_identical_returns_match_fixed_weight(self):
        """When all stocks have the same return, drift has no effect."""
        # Both stocks return +2% each day
        prices_a = [100.0, 102.0, 104.04]
        prices_b = [50.0, 51.0, 52.02]

        ohlcv_map = {
            "A": _make_price_series(prices_a),
            "B": _make_price_series(prices_b),
        }
        engine = _make_engine(ohlcv_map)
        holdings = {"A": 0.6, "B": 0.4}

        start = datetime(2023, 12, 31)
        end = datetime(2024, 1, 5)
        results = _run_daily_returns(engine, holdings, start, end)

        assert len(results) == 2
        # Both days: portfolio return should equal individual stock return (2%)
        for _, ret in results:
            assert abs(ret - 0.02) < 1e-10

    def test_nan_handling_trading_halt(self):
        """A stock with NaN (trading halt) on one day should not crash."""
        prices_a = [100.0, 110.0, 121.0]
        # Stock B has a gap on day 2 (NaN close)
        dates = pd.bdate_range("2024-01-01", periods=3, tz="UTC")
        df_b = pd.DataFrame({
            "open": [50.0, 50.0, np.nan],
            "high": [50.0, 50.0, np.nan],
            "low": [50.0, 50.0, np.nan],
            "close": [50.0, 50.0, np.nan],
            "volume": [1_000_000, 1_000_000, 0],
        }, index=dates)

        ohlcv_map = {
            "A": _make_price_series(prices_a),
            "B": df_b,
        }
        engine = _make_engine(ohlcv_map)
        holdings = {"A": 0.5, "B": 0.5}

        start = datetime(2023, 12, 31)
        end = datetime(2024, 1, 5)
        results = _run_daily_returns(engine, holdings, start, end)

        # Should not crash; B's NaN day treated as 0% return
        assert len(results) >= 1

    def test_single_stock_portfolio(self):
        """Drift-aware should work correctly with a single stock + cash."""
        prices = [100.0, 105.0, 110.25]  # +5% then +5%
        ohlcv_map = {"X": _make_price_series(prices)}
        engine = _make_engine(ohlcv_map)
        holdings = {"X": 0.8}  # 80% exposure, 20% cash

        start = datetime(2023, 12, 31)
        end = datetime(2024, 1, 5)
        results = _run_daily_returns(engine, holdings, start, end)

        assert len(results) == 2
        # Day 1: stock +5%, cash 0%
        # total_before = 0.8 + 0.2 = 1.0
        # total_after = 0.84 + 0.2 = 1.04
        # return = 4%
        assert abs(results[0][1] - 0.04) < 1e-10
        # Day 2: drift-aware — stock portion grew to 0.84
        # total_before = 0.84 + 0.2 = 1.04
        # stock +5%: 0.84 * 1.05 = 0.882
        # total_after = 0.882 + 0.2 = 1.082
        # return = 1.082 / 1.04 - 1 = 0.040384...
        expected = 1.082 / 1.04 - 1.0
        assert abs(results[1][1] - expected) < 1e-10

    def test_zero_weight_stock_excluded(self):
        """Stocks with weight <= 0 should be excluded."""
        prices_a = [100.0, 110.0]
        prices_b = [100.0, 50.0]  # -50% crash

        ohlcv_map = {
            "A": _make_price_series(prices_a),
            "B": _make_price_series(prices_b),
        }
        engine = _make_engine(ohlcv_map)
        holdings = {"A": 0.5, "B": 0.0}

        start = datetime(2023, 12, 31)
        end = datetime(2024, 1, 5)
        results = _run_daily_returns(engine, holdings, start, end)

        assert len(results) == 1
        # A=0.5 weight, cash=0.5, A returns +10%
        # total_before=1.0, total_after=0.55+0.5=1.05, ret=5%
        assert abs(results[0][1] - 0.05) < 1e-10
