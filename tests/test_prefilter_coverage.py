"""Regression tests for pre-filter coverage hard-fail.

Prevents the silent-degradation failure mode where a partial turnover dict
(e.g. early historical date with cold cache) would still produce a top-N
selection, silently distorting the universe. See 2026-04-15 alpha illusion.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from src.backtest.universe import HistoricalUniverse


def _make_stock_info(n: int = 1000) -> pd.DataFrame:
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
    def __init__(self, stock_info: pd.DataFrame):
        self._info = stock_info

    def fetch_stock_info(self):
        return self._info

    def fetch_delisting(self):
        return pd.DataFrame()

    def fetch_ohlcv(self, *args, **kwargs):
        return None


class TestPreFilterCoverage:

    def test_low_coverage_raises(self):
        """Turnover covering < 80% of working set must raise."""
        info = _make_stock_info(1000)
        src = _FakeSource(info)
        u = HistoricalUniverse(src)
        u.load()

        # Return turnover for only 100 / 1000 stocks (10% coverage)
        partial = {f"{2000 + i:04d}": 1e9 for i in range(100)}
        with patch(
            "src.data.twse_scraper.fetch_combined_turnover", return_value=partial
        ):
            with pytest.raises(RuntimeError, match="coverage too low"):
                u.get_universe_at(
                    datetime(2024, 1, 15),
                    portfolio_config={"auto_universe_pre_filter_size": 400},
                    source=src,
                )

    def test_empty_turnover_raises(self):
        """Empty turnover must raise (was previously silent fallback)."""
        info = _make_stock_info(1000)
        src = _FakeSource(info)
        u = HistoricalUniverse(src)
        u.load()

        with patch(
            "src.data.twse_scraper.fetch_combined_turnover", return_value={}
        ):
            with pytest.raises(RuntimeError, match="turnover unavailable"):
                u.get_universe_at(
                    datetime(2024, 1, 15),
                    portfolio_config={"auto_universe_pre_filter_size": 400},
                    source=src,
                )

    def test_high_coverage_passes(self, tmp_path, monkeypatch):
        """Coverage >= 80% should proceed without raising."""
        # 隔離 DATA_CACHE_DIR 到空目錄，避免進 size-proxy loop 時讀到真 cache
        # 並被新加的 size-proxy success-rate guard 觸發。
        monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
        (tmp_path / "ohlcv").mkdir()

        info = _make_stock_info(1000)
        src = _FakeSource(info)
        u = HistoricalUniverse(src)
        u.load()

        # 900 / 1000 = 90% coverage
        good = {f"{2000 + i:04d}": float(1000 - i) for i in range(900)}
        with patch(
            "src.data.twse_scraper.fetch_combined_turnover", return_value=good
        ):
            # Should not raise — may return small list due to no OHLCV, that's fine
            u.get_universe_at(
                datetime(2024, 1, 15),
                portfolio_config={"auto_universe_pre_filter_size": 400},
                source=src,
            )
