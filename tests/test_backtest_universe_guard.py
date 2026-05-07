"""Regression tests for backtest size-proxy guard.

直接打到 `src/backtest/universe.py` 的 size-proxy loop，而非只 mock 上游。
確保 cached_total 的小樣本洞（Codex 2026-04-15 指出）不會繞過字典序保護。
"""

from __future__ import annotations

import pathlib
from datetime import datetime

import pandas as pd
import pytest

from src.backtest.universe import HistoricalUniverse


def _stock_info(n: int = 30) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_id": [f"{2000 + i:04d}" for i in range(n)],
            "stock_name": [f"S{i}" for i in range(n)],
            "type": ["twse"] * n,
            "industry_category": ["電子"] * n,
            "date": pd.Timestamp("2024-01-01"),
        }
    )


class _FakeSource:
    def __init__(self, info, fetch_behavior="raise"):
        self._info = info
        self._behavior = fetch_behavior

    def fetch_stock_info(self):
        return self._info

    def fetch_delisting(self):
        return pd.DataFrame()

    def fetch_ohlcv(self, sym, tf, n):
        if self._behavior == "raise":
            raise RuntimeError("simulated cache corruption")
        if self._behavior == "none":
            return None
        raise ValueError(self._behavior)


def _prime_ohlcv_cache(tmp_path: pathlib.Path, stock_ids: list[str]) -> pathlib.Path:
    """建立 data/cache/ohlcv 目錄並放入 stub pkl，以模擬 cached_syms。"""
    ohlcv_dir = tmp_path / "ohlcv"
    ohlcv_dir.mkdir()
    idx = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
    stub = pd.DataFrame({"close": [100.0] * 30, "volume": [1000.0] * 30}, index=idx)
    for sid in stock_ids:
        stub.to_pickle(ohlcv_dir / f"{sid}.pkl")
    return ohlcv_dir


class TestBacktestSizeProxyGuard:

    def test_small_sample_all_fail_raises(self, tmp_path, monkeypatch):
        """Codex 指出的小樣本洞：9 cached + 全 raise → 必須 raise（cached_success==0）."""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        info = _stock_info(20)
        cached = [f"{2000 + i:04d}" for i in range(9)]  # 9 < 10 門檻
        _prime_ohlcv_cache(tmp_path, cached)

        src = _FakeSource(info, fetch_behavior="raise")
        u = HistoricalUniverse(src)
        u.load()

        with pytest.raises(RuntimeError, match="all 9 cached stocks"):
            u.get_universe_at(
                datetime(2024, 1, 15),
                portfolio_config={"auto_universe_size": 10},
                source=src,
            )

    def test_large_sample_rate_below_min_raises(self, tmp_path, monkeypatch):
        """cached_total >= 10 且 rate < min（但 success > 0）→ raise."""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        info = _stock_info(30)
        cached = [f"{2000 + i:04d}" for i in range(20)]
        _prime_ohlcv_cache(tmp_path, cached)

        good = {"2000", "2001"}  # 2/20 = 10% < 60%

        class _PartialFail:
            def fetch_stock_info(self): return info
            def fetch_delisting(self): return pd.DataFrame()
            def fetch_ohlcv(self, sym, tf, n):
                if sym in good:
                    idx = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
                    return pd.DataFrame(
                        {"close": [100.0]*30, "volume": [1000.0]*30}, index=idx
                    )
                raise RuntimeError("partial fail")

        src = _PartialFail()
        u = HistoricalUniverse(src)
        u.load()

        with pytest.raises(RuntimeError, match="success rate too low"):
            u.get_universe_at(
                datetime(2024, 1, 15),
                portfolio_config={
                    "auto_universe_size": 10,
                    "auto_universe_size_proxy_min_success": 0.60,
                },
                source=src,
            )

    def test_empty_cache_does_not_raise_but_warns(self, tmp_path, monkeypatch, caplog):
        """cached_total == 0：不 raise（保留既有語意），但也不偽稱 size_ranked。"""
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        (tmp_path / "ohlcv").mkdir()  # 空 ohlcv 目錄
        info = _stock_info(20)

        src = _FakeSource(info, fetch_behavior="none")
        u = HistoricalUniverse(src)
        u.load()

        import logging
        with caplog.at_level(logging.WARNING):
            result = u.get_universe_at(
                datetime(2024, 1, 15),
                portfolio_config={"auto_universe_size": 10},
                source=src,
            )
        assert len(result) > 0  # 不 raise
        # 必須觸發 fallback warning，表示沒有宣稱為 size_ranked
        assert any(
            "No cached OHLCV" in r.message or "No size data available" in r.message
            for r in caplog.records
        )
