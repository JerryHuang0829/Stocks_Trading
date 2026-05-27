"""Smoke test for v5.0 ML pipeline dependencies.

Added 2026-05-25 per Codex v5.0 R1 P1-3 fix. Verifies xgboost / optuna / shap
import cleanly in the quant conda env BEFORE Step 4 engineering starts;
without this, Step 4 dev would fail mid-way when the wrapper modules
import these packages.

Each test passes a minimal sanity check beyond bare import:
  - xgboost: smoke create + fit a 5-row classifier
  - optuna:  smoke create + run 2-trial study
  - shap:    smoke explain a tiny tree
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_xgboost_import_and_fit():
    """xgboost installed + can fit a minimal classifier."""
    import xgboost as xgb
    X = np.array([[0.0], [1.0], [2.0], [3.0], [4.0]])
    y = np.array([0, 0, 1, 1, 1])
    clf = xgb.XGBClassifier(n_estimators=2, max_depth=1, verbosity=0)
    clf.fit(X, y)
    preds = clf.predict(X)
    assert preds.shape == (5,)


def test_xgboost_ranker_import_and_fit():
    """XGBRanker (LambdaMART) usable for ranking target."""
    import xgboost as xgb
    X = np.random.RandomState(0).randn(20, 3)
    y = np.array([0, 1, 2, 1, 0] * 4)
    groups = [5] * 4  # 4 query groups of 5 samples
    ranker = xgb.XGBRanker(
        n_estimators=2, max_depth=1, objective="rank:pairwise", verbosity=0,
    )
    ranker.fit(X, y, group=groups)
    scores = ranker.predict(X)
    assert scores.shape == (20,)


def test_optuna_import_and_minimal_study():
    """optuna installed + 2-trial study runs."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def _obj(trial):
        x = trial.suggest_float("x", -1.0, 1.0)
        return x ** 2

    study = optuna.create_study(direction="minimize")
    study.optimize(_obj, n_trials=2)
    assert len(study.trials) == 2


def test_shap_import_and_explain():
    """shap installed + TreeExplainer works on minimal xgboost model."""
    import shap
    import xgboost as xgb
    X = np.array([[0.0], [1.0], [2.0], [3.0], [4.0]])
    y = np.array([0, 0, 1, 1, 1])
    clf = xgb.XGBClassifier(n_estimators=2, max_depth=1, verbosity=0)
    clf.fit(X, y)
    explainer = shap.TreeExplainer(clf)
    shap_values = explainer.shap_values(X)
    assert shap_values.shape == (5, 1)


def test_versions_in_expected_ranges():
    """Sanity-check pinned ranges from requirements-dev.txt."""
    import xgboost
    import optuna
    import shap
    # Loose check — full pin enforcement is in requirements-dev.txt
    assert xgboost.__version__ >= "1.7"
    assert optuna.__version__ >= "3.5"
    assert shap.__version__ >= "0.43"


def test_tabulate_import():
    """tabulate is required by pandas DataFrame.to_markdown() in
    scripts/_run_v5_ml_experiment.py (Step 5 deliverables).

    Codex v5.0 R6-equivalent fix (2026-05-26): added after smoke 1 failed
    at to_markdown without tabulate installed.
    """
    import tabulate
    from packaging.version import Version
    assert Version(tabulate.__version__) >= Version("0.9")
