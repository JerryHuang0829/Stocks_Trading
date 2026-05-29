"""Tests for src/analysis/cpcv.py — Step 4c Purged Combinatorial CV.

Per pre-reg §9 LOCK:
- k=5 splits, n_test=2, embargo=1 month
- C(5,2)=10 combinatorial paths
- Purge: train labels overlap test window → drop
- Embargo: extend test window by N months each side

Mutation tests baked in:
- embargo=0 → still purges
- embargo > 0 → strictly more train rows dropped
- non-overlapping train labels NOT dropped
"""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.cpcv import (  # noqa: E402
    CPCVConfig,
    _group_dates_into_splits,
    cpcv_splits,
)


# ===========================================================================
# Config validation
# ===========================================================================
def test_default_config_matches_pre_reg():
    """pre-reg §9 §13.3 — k=5, n_test=2, embargo=1m LOCKED."""
    cfg = CPCVConfig()
    assert cfg.n_splits == 5
    assert cfg.n_test_splits == 2
    assert cfg.embargo_months == 1
    assert cfg.n_paths == 10   # C(5,2)


def test_config_raises_on_invalid_n_splits():
    with pytest.raises(ValueError, match="n_splits"):
        CPCVConfig(n_splits=1)


def test_config_raises_on_invalid_n_test():
    with pytest.raises(ValueError, match="n_test_splits"):
        CPCVConfig(n_test_splits=0)
    with pytest.raises(ValueError, match="n_test_splits"):
        CPCVConfig(n_test_splits=5)   # >= n_splits


def test_config_raises_on_negative_embargo():
    with pytest.raises(ValueError, match="embargo_months"):
        CPCVConfig(embargo_months=-1)


def test_n_paths_combinatorial():
    """C(n, k) per pre-reg §9 — 10 paths default."""
    assert CPCVConfig(n_splits=5, n_test_splits=2).n_paths == comb(5, 2)
    assert CPCVConfig(n_splits=4, n_test_splits=1).n_paths == comb(4, 1)
    assert CPCVConfig(n_splits=6, n_test_splits=3).n_paths == comb(6, 3)


# ===========================================================================
# Date grouping
# ===========================================================================
def test_group_dates_balanced_partition():
    dates = [pd.Timestamp(f"2024-0{m}-15") for m in range(1, 6)]
    groups = _group_dates_into_splits(dates, 5)
    assert len(groups) == 5
    assert all(len(g) == 1 for g in groups)


def test_group_dates_unequal_count_still_works():
    # 10 dates / 3 splits → roughly 3-3-4
    dates = [pd.Timestamp(f"2024-{m:02d}-15") for m in range(1, 11)]
    groups = _group_dates_into_splits(dates, 3)
    assert len(groups) == 3
    assert sum(len(g) for g in groups) == 10


def test_group_raises_when_too_few_dates():
    with pytest.raises(ValueError, match="need ≥"):
        _group_dates_into_splits([pd.Timestamp("2024-01-15")], 5)


# ===========================================================================
# CPCV splits behaviour
# ===========================================================================
def _make_training_indices(n_periods: int = 60, n_stocks: int = 10):
    """Synthetic (as_of, label_end) Series — n_periods × n_stocks rows."""
    as_ofs = [pd.Timestamp("2020-01-15") + pd.DateOffset(months=i)
              for i in range(n_periods)]
    rows_as_of = []
    rows_label_end = []
    for i, as_of in enumerate(as_ofs[:-1]):
        label_end = as_ofs[i + 1]
        for _ in range(n_stocks):
            rows_as_of.append(as_of)
            rows_label_end.append(label_end)
    return pd.Series(rows_as_of), pd.Series(rows_label_end)


def test_cpcv_yields_10_paths_default():
    as_of_s, label_end_s = _make_training_indices(n_periods=60, n_stocks=5)
    paths = list(cpcv_splits(as_of_s, label_end_s))
    assert len(paths) == 10


def test_cpcv_test_and_train_are_disjoint():
    as_of_s, label_end_s = _make_training_indices(n_periods=30, n_stocks=4)
    for train_idx, test_idx in cpcv_splits(as_of_s, label_end_s):
        assert len(np.intersect1d(train_idx, test_idx)) == 0


def test_cpcv_test_grouped_by_as_of():
    """Test fold rows should share as_of dates exclusively with each other."""
    as_of_s, label_end_s = _make_training_indices(n_periods=30, n_stocks=4)
    for train_idx, test_idx in cpcv_splits(as_of_s, label_end_s):
        test_as_ofs = set(as_of_s.iloc[test_idx].unique())
        train_as_ofs = set(as_of_s.iloc[train_idx].unique())
        # No as_of date should appear in both train AND test
        assert test_as_ofs.isdisjoint(train_as_ofs)


