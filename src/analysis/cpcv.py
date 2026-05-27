"""v5.0 Step 4c — Purged Combinatorial Cross-Validation (CPCV).

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §9 CPCV (LdP 2018) — k=5 splits, n_test=2, embargo=1 month → C(5,2)=10 paths
  §13 condition 3 (CPCV setup LOCKED)

López de Prado 2018 *Advances in Financial Machine Learning* Ch.7:
  Combinatorial Purged CV addresses two failure modes in standard k-fold CV
  on time-series labels:
    1. **Backtest overfitting** — k-fold gives only k train/test paths, easy
       to cherry-pick splits. CPCV's C(k, n_test) paths (e.g. 10 for k=5,
       n_test=2) make multi-test correction (DSR) honest.
    2. **Label leakage** — when label[t] reads close[t+horizon], a train
       row at t whose label_end falls into a test window introduces forward
       information into training.  **Purge** removes such rows; **embargo**
       extends the buffer.

This module is pure: takes pre-computed as_of + label_end Series, returns
(train_idx, test_idx) tuples. Does not load cache or compute returns.

LOCKED config per pre-reg:
    n_splits = 5, n_test_splits = 2, embargo_months = 1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from typing import Iterator

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


DEFAULT_N_SPLITS = 5
DEFAULT_N_TEST_SPLITS = 2
DEFAULT_EMBARGO_MONTHS = 1


@dataclass
class CPCVConfig:
    """v5.0 CPCV config (pre-reg §9 LOCK)."""
    n_splits: int = DEFAULT_N_SPLITS
    n_test_splits: int = DEFAULT_N_TEST_SPLITS
    embargo_months: int = DEFAULT_EMBARGO_MONTHS

    def __post_init__(self):
        if self.n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if not 1 <= self.n_test_splits < self.n_splits:
            raise ValueError("n_test_splits must be in [1, n_splits-1]")
        if self.embargo_months < 0:
            raise ValueError("embargo_months must be >= 0")

    @property
    def n_paths(self) -> int:
        """C(n_splits, n_test_splits) combinatorial paths."""
        from math import comb
        return comb(self.n_splits, self.n_test_splits)


def _group_dates_into_splits(
    unique_as_ofs: list[pd.Timestamp],
    n_splits: int,
) -> list[set[pd.Timestamp]]:
    """Chronologically partition unique as_ofs into n_splits groups.

    Each group has roughly equal count of as_of dates. Returns list of sets
    for O(1) membership testing.
    """
    n = len(unique_as_ofs)
    if n < n_splits:
        raise ValueError(
            f"need ≥ {n_splits} unique as_of dates for n_splits={n_splits}; got {n}"
        )
    bounds = np.linspace(0, n, n_splits + 1, dtype=int)
    return [set(unique_as_ofs[bounds[i]:bounds[i + 1]]) for i in range(n_splits)]


def cpcv_splits(
    as_of_series: pd.Series,
    label_end_series: pd.Series,
    config: CPCVConfig | None = None,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_idx, test_idx) for each of C(n_splits, n_test_splits) paths.

    Both Series MUST be aligned by index (typically training_df.index from
    ml_pooling.build_training_matrix()).

    Parameters
    ----------
    as_of_series : pd.Series
        Per-row as_of timestamp.  Indexed identical to label_end_series.
    label_end_series : pd.Series
        Per-row label_end timestamp (when the forward-return label is realised).
    config : CPCVConfig | None
        Defaults to k=5 / n_test=2 / embargo=1m per pre-reg §9.

    Yields
    ------
    (train_idx, test_idx)
        np.ndarray of integer positional indices (0-based) into the row order
        of as_of_series. Caller uses df.iloc[train_idx] / df.iloc[test_idx].

    Notes
    -----
    Purge + embargo logic (per LdP 2018 §7.4):
      - Test time window = [min(test_as_ofs), max(test_label_ends)]
      - Embargo expands the window by ``embargo_months`` on EACH side
      - Train rows are excluded if:
        (a) their as_of falls in any test group, OR
        (b) their label_end falls within the embargoed test window, OR
        (c) their as_of falls within the embargoed test window

    Why include (c)? If train as_of is inside the embargo extension on the
    right of the test window, its label could land in a future (post-embargo)
    test group's eligible window — protects from serial-corr leakage.
    """
    if config is None:
        config = CPCVConfig()
    if len(as_of_series) != len(label_end_series):
        raise ValueError("as_of_series and label_end_series length mismatch")
    if as_of_series.empty:
        raise ValueError("as_of_series is empty")

    # Normalize to numpy arrays for fast index ops
    as_of_arr = pd.to_datetime(as_of_series).reset_index(drop=True)
    label_end_arr = pd.to_datetime(label_end_series).reset_index(drop=True)

    unique_as_ofs = sorted(as_of_arr.unique())
    groups = _group_dates_into_splits(unique_as_ofs, config.n_splits)
    embargo_td = pd.Timedelta(days=30 * config.embargo_months)

    n_paths_yielded = 0
    for test_group_combo in combinations(range(config.n_splits), config.n_test_splits):
        # Test mask = rows whose as_of falls in any chosen test group
        test_as_ofs_set: set = set()
        for g_idx in test_group_combo:
            test_as_ofs_set |= groups[g_idx]
        test_mask = as_of_arr.isin(test_as_ofs_set)
        test_idx = np.where(test_mask)[0]
        if len(test_idx) == 0:
            logger.warning("CPCV path %s yielded empty test set; skipped",
                           test_group_combo)
            continue

        # LdP 2018 §7.4: handle each test group's window INDIVIDUALLY so that
        # non-contiguous test combos (e.g. (g0, g4) with k=5) don't merge the
        # gap between them into one giant exclusion window — that would empty
        # the train set unnecessarily.
        train_mask = ~test_mask
        for g_idx in test_group_combo:
            g_as_ofs = groups[g_idx]
            if not g_as_ofs:
                continue
            g_row_mask = as_of_arr.isin(g_as_ofs)
            if not g_row_mask.any():
                continue
            g_start = min(g_as_ofs)
            # group's label_end window right edge = max label_end across rows in this group
            g_end = label_end_arr[g_row_mask].max()
            embargoed_start = g_start - embargo_td
            embargoed_end = g_end + embargo_td
            # purge: train rows whose label_end falls in this group's embargoed window
            purge_overlap = ((label_end_arr >= embargoed_start)
                             & (label_end_arr <= embargoed_end))
            # embargo: train rows whose as_of falls in this group's embargoed window
            as_of_in_buffer = ((as_of_arr >= embargoed_start)
                               & (as_of_arr <= embargoed_end))
            train_mask = train_mask & ~purge_overlap & ~as_of_in_buffer

        train_idx = np.where(train_mask)[0]
        if len(train_idx) == 0:
            logger.warning(
                "CPCV path %s yielded empty train set after purge+embargo; skipped",
                test_group_combo,
            )
            continue

        n_paths_yielded += 1
        yield train_idx, test_idx

    logger.info(
        "cpcv_splits: yielded %d / %d combinatorial paths (k=%d, n_test=%d, embargo=%d month)",
        n_paths_yielded, config.n_paths, config.n_splits,
        config.n_test_splits, config.embargo_months,
    )
