"""Tests for src/analysis/ml_shap.py — Step 4f SHAP analysis.

Per pre-reg §11 + §13 condition 13:
- mean(|SHAP|) per feature
- Interaction strength for locked pairs
- Serializable summary for v5_shap_summary.json deliverable
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_models import XGBoostClassifierWrapper  # noqa: E402
from src.analysis.ml_shap import (  # noqa: E402
    ShapSummary,
    build_shap_summary,
    compute_feature_importance,
    compute_interaction_strength,
    compute_shap_values,
)


# ===========================================================================
# Fixtures: train a small XGB model
# ===========================================================================
@pytest.fixture(scope="module")
def fitted_xgb_model():
    rng = np.random.RandomState(0)
    X = rng.randn(200, 4)
    # y label correlated mostly with X[:, 0] then X[:, 1]
    y = ((X[:, 0] + 0.5 * X[:, 1] + 0.1 * rng.randn(200)) > 0).astype(int)
    clf = XGBoostClassifierWrapper(n_estimators=30, max_depth=3)
    clf.fit(X, y)
    return clf, X, ["f0", "f1", "f2", "f3"]


# ===========================================================================
# compute_shap_values
# ===========================================================================
def test_compute_shap_values_shape(fitted_xgb_model):
    clf, X, feature_names = fitted_xgb_model
    sv = compute_shap_values(clf, X)
    assert sv.shape == X.shape


def test_compute_shap_values_finite(fitted_xgb_model):
    clf, X, _ = fitted_xgb_model
    sv = compute_shap_values(clf, X)
    assert np.isfinite(sv).all()


# ===========================================================================
# compute_feature_importance
# ===========================================================================
def test_feature_importance_dict_matches_features(fitted_xgb_model):
    clf, X, feature_names = fitted_xgb_model
    sv = compute_shap_values(clf, X)
    imp = compute_feature_importance(sv, feature_names)
    assert set(imp.keys()) == set(feature_names)
    for v in imp.values():
        assert v >= 0   # mean(|SHAP|) is non-negative


def test_feature_importance_raises_on_mismatch():
    sv = np.zeros((10, 3))
    with pytest.raises(ValueError, match="cols but feature_names"):
        compute_feature_importance(sv, ["a", "b"])


def test_top_feature_has_highest_importance(fitted_xgb_model):
    """X[:, 0] is the strongest signal — its feature should be top-ranked."""
    clf, X, feature_names = fitted_xgb_model
    sv = compute_shap_values(clf, X)
    imp = compute_feature_importance(sv, feature_names)
    # f0 should have higher importance than f2/f3 (noise features)
    assert imp["f0"] > imp["f2"]
    assert imp["f0"] > imp["f3"]


# ===========================================================================
# compute_interaction_strength
# ===========================================================================
def test_interaction_strength_returns_specified_pairs(fitted_xgb_model):
    clf, X, feature_names = fitted_xgb_model
    pairs = [("f0", "f1"), ("f2", "f3")]
    strength = compute_interaction_strength(clf, X, feature_names, pairs,
                                            max_samples=50)
    assert set(strength.keys()) == {"f0_x_f1", "f2_x_f3"}
    for v in strength.values():
        assert v >= 0
        assert np.isfinite(v)


def test_interaction_strength_skips_unknown_features(fitted_xgb_model, caplog):
    clf, X, feature_names = fitted_xgb_model
    pairs = [("f0", "f1"), ("unknown", "f3")]
    import logging
    with caplog.at_level(logging.WARNING):
        strength = compute_interaction_strength(clf, X, feature_names, pairs,
                                                max_samples=50)
    # Only f0_x_f1 should appear
    assert "f0_x_f1" in strength
    assert "unknown_x_f3" not in strength


# ===========================================================================
# build_shap_summary
# ===========================================================================
def test_build_shap_summary_basic(fitted_xgb_model):
    clf, X, feature_names = fitted_xgb_model
    summary = build_shap_summary(clf, X, feature_names,
                                 interaction_pairs=[("f0", "f1")])
    assert isinstance(summary, ShapSummary)
    assert summary.n_samples == 200
    assert summary.model_class == "XGBClassifier"
    assert "f0" in summary.feature_importance
    # Ranked list sorted desc by importance
    sorted_vals = [v for _, v in summary.feature_importance_ranked]
    assert all(sorted_vals[i] >= sorted_vals[i + 1] for i in range(len(sorted_vals) - 1))
    # Interaction included
    assert "f0_x_f1" in summary.interaction_strength


def test_shap_summary_to_dict_json_safe(fitted_xgb_model):
    clf, X, feature_names = fitted_xgb_model
    summary = build_shap_summary(clf, X, feature_names,
                                 interaction_pairs=[("f0", "f1")])
    import json
    s = json.dumps(summary.to_dict())
    parsed = json.loads(s)
    assert "feature_importance" in parsed
    assert "feature_importance_ranked" in parsed
    assert "interaction_strength" in parsed


def test_build_shap_summary_without_interactions(fitted_xgb_model):
    clf, X, feature_names = fitted_xgb_model
    summary = build_shap_summary(clf, X, feature_names,
                                 interaction_pairs=None)
    assert summary.interaction_strength == {}
