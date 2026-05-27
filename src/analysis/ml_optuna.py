"""v5.0 Step 4e — Optuna nested CV hyperparameter search.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §8 Optuna search space (LOCKED)
  §12 Protocol — Option A: independent Optuna per (model, top_n) cell
  §13 condition 2 — n_trials = 50 per cell; 50 × 2 models × 4 top_n = 400 total

Nested CV design (per pre-reg §8.3):
  outer = CPCV (k=5, n_test=2, embargo=1m) from `cpcv.py`
  inner = TimeSeriesSplit(n_splits=5) on outer-train fold
  Objective = mean Sharpe across inner folds of resulting long-only top_n
              monthly portfolio (built from ML predictions on inner-val).

Per Option A: each (model_name, top_n) cell runs its OWN Optuna study with
50 trials → 50 × 2 × 4 = 400 trials total → DSR n_trials = 400 anchor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal

import numpy as np
import pandas as pd

from src.analysis.cpcv import CPCVConfig, cpcv_splits
from src.analysis.ml_models import (
    LOCKED_TOP_N_VALUES,
    LambdaMARTWrapper,
    XGBoostClassifierWrapper,
)

logger = logging.getLogger(__name__)


DEFAULT_N_TRIALS = 50               # pre-reg §8.3 LOCK
DEFAULT_INNER_CV_N_SPLITS = 5       # pre-reg §8.3 LOCK (TimeSeriesSplit)
DEFAULT_SAMPLER_SEED = 42           # pre-reg §8.3 LOCK
DEFAULT_PRUNER_STARTUP = 10         # pre-reg §8.3 LOCK
DEFAULT_EARLY_STOPPING_ROUNDS = 20  # pre-reg §8.3 LOCK
DEFAULT_ANNUALIZATION = 12          # monthly → annual Sharpe


@dataclass
class OptunaConfig:
    """v5.0 Optuna config (pre-reg §8.3 LOCK)."""
    n_trials: int = DEFAULT_N_TRIALS
    inner_cv_n_splits: int = DEFAULT_INNER_CV_N_SPLITS
    sampler_seed: int = DEFAULT_SAMPLER_SEED
    pruner_startup: int = DEFAULT_PRUNER_STARTUP
    early_stopping_rounds: int = DEFAULT_EARLY_STOPPING_ROUNDS
    annualization: int = DEFAULT_ANNUALIZATION
    # Per pre-reg §8.4 multi-test math anchor
    total_trials_per_full_run: int = DEFAULT_N_TRIALS * 2 * len(LOCKED_TOP_N_VALUES)


# ---------------------------------------------------------------------------
# Inner-CV Sharpe metric
# ---------------------------------------------------------------------------
def _portfolio_monthly_returns_from_scores(
    scores: np.ndarray,
    as_of_arr: np.ndarray,
    fwd_return_arr: np.ndarray,
    top_n: int,
) -> pd.Series:
    """Build monthly long-only top_n portfolio returns from ML scores.

    For each unique as_of: rank symbols by score, pick top_n, return = mean
    forward return of selected (equal-weight monthly portfolio per pre-reg §7).
    """
    df = pd.DataFrame({
        "score": scores,
        "as_of": as_of_arr,
        "fwd": fwd_return_arr,
    })
    monthly: dict[pd.Timestamp, float] = {}
    for as_of, group in df.groupby("as_of", sort=True):
        if len(group) < top_n:
            # not enough symbols — skip period
            continue
        top = group.nlargest(top_n, "score", keep="first")
        monthly[pd.Timestamp(as_of)] = float(top["fwd"].mean())
    return pd.Series(monthly).sort_index()


def _sharpe_ratio(returns: pd.Series, annualization: int) -> float:
    """Annualized Sharpe. Returns 0 (penalty) if not enough data or zero std."""
    clean = returns.dropna()
    if len(clean) < 2:
        return 0.0
    mu = float(clean.mean())
    sd = float(clean.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return mu / sd * np.sqrt(annualization)


def evaluate_cell_sharpe(
    model: XGBoostClassifierWrapper | LambdaMARTWrapper,
    X_val: np.ndarray,
    as_of_val: np.ndarray,
    fwd_val: np.ndarray,
    top_n: int,
    annualization: int = DEFAULT_ANNUALIZATION,
) -> float:
    """Predict on val → top_n portfolio → annualized Sharpe."""
    scores = model.predict_score(X_val)
    monthly_returns = _portfolio_monthly_returns_from_scores(
        scores, as_of_val, fwd_val, top_n,
    )
    return _sharpe_ratio(monthly_returns, annualization)


# ---------------------------------------------------------------------------
# Inner CV (TimeSeriesSplit on as_of dimension)
# ---------------------------------------------------------------------------
def _inner_cv_indices_by_as_of(
    as_of_arr: np.ndarray,
    n_splits: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """TimeSeriesSplit groups by unique as_of date.

    Returns (train_idx, val_idx) row-position tuples. n_splits inner folds,
    expanding-window style: fold i train = unique_as_ofs[:bound_i],
                            fold i val = unique_as_ofs[bound_i:bound_{i+1}].
    """
    unique_as_ofs = np.array(sorted(pd.unique(as_of_arr)))
    n = len(unique_as_ofs)
    if n < n_splits + 1:
        raise ValueError(
            f"inner CV needs ≥ {n_splits + 1} unique as_of dates; got {n}"
        )
    bounds = np.linspace(n // (n_splits + 1), n, n_splits + 1, dtype=int)
    out = []
    for i in range(n_splits):
        train_dates = set(unique_as_ofs[:bounds[i]])
        val_dates = set(unique_as_ofs[bounds[i]:bounds[i + 1]])
        train_mask = np.isin(as_of_arr, list(train_dates))
        val_mask = np.isin(as_of_arr, list(val_dates))
        if train_mask.sum() == 0 or val_mask.sum() == 0:
            continue
        out.append((np.where(train_mask)[0], np.where(val_mask)[0]))
    return out


# ---------------------------------------------------------------------------
# Objective factories per pre-reg §8.1 / §8.2 search space
# ---------------------------------------------------------------------------
def _sample_xgb_params(trial) -> dict:
    """pre-reg §8.1 LOCKED search space."""
    return dict(
        n_estimators=trial.suggest_int("n_estimators", 50, 500),
        max_depth=trial.suggest_int("max_depth", 3, 8),
        learning_rate=trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        subsample=trial.suggest_float("subsample", 0.5, 1.0),
        colsample_bytree=trial.suggest_float("colsample_bytree", 0.5, 1.0),
        min_child_weight=trial.suggest_int("min_child_weight", 1, 10),
        gamma=trial.suggest_float("gamma", 1e-3, 1.0, log=True),
        reg_alpha=trial.suggest_float("reg_alpha", 1e-3, 1.0, log=True),
        reg_lambda=trial.suggest_float("reg_lambda", 1e-3, 1.0, log=True),
    )


def _sample_lambdamart_params(trial) -> dict:
    """pre-reg §8.2 LOCKED search space."""
    return dict(
        n_estimators=trial.suggest_int("n_estimators", 50, 500),
        max_depth=trial.suggest_int("max_depth", 3, 8),
        learning_rate=trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        subsample=trial.suggest_float("subsample", 0.5, 1.0),
        colsample_bytree=trial.suggest_float("colsample_bytree", 0.5, 1.0),
    )


def _cv_folds_for_objective(
    as_of_arr: np.ndarray,
    label_end_arr: np.ndarray | None,
    cpcv_config: CPCVConfig | None,
    inner_cv_n_splits: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per pre-reg §8.3 / §9 (Codex v5.0 R6 P0 fix):
    - If cpcv_config provided: use CPCV outer (10 paths) — pre-reg LOCK
    - Else: fallback to TimeSeriesSplit inner-only (legacy, R6-flagged)
    """
    if cpcv_config is not None:
        if label_end_arr is None:
            raise ValueError("CPCV requires label_end_arr")
        as_of_s = pd.Series(as_of_arr)
        label_end_s = pd.Series(label_end_arr)
        return list(cpcv_splits(as_of_s, label_end_s, cpcv_config))
    return _inner_cv_indices_by_as_of(as_of_arr, inner_cv_n_splits)


