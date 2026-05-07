"""Unit tests for finmind.py — DiskCache, CSV fallback, stock_info flow.

These tests use tmp_path and mock the FinMind API so no real network calls are made.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.finmind import _DiskCache, FinMindSource


# ---------------------------------------------------------------------------
# _DiskCache tests
# ---------------------------------------------------------------------------

class TestDiskCache:

    def test_load_returns_none_when_empty(self, tmp_path):
        cache = _DiskCache(tmp_path / "cache")
        assert cache.load("ohlcv", "2330") is None

    def test_save_and_load_roundtrip(self, tmp_path):
        cache = _DiskCache(tmp_path / "cache")
        df = pd.DataFrame({"close": [100, 101, 102]})
        cache.save("ohlcv", df, "2330")
        loaded = cache.load("ohlcv", "2330")
        assert loaded is not None
        assert len(loaded) == 3
        assert list(loaded["close"]) == [100, 101, 102]

    def test_load_uses_mem_cache_on_second_call(self, tmp_path):
        cache = _DiskCache(tmp_path / "cache")
        df = pd.DataFrame({"a": [1]})
        cache.save("test", df, "sym")
        first = cache.load("test", "sym")
        second = cache.load("test", "sym")
        assert first is second  # same object from memory

    def test_meta_returns_none_when_no_meta(self, tmp_path):
        cache = _DiskCache(tmp_path / "cache")
        assert cache.meta("stock_info") is None

    def test_save_and_read_meta(self, tmp_path):
        cache = _DiskCache(tmp_path / "cache")
        cache.save("stock_info", pd.DataFrame({"a": [1]}))
        cache.save_meta("stock_info", "2026-04-01")
        assert cache.meta("stock_info") == "2026-04-01"

    def test_corrupted_pickle_returns_none_and_keeps_file(self, tmp_path):
        cache = _DiskCache(tmp_path / "cache")
        # Write garbage to the pickle file
        path = cache._path("ohlcv", "BAD")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a pickle")
        result = cache.load("ohlcv", "BAD")
        assert result is None
        assert path.exists()  # P7: log-only, don't delete (avoid data loss on version mismatch)

    def test_subdirectories_created_automatically(self, tmp_path):
        cache = _DiskCache(tmp_path / "deep" / "cache")
        df = pd.DataFrame({"x": [1]})
        cache.save("revenue", df, "2330")
        assert (tmp_path / "deep" / "cache" / "revenue" / "2330.pkl").exists()


# ---------------------------------------------------------------------------
# CSV fallback tests
# ---------------------------------------------------------------------------

class TestCSVFallback:

    @pytest.fixture
    def source(self, tmp_path):
        """Create a FinMindSource with a temp cache dir and mocked API."""
        with patch.dict("os.environ", {"FINMIND_TOKEN": "fake_token"}):
            s = FinMindSource.__new__(FinMindSource)
            s._disk = _DiskCache(tmp_path / "cache")
            s._mem = {}
            s._simple = MagicMock()
            s.loader = MagicMock()
            s._last_request_time = 0
            return s

    def test_save_and_load_csv_roundtrip(self, source):
        df = pd.DataFrame({
            "stock_id": ["2330", "2317"],
            "stock_name": ["台積電", "鴻海"],
            "industry_category": ["半導體業", "電子工業"],
        })
        source._save_stock_info_csv_snapshot(df)
        loaded = source._load_stock_info_csv_fallback()
        assert loaded is not None
        assert len(loaded) == 2
        assert list(loaded["stock_id"]) == ["2330", "2317"]

    def test_load_csv_returns_none_when_no_file(self, source):
        result = source._load_stock_info_csv_fallback()
        assert result is None

    def test_ensure_csv_creates_when_missing(self, source):
        df = pd.DataFrame({"stock_id": ["2330"]})
        csv_path = source._disk._dir / "stock_info" / "stock_info_snapshot.csv"
        assert not csv_path.exists()
        source._ensure_stock_info_csv(df)
        assert csv_path.exists()

    def test_ensure_csv_skips_when_exists(self, source):
        df = pd.DataFrame({"stock_id": ["2330"]})
        source._save_stock_info_csv_snapshot(df)
        csv_path = source._disk._dir / "stock_info" / "stock_info_snapshot.csv"
        mtime_before = csv_path.stat().st_mtime
        # Second call should not overwrite
        source._ensure_stock_info_csv(df)
        mtime_after = csv_path.stat().st_mtime
        assert mtime_before == mtime_after


# ---------------------------------------------------------------------------
# fetch_stock_info flow tests
# ---------------------------------------------------------------------------

class TestFetchStockInfo:

    @pytest.fixture
    def source(self, tmp_path):
        with patch.dict("os.environ", {"FINMIND_TOKEN": "fake_token"}):
            s = FinMindSource.__new__(FinMindSource)
            s._disk = _DiskCache(tmp_path / "cache")
            s._mem = {}
            s._simple = MagicMock()
            s.loader = MagicMock()
            s._last_request_time = 0
            s._request_interval = 0  # no rate limiting in tests
            s._backtest_mode = False
            return s

    def test_fresh_fetch_caches_and_saves_csv(self, source):
        api_df = pd.DataFrame({
            "stock_id": ["2330", "2317"],
            "stock_name": ["台積電", "鴻海"],
            "industry_category": ["半導體業", "電子工業"],
        })
        source.loader.taiwan_stock_info.return_value = api_df

        result = source.fetch_stock_info()
        assert result is not None
        assert len(result) == 2

        # Check pickle cache was saved
        cached = source._disk.load("stock_info")
        assert cached is not None

        # Check CSV was saved
        csv_path = source._disk._dir / "stock_info" / "stock_info_snapshot.csv"
        assert csv_path.exists()

    def test_cache_hit_returns_cached_and_ensures_csv(self, source):
        df = pd.DataFrame({"stock_id": ["2330"]})
        source._disk.save("stock_info", df)
        source._disk.save_meta("stock_info", datetime.now().strftime("%Y-%m-%d"))

        result = source.fetch_stock_info()
        assert result is not None
        assert len(result) == 1
        # API should NOT be called
        source.loader.taiwan_stock_info.assert_not_called()

        # CSV should be created via _ensure
        csv_path = source._disk._dir / "stock_info" / "stock_info_snapshot.csv"
        assert csv_path.exists()

    def test_expired_cache_refetches(self, source):
        old_df = pd.DataFrame({"stock_id": ["OLD"]})
        source._disk.save("stock_info", old_df)
        # Meta shows 8 days ago (expired TTL of 7 days)
        old_date = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
        source._disk.save_meta("stock_info", old_date)

        new_df = pd.DataFrame({"stock_id": ["NEW"]})
        source.loader.taiwan_stock_info.return_value = new_df

        result = source.fetch_stock_info()
        assert result is not None
        assert list(result["stock_id"]) == ["NEW"]
        source.loader.taiwan_stock_info.assert_called_once()

    def test_api_failure_falls_back_to_cache(self, source):
        cached_df = pd.DataFrame({"stock_id": ["CACHED"]})
        source._disk.save("stock_info", cached_df)
        # Meta expired
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        source._disk.save_meta("stock_info", old_date)

        source.loader.taiwan_stock_info.side_effect = Exception("API down")

        result = source.fetch_stock_info()
        assert result is not None
        assert list(result["stock_id"]) == ["CACHED"]

    def test_api_failure_no_cache_falls_back_to_csv(self, source):
        # No pickle cache, but CSV exists
        csv_df = pd.DataFrame({"stock_id": ["CSV_FALLBACK"]})
        source._save_stock_info_csv_snapshot(csv_df)

        source.loader.taiwan_stock_info.side_effect = Exception("API down")

        result = source.fetch_stock_info()
        assert result is not None
        assert list(result["stock_id"]) == ["CSV_FALLBACK"]

    def test_api_failure_no_cache_no_csv_returns_none(self, source):
        source.loader.taiwan_stock_info.side_effect = Exception("API down")
        result = source.fetch_stock_info()
        assert result is None
