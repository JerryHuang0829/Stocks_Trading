"""Unit tests for scripts.run_factor_ic helpers (P1-新1 / P1-新2 / follow-up-4).

Focuses on the pure helpers `_resolve_price_asof` and
`_compute_intersection_universe`; the CLI `main()` requires a live cache and
is covered by the IC smoke run instead.

Note (external audit Round 3.5 fix, 2026-04-17)
--------------------------------------
Importing `scripts.run_factor_ic` pulls `src.strategy.indicators` which
`import pandas_ta as ta`. On minimal Python installs (external audit sandbox / bare
system Python) that import hangs for ~3 minutes before raising.

Earlier version tried ``try: import pandas_ta`` first and only stubbed on
exception — external audit showed the `try:` branch never returns in those envs, so
the fallback stub never runs.

New policy: **unconditional pre-stub before any import that touches
run_factor_ic**. `sys.modules.setdefault` means we only stub if pandas_ta
hasn't been resolved yet; hosts that have a usable pandas_ta (Docker,
conda env "quant") will already have it imported by pytest's top-level
collection phase or by an earlier test, so the setdefault is a no-op.
"""

from __future__ import annotations

import sys
from types import ModuleType

# Unconditional pre-stub — see module docstring for rationale.
# `setdefault` is the critical primitive: it cannot override a real
# `pandas_ta` if one is already in `sys.modules`, so this remains safe on
# Docker / conda env "quant" hosts.
sys.modules.setdefault("pandas_ta", ModuleType("pandas_ta"))

import pandas as pd
import pytest

from scripts.run_factor_ic import (
    DEFAULT_MAX_GAP_DAYS,
    _compute_intersection_universe,
    _resolve_price_asof,
)


# ---------------------------- _resolve_price_asof ----------------------------


def test_resolve_price_asof_returns_latest_on_trading_day():
    dates = pd.bdate_range("2024-01-02", periods=20)
    series = pd.Series(range(20), index=dates, dtype=float)
    target = dates[10]
    resolved = _resolve_price_asof(series, target)
    assert resolved is not None
    price, anchor = resolved
    assert anchor == target
    assert price == 10.0


def test_resolve_price_asof_falls_back_to_previous_trading_day_within_gap():
    dates = pd.bdate_range("2024-01-02", periods=20)
    series = pd.Series(range(20), index=dates, dtype=float)
    # Target is 2 calendar days after the LAST trading day — no later prints,
    # so resolution must anchor on that last day and the small gap is tolerated.
    target = dates[-1] + pd.Timedelta(days=2)
    resolved = _resolve_price_asof(series, target, max_gap_days=3)
    assert resolved is not None
    _, anchor = resolved
    assert anchor == dates[-1]


def test_resolve_price_asof_rejects_gap_exceeding_tolerance():
    dates = pd.bdate_range("2024-01-02", periods=10)
    series = pd.Series(range(10), index=dates, dtype=float)
    # Target 10 days after last available → exceeds default max_gap_days=5
    target = dates[-1] + pd.Timedelta(days=10)
    resolved = _resolve_price_asof(series, target, max_gap_days=DEFAULT_MAX_GAP_DAYS)
    assert resolved is None


def test_resolve_price_asof_empty_series_returns_none():
    empty = pd.Series([], dtype=float)
    assert _resolve_price_asof(empty, pd.Timestamp("2024-01-10")) is None


def test_resolve_price_asof_handles_nan_prefix():
    dates = pd.bdate_range("2024-01-02", periods=10)
    values = [None] * 5 + list(range(5, 10))
    series = pd.Series(values, index=dates, dtype=float)
    # All observations up to day 4 are NaN → dropna must pick day 5 (index 5)
    target = dates[4]
    resolved = _resolve_price_asof(series, target, max_gap_days=DEFAULT_MAX_GAP_DAYS)
    assert resolved is None  # no valid obs at or before day 4

    target = dates[6]
    resolved = _resolve_price_asof(series, target, max_gap_days=DEFAULT_MAX_GAP_DAYS)
    assert resolved is not None
    _, anchor = resolved
    assert anchor == dates[6]


# ---------------------------- _compute_intersection_universe ----------------------------


def _write_panel(tmp_dir, name: str, symbols: dict[str, int]) -> None:
    """Write trivial pickles under tmp_dir/<name>/<symbol>.pkl with given row counts."""
    panel = tmp_dir / name
    panel.mkdir(parents=True, exist_ok=True)
    for symbol, n in symbols.items():
        dates = pd.bdate_range("2020-01-01", periods=max(n, 1))
        df = pd.DataFrame({"x": list(range(n))}, index=dates[:n])
        df.to_pickle(panel / f"{symbol}.pkl")


def test_intersection_universe_basic(tmp_path):
    # 60 symbols wide enough for min_universe_size in each panel.
    base = {f"{1000 + i}": 300 for i in range(60)}
    _write_panel(tmp_path, "ohlcv", base)
    _write_panel(tmp_path, "revenue", base)
    # Drop 10 symbols from margin_short → intersection loses them.
    margin_subset = {s: 300 for s in list(base)[:50]}
    _write_panel(tmp_path, "margin_short", margin_subset)
    out = _compute_intersection_universe(
        tmp_path,
        panel_names=("ohlcv", "revenue", "margin_short"),
        min_obs_per_symbol=250,
    )
    assert len(out) == 50
    assert all(s in set(margin_subset) for s in out)


