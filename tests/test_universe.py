"""Tests for HistoricalUniverse edge cases."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from src.backtest.universe import HistoricalUniverse
except ImportError:
    pytest.skip(
        "Cannot import HistoricalUniverse (likely missing dependency on Windows); run in Docker",
        allow_module_level=True,
    )


class _FakeSource:
    """Minimal fake source for universe tests."""

    def __init__(self, stock_info=None, delisting=None):
        self._stock_info = stock_info
        self._delisting = delisting

    def fetch_stock_info(self):
        return self._stock_info

    def fetch_delisting(self):
        return self._delisting


class TestMissingStockId:
    """Regression: stock_info without stock_id column must not raise KeyError."""

    def test_no_stock_id_returns_empty(self, caplog):
        """stock_info 缺少 stock_id 欄位時應 warning 並回傳空 list，不丟例外。"""
        bad_info = pd.DataFrame({
            "date": ["2024-01-01", "2024-02-01"],
            "stock_name": ["A", "B"],
            "industry_category": ["電子工業", "塑膠工業"],
        })
        source = _FakeSource(stock_info=bad_info, delisting=pd.DataFrame())
        universe = HistoricalUniverse(source)
        universe._stock_info = bad_info  # bypass load() which requires real API

        with caplog.at_level(logging.WARNING):
            result = universe.get_universe_at(
                datetime(2024, 6, 1),
                {"exclude_etf": True, "auto_universe_markets": ["twse"]},
            )

        assert result == []
        assert "stock_id" in caplog.text

    def test_normal_stock_info_works(self):
        """正常 stock_info 應正常回傳 universe。"""
        info = pd.DataFrame({
            "stock_id": ["2330", "2317", "1301"],
            "stock_name": ["台積電", "鴻海", "台塑"],
            "date": ["2024-01-01", "2024-01-01", "2024-01-01"],
            "industry_category": ["半導體業", "電子工業", "塑膠工業"],
            "type": ["twse", "twse", "twse"],
        })
        source = _FakeSource(stock_info=info, delisting=pd.DataFrame())
        universe = HistoricalUniverse(source)
        universe._stock_info = info

        result = universe.get_universe_at(
            datetime(2024, 6, 1),
            {"exclude_etf": True, "auto_universe_markets": ["twse"]},
        )

        symbols = [r["symbol"] for r in result]
        assert "2330" in symbols
        assert len(result) == 3
