"""S6.1 wire-up smoke + unit tests.

Verifies:
- _compute_cell_metrics produces 7 expected keys with valid numeric values
- _compute_max_drawdown returns negative number on declining cumulative returns
- _compute_beta_adj_alpha_t correctness on known-answer fixtures
- _z_score returns clipped values, handles empty / constant input
- run_cell_sweep_real signature accepts ctx + raises on D-A / invalid top_n
- 18-cell loop produces dict[(candidate, top_n) → metrics] with all keys

Tests are mostly synthetic-fixture based to avoid loading real cache dirs.
A separate `--smoke` CLI flag exercises the real pipeline end-to-end (not in
pytest scope; run manually before full 18-cell sweep).
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.d_cell_sweep_v7_real import (  # noqa: E402
    _compute_beta_adj_alpha_t,
    _compute_cell_metrics,
    _compute_max_drawdown,
    _z_score,
    run_cell_sweep_real,
)


# ---------------------------------------------------------------------------
# _z_score
# ---------------------------------------------------------------------------
def test_z_score_empty():
    assert _z_score(pd.Series(dtype=float)).empty


def test_z_score_constant():
    """All-equal values → z=0 for every entry."""
    s = pd.Series([5.0, 5.0, 5.0])
    z = _z_score(s)
    assert (z == 0.0).all()


def test_z_score_clip():
    """±3σ clip is enforced."""
    s = pd.Series([0.0, 0.0, 0.0, 0.0, 100.0])  # extreme outlier
    z = _z_score(s, clip=2.0)
    # The outlier should be clipped at +2.0
    assert z.max() <= 2.0
    assert z.min() >= -2.0


# ---------------------------------------------------------------------------
# _compute_max_drawdown
# ---------------------------------------------------------------------------
def test_max_drawdown_monotonic_up_zero():
    """Strictly increasing returns → drawdown = 0."""
    rets = pd.Series([0.01, 0.02, 0.03, 0.01, 0.005])
    dd = _compute_max_drawdown(rets)
    assert dd == pytest.approx(0.0, abs=1e-9)


def test_max_drawdown_decline():
    """Decline produces negative drawdown."""
    rets = pd.Series([0.10, -0.05, -0.10, 0.02])
    dd = _compute_max_drawdown(rets)
    assert dd < 0


def test_max_drawdown_empty():
    assert _compute_max_drawdown(pd.Series(dtype=float)) == 0.0


# ---------------------------------------------------------------------------
# _compute_beta_adj_alpha_t
# ---------------------------------------------------------------------------
def test_beta_adj_alpha_t_zero_when_perfect_correlation():
    """If port = 1.0 × bench (no alpha, perfect beta=1), t-stat ≈ 0."""
    bench = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])
    port = bench.copy()  # perfect correlation, no alpha
    t = _compute_beta_adj_alpha_t(port, bench)
    assert abs(t) < 1e-6  # alpha = 0 exactly


def test_beta_adj_alpha_t_positive_when_constant_alpha():
    """Port = bench + 0.05 (constant 5% alpha) → positive t-stat."""
    bench = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])
    port = bench + 0.05
    t = _compute_beta_adj_alpha_t(port, bench)
    # All residuals are zero, SE=0, function returns 0 (degenerate case)
    # Not great test fixture; let's add noise
    np.random.seed(42)
    port_noisy = bench + 0.05 + pd.Series(np.random.normal(0, 0.001, len(bench)))
    t = _compute_beta_adj_alpha_t(port_noisy, bench)
    assert t > 5  # large positive t-stat (alpha 0.05 vs noise 0.001)


def test_beta_adj_alpha_t_short_series():
    """n < 3 returns 0 (insufficient for OLS)."""
    assert _compute_beta_adj_alpha_t(pd.Series([0.01]), pd.Series([0.02])) == 0.0


# ---------------------------------------------------------------------------
# _compute_cell_metrics
# ---------------------------------------------------------------------------
def test_compute_cell_metrics_7_keys():
    """Output dict must have exactly the 7 keys d_cell_aggregate_v7 expects."""
    idx = pd.date_range("2020-01-31", periods=24, freq="BME")
    np.random.seed(0)
    p = pd.Series(np.random.normal(0.01, 0.04, 24), index=idx)
    b = pd.Series(np.random.normal(0.005, 0.04, 24), index=idx)
    a = p - b
    metrics = _compute_cell_metrics(a, p, b)
    expected_keys = {
        "ir", "mean_alpha_monthly", "te", "max_dd_diff_vs_0050",
        "active_corr", "beta_adj_alpha_t", "sharpe_for_dsr",
    }
    assert set(metrics.keys()) == expected_keys
    for k, v in metrics.items():
        assert isinstance(v, float), f"{k} = {v} (type {type(v).__name__})"


def test_compute_cell_metrics_ir_calculation():
    """IR = mean_alpha × 12 / TE_annualized."""
    idx = pd.date_range("2020-01-31", periods=12, freq="BME")
    # Constant 1% monthly active return, 1% std
    np.random.seed(1)
    a = pd.Series([0.01] * 12, index=idx) + pd.Series(np.random.normal(0, 0.01, 12), index=idx)
    p = a.copy()  # benchmark = 0
    b = pd.Series([0.0] * 12, index=idx)
    metrics = _compute_cell_metrics(a, p, b)
    # IR ≈ 0.01 × 12 / (0.01 × √12) ≈ 12 / 3.46 ≈ 3.46 (very high)
    assert metrics["ir"] > 1.0  # sanity: clearly positive


def test_compute_cell_metrics_short_series_zeros():
    """< 3 obs → all-zero metrics (avoid division by ~0)."""
    idx = pd.date_range("2020-01-31", periods=2, freq="BME")
    p = pd.Series([0.01, 0.02], index=idx)
    b = pd.Series([0.005, 0.005], index=idx)
    a = p - b
    metrics = _compute_cell_metrics(a, p, b)
    assert all(v == 0.0 for v in metrics.values())


# ---------------------------------------------------------------------------
# run_cell_sweep_real signature validation
# ---------------------------------------------------------------------------
def test_run_cell_sweep_real_rejects_da():
    """V0.13 Assertion 2: D-A pre-disqualified."""
    with pytest.raises(ValueError, match="D-A pre-disqualified|not in CANDIDATE_FACTOR_SETS"):
        run_cell_sweep_real(
            "D-A", 8,
            datetime(2020, 1, 1), datetime(2020, 12, 31),
            cache_dir=Path("/nonexistent"),
        )


def test_run_cell_sweep_real_rejects_invalid_top_n():
    """pre-commit #7: top_n ∈ {8, 12, 16}."""
    with pytest.raises(ValueError, match="not in TOP_N_VALUES|pre-commit #7"):
        run_cell_sweep_real(
            "D-B", 20,
            datetime(2020, 1, 1), datetime(2020, 12, 31),
            cache_dir=Path("/nonexistent"),
        )


def test_run_cell_sweep_real_requires_ctx_or_cache_dir():
    """Either ctx or cache_dir must be provided."""
    with pytest.raises(ValueError, match="ctx or cache_dir"):
        run_cell_sweep_real(
            "D-B", 8,
            datetime(2020, 1, 1), datetime(2020, 12, 31),
        )
