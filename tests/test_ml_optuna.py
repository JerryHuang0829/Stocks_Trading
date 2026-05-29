"""Tests for src/analysis/ml_optuna.py — Step 4e nested CV.

Per pre-reg §8 LOCK:
- Sharpe metric on long-only top_n portfolio
- TimeSeriesSplit inner CV
- Optuna TPE sampler + MedianPruner

These tests use tiny synthetic data + n_trials=2 to stay fast.
Full 50-trial × 8-cell production runs happen in Step 5.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_optuna import (  # noqa: E402
    OptunaConfig,
    _inner_cv_indices_by_as_of,
    _portfolio_monthly_returns_from_scores,
    _sharpe_ratio,
    run_optuna_for_cell,
)


# ===========================================================================
# Locks
# ===========================================================================
def test_default_config_matches_pre_reg():
    """pre-reg §8.3 + §13.2 — n_trials=50, inner CV k=5 LOCKED."""
    cfg = OptunaConfig()
    assert cfg.n_trials == 50
    assert cfg.inner_cv_n_splits == 5
    assert cfg.sampler_seed == 42
    assert cfg.pruner_startup == 10
    assert cfg.early_stopping_rounds == 20
    # Math anchor: 50 × 2 models × 4 top_n = 400
    assert cfg.total_trials_per_full_run == 400


# ===========================================================================
# Helpers
# ===========================================================================
def test_sharpe_ratio_basic():
    rets = pd.Series([0.01, 0.02, -0.005, 0.015, 0.008])
    sh = _sharpe_ratio(rets, annualization=12)
    # Sanity: positive returns → positive Sharpe
    assert sh > 0


def test_sharpe_ratio_zero_std_returns_zero():
    rets = pd.Series([0.01] * 5)
    sh = _sharpe_ratio(rets, annualization=12)
    assert sh == 0.0


def test_sharpe_ratio_too_few_returns_zero():
    assert _sharpe_ratio(pd.Series([0.01]), annualization=12) == 0.0


def test_portfolio_monthly_returns_picks_top_n():
    """For each as_of, pick top_n by score → mean of forward returns."""
    scores = np.array([1.0, 5.0, 3.0,  10.0, 2.0, 1.0])
    as_ofs = np.array([pd.Timestamp("2024-01-15")] * 3
                      + [pd.Timestamp("2024-02-15")] * 3)
    fwds = np.array([0.01, 0.05, 0.03, 0.10, 0.02, 0.01])
    rets = _portfolio_monthly_returns_from_scores(scores, as_ofs, fwds, top_n=2)
    # Jan: top 2 scores are 5.0 (fwd 0.05) and 3.0 (fwd 0.03) → mean 0.04
    # Feb: top 2 scores are 10.0 (fwd 0.10) and 2.0 (fwd 0.02) → mean 0.06
    assert rets[pd.Timestamp("2024-01-15")] == pytest.approx(0.04)
    assert rets[pd.Timestamp("2024-02-15")] == pytest.approx(0.06)


def test_portfolio_skips_period_with_insufficient_symbols():
    """Period with < top_n eligible symbols is skipped, not crashed."""
    scores = np.array([1.0, 2.0])
    as_ofs = np.array([pd.Timestamp("2024-01-15")] * 2)
    fwds = np.array([0.01, 0.02])
    rets = _portfolio_monthly_returns_from_scores(scores, as_ofs, fwds, top_n=10)
    assert rets.empty


# ===========================================================================
# Inner CV
# ===========================================================================
def test_inner_cv_basic_expanding_window():
    """6 unique as_ofs, n_splits=2 → 2 (train, val) folds, expanding window."""
    as_ofs = np.array([pd.Timestamp(f"2024-0{m}-15") for m in range(1, 7)
                       for _ in range(3)])  # 3 rows per as_of
    folds = _inner_cv_indices_by_as_of(as_ofs, n_splits=2)
    assert len(folds) == 2
    for train_idx, val_idx in folds:
        # No overlap
        assert len(np.intersect1d(train_idx, val_idx)) == 0


def test_inner_cv_raises_when_insufficient_dates():
    as_ofs = np.array([pd.Timestamp("2024-01-15")] * 5)
    with pytest.raises(ValueError, match="needs ≥"):
        _inner_cv_indices_by_as_of(as_ofs, n_splits=5)


# ===========================================================================
# Optuna integration (smoke — 2 trials, tiny data, fast)
# ===========================================================================
def _make_optuna_input(n_as_ofs: int = 8, n_stocks: int = 30, seed: int = 0):
    rng = np.random.RandomState(seed)
    rows = []
    for i in range(n_as_ofs):
        as_of = pd.Timestamp(f"2024-{i+1:02d}-15") if i < 12 else \
                pd.Timestamp("2024-12-15")
        # Generate features with some predictive power
        for j in range(n_stocks):
            f1 = rng.randn()
            f2 = rng.randn()
            # forward return correlated with f1
            fwd = 0.005 * f1 + 0.02 * rng.randn()
            label = int(fwd > np.percentile(rng.randn(20), 90))
            rows.append({
                "as_of": as_of, "f1": f1, "f2": f2,
                "fwd": fwd, "label": label,
            })
    df = pd.DataFrame(rows)
    return df


def test_run_optuna_xgboost_smoke():
    """Smoke: 2 trials of XGBoost objective complete + return valid types."""
    df = _make_optuna_input(n_as_ofs=8, n_stocks=20, seed=0)
    X = df[["f1", "f2"]].values
    y = df["label"].values
    as_of_arr = df["as_of"].values
    fwd_arr = df["fwd"].values
    # 2 trials, 2 inner folds, top_n=5
    config = OptunaConfig(n_trials=2, inner_cv_n_splits=2,
                          early_stopping_rounds=2, pruner_startup=10)
    best_params, best_value = run_optuna_for_cell(
        "xgboost", X, y, as_of_arr, fwd_arr, top_n=5, config=config,
    )
    assert isinstance(best_params, dict)
    assert isinstance(best_value, float)
    # All expected param keys present
    for k in ["n_estimators", "max_depth", "learning_rate"]:
        assert k in best_params


def test_run_optuna_lambdamart_smoke():
    df = _make_optuna_input(n_as_ofs=8, n_stocks=20, seed=1)
    X = df[["f1", "f2"]].values
    y = df["label"].values
    as_of_arr = df["as_of"].values
    fwd_arr = df["fwd"].values
    config = OptunaConfig(n_trials=2, inner_cv_n_splits=2,
                          early_stopping_rounds=2, pruner_startup=10)
    best_params, best_value = run_optuna_for_cell(
        "lambdamart", X, y, as_of_arr, fwd_arr, top_n=5, config=config,
    )
    assert isinstance(best_params, dict)
    assert isinstance(best_value, float)


def test_run_optuna_rejects_unknown_model():
    df = _make_optuna_input()
    with pytest.raises(ValueError, match="must be 'xgboost' or 'lambdamart'"):
        run_optuna_for_cell(
            "random_forest", df[["f1", "f2"]].values, df["label"].values,
            df["as_of"].values, df["fwd"].values, top_n=5,
        )
