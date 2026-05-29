"""v5.0 Step 4f — SHAP feature importance + interaction values.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §11 SHAP feature importance (LOCKED 必算,非 gate)
  §13 condition 13 (SHAP 必算)
  §13 condition 14 deliverable: `reports/phase_d_v5/v5_shap_summary.json`

LdP / Lundberg 2017 SHAP TreeExplainer on the best XGBoost model from Step 4e
gives per-feature contribution to each prediction. Mean(|SHAP|) over the test
set is the standard "feature importance" metric — directly comparable across
features and stable to scale.

Per pre-reg §11:
  - mean(|SHAP|) per feature(全 30-40 維)
  - SHAP dependence plot for top 5 features (deferred — caller draws if needed)
  - Interaction values for §6 locked interactions

This module produces:
  - per-feature importance (sorted desc)
  - per-interaction (LOCKED 5 from §6) interaction strength
  - JSON-serializable summary (for v5_shap_summary.json deliverable)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ShapSummary:
    """Serializable SHAP analysis result."""
    feature_importance: dict[str, float]
    feature_importance_ranked: list[tuple[str, float]]
    interaction_strength: dict[str, float]   # mean(|SHAP interaction|) per pair
    n_samples: int
    model_class: str

    def to_dict(self) -> dict:
        return {
            "feature_importance": self.feature_importance,
            "feature_importance_ranked": [
                {"feature": f, "mean_abs_shap": v}
                for f, v in self.feature_importance_ranked
            ],
            "interaction_strength": self.interaction_strength,
            "n_samples": self.n_samples,
            "model_class": self.model_class,
        }


def compute_shap_values(model, X: np.ndarray) -> np.ndarray:
    """Compute per-prediction SHAP values for a tree model.

    Returns
    -------
    shap_values : np.ndarray, shape (n_samples, n_features)
        SHAP value contributions. For multiclass / ranking, this is the
        contribution to the predicted score.
    """
    import shap
    # Unwrap our wrapper if needed — TreeExplainer expects the actual sklearn-style model
    underlying = getattr(model, "_model", None) or model
    explainer = shap.TreeExplainer(underlying)
    values = explainer.shap_values(X)
    # XGBoost binary classifier in some shap versions returns list[ndarray] per class
    if isinstance(values, list):
        # For binary classifier, take class 1's contribution
        values = values[1] if len(values) == 2 else values[0]
    return np.asarray(values)


def compute_feature_importance(
    shap_values: np.ndarray,
    feature_names: list[str],
) -> dict[str, float]:
    """mean(|SHAP|) per feature — standard Lundberg 2017 importance metric."""
    if shap_values.shape[1] != len(feature_names):
        raise ValueError(
            f"shap_values has {shap_values.shape[1]} cols but feature_names "
            f"has {len(feature_names)}"
        )
    mean_abs = np.abs(shap_values).mean(axis=0)
    return {name: float(val) for name, val in zip(feature_names, mean_abs)}


def compute_interaction_strength(
    model,
    X: np.ndarray,
    feature_names: list[str],
    interaction_pairs: list[tuple[str, str]],
    max_samples: int = 1000,
) -> dict[str, float]:
    """SHAP interaction values for specified feature pairs.

    Per pre-reg §11: 'Interaction values for §6 locked interactions'.
    Each pair (a, b) → mean(|interaction[i, a, b]|) over sampled rows.

    Uses shap.TreeExplainer.shap_interaction_values which is O(n × p²) — costly,
    so we sub-sample max_samples rows. For 5 locked pairs + ~30 features +
    sub-sampled 1000 rows, this is tractable (~few seconds).
    """
    import shap
    underlying = getattr(model, "_model", None) or model
    # Sub-sample if needed
    n = len(X)
    if n > max_samples:
        rng = np.random.RandomState(42)
        idx = rng.choice(n, size=max_samples, replace=False)
        X_sub = X[idx]
    else:
        X_sub = X
    explainer = shap.TreeExplainer(underlying)
    int_values = explainer.shap_interaction_values(X_sub)
    # Binary classifier list-of-array handling
    if isinstance(int_values, list):
        int_values = int_values[1] if len(int_values) == 2 else int_values[0]
    int_values = np.asarray(int_values)
    # int_values shape: (n, p, p) — element [i, a, b] = interaction contribution
    name_to_idx = {n: i for i, n in enumerate(feature_names)}
    out: dict[str, float] = {}
    for a, b in interaction_pairs:
        if a not in name_to_idx or b not in name_to_idx:
            logger.warning("interaction pair (%s, %s) — feature missing; skipped", a, b)
            continue
        i_a = name_to_idx[a]
        i_b = name_to_idx[b]
        # Symmetric: [i, a, b] + [i, b, a] = same value (per SHAP definition)
        strength = float(np.abs(int_values[:, i_a, i_b]).mean())
        out[f"{a}_x_{b}"] = strength
    return out


def build_shap_summary(
    model,
    X: np.ndarray,
    feature_names: list[str],
    interaction_pairs: list[tuple[str, str]] | None = None,
    max_interaction_samples: int = 1000,
) -> ShapSummary:
    """End-to-end SHAP analysis → serializable summary."""
    shap_values = compute_shap_values(model, X)
    importance = compute_feature_importance(shap_values, feature_names)
    ranked = sorted(importance.items(), key=lambda kv: -kv[1])
    interaction_strength: dict[str, float] = {}
    if interaction_pairs:
        try:
            interaction_strength = compute_interaction_strength(
                model, X, feature_names, interaction_pairs,
                max_samples=max_interaction_samples,
            )
        except Exception as exc:
            logger.warning("interaction_strength computation failed: %s; "
                           "summary will omit interaction strengths", exc)
    return ShapSummary(
        feature_importance=importance,
        feature_importance_ranked=ranked,
        interaction_strength=interaction_strength,
        n_samples=int(X.shape[0]),
        model_class=type(getattr(model, "_model", model)).__name__,
    )
