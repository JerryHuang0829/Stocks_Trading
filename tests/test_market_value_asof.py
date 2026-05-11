"""Mutation tests for `_load_market_value_panel` + `_market_value_asof` (P0-A 2026-05-10).

Verifies the PIT-correct as-of lookup replaces the legacy `_load_market_value()`
which returned latest mv across all rebalance dates (PIT violation per R26).
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts._factor_ic_helpers import (
    _load_market_value_panel,
    _market_value_asof,
    _load_issued_capital_panel,
    _issued_capital_asof,
)


def _make_mv_cache(tmp_path) -> pd.DataFrame:
    """Build a simple market_value cache with two stocks across 5 dates."""
    rows = []
    for stock_id, base in [("1101", 1e10), ("2330", 1e12)]:
        for i, date in enumerate(pd.date_range("2020-01-01", periods=5, freq="MS")):
            rows.append({
                "stock_id": stock_id,
                "date": date,
                "market_value": float(base * (1 + 0.1 * i)),
                "issued_shares": float(1e8 * (1 + 0.02 * i)),
            })
    df = pd.DataFrame(rows)
    cache_dir = tmp_path / "cache"
    (cache_dir / "market_value").mkdir(parents=True)
    df.to_pickle(cache_dir / "market_value" / "_global.pkl")
    return cache_dir, df


def test_market_value_asof_returns_correct_snapshot(tmp_path):
    """As-of lookup must return mv at or before target_date, never future."""
    cache_dir, df = _make_mv_cache(tmp_path)
    panel = _load_market_value_panel(cache_dir)

    # 2020-01-15 is between 2020-01-01 and 2020-02-01 → must take 2020-01-01 value
    asof_jan = _market_value_asof(panel, pd.Timestamp("2020-01-15"))
    assert asof_jan["1101"] == 1e10  # base, not 1.1×base
    assert asof_jan["2330"] == 1e12

    # 2020-03-15 → must take 2020-03-01 value (i=2, factor 1.20)
    asof_mar = _market_value_asof(panel, pd.Timestamp("2020-03-15"))
    assert asof_mar["1101"] == pytest.approx(1.2e10)
    assert asof_mar["2330"] == pytest.approx(1.2e12)

    # 2020-06-15 → past last record (2020-05-01, i=4, factor 1.40) → take last
    asof_after = _market_value_asof(panel, pd.Timestamp("2020-06-15"))
    assert asof_after["1101"] == pytest.approx(1.4e10)


def test_market_value_asof_drops_symbols_with_no_prior_record(tmp_path):
    """If a symbol has no record at or before target_date, drop it (not pass latest)."""
    cache_dir, df = _make_mv_cache(tmp_path)
    panel = _load_market_value_panel(cache_dir)

    # 2019-12-15 → before any record → empty dict
    asof_pre = _market_value_asof(panel, pd.Timestamp("2019-12-15"))
    assert asof_pre == {}, f"Expected empty for pre-history target, got {asof_pre}"


def test_market_value_asof_mutation_against_legacy_keep_last(tmp_path):
    """Mutation test: if someone reverts to `keep="last"` + global drop_duplicates,
    a 2020 query would silently see the 2020-05 value (latest in cache). The new
    asof correctly filters by date <= target."""
    cache_dir, df = _make_mv_cache(tmp_path)
    panel = _load_market_value_panel(cache_dir)

    asof_jan = _market_value_asof(panel, pd.Timestamp("2020-01-15"))
    legacy_latest = (
        df.sort_values("date")
        .drop_duplicates("stock_id", keep="last")
        .set_index("stock_id")["market_value"]
        .to_dict()
    )

    # The two MUST differ for PIT correctness; if they match, the as-of lookup
    # collapsed to legacy behavior (= a regression to PIT violation).
    assert asof_jan["1101"] != legacy_latest["1101"], (
        f"as-of lookup matches legacy keep=last: {asof_jan['1101']} vs "
        f"{legacy_latest['1101']} — PIT correctness regressed"
    )


def test_market_value_panel_handles_empty_cache(tmp_path):
    cache_dir = tmp_path / "empty_cache"
    cache_dir.mkdir()
    panel = _load_market_value_panel(cache_dir)
    assert panel.empty
    assert _market_value_asof(panel, pd.Timestamp("2020-01-01")) == {}


def test_issued_capital_panel_pit_lookup(tmp_path):
    """P1-A: same PIT pattern for issued_capital."""
    cache_dir, df = _make_mv_cache(tmp_path)
    panel = _load_issued_capital_panel(cache_dir)

    # 2020-01-15 → take 2020-01-01 value (i=0, factor 1.00)
    asof_jan = _issued_capital_asof(panel, pd.Timestamp("2020-01-15"))
    assert asof_jan["1101"] == pytest.approx(1e8)

    # 2020-03-15 → take 2020-03-01 (i=2, factor 1.04)
    asof_mar = _issued_capital_asof(panel, pd.Timestamp("2020-03-15"))
    assert asof_mar["1101"] == pytest.approx(1.04e8)


def test_load_market_value_legacy_emits_deprecation_warning(tmp_path):
    """The legacy entry point should warn so callers migrate."""
    from scripts._factor_ic_helpers import _load_market_value

    cache_dir, _ = _make_mv_cache(tmp_path)
    with pytest.warns(DeprecationWarning, match="PIT violation"):
        result = _load_market_value(cache_dir)
    assert result  # still returns data for backward compat


def test_load_issued_capital_legacy_emits_deprecation_warning(tmp_path):
    from scripts._factor_ic_helpers import _load_issued_capital

    cache_dir, _ = _make_mv_cache(tmp_path)
    with pytest.warns(DeprecationWarning, match="PIT violation"):
        result = _load_issued_capital(cache_dir)
    assert result
