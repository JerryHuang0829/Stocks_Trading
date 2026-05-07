"""V0.16 negative cache test — empty FinMind responses still mark done_set.

Verifies the post-V0.16 invariant: if FinMind API call succeeds (no exception)
but returns None or empty DataFrame for a stock_id, that stock is added to
done_set AS IF data were saved. This prevents wasteful re-fetch on restart for
stocks that genuinely have no quarterly_financial_statements data (small cap /
preferred / delisted / warrants).

Distinct from:
- KeyError 'data' (quota exhaustion) → does NOT mark done, force-rotates
- Connection / proxy errors → does NOT mark done, retries
- Exception with PROXY_DEATH_MARKERS substr → does NOT mark done

Mocks the FinMindSource.fetch_quarterly_financial_full + TokenRotator to
avoid network. Asserts done_set membership after a single rebuild_dataset
iteration with mocked empty response.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def mock_cache_setup(tmp_path, monkeypatch):
    """Set up a tmp cache dir + stock_info CSV + 1 OHLCV pkl + env tokens."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "stock_info").mkdir()
    (cache_dir / "ohlcv").mkdir()

    # Minimal stock_info CSV with 2 stocks
    stock_csv = cache_dir / "stock_info" / "stock_info_snapshot.csv"
    stock_csv.write_text("stock_id,industry_category\n1107,Chemical\n2330,Semiconductor\n",
                         encoding="utf-8")

    # Minimal OHLCV (avoid top_n=None branch needing turnover scoring)
    # We'll use top_n=None so this is unused, but create files for completeness.
    for sym in ["1107", "2330"]:
        ohlcv_pkl = cache_dir / "ohlcv" / f"{sym}.pkl"
        df = pd.DataFrame({
            "close": [100.0] * 60,
            "volume": [1000.0] * 60,
        }, index=pd.date_range("2024-01-01", periods=60))
        df.to_pickle(ohlcv_pkl)

    monkeypatch.setenv("FINMIND_TOKEN", "tok1_xxx")
    monkeypatch.setenv("FINMIND_TOKEN2", "tok2_xxx")
    monkeypatch.setenv("FINMIND_TOKEN3", "tok3_xxx")

    return cache_dir


def test_empty_dataframe_marks_done_set(mock_cache_setup, monkeypatch):
    """V0.16 core: empty fetch result still adds to done_set (negative cache)."""
    cache_dir = mock_cache_setup

    # Mock fetch_fn to return empty DataFrame (FinMind has no data)
    empty_df = pd.DataFrame()

    class MockSource:
        def fetch_quarterly_financial_full(self, sym, start_date=None):
            return empty_df

    monkeypatch.setattr("scripts.cache_fill_new_factors._make_source",
                        lambda rotator, cache_dir: MockSource())

    # Stub TokenRotator to avoid actual login + skip starting_with_proxy fetch
    class StubRotator:
        QUOTA_PER_SLOT = 580
        def __init__(self):
            self._current_slot = 0
            self._calls_on_current = 0
            self._current_proxy = None
            self._slots = [("tok1", None)]
        @property
        def current_label(self):
            return "Token1+Direct"
        @property
        def calls_on_current(self):
            return self._calls_on_current
        def record_call(self):
            self._calls_on_current += 1
        def record_quota_error(self):
            pass
        def get_loader(self):
            return None
        def get_backup_proxy(self):
            return None
        def patch_current_proxy(self, p):
            pass
        def start_with_proxy(self):
            return True

    monkeypatch.setattr("scripts.cache_fill_new_factors.TokenRotator", StubRotator)

    from scripts.cache_fill_new_factors import rebuild_dataset

    rebuild_dataset(
        "quarterly_financial_full",
        cache_dir,
        starting_with_proxy=False,
        top_n=None,
    )

    # Verify both stocks are in progress JSON (negative cache marker)
    progress_path = cache_dir.parent / "cache_fill_quarterly_financial_full_progress.json"
    assert progress_path.exists(), "Progress JSON should be written"
    done = set(json.loads(progress_path.read_text(encoding="utf-8")))
    assert "1107" in done, "Empty-fetch stock 1107 must be in done_set (V0.16 neg cache)"
    assert "2330" in done, "Empty-fetch stock 2330 must be in done_set (V0.16 neg cache)"


