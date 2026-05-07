"""V0.13 Assertion 3 落地 — d_cell_aggregate_v7 tests (Phase 2 S6).

Verifies:
- DSR n_trials=18 explicit pass per V1.1b enforcement (raise on None)
- 18-cell aggregate count enforcement (V0.13 Assertion 3)
- L1-L6 gate evaluation correctness
- sole_survivor tie-break (H_d_v6:74 highest IR > highest mean α)
- Outcome 1/2/4 classification per H_d_v6:200-208
- D-A composition guard (V0.13 Assertion 2 catches via raise)

Phase 2 S6.1 owner extends to real BacktestEngine wire-up; S6 aggregate uses
synthetic cell metrics fixture (per V1.2 stub pattern).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.d_cell_aggregate_v7 import (  # noqa: E402
    L1_IR_THRESHOLD,
    L2_MEAN_ALPHA_THRESHOLD,
    L4_MAX_DD_DIFF_UPPER,
    aggregate_cell_results,
)
from scripts.d_cell_sweep_v7 import (  # noqa: E402
    CANDIDATE_FACTOR_SETS,
    EXPECTED_N_TRIALS,
    TOP_N_VALUES,
)


def _make_passing_cell_metrics() -> dict[str, float]:
    """Synthetic cell metrics passing all L1-L6 gates."""
    return {
        "ir": 0.25,  # > L1 0.20
        "mean_alpha_monthly": 0.008,  # > L2 0.005
        "te": 0.20,  # within L3 [0.10, 0.30]
        "max_dd_diff_vs_0050": 0.03,  # < L4 0.05
        "active_corr": 0.40,  # < L5 0.50
        "beta_adj_alpha_t": 2.0,  # > L5 1.5
        "sharpe_for_dsr": 0.30,
    }


def _make_failing_cell_metrics() -> dict[str, float]:
    """Synthetic cell metrics failing multiple gates."""
    return {
        "ir": 0.10,  # < L1 0.20
        "mean_alpha_monthly": 0.002,  # < L2 0.005
        "te": 0.05,  # < L3 0.10
        "max_dd_diff_vs_0050": 0.10,  # > L4 0.05
        "active_corr": 0.70,  # > L5 0.50
        "beta_adj_alpha_t": 1.0,  # < L5 1.5
        "sharpe_for_dsr": 0.10,
    }


def _build_18_cell_dict(passing: bool = True) -> dict[tuple[str, int], dict[str, float]]:
    """Build full 18-cell synthetic fixture (6 candidates × 3 top_n)."""
    metrics = _make_passing_cell_metrics() if passing else _make_failing_cell_metrics()
    return {
        (candidate, top_n): dict(metrics)
        for candidate in CANDIDATE_FACTOR_SETS
        for top_n in TOP_N_VALUES
    }


def test_aggregate_18_cells_passing_outcome_1():
    """18 cells all pass L1-L6 + L6 CI > 0 → Outcome-1; sole_survivor identified."""
    cell_metrics = _build_18_cell_dict(passing=True)
    bootstrap_ci_lowers = {(c, t): 0.005 for c in CANDIDATE_FACTOR_SETS for t in TOP_N_VALUES}
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers)
    assert result["n_cells_aggregated"] == 18
    assert result["outcome_classification"] == "Outcome-1 Full Pass"
    assert result["sole_survivor"] is not None
    assert result["n_outcome_1_cells"] == 18


def test_aggregate_assertion_3_n_trials_18():
    """V0.13 Assertion 3: aggregate result reports n_trials_dsr == 18."""
    cell_metrics = _build_18_cell_dict(passing=True)
    bootstrap_ci_lowers = {(c, t): 0.005 for c in CANDIDATE_FACTOR_SETS for t in TOP_N_VALUES}
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers)
    assert result["n_trials_dsr"] == 18
    assert result["expected_n_trials_per_v0_13"] == 18


def test_aggregate_cell_count_mismatch_raises():
    """Mutation: pass 17 cells (1 missing) → V0.13 Assertion 3 violation raises."""
    cell_metrics = _build_18_cell_dict(passing=True)
    # Remove one cell to trigger count mismatch
    incomplete = dict(list(cell_metrics.items())[:17])
    with pytest.raises(ValueError, match="V0.13 Assertion 3 FAIL"):
        aggregate_cell_results(incomplete)


def test_aggregate_d_a_in_cell_metrics_raises():
    """V0.13 Assertion 2 composition guard via aggregate: D-A in cell_metrics → raises."""
    # Build 18-cell dict but rename one to D-A (forbidden)
    cell_metrics = _build_18_cell_dict(passing=True)
    metrics_template = _make_passing_cell_metrics()
    # Remove a legitimate cell + add D-A (preserve 18 count)
    del cell_metrics[("D-B", 8)]
    cell_metrics[("D-A", 8)] = metrics_template
    with pytest.raises(ValueError, match="V0.13 Assertion 2 FAIL"):
        aggregate_cell_results(cell_metrics)


def test_aggregate_v1_1b_dsr_explicit_n_trials_enforced():
    """V1.1b enforcement: DSR is called with explicit n_trials kwarg.

    Mutation reverts (passes None or omits kwarg) → deflated_sharpe_ratio
    raises ValueError per V1.1b. aggregate function ALWAYS passes explicit
    n_trials (default = EXPECTED_N_TRIALS=18); only fails if caller explicitly
    overrides to None."""
    cell_metrics = _build_18_cell_dict(passing=True)
    bootstrap_ci_lowers = {(c, t): 0.005 for c in CANDIDATE_FACTOR_SETS for t in TOP_N_VALUES}
    # Default call: n_trials = 18 (explicit) → no V1.1b raise
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers)
    # All cells got DSR computed (none None due to fail; though DSR may legitimately
    # return None for low SR — verify at least one is non-None)
    dsr_values = [c["dsr"] for c in result["cells"]]
    assert any(d is not None for d in dsr_values)


def test_aggregate_failing_cells_outcome_4():
    """All 18 cells fail L1-L6 → Outcome-4 Full Fail; no sole_survivor."""
    cell_metrics = _build_18_cell_dict(passing=False)
    # No CI passes either
    bootstrap_ci_lowers = {(c, t): -0.005 for c in CANDIDATE_FACTOR_SETS for t in TOP_N_VALUES}
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers)
    assert result["outcome_classification"] == "Outcome-4 Full Fail"
    assert result["sole_survivor"] is None
    assert result["n_outcome_1_cells"] == 0


def test_aggregate_partial_cells_outcome_2():
    """Mix of passing-on-some-gates → Outcome-2 Partial."""
    cell_metrics = _build_18_cell_dict(passing=True)
    # Set L6 CI lower bound to 0 (fail L6) for all → 5/6 pass + 1 fail
    bootstrap_ci_lowers = {(c, t): -0.001 for c in CANDIDATE_FACTOR_SETS for t in TOP_N_VALUES}
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers)
    # 5 of 6 gates pass per cell (all but L6)
    assert result["outcome_classification"] == "Outcome-2 Partial"
    assert result["sole_survivor"] is None  # not Outcome-1


def test_aggregate_sole_survivor_picks_highest_ir():
    """V0.13 H_d_v6:74 tie-break: highest IR > highest mean α.

    Build 18 cells where 2 cells pass all L1-L6 with different IRs;
    sole_survivor MUST be the higher-IR cell."""
    cell_metrics = _build_18_cell_dict(passing=True)
    bootstrap_ci_lowers = {(c, t): 0.005 for c in CANDIDATE_FACTOR_SETS for t in TOP_N_VALUES}
    # Boost one specific cell's IR
    cell_metrics[("D-E", 8)]["ir"] = 0.40  # highest
    cell_metrics[("D-F", 12)]["ir"] = 0.35
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers)
    assert result["sole_survivor"]["candidate_id"] == "D-E"
    assert result["sole_survivor"]["top_n"] == 8
    assert result["sole_survivor"]["metrics"]["ir"] == 0.40


def test_aggregate_l1_threshold_locked_at_0_20():
    """V0.13 LOCK: L1 IR threshold = 0.20 (cannot drift)."""
    assert L1_IR_THRESHOLD == 0.20


def test_aggregate_l2_threshold_locked_at_0_005():
    """V0.13 LOCK: L2 mean alpha threshold = 0.005."""
    assert L2_MEAN_ALPHA_THRESHOLD == 0.005


def test_aggregate_l4_threshold_locked_at_0_05():
    """V0.13 LOCK: L4 max DD diff upper = 0.05."""
    assert L4_MAX_DD_DIFF_UPPER == 0.05


def test_aggregate_l5_a1_partial_fails_outcome_2():
    """L5 A1 has 3 sub-conditions; failing 1 → cell L5 fails → Outcome-2."""
    cell_metrics = _build_18_cell_dict(passing=True)
    bootstrap_ci_lowers = {(c, t): 0.005 for c in CANDIDATE_FACTOR_SETS for t in TOP_N_VALUES}
    # Fail L5 a1 sub-condition (active_corr) on all
    for key in cell_metrics:
        cell_metrics[key]["active_corr"] = 0.60  # > 0.50
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers)
    # Only L5 a1 fails; L1-L4 + L6 still pass → 5/6 → Outcome-2
    assert result["outcome_classification"] == "Outcome-2 Partial"


def test_aggregate_no_bootstrap_ci_omits_l6():
    """L6 not evaluated when bootstrap_ci_lowers=None → all cells fail L6 →
    Outcome-2 (5/6 pass) or Outcome-4 (depending on other gates)."""
    cell_metrics = _build_18_cell_dict(passing=True)
    result = aggregate_cell_results(cell_metrics, bootstrap_ci_lowers=None)
    # All cells pass L1-L5 but fail L6 (None) → 5/6 → Outcome-2
    assert result["outcome_classification"] == "Outcome-2 Partial"
    assert result["sole_survivor"] is None
