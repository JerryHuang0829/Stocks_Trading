"""Tests for src/analysis/ml_audit.py — Step 6 L1-L6 + DSR evaluator."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_audit import (  # noqa: E402
    AUDIT_JSON_SCHEMA_VERSION,
    DSR_FORMULA_VERSION,
    DSR_N_TRIALS,
    L1_IR_THRESHOLD,
    L6_BOOTSTRAP_CI_LEVEL,
    _bootstrap_ci_lower,
    compute_actual_turnover_one_way,
    compute_beta_adj_t_stat,
    evaluate_cell_gates,
    run_audit_from_cell_summary,
)


def test_locked_thresholds():
    """pre-reg §10 + §8.4 LOCK values."""
    assert L1_IR_THRESHOLD == 0.20
    assert L6_BOOTSTRAP_CI_LEVEL == 0.80
    assert DSR_N_TRIALS == 400


def test_bootstrap_ci_lower_positive_for_strong_returns():
    rng = np.random.RandomState(0)
    rets = pd.Series(rng.normal(0.02, 0.01, 60))   # strong positive mean
    lo = _bootstrap_ci_lower(rets, n_boot=200, seed=0)
    assert lo > 0


def test_bootstrap_ci_lower_is_below_mean_for_noisy_returns():
    """Lower CI bound should be below the sample mean (CI brackets it)."""
    rng = np.random.RandomState(0)
    rets = pd.Series(rng.normal(0.001, 0.05, 12))
    lo = _bootstrap_ci_lower(rets, n_boot=200, seed=0)
    assert lo < rets.mean() + 0.01   # lower bound is below the mean (+ slack)


def test_evaluate_cell_gates_strong_signal_passes_some():
    """Strong active returns should pass L1 + L2 + L6 at least."""
    n = 12
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    # Portfolio = bench + 1.5%/month active (strong)
    port = bench + 0.015
    port.index = bench.index
    audit = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="strong_test", model_name="xgboost", top_n=15,
        oos_sharpe=1.0,
    )
    # L1 IR should be high (very strong active = bench + 1.5% with 0 noise)
    l1 = next(g for g in audit.gates if g.name == "L1_IR")
    # Active = port - bench = 0.015 every month → mean 0.015, std ~0 → huge IR
    assert l1.passed
    # Some gates may still fail (L3 TE too low because perfectly constant active)
    # but at least L1 + L2 pass
    assert audit.n_gates_pass >= 2


def test_evaluate_cell_gates_zero_signal_fails():
    """Zero active returns → most gates fail."""
    n = 12
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    port = bench.copy()   # zero active
    audit = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="zero_test", model_name="xgboost", top_n=15,
        oos_sharpe=0.0,
    )
    # L1 IR = 0 / 0 → 0 (or near), fails ≥ 0.20
    l1 = next(g for g in audit.gates if g.name == "L1_IR")
    assert not l1.passed


def test_evaluate_cell_gates_handles_insufficient_periods():
    bench = pd.Series([0.01], index=[pd.Timestamp("2025-01-15")])
    port = pd.Series([0.02], index=[pd.Timestamp("2025-01-15")])
    audit = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="tiny", model_name="xgboost", top_n=15, oos_sharpe=0.0,
    )
    # All gates marked insufficient
    for g in audit.gates:
        assert not g.passed
        assert "insufficient" in g.note or g.value is None


# ===========================================================================
# Turnover / beta-adjusted t-stat / DSR provenance
# ===========================================================================
def test_compute_actual_turnover_one_way_zero_for_identical_holdings():
    """Same holdings every period → 0 new names → turnover 0."""
    holdings = [set(["A", "B", "C"])] * 3
    assert compute_actual_turnover_one_way(holdings) == 0.0


def test_compute_actual_turnover_one_way_one_for_total_change():
    """Completely different holdings every period → all new → turnover 1."""
    holdings = [set(["A", "B"]), set(["C", "D"]), set(["E", "F"])]
    assert compute_actual_turnover_one_way(holdings) == 1.0


def test_compute_actual_turnover_one_way_partial():
    """1 new name out of 4 in second period → 0.25 for that pair."""
    holdings = [{"A", "B", "C", "D"}, {"A", "B", "C", "X"}]
    assert compute_actual_turnover_one_way(holdings) == pytest.approx(0.25)


def test_compute_actual_turnover_one_way_empty():
    assert compute_actual_turnover_one_way([]) == 0.0
    assert compute_actual_turnover_one_way([set(["A"])]) == 0.0


def test_compute_beta_adj_t_stat_strong_alpha():
    """Portfolio = bench + constant → alpha > 0, t large."""
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, 50))
    port = bench + 0.02   # +2%/period constant alpha
    alpha, beta, t = compute_beta_adj_t_stat(port, bench)
    assert alpha == pytest.approx(0.02, abs=1e-9)
    assert beta == pytest.approx(1.0, abs=1e-9)
    assert t > 100  # essentially infinite SE since residuals are 0


def test_compute_beta_adj_t_stat_zero_alpha():
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, 50))
    port = bench   # identical → alpha 0
    alpha, beta, t = compute_beta_adj_t_stat(port, bench)
    assert alpha == pytest.approx(0.0, abs=1e-9)
    assert beta == pytest.approx(1.0, abs=1e-9)


def test_compute_beta_adj_t_stat_insufficient_n():
    bench = pd.Series([0.01, 0.02])
    port = pd.Series([0.02, 0.03])
    alpha, beta, t = compute_beta_adj_t_stat(port, bench)
    assert (alpha, beta, t) == (0.0, 0.0, 0.0)


def test_evaluate_cell_gates_actual_turnover_replaces_proxy():
    """When turnover_one_way is passed, L2 uses it instead of 2.0 proxy."""
    n = 12
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    port = bench + 0.01
    # Proxy 2.0 → cost = 0.0067 × 2.0 = 0.0134; net α = 0.01 - 0.0134 = -0.0034
    audit_proxy = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="proxy", model_name="xgboost", top_n=15,
        oos_sharpe=0.5,
    )
    # Actual 0.3 → cost = 0.0067 × 0.3 = 0.00201; net α = 0.01 - 0.002 = 0.008
    audit_actual = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="actual", model_name="xgboost", top_n=15,
        oos_sharpe=0.5, turnover_one_way=0.3,
    )
    l2_proxy = next(g for g in audit_proxy.gates if g.name == "L2_net_alpha_monthly")
    l2_actual = next(g for g in audit_actual.gates if g.name == "L2_net_alpha_monthly")
    assert l2_actual.value > l2_proxy.value   # lower cost = higher net α
    assert "actual" in l2_actual.threshold_desc.lower() or \
           "0.300" in l2_actual.threshold_desc


def test_audit_dsr_psi_attached():
    n = 12
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    port = bench + rng.normal(0.01, 0.02, n)
    audit = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="dsr_test", model_name="xgboost", top_n=15,
        oos_sharpe=0.5, dsr_n_trials=400,
    )
    assert audit.dsr_n_trials == 400
    # dsr_psi should be a float in [0, 1] or None
    if audit.dsr_psi is not None:
        assert 0.0 <= audit.dsr_psi <= 1.0


def test_audit_dsr_n_trials_metadata_propagates_to_cell():
    """dsr_n_trials passed to evaluate_cell_gates must appear in the
    per-cell CellAudit dataclass AND its to_dict output — guarding the
    previously-silent mismatch where top-level audit said n=N but per-cell
    said n=400 because the dataclass default leaked."""
    n = 12
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    port = bench + 0.005
    for n_trials in (8, 50, 400):
        audit = evaluate_cell_gates(
            oos_returns=port, bench_monthly=bench,
            cell_id=f"meta_test_n{n_trials}", model_name="xgboost", top_n=15,
            oos_sharpe=0.5, dsr_n_trials=n_trials,
        )
        assert audit.dsr_n_trials == n_trials, (
            f"dataclass dsr_n_trials mismatch: got {audit.dsr_n_trials}, "
            f"expected {n_trials}"
        )
        d = audit.to_dict()
        assert d["dsr_n_trials"] == n_trials, (
            f"to_dict dsr_n_trials mismatch: got {d['dsr_n_trials']}, "
            f"expected {n_trials}"
        )


def test_audit_dsr_observed_sr_is_per_period_active_not_oos_sharpe():
    """audit.dsr_observed_sr must be per-period active SR (mean(active)/std(active)),
    NOT the annualized oos_sharpe parameter — observability fix so JSON readers
    can verify what was actually fed to deflated_sharpe_ratio."""
    n = 12
    rng = np.random.RandomState(7)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    # Construct deterministic active: 1.0%/month with 2.0%/month std
    active_known = pd.Series([0.03, -0.01] * (n // 2),
                             index=bench.index)
    port = bench + active_known
    expected_per_period_sr = float(active_known.mean() / active_known.std(ddof=1))
    audit = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="basis_test", model_name="xgboost", top_n=15,
        oos_sharpe=99.0,   # intentionally wrong → must NOT show up as observed_sr
        dsr_n_trials=8,
    )
    assert audit.dsr_observed_sr is not None
    assert abs(audit.dsr_observed_sr - expected_per_period_sr) < 1e-9, (
        f"dsr_observed_sr {audit.dsr_observed_sr} should be per-period active SR "
        f"{expected_per_period_sr}, not oos_sharpe=99.0"
    )
    # Provenance metadata present
    d = audit.to_dict()
    assert "dsr_observed_sr_basis" in d
    assert "dsr_formula_version" in d
    assert "per_period" in d["dsr_observed_sr_basis"]


def test_dsr_n_trials_module_constant_isolation():
    """Passing dsr_n_trials kwarg must NOT mutate the module-level DSR_N_TRIALS
    constant (guard against future refactor that might write back to module)."""
    import src.analysis.ml_audit as ml_audit_module
    original = ml_audit_module.DSR_N_TRIALS
    n = 12
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    port = bench + 0.003
    evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="isolation", model_name="xgboost", top_n=15,
        oos_sharpe=0.5, dsr_n_trials=8,
    )
    assert ml_audit_module.DSR_N_TRIALS == original == 400


def test_audit_dsr_observed_sr_none_when_active_std_degenerate():
    """When active returns have ~zero std (port == bench every period), there
    is no per-period SR computable. audit.dsr_observed_sr MUST be None (not 0.0)
    so JSON readers can distinguish "no signal" from "signal=0"."""
    n = 12
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0.005, 0.04, n),
                      index=pd.date_range("2025-01-15", periods=n, freq="ME"))
    port = bench.copy()   # zero active everywhere → std(active) = 0
    audit = evaluate_cell_gates(
        oos_returns=port, bench_monthly=bench,
        cell_id="degenerate_test", model_name="xgboost", top_n=15,
        oos_sharpe=0.5, dsr_n_trials=8,
    )
    assert audit.dsr_observed_sr is None, (
        f"degenerate active_std should yield dsr_observed_sr=None, "
        f"got {audit.dsr_observed_sr}"
    )
    assert audit.dsr_psi is None
    d = audit.to_dict()
    assert d["dsr_observed_sr"] is None


def test_run_audit_from_cell_summary_locks_producer_schema(tmp_path):
    """Lock the run_audit_from_cell_summary JSON producer contract.

    Per-cell math is covered by the gate tests above; this pins the AGGREGATE
    producer output — top-level key set, schema_version, formula/n_trials
    propagation, vs_baseline shape (incl. the no-baseline NaN edge), the
    GO/NO-GO verdict rule, and JSON round-trip — so a refactor that drops a
    key, breaks the baseline join, or bumps schema_version is caught.
    """
    dates = pd.date_range("2025-01-31", periods=12, freq="ME")
    bench_vals = [0.01, -0.02, 0.03, 0.0, 0.015, -0.01,
                  0.02, 0.005, -0.015, 0.025, 0.0, 0.01]
    bench = pd.Series(bench_vals, index=dates)
    ml15 = [b + a for b, a in zip(bench_vals,
            [0.004, -0.001, 0.006, 0.002, -0.003, 0.005,
             0.001, 0.004, -0.002, 0.003, 0.0, 0.002])]
    ml20 = [b + a for b, a in zip(bench_vals,
            [0.001, 0.0, 0.002, -0.001, 0.001, 0.0,
             0.001, -0.001, 0.0, 0.002, -0.001, 0.001])]
    cell_summary = {
        "ml_cells": [
            {"model_name": "xgboost", "top_n": 15,
             "oos_dates": [d.isoformat() for d in dates],
             "oos_monthly_returns": ml15, "oos_sharpe": 0.9},
            {"model_name": "xgboost", "top_n": 20,
             "oos_dates": [d.isoformat() for d in dates],
             "oos_monthly_returns": ml20, "oos_sharpe": 0.7},
        ],
        # baseline ONLY for top_n=15 -> top_n=20 exercises the no-baseline NaN edge
        "baseline_cells": [{"top_n": 15, "oos_sharpe": 0.5}],
    }
    cs_path = tmp_path / "cell_summary.json"
    out_path = tmp_path / "v5_dsr_audit.json"
    cs_path.write_text(json.dumps(cell_summary), encoding="utf-8")

    result = run_audit_from_cell_summary(str(cs_path), bench, str(out_path))

    # --- top-level schema contract ---
    expected_keys = {
        "schema_version", "audit_date", "source_cell_summary", "dsr_n_trials",
        "dsr_formula_version", "sharpe_diff_threshold", "n_cells",
        "n_cells_pass_all_gates", "n_cells_beat_baseline", "verdict",
        "cell_audits", "vs_baseline",
    }
    assert set(result.keys()) == expected_keys
    assert result["schema_version"] == AUDIT_JSON_SCHEMA_VERSION == "2"
    assert result["dsr_formula_version"] == DSR_FORMULA_VERSION
    assert result["dsr_n_trials"] == DSR_N_TRIALS == 400
    assert result["n_cells"] == 2
    assert len(result["cell_audits"]) == 2
    assert len(result["vs_baseline"]) == 2

    # --- per-cell DSR fields propagate consistently from the aggregate ---
    for ca in result["cell_audits"]:
        assert ca["dsr_n_trials"] == DSR_N_TRIALS
        assert ca["dsr_formula_version"] == DSR_FORMULA_VERSION
        assert "per_period" in ca["dsr_observed_sr_basis"]

    # --- verdict RULE locked (independent of whether the fixture passes gates) ---
    expected_verdict = (
        "GO" if result["n_cells_pass_all_gates"] > 0
        and result["n_cells_beat_baseline"] > 0
        else "NO-GO"
    )
    assert result["verdict"] == expected_verdict

    # --- no-baseline NaN edge for top_n=20 (no matching baseline_cell) ---
    vb20 = next(v for v in result["vs_baseline"] if "top_n_20" in v["cell_id"])
    assert math.isnan(vb20["baseline_sharpe"])
    assert vb20["beats_baseline_by_threshold"] is False
    vb15 = next(v for v in result["vs_baseline"] if "top_n_15" in v["cell_id"])
    assert vb15["baseline_sharpe"] == 0.5

    # --- JSON round-trip: file is valid and reloads with same contract ---
    reloaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert reloaded["schema_version"] == "2"
    assert reloaded["n_cells"] == 2
    assert len(reloaded["cell_audits"]) == 2