def test_none_return_marks_done_set(mock_cache_setup, monkeypatch):
    """V0.16: None return (per finmind.py empty branch) also marks done."""
    cache_dir = mock_cache_setup

    class MockSource:
        def fetch_quarterly_financial_full(self, sym, start_date=None):
            return None  # finmind.py:1232 returns None on df.empty

    monkeypatch.setattr("scripts.cache_fill_new_factors._make_source",
                        lambda rotator, cache_dir: MockSource())

    class StubRotator:
        QUOTA_PER_SLOT = 580
        def __init__(self):
            self._current_slot = 0
            self._calls_on_current = 0
            self._current_proxy = None
            self._slots = [("tok1", None)]
        @property
        def current_label(self):
            return "Token1+Direct"
        @property
        def calls_on_current(self):
            return self._calls_on_current
        def record_call(self):
            self._calls_on_current += 1
        def record_quota_error(self):
            pass
        def get_loader(self):
            return None
        def get_backup_proxy(self):
            return None
        def patch_current_proxy(self, p):
            pass
        def start_with_proxy(self):
            return True

    monkeypatch.setattr("scripts.cache_fill_new_factors.TokenRotator", StubRotator)

    from scripts.cache_fill_new_factors import rebuild_dataset

    rebuild_dataset(
        "quarterly_financial_full",
        cache_dir,
        starting_with_proxy=False,
        top_n=None,
    )

    progress_path = cache_dir.parent / "cache_fill_quarterly_financial_full_progress.json"
    done = set(json.loads(progress_path.read_text(encoding="utf-8")))
    assert "1107" in done
    assert "2330" in done


def test_proxy_error_does_NOT_mark_done(mock_cache_setup, monkeypatch):
    """V0.16 invariant: proxy/network exception is transient — must retry next run."""
    cache_dir = mock_cache_setup

    class MockSource:
        def fetch_quarterly_financial_full(self, sym, start_date=None):
            raise ConnectionError("'NoneType' object has no attribute 'json'")

    monkeypatch.setattr("scripts.cache_fill_new_factors._make_source",
                        lambda rotator, cache_dir: MockSource())

    class StubRotator:
        QUOTA_PER_SLOT = 580
        def __init__(self):
            self._current_slot = 0
            self._calls_on_current = 0
            self._current_proxy = None
            self._slots = [("tok1", None)]
        @property
        def current_label(self):
            return "Token1+Direct"
        @property
        def calls_on_current(self):
            return self._calls_on_current
        def record_call(self):
            self._calls_on_current += 1
        def record_quota_error(self):
            pass
        def get_loader(self):
            return None
        def get_backup_proxy(self):
            return None
        def patch_current_proxy(self, p):
            pass
        def start_with_proxy(self):
            return True

    monkeypatch.setattr("scripts.cache_fill_new_factors.TokenRotator", StubRotator)

    from scripts.cache_fill_new_factors import rebuild_dataset

    rebuild_dataset(
        "quarterly_financial_full",
        cache_dir,
        starting_with_proxy=False,
        top_n=None,
    )

    progress_path = cache_dir.parent / "cache_fill_quarterly_financial_full_progress.json"
    if progress_path.exists():
        done = set(json.loads(progress_path.read_text(encoding="utf-8")))
        assert "1107" not in done, (
            "Proxy errors must NOT mark done (transient, retry next run)"
        )
        assert "2330" not in done


def test_short_dataframe_below_min_rows_marks_done(mock_cache_setup, monkeypatch):
    """V0.16: df with < min_rows is also negative-cached (treated as no usable data)."""
    cache_dir = mock_cache_setup

    # min_rows for quarterly_financial_full = 12 (per DATASET_CONFIG)
    short_df = pd.DataFrame({
        "date": pd.date_range("2018-01-01", periods=5),
        "stock_id": ["1107"] * 5,
        "type": ["Revenue"] * 5,
        "value": [1.0] * 5,
        "origin_name": ["x"] * 5,
    })

    class MockSource:
        def fetch_quarterly_financial_full(self, sym, start_date=None):
            return short_df  # only 5 rows, below min_rows=12

    monkeypatch.setattr("scripts.cache_fill_new_factors._make_source",
                        lambda rotator, cache_dir: MockSource())

    class StubRotator:
        QUOTA_PER_SLOT = 580
        def __init__(self):
            self._current_slot = 0
            self._calls_on_current = 0
            self._current_proxy = None
            self._slots = [("tok1", None)]
        @property
        def current_label(self):
            return "Token1+Direct"
        @property
        def calls_on_current(self):
            return self._calls_on_current
        def record_call(self):
            self._calls_on_current += 1
        def record_quota_error(self):
            pass
        def get_loader(self):
            return None
        def get_backup_proxy(self):
            return None
        def patch_current_proxy(self, p):
            pass
        def start_with_proxy(self):
            return True

    monkeypatch.setattr("scripts.cache_fill_new_factors.TokenRotator", StubRotator)

    from scripts.cache_fill_new_factors import rebuild_dataset

    rebuild_dataset(
        "quarterly_financial_full",
        cache_dir,
        starting_with_proxy=False,
        top_n=None,
    )

    progress_path = cache_dir.parent / "cache_fill_quarterly_financial_full_progress.json"
    done = set(json.loads(progress_path.read_text(encoding="utf-8")))
    assert "1107" in done, "Below min_rows must be neg-cached (not retried)"
    assert "2330" in done