def test_intersection_universe_skips_small_panels(tmp_path):
    """Panels whose qualifying universe is below MIN_UNIVERSE_SIZE are skipped."""
    base = {f"{1000 + i}": 300 for i in range(60)}
    _write_panel(tmp_path, "ohlcv", base)
    _write_panel(tmp_path, "revenue", base)
    # Tiny margin_short cache (under MIN_UNIVERSE_SIZE=50) — must be excluded.
    _write_panel(tmp_path, "margin_short", {"1001": 300, "1002": 300})
    out = _compute_intersection_universe(
        tmp_path,
        panel_names=("ohlcv", "revenue", "margin_short"),
        min_obs_per_symbol=250,
    )
    # Intersection only considers OHLCV + revenue (margin dropped)
    assert len(out) == 60


def test_intersection_universe_filters_by_min_obs(tmp_path):
    base_small = {f"{1000 + i}": 50 for i in range(60)}   # below threshold
    base_ok = {f"{2000 + i}": 400 for i in range(60)}
    combined = {**base_small, **base_ok}
    _write_panel(tmp_path, "ohlcv", combined)
    _write_panel(tmp_path, "revenue", combined)
    out = _compute_intersection_universe(
        tmp_path,
        panel_names=("ohlcv", "revenue"),
        min_obs_per_symbol=250,
    )
    # Only symbols with >=250 rows in BOTH panels remain.
    assert len(out) == 60
    assert all(s.startswith("2") for s in out)


def test_intersection_universe_ignores_underscore_and_non_digit(tmp_path):
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir(parents=True)
    dates = pd.bdate_range("2020-01-01", periods=300)
    pd.DataFrame({"x": range(300)}, index=dates).to_pickle(ohlcv / "2330.pkl")
    # Global snapshot & non-4-digit files must be skipped.
    pd.DataFrame({"x": range(300)}, index=dates).to_pickle(ohlcv / "_global.pkl")
    pd.DataFrame({"x": range(300)}, index=dates).to_pickle(ohlcv / "ABCD.pkl")
    out = _compute_intersection_universe(
        tmp_path,
        panel_names=("ohlcv",),
        min_obs_per_symbol=250,
    )
    # Only "2330" qualifies; but that's below MIN_UNIVERSE_SIZE so the panel is
    # dropped entirely → empty result.
    assert out == []


def test_intersection_universe_empty_when_no_panels(tmp_path):
    out = _compute_intersection_universe(
        tmp_path, panel_names=("ohlcv", "revenue"),
    )
    assert out == []


# ---------------------------- follow-up-4: per-panel min_obs ----------------------------


def test_intersection_universe_per_panel_min_obs_keeps_quarterly(tmp_path):
    """follow-up-4 (audit-confirmed): quarterly_eps panel must not be dropped.

    Pre-fix: uniform `min_obs_per_symbol=250` applied to every panel. A
    quarterly panel has ~28 rows per symbol (7Y × 4Q), so every symbol would
    fall below the threshold → panel dropped from intersection → PEAD factor
    universe silently collapses to ∅.

    Post-fix: pass a dict `{quarterly_eps: 12, default: 250}` and the panel
    qualifies.
    """
    # Daily panels: 300 rows/symbol clears any reasonable threshold.
    daily = {f"{1000 + i}": 300 for i in range(60)}
    _write_panel(tmp_path, "ohlcv", daily)
    _write_panel(tmp_path, "revenue", daily)
    # Quarterly panel: only 28 rows/symbol — would fail at 250 threshold.
    quarterly = {f"{1000 + i}": 28 for i in range(60)}
    _write_panel(tmp_path, "quarterly_eps", quarterly)

    # Legacy behaviour: uniform 250 → quarterly panel dropped
    legacy = _compute_intersection_universe(
        tmp_path,
        panel_names=("ohlcv", "revenue", "quarterly_eps"),
        min_obs_per_symbol=250,
    )
    assert len(legacy) == 60  # quarterly is SKIPPED (tiny qualifying set) not intersected

    # New behaviour: per-panel dict lets the quarterly panel in
    per_panel = _compute_intersection_universe(
        tmp_path,
        panel_names=("ohlcv", "revenue", "quarterly_eps"),
        min_obs_per_symbol={"ohlcv": 250, "revenue": 250, "quarterly_eps": 12},
    )
    assert len(per_panel) == 60


def test_intersection_universe_defaults_to_yaml_per_panel(tmp_path, monkeypatch):
    """follow-up-4: `min_obs_per_symbol=None` triggers yaml-based per-panel lookup.

    We stub the thresholds cache to force a deterministic per-panel map so
    the test is self-contained (doesn't depend on repo yaml contents).
    """
    from src.utils import thresholds

    fake = {
        "universe": {
            "min_obs_per_symbol": {
                "default": 250,
                "ohlcv": 250,
                "quarterly_eps": 12,
            },
            "min_universe_size": 10,  # lower so the tiny test universe qualifies
        },
    }
    monkeypatch.setattr(thresholds, "_cache", fake, raising=False)
    monkeypatch.setattr(thresholds, "_cache_source", "test-stub", raising=False)

    daily = {f"{1000 + i}": 300 for i in range(15)}
    quarterly = {f"{1000 + i}": 28 for i in range(15)}
    _write_panel(tmp_path, "ohlcv", daily)
    _write_panel(tmp_path, "quarterly_eps", quarterly)

    out = _compute_intersection_universe(
        tmp_path,
        panel_names=("ohlcv", "quarterly_eps"),
        min_obs_per_symbol=None,  # trigger yaml lookup
    )
    assert len(out) == 15
