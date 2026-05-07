"""Tests for _DataSlicer — the core look-ahead bias prevention mechanism.

Verifies that set_as_of() correctly truncates all data sources (OHLCV,
institutional, month revenue, market value) to prevent future data leakage.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from src.backtest.engine import _DataSlicer
except ImportError:
    pytest.skip(
        "Cannot import _DataSlicer (likely missing pandas_ta on Windows); run in Docker",
        allow_module_level=True,
    )


def _make_ohlcv(start="2024-01-01", periods=60) -> pd.DataFrame:
    """Create a simple OHLCV DataFrame with UTC-aware DatetimeIndex."""
    dates = pd.date_range(start, periods=periods, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "open": range(100, 100 + periods),
            "high": range(101, 101 + periods),
            "low": range(99, 99 + periods),
            "close": range(100, 100 + periods),
            "volume": [1000] * periods,
        },
        index=dates,
    )


def _make_date_df(start="2024-01-01", periods=12, freq="MS") -> pd.DataFrame:
    """Create a DataFrame with a naive 'date' column (like institutional/revenue)."""
    dates = pd.date_range(start, periods=periods, freq=freq)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "value": range(periods),
        }
    )


class _FakeSource:
    """Minimal fake FinMindSource for testing _DataSlicer."""

    def __init__(self, ohlcv=None, institutional=None, revenue=None, market_value=None):
        self._ohlcv = ohlcv
        self._inst = institutional
        self._rev = revenue
        self._mv = market_value

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self._ohlcv.copy() if self._ohlcv is not None else None

    def fetch_institutional(self, symbol, days):
        return self._inst.copy() if self._inst is not None else None

    def fetch_month_revenue(self, symbol, months):
        return self._rev.copy() if self._rev is not None else None

    def fetch_market_value(self, days=10):
        return self._mv.copy() if self._mv is not None else None

    def fetch_stock_info(self):
        return pd.DataFrame()


class TestDataSlicerOHLCV:
    """OHLCV truncation via UTC-aware index."""

    def test_truncates_future_ohlcv(self):
        """Data after as_of date should be excluded."""
        ohlcv = _make_ohlcv("2024-01-01", periods=60)
        source = _FakeSource(ohlcv=ohlcv)
        slicer = _DataSlicer(source)

        # Set as_of to mid-February — should exclude March data
        slicer.set_as_of(datetime(2024, 2, 15))
        result = slicer.fetch_ohlcv("TEST", "D", limit=1000)

        assert result is not None
        # All dates should be <= 2024-02-15
        max_date = result.index.max()
        assert max_date <= pd.Timestamp("2024-02-15", tz="UTC")

    def test_includes_data_up_to_as_of(self):
        """Data on or before as_of should be included."""
        ohlcv = _make_ohlcv("2024-01-01", periods=30)
        source = _FakeSource(ohlcv=ohlcv)
        slicer = _DataSlicer(source)

        slicer.set_as_of(datetime(2024, 3, 1))
        result = slicer.fetch_ohlcv("TEST", "D", limit=1000)

        assert result is not None
        assert len(result) == 30  # All data included

    def test_no_as_of_returns_all(self):
        """Without set_as_of, all data should be returned."""
        ohlcv = _make_ohlcv("2024-01-01", periods=20)
        source = _FakeSource(ohlcv=ohlcv)
        slicer = _DataSlicer(source)

        result = slicer.fetch_ohlcv("TEST", "D", limit=1000)
        assert result is not None
        assert len(result) == 20


class TestDataSlicerRevenue:
    """Month revenue truncation via 'date' column."""

    def test_truncates_future_revenue(self):
        """Revenue data after as_of should be excluded."""
        rev = _make_date_df("2024-01-01", periods=12, freq="MS")
        source = _FakeSource(revenue=rev)
        slicer = _DataSlicer(source)

        slicer.set_as_of(datetime(2024, 6, 15))
        result = slicer.fetch_month_revenue("TEST", months=12)

        assert result is not None
        dates = pd.to_datetime(result["date"])
        assert dates.max() <= pd.Timestamp("2024-06-15")

    def test_excludes_exactly_future_month(self):
        """A revenue record dated 2024-07-01 should be excluded when as_of is 2024-06-30."""
        rev = _make_date_df("2024-01-01", periods=12, freq="MS")
        source = _FakeSource(revenue=rev)
        slicer = _DataSlicer(source)

        slicer.set_as_of(datetime(2024, 6, 30))
        result = slicer.fetch_month_revenue("TEST", months=12)

        assert result is not None
        dates = pd.to_datetime(result["date"])
        # June record (2024-06-01) should be included, July (2024-07-01) excluded
        assert pd.Timestamp("2024-06-01") in dates.values
        assert pd.Timestamp("2024-07-01") not in dates.values


class TestDataSlicerInstitutional:
    """Institutional data truncation via 'date' column."""

    def test_truncates_future_institutional(self):
        """Institutional data after as_of should be excluded."""
        inst = _make_date_df("2024-01-01", periods=60, freq="B")
        source = _FakeSource(institutional=inst)
        slicer = _DataSlicer(source)

        slicer.set_as_of(datetime(2024, 2, 1))
        result = slicer.fetch_institutional("TEST", days=30)

        assert result is not None
        dates = pd.to_datetime(result["date"])
        assert dates.max() <= pd.Timestamp("2024-02-01")


class TestDataSlicerMarketValue:
    """Market value truncation via 'date' column."""

    def test_truncates_future_market_value(self):
        """Market value data after as_of should be excluded."""
        mv = _make_date_df("2024-01-01", periods=60, freq="B")
        source = _FakeSource(market_value=mv)
        slicer = _DataSlicer(source)

        slicer.set_as_of(datetime(2024, 2, 1))
        result = slicer.fetch_market_value(days=60)

        assert result is not None
        dates = pd.to_datetime(result["date"])
        assert dates.max() <= pd.Timestamp("2024-02-01")


class TestDataSlicerSetAsOfUpdates:
    """Verify that changing as_of properly re-slices cached data."""

    def test_advancing_as_of_reveals_more_data(self):
        """Moving as_of forward should reveal previously hidden data."""
        ohlcv = _make_ohlcv("2024-01-01", periods=60)
        source = _FakeSource(ohlcv=ohlcv)
        slicer = _DataSlicer(source)

        slicer.set_as_of(datetime(2024, 1, 15))
        result1 = slicer.fetch_ohlcv("TEST", "D", limit=1000)

        slicer.set_as_of(datetime(2024, 2, 15))
        result2 = slicer.fetch_ohlcv("TEST", "D", limit=1000)

        assert len(result2) > len(result1)

    def test_retreating_as_of_hides_data(self):
        """Moving as_of backward should hide data that was previously visible."""
        ohlcv = _make_ohlcv("2024-01-01", periods=60)
        source = _FakeSource(ohlcv=ohlcv)
        slicer = _DataSlicer(source)

        slicer.set_as_of(datetime(2024, 2, 15))
        result1 = slicer.fetch_ohlcv("TEST", "D", limit=1000)

        slicer.set_as_of(datetime(2024, 1, 15))
        result2 = slicer.fetch_ohlcv("TEST", "D", limit=1000)

        assert len(result2) < len(result1)


class TestDataSlicerEdgeCases:
    """Edge cases: empty data, None returns, etc."""

    def test_none_ohlcv(self):
        source = _FakeSource(ohlcv=None)
        slicer = _DataSlicer(source)
        slicer.set_as_of(datetime(2024, 6, 1))
        assert slicer.fetch_ohlcv("TEST", "D") is None

    def test_none_revenue(self):
        source = _FakeSource(revenue=None)
        slicer = _DataSlicer(source)
        slicer.set_as_of(datetime(2024, 6, 1))
        assert slicer.fetch_month_revenue("TEST") is None

    def test_empty_ohlcv(self):
        source = _FakeSource(ohlcv=pd.DataFrame())
        slicer = _DataSlicer(source)
        slicer.set_as_of(datetime(2024, 6, 1))
        assert slicer.fetch_ohlcv("TEST", "D") is None

    def test_as_of_before_all_data(self):
        """as_of earlier than all data → empty/None result."""
        ohlcv = _make_ohlcv("2024-06-01", periods=30)
        source = _FakeSource(ohlcv=ohlcv)
        slicer = _DataSlicer(source)
        slicer.set_as_of(datetime(2024, 1, 1))
        result = slicer.fetch_ohlcv("TEST", "D", limit=1000)
        assert result is None


class TestDataSlicerCacheInteraction:
    """Cache must not bypass as_of truncation."""

    def test_cached_ohlcv_still_respects_as_of(self):
        """Second fetch of same symbol with different as_of must re-slice from cache."""
        ohlcv = _make_ohlcv("2024-01-01", periods=60)
        source = _FakeSource(ohlcv=ohlcv)
        slicer = _DataSlicer(source)

        # First fetch populates cache
        slicer.set_as_of(datetime(2024, 3, 15))
        result1 = slicer.fetch_ohlcv("SAME", "D", limit=1000)

        # Second fetch with earlier as_of — cache exists but must re-slice
        slicer.set_as_of(datetime(2024, 1, 15))
        result2 = slicer.fetch_ohlcv("SAME", "D", limit=1000)

        assert result1 is not None and result2 is not None
        assert len(result2) < len(result1)
        assert result2.index.max() <= pd.Timestamp("2024-01-15", tz="UTC")

    def test_revenue_tail_does_not_bypass_as_of(self):
        """tail parameter limits rows but must not include data after as_of."""
        # 12 months of revenue: 2024-01 through 2024-12
        rev = _make_date_df("2024-01-01", periods=12, freq="MS")
        source = _FakeSource(revenue=rev)
        slicer = _DataSlicer(source)

        # as_of = 2024-06-15, tail (months) = 12
        # Should return at most 6 months (Jan-Jun), NOT all 12
        slicer.set_as_of(datetime(2024, 6, 15))
        result = slicer.fetch_month_revenue("TEST", months=12)

        assert result is not None
        dates = pd.to_datetime(result["date"])
        assert dates.max() <= pd.Timestamp("2024-06-15")
        assert len(result) <= 7  # Jan through Jun at most