def _make_xgb_objective(
    X: np.ndarray,
    y: np.ndarray,
    as_of_arr: np.ndarray,
    fwd_return_arr: np.ndarray,
    top_n: int,
    config: OptunaConfig,
    cv_folds: list[tuple[np.ndarray, np.ndarray]],
) -> Callable:
    def objective(trial) -> float:
        params = _sample_xgb_params(trial)
        sharpes = []
        for train_idx, val_idx in cv_folds:
            model = XGBoostClassifierWrapper(**params)
            model.fit(
                X[train_idx], y[train_idx],
                X_val=X[val_idx], y_val=y[val_idx],
                early_stopping_rounds=config.early_stopping_rounds,
            )
            sh = evaluate_cell_sharpe(
                model, X[val_idx], as_of_arr[val_idx],
                fwd_return_arr[val_idx], top_n,
                annualization=config.annualization,
            )
            sharpes.append(sh)
            trial.report(np.mean(sharpes), step=len(sharpes))
            if trial.should_prune():
                import optuna
                raise optuna.TrialPruned()
        return float(np.mean(sharpes)) if sharpes else 0.0

    return objective


def _make_lambdamart_objective(
    X: np.ndarray,
    y: np.ndarray,
    as_of_arr: np.ndarray,
    fwd_return_arr: np.ndarray,
    top_n: int,
    config: OptunaConfig,
    cv_folds: list[tuple[np.ndarray, np.ndarray]],
) -> Callable:
    def objective(trial) -> float:
        params = _sample_lambdamart_params(trial)
        sharpes = []
        for train_idx, val_idx in cv_folds:
            train_groups = (pd.Series(as_of_arr[train_idx])
                            .value_counts(sort=False).tolist())
            val_groups = (pd.Series(as_of_arr[val_idx])
                          .value_counts(sort=False).tolist())
            model = LambdaMARTWrapper(**params)
            model.fit(
                X[train_idx], y[train_idx], group_train=train_groups,
                X_val=X[val_idx], y_val=y[val_idx], group_val=val_groups,
                early_stopping_rounds=config.early_stopping_rounds,
            )
            sh = evaluate_cell_sharpe(
                model, X[val_idx], as_of_arr[val_idx],
                fwd_return_arr[val_idx], top_n,
                annualization=config.annualization,
            )
            sharpes.append(sh)
            trial.report(np.mean(sharpes), step=len(sharpes))
            if trial.should_prune():
                import optuna
                raise optuna.TrialPruned()
        return float(np.mean(sharpes)) if sharpes else 0.0

    return objective


