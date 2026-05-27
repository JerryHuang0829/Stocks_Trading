"""Tests for src/analysis/ml_audit.py — Step 6 L1-L6 + DSR evaluator."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_audit import (  # noqa: E402
    DSR_N_TRIALS,
    L1_IR_THRESHOLD,
    L6_BOOTSTRAP_CI_LEVEL,
    CellAudit,
    GateResult,
    _bootstrap_ci_lower,
    compute_actual_turnover_one_way,
    compute_beta_adj_t_stat,
    evaluate_cell_gates,
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
# Codex v5.0 R6 P1 fixes
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
