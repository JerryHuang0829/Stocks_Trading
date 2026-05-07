"""Regression: fetch_dividends must enforce backtest strict contract.

Parallel to test_cache_strict.test_market_value_raises_on_corrupt_in_backtest:
fetch_dividends bypasses _DiskCache.load() (dividends are a ``list[dict]``,
not a DataFrame), so the strict=backtest_mode path did not apply. A corrupt
dividends.pkl in backtest mode previously fell through to live TWSE scraping,
silently changing dividend-adjusted total-return backtests.
"""

from __future__ import annotations

import pickle

import pytest

from src.data import finmind as fm
from src.data.finmind import FinMindSource, _DiskCacheCorruptedError


def _make_src(tmp_path, backtest_mode: bool) -> FinMindSource:
    src = FinMindSource.__new__(FinMindSource)
    src._disk = fm._DiskCache(tmp_path)
    src._backtest_mode = backtest_mode
    return src


def test_fetch_dividends_raises_on_corrupt_in_backtest(tmp_path):
    src = _make_src(tmp_path, backtest_mode=True)
    path = src._disk._path("dividends")
    path.write_bytes(b"not a valid pickle")
    with pytest.raises(_DiskCacheCorruptedError):
        src.fetch_dividends(2020, 2024)


def test_fetch_dividends_returns_cached_in_backtest(tmp_path):
    src = _make_src(tmp_path, backtest_mode=True)
    path = src._disk._path("dividends")
    records = [{"stock_id": "2330", "ex_date": "2024-06-20", "cash_dividend": 3.0}]
    with open(path, "wb") as f:
        pickle.dump(records, f)
    got = src.fetch_dividends(2020, 2024)
    assert got == records


def test_fetch_dividends_missing_cache_in_backtest_returns_none(tmp_path, monkeypatch):
    """Missing cache in backtest: return None (valid "no dividend data")
    but must not hit the live TWSE scraper."""
    src = _make_src(tmp_path, backtest_mode=True)

    def _fail_scrape(*args, **kwargs):
        raise AssertionError("fetch_twse_dividends must not be called in backtest")

    monkeypatch.setattr("src.data.twse_scraper.fetch_twse_dividends", _fail_scrape)
    got = src.fetch_dividends(2020, 2024)
    assert got is None


def test_fetch_dividends_live_skips_corrupt(tmp_path, monkeypatch):
    """Live mode: corrupt cache logs warning, falls through to scrape."""
    src = _make_src(tmp_path, backtest_mode=False)
    path = src._disk._path("dividends")
    path.write_bytes(b"not a valid pickle")

    monkeypatch.setattr(
        "src.data.twse_scraper.fetch_twse_dividends",
        lambda *a, **k: [{"stock_id": "2330", "ex_date": "2024-06-20"}],
    )
    got = src.fetch_dividends(2020, 2024)
    assert got and got[0]["stock_id"] == "2330"