# ---------------------------------------------------------------------------
# Main entry: run Optuna study for one cell
# ---------------------------------------------------------------------------
def run_optuna_for_cell(
    model_name: Literal["xgboost", "lambdamart"],
    X: np.ndarray,
    y: np.ndarray,
    as_of_arr: np.ndarray,
    fwd_return_arr: np.ndarray,
    top_n: int,
    config: OptunaConfig | None = None,
    *,
    label_end_arr: np.ndarray | None = None,
    cpcv_config: CPCVConfig | None = None,
) -> tuple[dict, float]:
    """Run Optuna study for one (model, top_n) cell.

    Per pre-reg §8.3 + §9 (Codex v5.0 R6 P0 fix 2026-05-26):
    - CPCV outer loop(10 paths)is the LOCKED outer CV
    - Caller must pass cpcv_config + label_end_arr to enable CPCV
    - If cpcv_config is None → falls back to inner TimeSeriesSplit only
      (legacy mode — Codex R6 flagged as spec violation if used in production)

    Returns
    -------
    (best_params, best_value)
        best_params : dict of hyperparameter → value (best trial)
        best_value  : best mean Sharpe across CV folds (CPCV paths or TimeSeriesSplit)
    """
    if config is None:
        config = OptunaConfig()
    if model_name not in ("xgboost", "lambdamart"):
        raise ValueError(f"model_name must be 'xgboost' or 'lambdamart'; got {model_name}")

    cv_folds = _cv_folds_for_objective(
        as_of_arr=as_of_arr,
        label_end_arr=label_end_arr,
        cpcv_config=cpcv_config,
        inner_cv_n_splits=config.inner_cv_n_splits,
    )
    cv_kind = "CPCV" if cpcv_config else "TimeSeriesSplit"
    logger.info("Optuna cell (model=%s top_n=%d): %d CV folds via %s",
                model_name, top_n, len(cv_folds), cv_kind)

    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    sampler = optuna.samplers.TPESampler(seed=config.sampler_seed)
    pruner = optuna.pruners.MedianPruner(n_startup_trials=config.pruner_startup)
    study = optuna.create_study(direction="maximize", sampler=sampler, pruner=pruner)

    if model_name == "xgboost":
        objective = _make_xgb_objective(
            X, y, as_of_arr, fwd_return_arr, top_n, config, cv_folds,
        )
    else:
        objective = _make_lambdamart_objective(
            X, y, as_of_arr, fwd_return_arr, top_n, config, cv_folds,
        )

    study.optimize(objective, n_trials=config.n_trials, show_progress_bar=False)
    return dict(study.best_params), float(study.best_value)
