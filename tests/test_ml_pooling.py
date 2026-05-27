"""Tests for src/analysis/ml_pooling.py — v5.0 Step 4a pooling pipeline.

Per pre-reg §5 + §13 LOCK:
- Schema strict (9 cols)
- 2025 strict OOS guarded on as_of AND label_end (Codex v5.0 R2 P0 fix)
- Complete-case drop with diagnostics
- Forward return stale-price guard
- Top-decile per-period independent

Test plan covers:
  T1-T3  Helpers (_resolve_price_asof / _compute_forward_return / top_decile)
  T4-T6  Schema validation (strict match)
  T7-T9  OOS boundary guards (as_of + label_end Codex P0 fix)
  T10-T12 Builder happy path + diagnostics
  T13-T15 Mutation tests (audit-style; ensure tests catch real bugs)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_pooling import (  # noqa: E402
    DEFAULT_TOP_DECILE_THRESHOLD,
    PoolingConfig,
    PoolingDiagnostics,
    _compute_forward_return,
    _resolve_price_asof,
    _validate_oos_boundary,
    _validate_panel_schema,
    build_training_matrix,
    compute_top_decile_labels,
)


# ===========================================================================
# Fixtures
# ===========================================================================
def _make_close_series(start: str, n_days: int, base: float = 100.0,
                       drift: float = 0.001) -> pd.Series:
    idx = pd.date_range(start, periods=n_days, freq="B")
    prices = base * np.exp(drift * np.arange(n_days))
    return pd.Series(prices, index=idx)


def _make_close_with_seed(start: str, n_days: int, seed: int) -> pd.Series:
    """Random-walk close series with per-symbol seeded drift (for diverse forward returns)."""
    idx = pd.date_range(start, periods=n_days, freq="B")
    rng = np.random.RandomState(seed)
    rets = rng.normal(0.001 + seed * 0.0003, 0.01, n_days)
    return pd.Series(100.0 * np.exp(np.cumsum(rets)), index=idx)


def _make_feature_panel(symbols: list[str], feature_names: list[str],
                       seed: int = 0) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    data = rng.randn(len(symbols), len(feature_names))
    return pd.DataFrame(data, index=symbols, columns=feature_names)


# ===========================================================================
# T1. _resolve_price_asof — stale-price guard
# ===========================================================================
def test_resolve_price_asof_returns_latest_within_gap():
    s = _make_close_series("2024-01-01", 20)
    target = s.index[-1]
    result = _resolve_price_asof(s, target, max_gap_days=5)
    assert result is not None
    price, anchor = result
    assert price == pytest.approx(float(s.iloc[-1]))
    assert anchor == target


def test_resolve_price_asof_returns_none_when_stale():
    """Price last seen 10 days ago, gap=5 → None."""
    s = _make_close_series("2024-01-01", 5)
    target = s.index[-1] + pd.Timedelta(days=20)
    result = _resolve_price_asof(s, target, max_gap_days=5)
    assert result is None


def test_resolve_price_asof_returns_none_for_empty():
    assert _resolve_price_asof(None, pd.Timestamp("2024-01-01"), 5) is None
    assert _resolve_price_asof(pd.Series(dtype=float),
                               pd.Timestamp("2024-01-01"), 5) is None


def test_resolve_price_asof_skips_non_positive():
    s = pd.Series([100.0, 101.0, 0.0, 99.0],
                  index=pd.date_range("2024-01-01", periods=4))
    # Target on the 0.0 day; should return None
    result = _resolve_price_asof(s, s.index[2], max_gap_days=5)
    assert result is None


# ===========================================================================
# T2. _compute_forward_return
# ===========================================================================
def test_forward_return_basic():
    s = pd.Series([100.0, 110.0],
                  index=pd.date_range("2024-01-01", periods=2, freq="B"))
    r = _compute_forward_return(s, s.index[0], s.index[1], max_gap_days=5)
    assert r == pytest.approx(0.10)


def test_forward_return_none_when_either_anchor_missing():
    s = pd.Series([100.0], index=[pd.Timestamp("2024-01-01")])
    r = _compute_forward_return(
        s, pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"),
        max_gap_days=5,
    )
    # End anchor 2024-02-01 has no data within 5 days → None
    assert r is None


# ===========================================================================
# T3. compute_top_decile_labels
# ===========================================================================
def test_top_decile_labels_basic():
    # 10 stocks ranked, top 10% = 1 stock above 0.9 quantile
    fwd = pd.Series(list(range(10)), index=[f"S{i}" for i in range(10)])
    labels = compute_top_decile_labels(fwd, threshold=0.9)
    assert labels.sum() == 1
    assert labels["S9"] == 1   # highest return


def test_top_decile_labels_per_period_independent():
    """Two periods of the same stocks must rank independently."""
    period_a = pd.Series([1.0, 5.0, 3.0], index=["A", "B", "C"])
    period_b = pd.Series([10.0, 2.0, 4.0], index=["A", "B", "C"])
    labels_a = compute_top_decile_labels(period_a, threshold=0.66)
    labels_b = compute_top_decile_labels(period_b, threshold=0.66)
    # In period_a: B is top, in period_b: A is top
    assert labels_a["B"] == 1 and labels_b["A"] == 1
    # Each period ranks independently
    assert labels_a["A"] == 0 and labels_b["B"] == 0


def test_top_decile_labels_empty():
    out = compute_top_decile_labels(pd.Series(dtype=float))
    assert out.empty


# ===========================================================================
# T4. _validate_panel_schema (strict)
# ===========================================================================
def test_schema_validation_passes_exact_match():
    panel = pd.DataFrame({"a": [1.0], "b": [2.0]}, index=["S1"])
    _validate_panel_schema(panel, ["a", "b"], pd.Timestamp("2024-01-01"))


def test_schema_validation_raises_on_missing_column():
    panel = pd.DataFrame({"a": [1.0]}, index=["S1"])
    with pytest.raises(ValueError, match="missing=\\['b'\\]"):
        _validate_panel_schema(panel, ["a", "b"], pd.Timestamp("2024-01-01"))


def test_schema_validation_raises_on_extra_column():
    panel = pd.DataFrame({"a": [1.0], "b": [2.0], "extra": [9.0]}, index=["S1"])
    with pytest.raises(ValueError, match="extra=\\['extra'\\]"):
        _validate_panel_schema(panel, ["a", "b"], pd.Timestamp("2024-01-01"))


def test_schema_validation_raises_for_non_dataframe():
    with pytest.raises(TypeError, match="must be DataFrame"):
        _validate_panel_schema({"a": [1.0]}, ["a"],
                               pd.Timestamp("2024-01-01"))


# ===========================================================================
# T5. _validate_oos_boundary (Codex v5.0 R2 P0 fix)
# ===========================================================================
OOS = pd.Timestamp("2025-01-01")


def test_oos_guard_passes_when_both_in_is():
    _validate_oos_boundary(
        as_of=pd.Timestamp("2024-11-12"),
        label_end=pd.Timestamp("2024-12-12"),
        forbidden_oos_start=OOS,
    )


def test_oos_guard_raises_when_as_of_in_oos():
    with pytest.raises(ValueError, match="as_of=2025-01-12"):
        _validate_oos_boundary(
            as_of=pd.Timestamp("2025-01-12"),
            label_end=pd.Timestamp("2025-02-12"),
            forbidden_oos_start=OOS,
        )


def test_oos_guard_raises_when_label_end_in_oos():
    """Codex v5.0 R2 P0: as_of in IS but label_end falls in OOS → still raise.

    This is the silent-bug case the original 4a design missed.
    as_of = 2024-12-12 (IS), label_end = 2025-01-12 (OOS) →
    forward return = close[2025-01-12]/close[2024-12-12] → READS OOS DATA.
    """
    with pytest.raises(ValueError, match="label_end=2025-01-12.*reads OOS"):
        _validate_oos_boundary(
            as_of=pd.Timestamp("2024-12-12"),
            label_end=pd.Timestamp("2025-01-12"),
            forbidden_oos_start=OOS,
        )


def test_oos_guard_boundary_at_oos_start_is_oos():
    """as_of exactly == forbidden_oos_start is OOS (strict <)."""
    with pytest.raises(ValueError, match="as_of=2025-01-01"):
        _validate_oos_boundary(
            as_of=OOS,
            label_end=pd.Timestamp("2025-02-01"),
            forbidden_oos_start=OOS,
        )


# ===========================================================================
# T6. build_training_matrix — happy path
# ===========================================================================
def _make_simple_run(n_symbols: int = 5, n_periods: int = 4,
                     feature_names: list[str] | None = None):
    if feature_names is None:
        feature_names = ["f1", "f2"]
    symbols = [f"S{i}" for i in range(n_symbols)]
    # Monthly rebalance dates, all in IS (before 2025-01-01)
    as_of_dates = [pd.Timestamp(f"2024-0{m}-15")
                   for m in range(1, n_periods + 1)]
    # Close series spans all dates with daily prints
    close = {s: _make_close_with_seed("2023-12-01", 200, seed=i)
             for i, s in enumerate(symbols)}
    # Feature panels per as_of
    panels = {
        d: _make_feature_panel(symbols, feature_names, seed=i)
        for i, d in enumerate(as_of_dates)
    }
    config = PoolingConfig(
        feature_names=feature_names,
        forbidden_oos_start=pd.Timestamp("2025-01-01"),
    )
    return symbols, as_of_dates, close, panels, config


def test_builder_basic_shape_and_columns():
    symbols, as_of_dates, close, panels, config = _make_simple_run(
        n_symbols=5, n_periods=4, feature_names=["f1", "f2"],
    )
    df, diag = build_training_matrix(panels, close, as_of_dates, config)
    # 4 as_of - 1 (last has no label) = 3 periods × 5 stocks = 15 rows
    assert len(df) == 15
    expected_cols = ["symbol", "as_of", "label_end", "f1", "f2",
                     "forward_return", "label_top_decile"]
    assert list(df.columns) == expected_cols


def test_builder_diagnostics_track_drops():
    symbols, as_of_dates, close, panels, config = _make_simple_run()
    # Inject NaN feature for one symbol on one period
    panels[as_of_dates[1]].loc["S2", "f1"] = np.nan
    df, diag = build_training_matrix(panels, close, as_of_dates, config)
    assert diag.drops_missing_feature[as_of_dates[1]] == 1
    assert diag.total_dropped_missing_feature == 1


def test_builder_label_end_uses_next_as_of():
    symbols, as_of_dates, close, panels, config = _make_simple_run(n_periods=3)
    df, diag = build_training_matrix(panels, close, as_of_dates, config)
    # Each row's label_end must equal the NEXT as_of
    for as_of, group in df.groupby("as_of"):
        idx = as_of_dates.index(as_of)
        expected_label_end = as_of_dates[idx + 1]
        assert (group["label_end"] == expected_label_end).all()


def test_builder_top_decile_label_per_period():
    """Sanity: in each period, at least one row should be labeled 1
    (top decile, threshold 0.9, 5 stocks → 1 row > 0.9 quantile after rank)."""
    symbols, as_of_dates, close, panels, config = _make_simple_run(
        n_symbols=10, n_periods=3,
    )
    df, diag = build_training_matrix(panels, close, as_of_dates, config)
    for as_of, group in df.groupby("as_of"):
        assert group["label_top_decile"].sum() >= 1


def test_builder_complete_case_drops_partial_nan_row():
    symbols, as_of_dates, close, panels, config = _make_simple_run(
        n_symbols=5, n_periods=3,
    )
    # One symbol has NaN on first period
    panels[as_of_dates[0]].loc["S3", "f2"] = np.nan
    df, _ = build_training_matrix(panels, close, as_of_dates, config)
    # First period should have 4 rows (S3 dropped); other 1 period × 5 = 5
    first = df[df["as_of"] == as_of_dates[0]]
    assert len(first) == 4
    assert "S3" not in first["symbol"].values


# ===========================================================================
# T7. OOS rejection at builder level
# ===========================================================================
def test_builder_rejects_as_of_in_oos():
    feature_names = ["f1"]
    symbols = ["S1"]
    # Include an OOS as_of in the list → must raise
    as_of_dates = [pd.Timestamp("2024-11-15"), pd.Timestamp("2025-02-15")]
    panels = {
        as_of_dates[0]: _make_feature_panel(symbols, feature_names),
        as_of_dates[1]: _make_feature_panel(symbols, feature_names),
    }
    close = {s: _make_close_series("2024-01-01", 300) for s in symbols}
    config = PoolingConfig(
        feature_names=feature_names,
        forbidden_oos_start=pd.Timestamp("2025-01-01"),
    )
    with pytest.raises(ValueError, match="label_end=.*reads OOS"):
        build_training_matrix(panels, close, as_of_dates, config)


def test_builder_rejects_label_end_in_oos_when_as_of_safe():
    """Codex v5.0 R2 P0: as_of in IS but label_end leaks → must raise."""
    feature_names = ["f1"]
    symbols = ["S1"]
    # as_of=2024-12-15 (IS) → next=2025-01-15 (OOS) → label_end leak
    as_of_dates = [pd.Timestamp("2024-12-15"), pd.Timestamp("2025-01-15")]
    panels = {d: _make_feature_panel(symbols, feature_names)
              for d in as_of_dates}
    close = {s: _make_close_series("2024-01-01", 300) for s in symbols}
    config = PoolingConfig(
        feature_names=feature_names,
        forbidden_oos_start=pd.Timestamp("2025-01-01"),
    )
    with pytest.raises(ValueError, match="label_end=2025-01-15.*reads OOS"):
        build_training_matrix(panels, close, as_of_dates, config)


def test_builder_rejects_missing_panel():
    feature_names = ["f1"]
    symbols = ["S1"]
    as_of_dates = [pd.Timestamp("2024-01-15"), pd.Timestamp("2024-02-15"),
                   pd.Timestamp("2024-03-15")]
    panels = {d: _make_feature_panel(symbols, feature_names)
              for d in as_of_dates[:2]}  # missing middle
    panels.pop(as_of_dates[1])
    close = {s: _make_close_series("2024-01-01", 300) for s in symbols}
    config = PoolingConfig(
        feature_names=feature_names,
        forbidden_oos_start=pd.Timestamp("2025-01-01"),
    )
    with pytest.raises(ValueError, match="feature panel missing"):
        build_training_matrix(panels, close, as_of_dates, config)


def test_builder_rejects_empty_as_of_dates():
    config = PoolingConfig(
        feature_names=["f1"],
        forbidden_oos_start=pd.Timestamp("2025-01-01"),
    )
    with pytest.raises(ValueError, match="as_of_dates is empty"):
        build_training_matrix({}, {}, [], config)


def test_builder_rejects_single_as_of():
    config = PoolingConfig(
        feature_names=["f1"],
        forbidden_oos_start=pd.Timestamp("2025-01-01"),
    )
    with pytest.raises(ValueError, match="at least 2 as_of_dates"):
        build_training_matrix(
            {pd.Timestamp("2024-06-15"): pd.DataFrame({"f1": [1.0]},
                                                     index=["S1"])},
            {"S1": _make_close_series("2024-01-01", 300)},
            [pd.Timestamp("2024-06-15")], config,
        )


# ===========================================================================
# T8. Mutation tests — audit-style
# ===========================================================================
def test_mutation_top_decile_uses_past_return_would_be_caught():
    """If someone mutates the label to use PAST instead of FORWARD return,
    the forward_return column would contradict. This test sets up a clear
    case to catch the mutation."""
    # 2 symbols A/B, 2 as_of dates 1 month apart, A jumps +50% between as_ofs.
    symbols = ["A", "B"]
    as_of_dates = [pd.Timestamp("2024-06-15"), pd.Timestamp("2024-07-15")]
    idx = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    jump_date = pd.Timestamp("2024-07-01")  # falls between the 2 as_ofs
    # A: 100 before jump, 150 after; B: flat 100 throughout
    a_prices = [100.0 if d < jump_date else 150.0 for d in idx]
    close = {
        "A": pd.Series(a_prices, index=idx),
        "B": pd.Series([100.0] * len(idx), index=idx),
    }
    panels = {d: pd.DataFrame({"f1": [0.5, 0.5]}, index=["A", "B"])
              for d in as_of_dates}
    config = PoolingConfig(
        feature_names=["f1"],
        forbidden_oos_start=pd.Timestamp("2025-01-01"),
        top_decile_threshold=0.5,   # strict >: with 2 stocks, only top (pct=1.0) > 0.5
    )
    df, _ = build_training_matrix(panels, close, as_of_dates, config)
    a_row = df[df["symbol"] == "A"].iloc[0]
    b_row = df[df["symbol"] == "B"].iloc[0]
    # A's forward return: close[2024-07-15] / close[2024-06-15] - 1 = 150/100 - 1 = 0.5
    assert a_row["forward_return"] == pytest.approx(0.5)
    assert b_row["forward_return"] == pytest.approx(0.0)
    # With threshold 0.49 and 2 stocks, the higher-return stock = 1
    assert a_row["label_top_decile"] == 1
    assert b_row["label_top_decile"] == 0


def test_mutation_oos_guard_off_by_one_would_be_caught():
    """If someone changes < to <= in the OOS check, label_end == OOS start
    would slip through. T7 boundary test already covers this — duplicate
    as audit emphasis."""
    with pytest.raises(ValueError):
        _validate_oos_boundary(
            as_of=pd.Timestamp("2024-12-15"),
            label_end=pd.Timestamp("2025-01-01"),   # exactly == OOS start
            forbidden_oos_start=pd.Timestamp("2025-01-01"),
        )
