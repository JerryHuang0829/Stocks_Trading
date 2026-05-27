"""v5.0 Step 5 — Experiment orchestrator.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §12 Linear baseline comparison protocol (LOCKED — Option A)
  §13 condition 14 — 5 deliverables

Orchestrates:
  1. For each (model ∈ {xgboost, lambdamart}, top_n ∈ {15,20,25,30}) cell:
        - Run Optuna 50 trials (independent per cell, Option A)
        - Retrain best on full IS
        - Predict on 2025 OOS
        - Compute IS Sharpe (CPCV mean) + OOS Sharpe + Sharpe diff vs baseline
  2. For each top_n: compute baseline z-score equal-weight on IS + OOS
  3. Aggregate results → DSR n_trials=400 multi-test corrected verdict

Deliverables (caller writes JSON / md):
  - cell_summary : per (model, top_n) cell IS + OOS metrics
  - baseline_summary : per top_n cell baseline metrics
  - vs_baseline : Sharpe_diff[top_n][model] table
  - shap : best-model SHAP summary
  - dsr_audit : DSR n_trials=400 confidence per cell

This module is the PIPELINE only — does NOT load cache, that lives in
`scripts/_run_v5_ml_experiment.py` (CLI entry).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from src.analysis.cpcv import CPCVConfig
from src.analysis.ml_models import (
    LOCKED_TOP_N_VALUES,
    LambdaMARTWrapper,
    XGBoostClassifierWrapper,
    baseline_score,
)
from src.analysis.ml_optuna import (
    OptunaConfig,
    _portfolio_monthly_returns_from_scores,
    _sharpe_ratio,
    run_optuna_for_cell,
)

logger = logging.getLogger(__name__)


@dataclass
class CellResult:
    """Per (model, top_n) cell IS + OOS metrics."""
    model_name: str
    top_n: int
    best_hyperparams: dict
    is_sharpe_inner_cv: float    # mean inner CV Sharpe (Optuna best)
    oos_sharpe: float            # 2025 strict OOS Sharpe
    oos_monthly_returns: list[float]
    oos_dates: list[str]
    n_oos_periods: int
    # Codex v5.0 R6 P1 fix: persist actual holdings for realised turnover compute
    oos_holdings: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "top_n": self.top_n,
            "best_hyperparams": self.best_hyperparams,
            "is_sharpe_inner_cv": float(self.is_sharpe_inner_cv),
            "oos_sharpe": float(self.oos_sharpe),
            "oos_monthly_returns": [float(r) for r in self.oos_monthly_returns],
            "oos_dates": list(self.oos_dates),
            "n_oos_periods": int(self.n_oos_periods),
            "oos_holdings": [list(h) for h in self.oos_holdings],
        }


@dataclass
class BaselineResult:
    """Per top_n linear baseline IS + OOS metrics."""
    top_n: int
    oos_sharpe: float
    oos_monthly_returns: list[float]
    oos_dates: list[str]
    n_oos_periods: int

    def to_dict(self) -> dict:
        return {
            "top_n": self.top_n,
            "oos_sharpe": float(self.oos_sharpe),
            "oos_monthly_returns": [float(r) for r in self.oos_monthly_returns],
            "oos_dates": list(self.oos_dates),
            "n_oos_periods": int(self.n_oos_periods),
        }


def _make_groups(as_of_arr: np.ndarray) -> list[int]:
    """Per-as_of row counts for LambdaMART query groups."""
    return pd.Series(as_of_arr).value_counts(sort=False).tolist()


def train_best_on_is_and_predict_oos(
    model_name: Literal["xgboost", "lambdamart"],
    best_params: dict,
    X_is: np.ndarray,
    y_is: np.ndarray,
    as_of_is: np.ndarray,
    X_oos: np.ndarray,
    as_of_oos: np.ndarray,
    fwd_oos: np.ndarray,
    top_n: int,
    symbol_oos: np.ndarray | None = None,
) -> tuple[float, pd.Series, list[list[str]]]:
    """Retrain best hyperparameters on full IS, predict OOS, compute Sharpe.

    Returns (oos_sharpe, oos_monthly_returns_series, holdings_by_period).
    Codex v5.0 R6 P1 fix: also return per-period selected stock IDs for
    realised turnover computation in audit step.
    """
    if model_name == "xgboost":
        model = XGBoostClassifierWrapper(**best_params)
        model.fit(X_is, y_is)
    elif model_name == "lambdamart":
        model = LambdaMARTWrapper(**best_params)
        groups_is = _make_groups(as_of_is)
        model.fit(X_is, y_is, group_train=groups_is)
    else:
        raise ValueError(f"unknown model: {model_name}")
    scores = model.predict_score(X_oos)
    monthly_returns = _portfolio_monthly_returns_from_scores(
        scores, as_of_oos, fwd_oos, top_n,
    )
    sh = _sharpe_ratio(monthly_returns, annualization=12)

    # Per-period holdings for realised turnover (Codex v5.0 R6 P1 fix)
    holdings: list[list[str]] = []
    if symbol_oos is not None:
        df = pd.DataFrame({"score": scores, "as_of": as_of_oos, "symbol": symbol_oos})
        for as_of in sorted(pd.unique(as_of_oos)):
            group = df[df["as_of"] == as_of]
            top = group.nlargest(top_n, "score", keep="first")
            holdings.append(top["symbol"].astype(str).tolist())
    return sh, monthly_returns, holdings


def run_ml_cells(
    feature_matrix_is: pd.DataFrame,
    feature_matrix_oos: pd.DataFrame,
    feature_cols: list[str],
    optuna_config: OptunaConfig | None = None,
    top_n_values: tuple[int, ...] = LOCKED_TOP_N_VALUES,
    models: tuple[str, ...] = ("xgboost", "lambdamart"),
    cpcv_config: CPCVConfig | None = None,
) -> list[CellResult]:
    """For each (model, top_n) cell: Optuna → best → OOS → CellResult.

    Per pre-reg §12 Option A + §9 CPCV LOCK (Codex v5.0 R6 P0 fix):
    - Caller SHOULD pass cpcv_config (production default = CPCVConfig() per pre-reg LOCK)
    - If cpcv_config is None → legacy TimeSeriesSplit-only mode (testing only)
    """
    if optuna_config is None:
        optuna_config = OptunaConfig()
    X_is = feature_matrix_is[feature_cols].values.astype(float)
    y_is = feature_matrix_is["label_top_decile"].values.astype(int)
    as_of_is = feature_matrix_is["as_of"].values
    label_end_is = feature_matrix_is["label_end"].values
    fwd_is = feature_matrix_is["forward_return"].values

    X_oos = feature_matrix_oos[feature_cols].values.astype(float)
    as_of_oos = feature_matrix_oos["as_of"].values
    fwd_oos = feature_matrix_oos["forward_return"].values
    symbol_oos = feature_matrix_oos["symbol"].values

    cells: list[CellResult] = []
    for model_name in models:
        for top_n in top_n_values:
            logger.info("running cell: model=%s top_n=%d (cpcv=%s)",
                        model_name, top_n, cpcv_config is not None)
            best_params, best_value = run_optuna_for_cell(
                model_name, X_is, y_is, as_of_is, fwd_is,
                top_n, config=optuna_config,
                label_end_arr=label_end_is, cpcv_config=cpcv_config,
            )
            oos_sharpe, oos_returns, holdings = train_best_on_is_and_predict_oos(
                model_name, best_params,
                X_is, y_is, as_of_is, X_oos, as_of_oos, fwd_oos, top_n,
                symbol_oos=symbol_oos,
            )
            cells.append(CellResult(
                model_name=model_name,
                top_n=top_n,
                best_hyperparams=best_params,
                is_sharpe_inner_cv=best_value,
                oos_sharpe=oos_sharpe,
                oos_monthly_returns=oos_returns.tolist(),
                oos_dates=[str(d.date()) for d in oos_returns.index],
                n_oos_periods=int(len(oos_returns)),
                oos_holdings=holdings,
            ))
    return cells


def run_baseline_cells(
    feature_matrix_oos: pd.DataFrame,
    base_feature_names: list[str],
    top_n_values: tuple[int, ...] = LOCKED_TOP_N_VALUES,
) -> list[BaselineResult]:
    """Linear baseline (z-score equal weight on BASE features) per top_n on OOS.

    Per pre-reg §7.3 + §12: baseline uses only the 5 base features (no
    contextual / interactions) — that's the LOCKED control.
    """
    scores = baseline_score(feature_matrix_oos, base_feature_names)
    out: list[BaselineResult] = []
    for top_n in top_n_values:
        monthly_returns = _portfolio_monthly_returns_from_scores(
            scores.values,
            feature_matrix_oos["as_of"].values,
            feature_matrix_oos["forward_return"].values,
            top_n,
        )
        sh = _sharpe_ratio(monthly_returns, annualization=12)
        out.append(BaselineResult(
            top_n=top_n,
            oos_sharpe=sh,
            oos_monthly_returns=monthly_returns.tolist(),
            oos_dates=[str(d.date()) for d in monthly_returns.index],
            n_oos_periods=int(len(monthly_returns)),
        ))
    return out


def build_vs_baseline_summary(
    cells: list[CellResult],
    baselines: list[BaselineResult],
    sharpe_diff_threshold: float = 0.05,
) -> pd.DataFrame:
    """Per pre-reg §1 + §12: ML must beat baseline by ≥ +0.05 Sharpe."""
    baseline_by_top_n = {b.top_n: b.oos_sharpe for b in baselines}
    rows = []
    for c in cells:
        baseline_sh = baseline_by_top_n.get(c.top_n, np.nan)
        diff = c.oos_sharpe - baseline_sh
        rows.append({
            "model_name": c.model_name,
            "top_n": c.top_n,
            "ml_oos_sharpe": c.oos_sharpe,
            "baseline_oos_sharpe": baseline_sh,
            "sharpe_diff": diff,
            "ml_beats_baseline_by_threshold": diff >= sharpe_diff_threshold,
        })
    return pd.DataFrame(rows)
