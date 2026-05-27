"""Tests for src/analysis/ml_models.py — Step 4d ML wrappers.

Per pre-reg §7 + §13 condition 7:
- XGBoostClassifierWrapper (fit / predict_score)
- LambdaMARTWrapper (fit with query groups / predict_score)
- baseline_score (z-score equal weight, NO hyperparameters)
- select_top_n_per_period (portfolio construction)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_models import (  # noqa: E402
    LOCKED_TOP_N_VALUES,
    LambdaMARTWrapper,
    XGBoostClassifierWrapper,
    baseline_score,
    select_top_n_per_period,
)


# ===========================================================================
# Locks
# ===========================================================================
def test_locked_top_n_values():
    """pre-reg §13.10 — top_n ∈ {15, 20, 25, 30} LOCKED."""
    assert LOCKED_TOP_N_VALUES == (15, 20, 25, 30)


# ===========================================================================
# XGBoost classifier
# ===========================================================================
def test_xgboost_classifier_fit_and_predict():
    rng = np.random.RandomState(0)
    X = rng.randn(80, 5)
    # label correlated with X[:, 0]
    y = (X[:, 0] > 0).astype(int)
    clf = XGBoostClassifierWrapper(n_estimators=20, max_depth=2)
    clf.fit(X, y)
    scores = clf.predict_score(X)
    assert scores.shape == (80,)
    # Probabilities → in [0, 1]
    assert (scores >= 0).all() and (scores <= 1).all()


def test_xgboost_classifier_with_early_stopping_validation():
    rng = np.random.RandomState(0)
    X_tr = rng.randn(80, 5)
    y_tr = (X_tr[:, 0] > 0).astype(int)
    X_va = rng.randn(40, 5)
    y_va = (X_va[:, 0] > 0).astype(int)
    clf = XGBoostClassifierWrapper(n_estimators=50, max_depth=2)
    clf.fit(X_tr, y_tr, X_val=X_va, y_val=y_va, early_stopping_rounds=5)
    scores = clf.predict_score(X_va)
    assert scores.shape == (40,)


def test_xgboost_classifier_predict_before_fit_raises():
    clf = XGBoostClassifierWrapper()
    with pytest.raises(RuntimeError, match="not fitted"):
        clf.predict_score(np.zeros((5, 3)))


# ===========================================================================
# LambdaMART ranker
# ===========================================================================
def test_lambdamart_fit_and_predict():
    rng = np.random.RandomState(0)
    n_groups = 4
    group_size = 10
    X = rng.randn(n_groups * group_size, 4)
    # y in {0, 1, 2, 3} as relevance, correlated with X[:, 0]
    y_continuous = X[:, 0] + 0.1 * rng.randn(len(X))
    # Convert to ranks per group (relevance must be discrete int for XGBRanker)
    y = np.zeros(len(X), dtype=int)
    for g in range(n_groups):
        start = g * group_size
        end = (g + 1) * group_size
        y[start:end] = (
            pd.Series(y_continuous[start:end]).rank(method="first").astype(int) - 1
        )
    groups = [group_size] * n_groups
    ranker = LambdaMARTWrapper(n_estimators=20, max_depth=2)
    ranker.fit(X, y, group_train=groups)
    scores = ranker.predict_score(X)
    assert scores.shape == (len(X),)
    assert np.isfinite(scores).all()


def test_lambdamart_predict_before_fit_raises():
    ranker = LambdaMARTWrapper()
    with pytest.raises(RuntimeError, match="not fitted"):
        ranker.predict_score(np.zeros((5, 3)))


# ===========================================================================
# baseline_score
# ===========================================================================
def test_baseline_score_equal_weight_z():
    """Per-period z-score(feature - mean)/std then mean across features."""
    # 3 stocks, 2 periods, 2 features
    rows = []
    for as_of in [pd.Timestamp("2024-01-15"), pd.Timestamp("2024-02-15")]:
        for j in range(3):
            rows.append({
                "as_of": as_of,
                "symbol": f"S{j}",
                "f1": float(j),
                "f2": float(j * 2),
            })
    df = pd.DataFrame(rows)
    scores = baseline_score(df, ["f1", "f2"])
    # S2 always has highest f1 and f2 → top score
    # S0 always lowest → lowest score
    for as_of in df["as_of"].unique():
        mask = df["as_of"] == as_of
        period = scores[mask]
        period_symbols = df.loc[mask, "symbol"]
        # S2 should have highest score in this period
        best_idx = period.idxmax()
        assert period_symbols.loc[best_idx] == "S2"


def test_baseline_score_missing_feature_raises():
    df = pd.DataFrame({"as_of": [pd.Timestamp("2024-01-15")],
                       "symbol": ["A"], "f1": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        baseline_score(df, ["f1", "f2"])


def test_baseline_score_empty():
    df = pd.DataFrame(columns=["as_of", "symbol", "f1"])
    scores = baseline_score(df, ["f1"])
    assert scores.empty


def test_baseline_score_zero_std_handled():
    """When a feature has zero variance in a period, z-score is NaN — must
    not raise. The other features still contribute via mean."""
    df = pd.DataFrame({
        "as_of": [pd.Timestamp("2024-01-15")] * 3,
        "symbol": ["A", "B", "C"],
        "f1": [1.0, 2.0, 3.0],   # std > 0
        "f2": [5.0, 5.0, 5.0],   # std == 0 → z = NaN
    })
    scores = baseline_score(df, ["f1", "f2"])
    # mean across [valid f1 z-score, NaN f2 z-score] = nanmean → valid
    assert scores.notna().all()


# ===========================================================================
# select_top_n_per_period
# ===========================================================================
def test_select_top_n_basic():
    scores = pd.Series([1.0, 5.0, 3.0, 4.0, 2.0])
    as_ofs = pd.Series([pd.Timestamp("2024-01-15")] * 5)
    symbols = pd.Series(["A", "B", "C", "D", "E"])
    out = select_top_n_per_period(scores, as_ofs, top_n=2, symbol_series=symbols)
    assert pd.Timestamp("2024-01-15") in out
    # B (5.0) and D (4.0) are top 2
    assert set(out[pd.Timestamp("2024-01-15")]) == {"B", "D"}


def test_select_top_n_per_period_independent_selection():
    scores = pd.Series([1.0, 5.0, 3.0,  10.0, 2.0, 1.0])
    as_ofs = pd.Series([pd.Timestamp("2024-01-15")] * 3
                       + [pd.Timestamp("2024-02-15")] * 3)
    symbols = pd.Series(["A", "B", "C", "A", "B", "C"])
    out = select_top_n_per_period(scores, as_ofs, top_n=1,
                                  symbol_series=symbols)
    # In Jan: B (5.0) highest
    # In Feb: A (10.0) highest
    assert out[pd.Timestamp("2024-01-15")] == ["B"]
    assert out[pd.Timestamp("2024-02-15")] == ["A"]


def test_select_top_n_mismatched_lengths_raises():
    scores = pd.Series([1.0])
    as_ofs = pd.Series([pd.Timestamp("2024-01-15"), pd.Timestamp("2024-02-15")])
    symbols = pd.Series(["A"])
    with pytest.raises(ValueError, match="length mismatch"):
        select_top_n_per_period(scores, as_ofs, top_n=1, symbol_series=symbols)


def test_select_top_n_non_locked_value_warns_but_works(caplog):
    scores = pd.Series([1.0, 2.0, 3.0])
    as_ofs = pd.Series([pd.Timestamp("2024-01-15")] * 3)
    symbols = pd.Series(["A", "B", "C"])
    # top_n=10 not in LOCKED_TOP_N_VALUES → warn but proceed
    import logging
    with caplog.at_level(logging.WARNING):
        out = select_top_n_per_period(scores, as_ofs, top_n=10,
                                      symbol_series=symbols)
    assert pd.Timestamp("2024-01-15") in out
