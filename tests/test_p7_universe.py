"""P7 direct tests: TWSE issued capital, market_value computation, size proxy universe.

These tests verify the P7 architecture decision:
- Universe selection uses close×volume (size proxy), NOT market_value
- market_value is monitoring-only (TWSE shares × OHLCV close)
- fetch_twse_issued_capital() correctly fetches TWSE + TPEX data
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Test 1: fetch_twse_issued_capital()
# ---------------------------------------------------------------------------

class TestFetchTwseIssuedCapital:

    def test_parse_twse_format(self):
        """_parse_company_profile handles TWSE Chinese field names."""
        from src.data.twse_scraper import _parse_company_profile

        data = [
            {"公司代號": "2330", "實收資本額(元)": "259325245210", "已發行普通股數或TDR原股發行股數": "25932524521"},
            {"公司代號": "2317", "實收資本額(元)": "139642222940", "已發行普通股數或TDR原股發行股數": "13964222294"},
        ]
        result = _parse_company_profile(data)
        assert len(result) == 2
        assert result["2330"] == 25932524521
        assert result["2317"] == 13964222294

    def test_parse_tpex_format(self):
        """_parse_company_profile handles TPEX English field names."""
        from src.data.twse_scraper import _parse_company_profile

        data = [
            {"SecuritiesCompanyCode": "5274", "Paidin.Capital.NTDollar": "378026850", "IssueShares": "37802685"},
            {"SecuritiesCompanyCode": "6547", "Paidin.Capital.NTDollar": "3287490500", "IssueShares": "328749050"},
        ]
        result = _parse_company_profile(data)
        assert len(result) == 2
        assert result["5274"] == 37802685
        assert result["6547"] == 328749050

    def test_parse_empty_data(self):
        """_parse_company_profile returns empty dict for empty data."""
        from src.data.twse_scraper import _parse_company_profile

        assert _parse_company_profile([]) == {}

    def test_parse_fallback_to_capital(self):
        """When IssueShares is missing, falls back to capital / 10."""
        from src.data.twse_scraper import _parse_company_profile

        data = [
            {"公司代號": "9999", "實收資本額(元)": "1000000000"},
        ]
        result = _parse_company_profile(data)
        assert result["9999"] == 100000000  # 1B / 10

    def test_fetch_twse_issued_capital_http_failure(self):
        """Returns empty dict when TWSE API fails."""
        from src.data.twse_scraper import fetch_twse_issued_capital

        with patch("src.data.twse_scraper.requests.get", side_effect=Exception("network")):
            result = fetch_twse_issued_capital()
            assert result == {}


# ---------------------------------------------------------------------------
# Test 2: _compute_market_value_from_twse()
# ---------------------------------------------------------------------------

class TestComputeMarketValueFromTwse:

    def test_computes_market_value_correctly(self, tmp_path):
        """Market value = shares × close price, with correct columns."""
        from src.data.finmind import FinMindSource, _DiskCache

        # Setup: create OHLCV cache with known data
        ohlcv_dir = tmp_path / "ohlcv"
        ohlcv_dir.mkdir()

        dates = pd.date_range("2024-01-01", "2024-03-31", freq="B", tz="UTC")
        ohlcv = pd.DataFrame({
            "open": [100.0] * len(dates),
            "high": [105.0] * len(dates),
            "low": [95.0] * len(dates),
            "close": [100.0] * len(dates),
            "volume": [1000000] * len(dates),
        }, index=dates)
        ohlcv.to_pickle(ohlcv_dir / "2330.pkl")

        # Create FinMindSource with mocked internals
        source = FinMindSource.__new__(FinMindSource)
        source._disk = _DiskCache(tmp_path)
        source._backtest_mode = False

        # Mock TWSE to return known shares
        with patch("src.data.twse_scraper.fetch_twse_issued_capital", return_value={"2330": 25_000_000_000}):
            result = source._compute_market_value_from_twse()

        assert result is not None
        assert set(result.columns) == {"stock_id", "date", "market_value"}
        assert (result["stock_id"] == "2330").all()
        # 100.0 close × 25B shares = 2.5T
        assert (result["market_value"] == 100.0 * 25_000_000_000).all()
        # Dates should be timezone-naive
        assert result["date"].dt.tz is None

    def test_returns_none_when_twse_fails(self, tmp_path):
        """Returns None if TWSE issued capital fetch fails."""
        from src.data.finmind import FinMindSource, _DiskCache

        source = FinMindSource.__new__(FinMindSource)
        source._disk = _DiskCache(tmp_path)
        source._backtest_mode = False

        with patch("src.data.twse_scraper.fetch_twse_issued_capital", return_value={}):
            result = source._compute_market_value_from_twse()

        assert result is None

    def test_returns_none_when_no_ohlcv_cache(self, tmp_path):
        """Returns None if OHLCV cache directory doesn't exist."""
        from src.data.finmind import FinMindSource, _DiskCache

        # Don't create ohlcv dir
        source = FinMindSource.__new__(FinMindSource)
        source._disk = _DiskCache(tmp_path)
        source._backtest_mode = False

        with patch("src.data.twse_scraper.fetch_twse_issued_capital", return_value={"2330": 100}):
            result = source._compute_market_value_from_twse()

        assert result is None


# ---------------------------------------------------------------------------
# Test 3: build_tw_stock_universe uses size proxy, NOT market_value
# ---------------------------------------------------------------------------

class TestUniverseSizeProxyPath:

    def test_build_universe_does_not_call_fetch_market_value(self):
        """build_tw_stock_universe source code must not reference fetch_market_value."""
        from src.portfolio.tw_stock import build_tw_stock_universe

        source_code = inspect.getsource(build_tw_stock_universe)
        assert "fetch_market_value" not in source_code

    def test_build_universe_calls_size_proxy(self):
        """build_tw_stock_universe must call _prepare_auto_universe_by_size_proxy."""
        from src.portfolio.tw_stock import build_tw_stock_universe

        source_code = inspect.getsource(build_tw_stock_universe)
        assert "_prepare_auto_universe_by_size_proxy" in source_code

    def test_old_prepare_auto_universe_removed(self):
        """_prepare_auto_universe (market_value version) must not exist."""
        from src.portfolio import tw_stock

        assert not hasattr(tw_stock, "_prepare_auto_universe"), \
            "_prepare_auto_universe still exists — should have been deleted in P7"

    def test_universe_py_does_not_rank_by_market_value(self):
        """universe.py get_universe_at must not call fetch_market_value for ranking."""
        from src.backtest.universe import HistoricalUniverse

        source_code = inspect.getsource(HistoricalUniverse.get_universe_at)
        # Should not have any active fetch_market_value calls (comments are OK)
        active_lines = [
            line.strip()
            for line in source_code.split("\n")
            if "fetch_market_value" in line and not line.strip().startswith("#")
        ]
        assert len(active_lines) == 0, f"universe.py still calls fetch_market_value: {active_lines}"
