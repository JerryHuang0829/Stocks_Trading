"""H2: corrupted pkl must raise _DiskCacheCorruptedError in strict mode.

Guards against silent fallback to live API in backtest mode, which would
break PIT reproducibility.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.finmind import _DiskCache, _DiskCacheCorruptedError


class TestDiskCacheStrict:
    def test_missing_returns_none_in_both_modes(self, tmp_path):
        c = _DiskCache(tmp_path)
        assert c.load("ohlcv", "2330", strict=False) is None
        assert c.load("ohlcv", "2330", strict=True) is None

    def test_corrupt_strict_raises(self, tmp_path):
        c = _DiskCache(tmp_path)
        path = c._path("ohlcv", "2330")
        path.write_bytes(b"not a valid pickle blob")
        with pytest.raises(_DiskCacheCorruptedError):
            c.load("ohlcv", "2330", strict=True)

    def test_corrupt_non_strict_returns_none(self, tmp_path):
        c = _DiskCache(tmp_path)
        path = c._path("ohlcv", "2330")
        path.write_bytes(b"not a valid pickle blob")
        assert c.load("ohlcv", "2330", strict=False) is None

    def test_market_value_raises_on_corrupt_in_backtest(self, tmp_path, monkeypatch):
        """H2-bypass: _compute_market_value_from_twse 直接 pd.read_pickle 的路徑
        在 backtest 模式遇 corrupt pkl 必須 raise，不能 silently continue。"""
        from src.data import finmind as fm
        from src.data.finmind import FinMindSource, _DiskCacheCorruptedError

        monkeypatch.setattr(
            "src.data.twse_scraper.fetch_twse_issued_capital",
            lambda: {"2330": 1_000_000.0},
        )
        src = FinMindSource.__new__(FinMindSource)
        src._disk = fm._DiskCache(tmp_path)
        src._backtest_mode = True
        pkl = src._disk._path("ohlcv", "2330")
        pkl.write_bytes(b"not a valid pickle")
        with pytest.raises(_DiskCacheCorruptedError):
            src._compute_market_value_from_twse()

    def test_market_value_skips_corrupt_in_live(self, tmp_path, monkeypatch):
        from src.data import finmind as fm
        from src.data.finmind import FinMindSource

        monkeypatch.setattr(
            "src.data.twse_scraper.fetch_twse_issued_capital",
            lambda: {"2330": 1_000.0, "2454": 1_000.0},
        )
        src = FinMindSource.__new__(FinMindSource)
        src._disk = fm._DiskCache(tmp_path)
        src._backtest_mode = False
        src._disk._path("ohlcv", "2330").write_bytes(b"not a valid pickle")
        good = src._disk._path("ohlcv", "2454")
        df = pd.DataFrame(
            {"close": [100.0]},
            index=pd.date_range("2024-01-01", periods=1, tz="UTC"),
        )
        df.to_pickle(good)
        result = src._compute_market_value_from_twse()
        assert result is not None
        assert "2454" in set(result["stock_id"])
        assert "2330" not in set(result["stock_id"])

    def test_valid_pickle_loads_in_strict(self, tmp_path):
        c = _DiskCache(tmp_path)
        df = pd.DataFrame({"close": [100.0, 101.0]})
        c.save("ohlcv", df, "2330")
        c._mem.clear()
        got = c.load("ohlcv", "2330", strict=True)
        assert got is not None
        assert list(got["close"]) == [100.0, 101.0]
