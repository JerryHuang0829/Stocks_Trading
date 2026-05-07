"""Regression tests: transient pickle-read failures must not poison the
module-level turnover cache. Only 'file missing' earns a permanent None.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pandas as pd
import pytest

from src.data import twse_scraper
from src.data.twse_scraper import _load_turnover_series


@pytest.fixture(autouse=True)
def _clear_cache():
    twse_scraper._TURNOVER_SERIES_CACHE.clear()
    yield
    twse_scraper._TURNOVER_SERIES_CACHE.clear()


def _make_df():
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    return pd.DataFrame({"close": range(10), "volume": [1000] * 10}, index=idx)


def test_transient_read_fail_is_retried(tmp_path: pathlib.Path):
    pkl = tmp_path / "2330.pkl"
    _make_df().to_pickle(pkl)

    calls = {"n": 0}
    orig = pd.read_pickle

    def flaky(path, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated OneDrive lock")
        return orig(path, *a, **kw)

    with patch("src.data.twse_scraper.pd.read_pickle", side_effect=flaky):
        r1 = _load_turnover_series("2330", tmp_path)
        r2 = _load_turnover_series("2330", tmp_path)

    assert r1 is None, "first call should fail transiently"
    assert r2 is not None, "second call must retry (not be poisoned by first failure)"
    assert not r2.empty


def test_missing_file_is_negative_cached(tmp_path: pathlib.Path):
    r1 = _load_turnover_series("9999", tmp_path)
    r2 = _load_turnover_series("9999", tmp_path)
    assert r1 is None and r2 is None
    key = (str(tmp_path), "9999")
    assert key in twse_scraper._TURNOVER_SERIES_CACHE
    assert twse_scraper._TURNOVER_SERIES_CACHE[key] is None
