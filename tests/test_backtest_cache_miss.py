"""Strict backtest mode: cache miss MUST raise, never fall through to live API.

Guarantees PIT reproducibility — backtest replay cannot depend on what the
live FinMind / TWSE endpoints return at replay time. Seed the cache once in
live mode, then replay in backtest mode with a frozen cache.

Companion to test_cache_strict.py (corrupt pickle) and test_dividends_strict.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data import finmind as fm
from src.data.finmind import (
    FinMindSource,
    _BacktestCacheMissError,
)


def _bare_source(tmp_path) -> FinMindSource:
    """Construct a FinMindSource without touching FinMind.DataLoader (no network)."""
    src = FinMindSource.__new__(FinMindSource)
    src._disk = fm._DiskCache(tmp_path)
    src._backtest_mode = True
    src._use_adjusted = False
    src._request_interval = 0.0
    src._last_request_time = 0.0
    src._simple_cache = fm._SimpleCache()
    return src


class TestBacktestCacheMissRaises:
    def test_ohlcv_backtest_miss_raises(self, tmp_path):
        src = _bare_source(tmp_path)
        with pytest.raises(_BacktestCacheMissError, match="ohlcv cache miss"):
            src.fetch_ohlcv("2330", "D", 100)

    def test_institutional_backtest_miss_raises(self, tmp_path):
        src = _bare_source(tmp_path)
        with pytest.raises(_BacktestCacheMissError, match="institutional cache miss"):
            src.fetch_institutional("2330", 30)

    def test_month_revenue_backtest_miss_raises(self, tmp_path):
        src = _bare_source(tmp_path)
        with pytest.raises(_BacktestCacheMissError, match="month_revenue cache miss"):
            src.fetch_month_revenue("2330", 15)

    def test_stock_info_backtest_miss_raises(self, tmp_path):
        src = _bare_source(tmp_path)
        with pytest.raises(_BacktestCacheMissError, match="stock_info cache miss"):
            src.fetch_stock_info()

    def test_market_value_backtest_miss_raises(self, tmp_path):
        src = _bare_source(tmp_path)
        with pytest.raises(_BacktestCacheMissError, match="market_value cache miss"):
            src.fetch_market_value(days=10)

    def test_delisting_backtest_miss_raises(self, tmp_path):
        src = _bare_source(tmp_path)
        with pytest.raises(_BacktestCacheMissError, match="delisting cache miss"):
            src.fetch_delisting()

    def test_financial_quality_backtest_miss_raises(self, tmp_path):
        src = _bare_source(tmp_path)
        with pytest.raises(_BacktestCacheMissError, match="financial_quality cache miss"):
            src.fetch_financial_quality("2330")


class TestBacktestCacheHitWorks:
    """Positive control: pre-seeded cache does NOT raise under backtest_mode."""

    def test_ohlcv_backtest_hit_returns_data(self, tmp_path):
        src = _bare_source(tmp_path)
        df = pd.DataFrame(
            {
                "open": [100.0] * 5,
                "high": [101.0] * 5,
                "low": [99.0] * 5,
                "close": [100.5] * 5,
                "volume": [1_000] * 5,
            },
            # Must be recent: fetch_ohlcv slices by now - limit*1.8 days.
            index=pd.date_range(end=pd.Timestamp.utcnow().normalize(), periods=5, tz="UTC"),
        )
        src._disk.save("ohlcv", df, "2330")
        src._disk._mem.clear()
        out = src.fetch_ohlcv("2330", "D", 100)
        assert out is not None
        assert not out.empty

    def test_institutional_empty_sentinel_returns_none(self, tmp_path):
        """Empty DataFrame is a valid 'no data for this symbol' sentinel; do not raise."""
        src = _bare_source(tmp_path)
        src._disk.save("institutional", pd.DataFrame(), "2330")
        src._disk._mem.clear()
        assert src.fetch_institutional("2330", 30) is None

    def test_month_revenue_empty_sentinel_returns_none(self, tmp_path):
        src = _bare_source(tmp_path)
        src._disk.save("revenue", pd.DataFrame(), "2330")
        src._disk._mem.clear()
        assert src.fetch_month_revenue("2330", 15) is None


class TestLiveModeUnchanged:
    """Guard: backtest_mode=False path is NOT affected by the new raises.
    (Cannot exercise full live fetch without network; verify at construction level.)"""

    def test_live_mode_miss_does_not_raise_backtest_error(self, tmp_path, monkeypatch):
        src = _bare_source(tmp_path)
        src._backtest_mode = False
        # Patch the live API to a known-empty return so we don't hit network.
        monkeypatch.setattr(
            src,
            "_compute_market_value_from_twse",
            lambda: None,
        )
        monkeypatch.setattr(
            src,
            "_fetch_market_value_finmind",
            lambda days=10: None,
        )
        # Should return None (cache miss in live mode), not raise.
        assert src.fetch_market_value(days=10) is None


class TestEndToEndEmptyCacheBacktest:
    """End-to-end: empty cache + backtest_mode must fail loud at the first fetch_*."""

    def test_pipeline_empty_cache_raises_early(self, tmp_path):
        src = _bare_source(tmp_path)
        # Simulate pipeline start: universe build reads stock_info first.
        with pytest.raises(_BacktestCacheMissError):
            src.fetch_stock_info()
        # Even if caller swallowed that, next ohlcv fetch would also raise.
        with pytest.raises(_BacktestCacheMissError):
            src.fetch_ohlcv("2330", "D", 252)
