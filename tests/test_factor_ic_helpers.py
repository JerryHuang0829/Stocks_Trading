"""F6 R21 backlog regression tests for scripts/_factor_ic_helpers.py.

Phase P5 Session 1 (2026-05-03): verify the 12 helpers extracted from
scripts/run_factor_ic.py preserve identical behavior + run_factor_ic.py
thin shim re-exports them correctly.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._factor_ic_helpers import (
    DEFAULT_MAX_GAP_DAYS,
    DEFAULT_MIN_OBS_PER_SYMBOL,
    MIN_UNIVERSE_SIZE,
    PANEL_DIRS_FOR_INTERSECTION,
    REGIME_SYMBOL,
    _forward_return,
    _normalise_index,
    _resolve_price_asof,
)


def test_normalise_index_strips_tz():
    """_normalise_index should strip tz + sort by date."""
    idx = pd.date_range(end="2024-12-31", periods=5, freq="B", tz="UTC")
    df = pd.DataFrame(
        {"close": [100.0, 101.0, 102.0, 103.0, 104.0]},
        index=idx[::-1],  # reversed
    )
    result = _normalise_index(df)
    assert result.index.tz is None
    assert result.index.is_monotonic_increasing


def test_resolve_price_asof_within_gap():
    """_resolve_price_asof returns price + anchor when target ≤ max_gap_days
    from last non-NaN row."""
    idx = pd.date_range(end="2024-12-31", periods=10, freq="B")
    s = pd.Series([100.0 + i for i in range(10)], index=idx)
    target = idx[-1] + pd.Timedelta(days=2)
    result = _resolve_price_asof(s, target, max_gap_days=DEFAULT_MAX_GAP_DAYS)
    assert result is not None
    price, anchor = result
    assert price == 109.0
    assert anchor == idx[-1]


def test_resolve_price_asof_beyond_gap_returns_none():
    """If gap > max_gap_days, return None (no silent backfill)."""
    idx = pd.date_range(end="2024-12-31", periods=5, freq="B")
    s = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=idx)
    target = idx[-1] + pd.Timedelta(days=10)  # > 5 day gap
    result = _resolve_price_asof(s, target, max_gap_days=5)
    assert result is None


def test_forward_return_basic():
    """_forward_return: (end_price / start_price) - 1.0."""
    idx = pd.date_range(end="2024-12-31", periods=10, freq="B")
    series = pd.Series([100.0 + i for i in range(10)], index=idx)
    close_by_symbol = {"TEST": series}
    start = idx[0]
    end = idx[-1]
    ret = _forward_return(close_by_symbol, "TEST", start, end)
    assert ret is not None
    # (109 / 100) - 1 = 0.09
    assert abs(ret - 0.09) < 1e-9


def test_forward_return_missing_symbol_returns_none():
    """Symbol not in dict → None."""
    ret = _forward_return({}, "MISSING", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"))
    assert ret is None


def test_run_factor_ic_re_exports_helpers():
    """F6 thin shim: scripts.run_factor_ic should re-export the same helpers."""
    from scripts import run_factor_ic
    # Verify helpers re-exported and identical to source
    from scripts import _factor_ic_helpers
    assert run_factor_ic._normalise_index is _factor_ic_helpers._normalise_index
    assert run_factor_ic._load_universe_ohlcv is _factor_ic_helpers._load_universe_ohlcv
    assert run_factor_ic._compute_regimes is _factor_ic_helpers._compute_regimes
    assert run_factor_ic.REGIME_SYMBOL == _factor_ic_helpers.REGIME_SYMBOL
    assert run_factor_ic.PANEL_DIRS_FOR_INTERSECTION == _factor_ic_helpers.PANEL_DIRS_FOR_INTERSECTION
