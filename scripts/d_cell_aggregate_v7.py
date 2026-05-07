"""V0.13 Assertion 3 落地 — 18-cell aggregate engine + sole_survivor logic.

Phase 2 Session 6 (2026-05-05) — H_d_v6 V0.13 §"Code-level enforcement"
Assertion 3 落地 in `d_cell_aggregate_v7.py`:
- DSR n_trials=18 explicit pass per V1.1b ic_analysis.py enforcement
- 18-cell aggregate (6 candidates × 3 top_n)
- L1-L6 gate evaluation per cell
- sole_survivor tie-break per H_d_v6:74 "highest IR > highest mean α"

Scope:
- S6 落地 aggregate logic + DSR enforcement + sole_survivor tie-break
- S6 stub-real split: real cell metrics 由 d_cell_sweep_v7.py::run_cell_sweep_real()
  + BacktestEngine 跑出（user 端 6-12 hr 含 cache fresh-rerun）；S6 aggregate
  接 cell metrics dict 為 input（synthetic fixture 驗 aggregate logic）
- d_cell_aggregate_v7 不直接跑 backtest — 接 (cell_id → metrics) dict

Caller flow (S6.1 user 端 real run):
    # 1. d_cell_sweep_v7.run_cell_sweep_real() 跑 18 cell BacktestEngine
    # 2. 收集 cell metrics 為 dict[(candidate_id, top_n) → metrics_dict]
    # 3. d_cell_aggregate_v7.aggregate_cell_results(cell_metrics) → cell_summary.json

per H_d_v6 V0.13 spec:
- DSR n_trials=18 explicit (V1.1b deflated_sharpe_ratio() raises on None)
- L1 IR ≥ 0.20 / L2 月α ≥ 0.005 / L3 TE ∈ [0.10, 0.30] / L4 ΔMaxDD ≤ +0.05 /
  L5 A1 三子 / L6 80% bootstrap CI lower bound > 0
- sole_survivor: highest IR > highest mean α (V1.1c P1 #14 unit alignment caveat:
  IR annualized vs α monthly — 預設 IR annualized > α monthly 為 tie-break)
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys
from typing import Any

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.d_cell_sweep_v7 import (  # noqa: E402
    CANDIDATE_FACTOR_SETS,
    EXPECTED_N_TRIALS,
    TOP_N_VALUES,
)
from src.analysis.ic_analysis import deflated_sharpe_ratio  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# V0.13 6 hard gates v7 thresholds (LOCKED per H_d_v6:23-36 + 13 pre-commit #1)
# ---------------------------------------------------------------------------
L1_IR_THRESHOLD: float = 0.20  # annualized monthly active IR
L2_MEAN_ALPHA_THRESHOLD: float = 0.005  # monthly alpha
L3_TE_LOWER: float = 0.10
L3_TE_UPPER: float = 0.30
L4_MAX_DD_DIFF_UPPER: float = 0.05  # vs 0050
L5_ACTIVE_CORR_UPPER: float = 0.50
L5_TE_LOWER: float = 0.10
L5_BETA_ADJ_T_LOWER: float = 1.5
L6_BOOTSTRAP_CI_LOWER_THRESHOLD: float = 0.0  # CI lower bound > 0


def _evaluate_gates(
    cell_metrics: dict[str, float],
    bootstrap_ci_lower: float | None = None,
) -> dict[str, bool]:
    """Evaluate L1-L6 hard gates per cell.

    Args:
        cell_metrics: dict with keys 'ir' (annualized) / 'mean_alpha_monthly' /
                      'te' / 'max_dd_diff_vs_0050' / 'active_corr' /
                      'beta_adj_alpha_t'
        bootstrap_ci_lower: 80% bootstrap CI lower bound (Phase 2 S7 owns;
                            None → L6 not evaluated)

    Returns:
        dict[gate_name → PASS bool]
    """
    return {
        "L1_ir_ge_0_20": cell_metrics.get("ir", 0.0) >= L1_IR_THRESHOLD,
        "L2_mean_alpha_ge_0_005": (
            cell_metrics.get("mean_alpha_monthly", 0.0) >= L2_MEAN_ALPHA_THRESHOLD
        ),
        "L3_te_in_range": (
            L3_TE_LOWER <= cell_metrics.get("te", 0.0) <= L3_TE_UPPER
        ),
        "L4_max_dd_diff_le_0_05": (
            cell_metrics.get("max_dd_diff_vs_0050", 1.0) <= L4_MAX_DD_DIFF_UPPER
        ),
        "L5_a1_active_corr_le_0_50": (
            cell_metrics.get("active_corr", 1.0) <= L5_ACTIVE_CORR_UPPER
        ),
        "L5_a1_te_ge_0_10": cell_metrics.get("te", 0.0) >= L5_TE_LOWER,
        "L5_a1_beta_adj_t_gt_1_5": (
            cell_metrics.get("beta_adj_alpha_t", 0.0) > L5_BETA_ADJ_T_LOWER
        ),
        "L6_bootstrap_ci_lower_gt_0": (
            bootstrap_ci_lower is not None
            and bootstrap_ci_lower > L6_BOOTSTRAP_CI_LOWER_THRESHOLD
        ),
    }


def _l5_a1_passes(gate_results: dict[str, bool]) -> bool:
    """L5 A1 gate: 3 sub-conditions all must pass."""
    return (
        gate_results["L5_a1_active_corr_le_0_50"]
        and gate_results["L5_a1_te_ge_0_10"]
        and gate_results["L5_a1_beta_adj_t_gt_1_5"]
    )


def _all_l1_l6_passes(gate_results: dict[str, bool]) -> bool:
    """All 6 hard gates (L1-L6) must pass for cell to be Outcome-1 candidate."""
    return (
        gate_results["L1_ir_ge_0_20"]
        and gate_results["L2_mean_alpha_ge_0_005"]
        and gate_results["L3_te_in_range"]
        and gate_results["L4_max_dd_diff_le_0_05"]
        and _l5_a1_passes(gate_results)
        and gate_results["L6_bootstrap_ci_lower_gt_0"]
    )


def aggregate_cell_results(
    cell_metrics: dict[tuple[str, int], dict[str, float]],
    bootstrap_ci_lowers: dict[tuple[str, int], float] | None = None,
    n_obs: int = 60,
    n_trials: int = EXPECTED_N_TRIALS,
) -> dict[str, Any]:
    """Aggregate 18-cell metrics + DSR + L1-L6 gates + sole_survivor.

    V0.13 Assertion 3 enforce: n_trials defaults to EXPECTED_N_TRIALS (= 18 from
    d_cell_sweep_v7 module-level lock); explicit pass via deflated_sharpe_ratio
    (which V1.1b raises on None).

    Args:
        cell_metrics: dict[(candidate_id, top_n) → metrics dict]
                      metrics dict requires keys: ir / mean_alpha_monthly / te /
                      max_dd_diff_vs_0050 / active_corr / beta_adj_alpha_t /
                      sharpe_for_dsr (optional)
        bootstrap_ci_lowers: dict[(candidate_id, top_n) → 80% CI lower bound]
                            (Phase 2 S7 produces; None → L6 skipped + caveat)
        n_obs: number of monthly observations for DSR (default 60 IS = 5 yr × 12 mo)
        n_trials: DSR multi-trial deflate count (default EXPECTED_N_TRIALS=18 per
                  V0.13 Assertion 3 + V1.1b enforcement)

    Returns:
        dict with keys 'cells' (per-cell list), 'sole_survivor' (winning cell or None),
        'outcome_classification' (Outcome 1/2/4 per H_d_v6:200-208).

    Raises:
        ValueError: if cell_metrics 數量 ≠ n_trials (V0.13 Assertion 3 violation)
        ValueError (via deflated_sharpe_ratio): if n_trials kwarg explicit None
                                                  (V1.1b enforcement)
    """
    if len(cell_metrics) != n_trials:
        raise ValueError(
            f"V0.13 Assertion 3 FAIL: cell_metrics count {len(cell_metrics)} ≠ "
            f"expected n_trials {n_trials} (= len(CANDIDATE_FACTOR_SETS) × "
            f"len(TOP_N_VALUES) per H_d_v6:142)."
        )

    cells_summary: list[dict[str, Any]] = []
    bootstrap_ci_lowers = bootstrap_ci_lowers or {}

    for (candidate_id, top_n), metrics in cell_metrics.items():
        # V0.13 Assertion 2 composition guard: D-A excluded
        if candidate_id == "D-A":
            raise ValueError(
                f"V0.13 Assertion 2 FAIL: cell ({candidate_id}, {top_n}) "
                f"contains D-A (pre-disqualified)."
            )

        ci_lower = bootstrap_ci_lowers.get((candidate_id, top_n))
        gate_results = _evaluate_gates(metrics, bootstrap_ci_lower=ci_lower)

        # DSR per cell with explicit n_trials (V1.1b enforce raise on None)
        sharpe = float(metrics.get("sharpe_for_dsr", metrics.get("ir", 0.0)))
        dsr = deflated_sharpe_ratio(
            sharpe,
            n_obs=n_obs,
            n_trials=n_trials,  # V0.13 Assertion 3 + V1.1b explicit kwarg
        )

        cells_summary.append({
            "candidate_id": candidate_id,
            "top_n": top_n,
            "metrics": metrics,
            "bootstrap_ci_lower": ci_lower,
            "dsr": dsr,
            "gates": gate_results,
            "l5_a1_passed": _l5_a1_passes(gate_results),
            "all_l1_l6_passed": _all_l1_l6_passes(gate_results),
        })

    # Sole survivor: highest IR among Outcome-1 cells (per H_d_v6:74 tie-break)
    outcome_1_cells = [c for c in cells_summary if c["all_l1_l6_passed"]]
    sole_survivor: dict[str, Any] | None = None
    if outcome_1_cells:
        # Tie-break: highest IR > highest mean α (H_d_v6:74)
        sole_survivor = max(
            outcome_1_cells,
            key=lambda c: (
                c["metrics"].get("ir", 0.0),  # primary: IR
                c["metrics"].get("mean_alpha_monthly", 0.0),  # tie-break
            ),
        )

    # Outcome classification per H_d_v6:200-208
    # 6 gates: L1 / L2 / L3 / L4 / L5(A1 三子合) / L6
    def _count_l1_l6_passed(c: dict[str, Any]) -> int:
        return (
            int(c["gates"]["L1_ir_ge_0_20"])
            + int(c["gates"]["L2_mean_alpha_ge_0_005"])
            + int(c["gates"]["L3_te_in_range"])
            + int(c["gates"]["L4_max_dd_diff_le_0_05"])
            + int(c["l5_a1_passed"])
            + int(c["gates"]["L6_bootstrap_ci_lower_gt_0"])
        )

    n_pass_l1_l6 = sum(1 for c in cells_summary if c["all_l1_l6_passed"])
    n_pass_4_or_5_of_6 = sum(
        1 for c in cells_summary
        if 4 <= _count_l1_l6_passed(c) <= 5
    )
    if n_pass_l1_l6 >= 1:
        outcome = "Outcome-1 Full Pass"
    elif n_pass_4_or_5_of_6 >= 1:
        outcome = "Outcome-2 Partial"
    else:
        outcome = "Outcome-4 Full Fail"

    return {
        "n_trials_dsr": n_trials,
        "expected_n_trials_per_v0_13": EXPECTED_N_TRIALS,
        "n_cells_aggregated": len(cells_summary),
        "outcome_classification": outcome,
        "n_outcome_1_cells": n_pass_l1_l6,
        "sole_survivor": sole_survivor,
        "cells": cells_summary,
    }


def write_cell_summary(
    aggregate_result: dict[str, Any],
    output_path: pathlib.Path,
) -> None:
    """Write aggregate result to JSON. Used for cell_summary_v6.json output
    consumed by R25-final Codex audit + Phase 2 S8 sole_survivor lock."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(aggregate_result, f, ensure_ascii=False, indent=2, default=str)
    logger.info("cell_summary written: %s", output_path)


