"""v5.0 Step 4d — ML model wrappers + portfolio construction.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §7 Target & Model spec (LOCKED 3 models: XGBoost / LambdaMART / Linear baseline)
  §8 Optuna search space (LOCKED — DSR n_trials anchor)
  §13 condition 7 (3 models LOCKED — no RF/NN/ensemble/stacking)

LOCKED model set per pre-reg §13 condition 7:
  1. XGBoost classifier — binary top-decile target, returns predict_proba
  2. LambdaMART ranker — XGBRanker with rank:pairwise; one query group per as_of
  3. Linear baseline — z-score equal-weight (no hyperparameters; control)

This module is pure ML logic + portfolio selection. Optuna search runs in
Step 4e (`ml_optuna.py`), CPCV splits come from Step 4c (`cpcv.py`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# pre-reg §13 condition 10 — LOCKED top_n sweep set
LOCKED_TOP_N_VALUES: tuple[int, ...] = (15, 20, 25, 30)


# pre-reg §8.3 — early_stopping_rounds for XGBoost
DEFAULT_XGB_EARLY_STOPPING_ROUNDS = 20
DEFAULT_RANDOM_STATE = 42


@dataclass
class XGBoostClassifierWrapper:
    """XGBoost binary classifier for top-decile target (pre-reg §7.2 model A).

    Hyperparameter search space LOCKED per pre-reg §8.1.
    Returns predict_proba[:, 1] (probability of top-decile label) for ranking.
    """
    n_estimators: int = 100
    max_depth: int = 5
    learning_rate: float = 0.1
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    min_child_weight: int = 1
    gamma: float = 0.01
    reg_alpha: float = 0.01
    reg_lambda: float = 1.0
    random_state: int = DEFAULT_RANDOM_STATE
    _model: object | None = field(default=None, init=False, repr=False)

    def fit(self, X_train: np.ndarray, y_train: np.ndarray,
            X_val: np.ndarray | None = None,
            y_val: np.ndarray | None = None,
            early_stopping_rounds: int = DEFAULT_XGB_EARLY_STOPPING_ROUNDS) -> "XGBoostClassifierWrapper":
        import xgboost as xgb
        params = dict(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            min_child_weight=self.min_child_weight,
            gamma=self.gamma,
            reg_alpha=self.reg_alpha,
            reg_lambda=self.reg_lambda,
            random_state=self.random_state,
            objective="binary:logistic",
            eval_metric="logloss",
            verbosity=0,
        )
        if X_val is not None and y_val is not None:
            params["early_stopping_rounds"] = early_stopping_rounds
            self._model = xgb.XGBClassifier(**params)
            self._model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )
        else:
            self._model = xgb.XGBClassifier(**params)
            self._model.fit(X_train, y_train, verbose=False)
        return self

    def predict_score(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("model not fitted")
        # Probability of class 1 (top-decile label) → ranking score
        return self._model.predict_proba(X)[:, 1]


@dataclass
class LambdaMARTWrapper:
    """LambdaMART ranker (pre-reg §7.2 model B) — XGBRanker rank:pairwise.

    Query groups: one per as_of (caller passes group counts as `group=[...]`).
    Hyperparameter search space LOCKED per pre-reg §8.2.
    """
    n_estimators: int = 100
    max_depth: int = 5
    learning_rate: float = 0.1
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    random_state: int = DEFAULT_RANDOM_STATE
    _model: object | None = field(default=None, init=False, repr=False)

    def fit(self, X_train: np.ndarray, y_train: np.ndarray,
            group_train: Sequence[int],
            X_val: np.ndarray | None = None,
            y_val: np.ndarray | None = None,
            group_val: Sequence[int] | None = None,
            early_stopping_rounds: int = DEFAULT_XGB_EARLY_STOPPING_ROUNDS) -> "LambdaMARTWrapper":
        import xgboost as xgb
        params = dict(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            random_state=self.random_state,
            objective="rank:pairwise",
            verbosity=0,
        )
        if X_val is not None and y_val is not None and group_val is not None:
            params["early_stopping_rounds"] = early_stopping_rounds
            self._model = xgb.XGBRanker(**params)
            self._model.fit(
                X_train, y_train, group=list(group_train),
                eval_set=[(X_val, y_val)], eval_group=[list(group_val)],
                verbose=False,
            )
        else:
            self._model = xgb.XGBRanker(**params)
            self._model.fit(X_train, y_train, group=list(group_train), verbose=False)
        return self

    def predict_score(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("model not fitted")
        return self._model.predict(X)


# ---------------------------------------------------------------------------
# Linear baseline (pre-reg §7.3 — z-score equal weight, NO hyperparameters)
# ---------------------------------------------------------------------------
def baseline_score(
    feature_df: pd.DataFrame,
    feature_names: list[str],
    as_of_col: str = "as_of",
) -> pd.Series:
    """Z-score equal-weight composite score (pre-reg §7.3 control).

    Per-period cross-section z-score(每月獨立 demean+std normalize), then
    average across features.  Output indexed identical to input.

    Per pre-reg §13 conditions 7 + 8: this is the ONLY linear baseline
    (no winsorization, no hyperparameters) — adding any tuning knob =
    spec violation.
    """
    if feature_df.empty:
        return pd.Series(dtype=float)
    missing = set(feature_names) - set(feature_df.columns)
    if missing:
        raise ValueError(
            f"baseline_score: feature_df missing columns {sorted(missing)}"
        )

    scores = pd.Series(0.0, index=feature_df.index)
    for as_of, group in feature_df.groupby(as_of_col):
        feats = group[feature_names].astype(float)
        means = feats.mean()
        stds = feats.std(ddof=0).replace(0, np.nan)
        z = (feats - means) / stds   # NaN std → NaN z (won't affect mean)
        composite = z.mean(axis=1)   # equal weight across features
        scores.loc[group.index] = composite
    return scores


# ---------------------------------------------------------------------------
# Portfolio construction (top_n per period from score)
# ---------------------------------------------------------------------------
def select_top_n_per_period(
    scores: pd.Series,
    as_of_series: pd.Series,
    top_n: int,
    symbol_series: pd.Series,
) -> dict[pd.Timestamp, list[str]]:
    """Pick top_n highest-scoring symbols per as_of.

    Used by both ML and baseline paths for portfolio construction.
    Tie-break: pandas nlargest (stable on index order; documented as such).

    Parameters
    ----------
    scores, as_of_series, symbol_series :
        All Series with identical index (typically training/test DataFrame rows).
    top_n : int
        Number of symbols per period. Must be in LOCKED_TOP_N_VALUES for the
        v5.0 experiment.

    Returns
    -------
    {as_of: [symbol, ...] sorted by descending score}
    """
    if top_n not in LOCKED_TOP_N_VALUES:
        logger.warning("top_n=%d not in pre-reg LOCKED set %s; "
                       "use only for ad-hoc analysis (not v5.0 deliverable)",
                       top_n, LOCKED_TOP_N_VALUES)
    if len(scores) != len(as_of_series) or len(scores) != len(symbol_series):
        raise ValueError("scores / as_of_series / symbol_series length mismatch")

    out: dict[pd.Timestamp, list[str]] = {}
    df = pd.DataFrame({
        "score": scores.values,
        "as_of": as_of_series.values,
        "symbol": symbol_series.values,
    })
    for as_of, group in df.groupby("as_of", sort=False):
        # nlargest is stable (preserves original order on tie)
        top = group.nlargest(top_n, "score", keep="first")
        out[pd.Timestamp(as_of)] = top["symbol"].tolist()
    return out
