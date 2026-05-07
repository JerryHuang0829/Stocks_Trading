"""V0.22 FinMindTransientError classification + V0.16 neg-cache exclusion tests.

Verifies:
- _maybe_raise_transient classifies "ip banned" / "unexpected response" / "rate limit"
  / "503" / "502" / "504" → raise FinMindTransientError
- Non-transient exceptions (KeyError, ConnectionError without keyword) → no raise
- cache_fill_new_factors.py rebuild_dataset catches FinMindTransientError and does
  NOT add symbol to done_set (V0.16 invariant for transient errors)

Mocks the FinMindSource fetch method to inject specific exception types.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.data.finmind import FinMindTransientError, _maybe_raise_transient


# ---------------------------------------------------------------------------
# _maybe_raise_transient classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("msg", [
    "FinMind API unexpected response: ip banned",
    "FinMind API ip blocked",
    "FinMind API unexpected response",
    "rate limit exceeded",
    "rate-limit hit",
    "Service Unavailable: 503",
    "too many requests",
    "Server returned 503",
    "Server returned 502 Bad Gateway",
    "Server returned 504 Gateway Timeout",
])
def test_maybe_raise_transient_raises_on_known_keywords(msg):
    """All transient keywords must raise FinMindTransientError."""
    exc = Exception(msg)
    with pytest.raises(FinMindTransientError, match="Transient FinMind API error"):
        _maybe_raise_transient(exc, "2330", "balance_sheet")


@pytest.mark.parametrize("msg", [
    "'NoneType' object has no attribute 'json'",
    "Connection refused by remote host",
    "TimeoutError",
    "ValueError: invalid response format",
    "KeyError: 'unknown_field'",
    "Some random non-transient error",
])
def test_maybe_raise_transient_no_raise_on_other_errors(msg):
    """Non-transient exceptions must NOT raise — caller continues to return None."""
    exc = Exception(msg)
    # Should not raise
    _maybe_raise_transient(exc, "2330", "balance_sheet")


def test_maybe_raise_transient_case_insensitive():
    """Keyword matching is case-insensitive (msg.lower() preprocessing)."""
    exc = Exception("FinMind API: IP BANNED for 2330")
    with pytest.raises(FinMindTransientError):
        _maybe_raise_transient(exc, "2330", "balance_sheet")


# ---------------------------------------------------------------------------
# cache_fill_new_factors.py: V0.22 — transient errors do NOT mark done
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_cache_setup(tmp_path, monkeypatch):
    """Tmp cache dir + minimal stock_info CSV + 2 OHLCV pkls + env tokens."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "stock_info").mkdir()
    (cache_dir / "ohlcv").mkdir()
    stock_csv = cache_dir / "stock_info" / "stock_info_snapshot.csv"
    stock_csv.write_text("stock_id,industry_category\n2330,Semiconductor\n2317,Electronics\n",
                         encoding="utf-8")
    for sym in ["2330", "2317"]:
        ohlcv_pkl = cache_dir / "ohlcv" / f"{sym}.pkl"
        df = pd.DataFrame({"close": [500.0]*60, "volume": [10000.0]*60},
                          index=pd.date_range("2024-01-01", periods=60))
        df.to_pickle(ohlcv_pkl)
    monkeypatch.setenv("FINMIND_TOKEN", "tok1")
    monkeypatch.setenv("FINMIND_TOKEN2", "tok2")
    monkeypatch.setenv("FINMIND_TOKEN3", "tok3")
    return cache_dir


def test_transient_error_does_NOT_mark_done(mock_cache_setup, monkeypatch):
    """V0.22 critical invariant: ip banned / transient errors do NOT add to
    done_set.

    Reproduces the 2026-05-06 bug where TSMC was falsely neg-cached due to
    "ip banned" being treated as legitimate empty data.
    """
    cache_dir = mock_cache_setup

    class MockSource:
        def fetch_quarterly_financial_full(self, sym, start_date=None):
            raise FinMindTransientError(
                f"Transient FinMind API error on balance_sheet for {sym}: "
                f"FinMind API unexpected response: ip banned"
            )

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
        def current_label(self): return "Token1+Direct"
        @property
        def calls_on_current(self): return self._calls_on_current
        def record_call(self): self._calls_on_current += 1
        def record_quota_error(self): pass
        def get_loader(self): return None
        def get_backup_proxy(self): return None
        def patch_current_proxy(self, p): pass
        def start_with_proxy(self): return True

    monkeypatch.setattr("scripts.cache_fill_new_factors.TokenRotator", StubRotator)

    from scripts.cache_fill_new_factors import rebuild_dataset
    rebuild_dataset(
        "quarterly_financial_full", cache_dir,
        starting_with_proxy=False, top_n=None,
    )

    progress_path = cache_dir.parent / "cache_fill_quarterly_financial_full_progress.json"
    if progress_path.exists():
        done = set(json.loads(progress_path.read_text(encoding="utf-8")))
        assert "2330" not in done, (
            "V0.22 invariant: transient error must NOT mark TSMC as done"
        )
        assert "2317" not in done, (
            "V0.22 invariant: transient error must NOT mark 鴻海 as done"
        )