def main() -> None:
    """CLI entrypoint stub. Real wire-up in S6.1 (cache fresh-rerun + 18 cell run).

    For S6 stub-level verification, run:
        python scripts/d_cell_aggregate_v7.py
    will print Assertion 2/3 enforcement + V0.13 spec lock summary (no real run).
    """
    print(f"d_cell_aggregate_v7 V0.13 spec lock summary:")
    print(f"  CANDIDATE_FACTOR_SETS: {CANDIDATE_FACTOR_SETS}")
    print(f"  TOP_N_VALUES: {TOP_N_VALUES}")
    print(f"  EXPECTED_N_TRIALS (V0.13 Assertion 3): {EXPECTED_N_TRIALS}")
    print(f"  L1 IR threshold: {L1_IR_THRESHOLD}")
    print(f"  L2 mean alpha threshold: {L2_MEAN_ALPHA_THRESHOLD}")
    print(f"  L3 TE range: [{L3_TE_LOWER}, {L3_TE_UPPER}]")
    print(f"  L4 max DD diff upper: {L4_MAX_DD_DIFF_UPPER}")
    print(f"  L5 A1 sub-conditions: active_corr <= {L5_ACTIVE_CORR_UPPER} / "
          f"TE >= {L5_TE_LOWER} / beta-adj-t > {L5_BETA_ADJ_T_LOWER}")
    print(f"  L6 bootstrap CI lower threshold: > {L6_BOOTSTRAP_CI_LOWER_THRESHOLD}")
    print(f"  S6 stub: real cell_metrics input from d_cell_sweep_v7.run_cell_sweep_real()")
    print(f"            (S6.1 user 端 cache fresh-rerun 6-12 hr + 18 cell BacktestEngine run)")


if __name__ == "__main__":
    main()