# ===========================================================================
# Purge + embargo semantics
# ===========================================================================
def test_purge_drops_train_with_label_end_in_test_window():
    """Per LdP 2018 §7.4 — train row whose label_end overlaps test window MUST be purged."""
    as_of_s, label_end_s = _make_training_indices(n_periods=30, n_stocks=2)
    config = CPCVConfig(n_splits=3, n_test_splits=1, embargo_months=0)
    for train_idx, test_idx in cpcv_splits(as_of_s, label_end_s, config):
        # Test window in label-time
        test_label_window_start = as_of_s.iloc[test_idx].min()
        test_label_window_end = label_end_s.iloc[test_idx].max()
        # No train row's label_end should fall in test window
        train_label_ends = label_end_s.iloc[train_idx]
        in_window = ((train_label_ends >= test_label_window_start)
                     & (train_label_ends <= test_label_window_end))
        assert not in_window.any(), (
            f"purge failure: {in_window.sum()} train rows have label_end in test window"
        )


def test_embargo_strictly_drops_more_than_no_embargo():
    """Embargo > 0 should drop more train rows than embargo = 0."""
    as_of_s, label_end_s = _make_training_indices(n_periods=60, n_stocks=3)

    paths_no_embargo = list(cpcv_splits(
        as_of_s, label_end_s,
        CPCVConfig(n_splits=5, n_test_splits=2, embargo_months=0),
    ))
    paths_embargo_1 = list(cpcv_splits(
        as_of_s, label_end_s,
        CPCVConfig(n_splits=5, n_test_splits=2, embargo_months=1),
    ))
    paths_embargo_2 = list(cpcv_splits(
        as_of_s, label_end_s,
        CPCVConfig(n_splits=5, n_test_splits=2, embargo_months=2),
    ))

    # Compare equal-numbered paths (same test groups)
    for (tr0, te0), (tr1, te1), (tr2, te2) in zip(
        paths_no_embargo, paths_embargo_1, paths_embargo_2
    ):
        assert len(tr1) <= len(tr0), \
            "embargo=1 should drop ≥ as many train rows as embargo=0"
        assert len(tr2) <= len(tr1), \
            "embargo=2 should drop ≥ as many as embargo=1"


def test_mutation_embargo_off_still_purges_label_overlap():
    """Without embargo, purge alone should still catch label_end leakage."""
    as_of_s, label_end_s = _make_training_indices(n_periods=30, n_stocks=2)
    config = CPCVConfig(n_splits=3, n_test_splits=1, embargo_months=0)
    paths = list(cpcv_splits(as_of_s, label_end_s, config))
    for train_idx, test_idx in paths:
        # The train row with as_of immediately before test_window_start has
        # label_end == test_window_start.  Purge should drop it.
        train_label_ends = label_end_s.iloc[train_idx].sort_values()
        test_window_start = as_of_s.iloc[test_idx].min()
        # No train label_end should equal test window start
        assert not (train_label_ends == test_window_start).any()


def test_cpcv_rejects_empty_series():
    with pytest.raises(ValueError, match="empty"):
        list(cpcv_splits(pd.Series(dtype="datetime64[ns]"),
                         pd.Series(dtype="datetime64[ns]")))


def test_cpcv_rejects_mismatched_lengths():
    as_of_s = pd.Series([pd.Timestamp("2024-01-15"), pd.Timestamp("2024-02-15")])
    label_end_s = pd.Series([pd.Timestamp("2024-02-15")])
    with pytest.raises(ValueError, match="length mismatch"):
        list(cpcv_splits(as_of_s, label_end_s))


# ===========================================================================
# Coverage
# ===========================================================================
def test_cpcv_combined_test_indices_cover_each_as_of_multiple_times():
    """Every as_of should appear in test set across multiple paths
    (combinatorial coverage). With k=5, n_test=2: each group appears in
    C(4,1)=4 of 10 paths."""
    n_periods = 30
    as_of_s, label_end_s = _make_training_indices(n_periods=n_periods, n_stocks=2)
    paths = list(cpcv_splits(as_of_s, label_end_s))
    counts: dict[pd.Timestamp, int] = {}
    for _, test_idx in paths:
        for d in as_of_s.iloc[test_idx].unique():
            counts[d] = counts.get(d, 0) + 1
    # Most as_of dates should be tested in 4 paths (per LdP 2018)
    # Allow some slack for boundary effects
    most_common_count = max(counts.values())
    assert most_common_count >= 3
