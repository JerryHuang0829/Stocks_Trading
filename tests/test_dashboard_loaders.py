"""Smoke + schema tests for dashboard/utils.py loaders。

目的：確保 reports/ 內 JSON schema 不變動造成 dashboard 整片 broken。
範圍：純 schema 驗證（不執行 Streamlit runtime），st.cache_data decorator
不影響 schema check。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dashboard"))

# Importing utils requires streamlit; if env lacks streamlit skip
streamlit = pytest.importorskip("streamlit")
import utils  # noqa: E402


def test_load_cell_summary_schema():
    summary = utils.load_cell_summary()
    assert summary is not None, "cell_summary.json 讀不到"
    expected_keys = {
        "n_trials_dsr",
        "expected_n_trials_per_v0_13",
        "n_cells_aggregated",
        "outcome_classification",
        "n_outcome_1_cells",
        "sole_survivor",
        "cells",
    }
    missing = expected_keys - set(summary.keys())
    assert not missing, f"cell_summary 缺 keys: {missing}"
    assert isinstance(summary["cells"], list)
    assert len(summary["cells"]) == 18, f"預期 18 cells, 實際 {len(summary['cells'])}"
    assert summary["outcome_classification"] == "Outcome-2 Partial"
    assert summary["n_outcome_1_cells"] == 0
    assert summary["sole_survivor"] is None


def test_load_cell_metrics_18cells():
    metrics = utils.load_cell_metrics()
    assert metrics is not None
    assert len(metrics) == 18, f"預期 18 cells, 實際 {len(metrics)}"
    sample_key = next(iter(metrics))
    sample = metrics[sample_key]
    expected_keys = {
        "ir",
        "mean_alpha_monthly",
        "te",
        "max_dd_diff_vs_0050",
    }
    missing = expected_keys - set(sample.keys())
    assert not missing, f"cell_metrics[{sample_key}] 缺 keys: {missing}"


def test_load_factor_ic_5factors():
    for factor in utils.FIVE_FACTORS:
        ic = utils.load_factor_ic(factor)
        assert ic is not None, f"{factor}_ic.json 讀不到"
        assert "overall" in ic
        assert "by_regime" in ic
        assert "by_bucket" in ic
        overall = ic["overall"]
        for k in ("mean_ic", "ic_ir", "t_stat", "p_value", "n", "bootstrap_ci_95"):
            assert k in overall, f"{factor} overall 缺 {k}"


def test_load_factor_ic_phase_d_3factors():
    """2026-05-11 補測：Phase D 3 因子 single IC schema 對齊 Phase A1（含 enrichment）。

    R31 finding 1 fix: 早期版本只檢查 overall/by_regime/by_bucket，
    沒驗 enrichment diagnostics 是否齊全；現補檢查 decile / monotonicity /
    peak / price_score / pit_violation 等與 Phase A1 5 因子 JSON 一致。
    """
    # 取一個 Phase A1 JSON 作 schema baseline
    a1_ref = utils.load_factor_ic("high_proximity")
    assert a1_ref is not None
    a1_keys = set(a1_ref.keys())
    for factor in utils.PHASE_D_FACTORS:
        ic = utils.load_factor_ic(factor)
        assert ic is not None, f"{factor}_ic.json 讀不到（請先跑 run_phase_d_factor_ic.py）"
        assert "overall" in ic
        assert "by_regime" in ic
        assert "by_bucket" in ic
        overall = ic["overall"]
        for k in ("mean_ic", "ic_ir", "t_stat", "p_value", "n", "bootstrap_ci_95"):
            assert k in overall, f"{factor} overall 缺 {k}"
        # Enrichment diagnostics parity (R31 finding 1)
        for k in (
            "decile_returns_per_period",
            "decile_avg_returns_across_periods",
            "monotonicity_spearman_rho",
            "peak_in_middle_t_stats",
            "price_score_corr_per_period",
            "price_score_corr_summary",
            "pit_violation",
            "enriched_diagnostics_date",
        ):
            assert k in ic, f"{factor} 缺 enrichment 欄位 {k}（schema 未對齊 Phase A1）"
        # Top-level key parity (no missing relative to Phase A1 reference)
        missing = a1_keys - set(ic.keys())
        assert not missing, f"{factor} top-level keys 缺 {missing}（schema 未對齊 high_proximity）"


def test_load_all_eight_factor_ics():
    """ALL_FACTORS = FIVE_FACTORS + PHASE_D_FACTORS = 8 因子 loadable 全集合。"""
    out = utils.load_all_eight_factor_ics()
    assert isinstance(out, dict)
    assert len(out) == 8, f"預期 8 因子 IC，實際 {len(out)}"
    assert set(out.keys()) == set(utils.ALL_FACTORS)


def test_load_factor_correlation_5x5():
    corr = utils.load_factor_correlation()
    assert corr is not None
    factors = corr.get("factors", [])
    assert len(factors) == 5
    matrix = corr.get("matrix", {})
    assert len(matrix) == 5


def test_load_d1v2_backtest_both_periods():
    for period in ("is", "oos"):
        m = utils.load_d1v2_metrics(period)
        assert m is not None, f"d1v2 {period} metrics 讀不到"
        for k in ("sharpe_ratio", "information_ratio", "annualized_alpha", "beta"):
            assert k in m, f"d1v2 {period} metrics 缺 {k}"

        d = utils.load_d1v2_daily_returns(period)
        assert d is not None
        assert "portfolio" in d
        assert "benchmark" in d

        s = utils.load_d1v2_snapshots(period)
        assert s is not None
        assert isinstance(s, list)
        assert len(s) > 0


def test_get_monthly_active_return_dates_returns_59():
    """60 rebalance dates 產生 59 forward returns，dashboard 必須對齊。"""
    dates = utils.get_monthly_active_return_dates()
    assert dates is not None
    assert len(dates) == 59, f"預期 59 dates，實際 {len(dates)}"
    # First should be 2020-02 (skip 2020-01 first rebalance)
    assert dates[0].startswith("2020-02"), f"first date 應為 2020-02-XX，實際 {dates[0]}"
    # Last should be 2024-12
    assert dates[-1].startswith("2024-12"), f"last date 應為 2024-12-XX，實際 {dates[-1]}"


def test_format_sole_survivor_none():
    assert utils.format_sole_survivor(None) == "(無 survivor / CONFIRM-NO-GO)"
    assert utils.format_sole_survivor("D-C|12") == "D-C|12"


def test_load_monthly_active_returns_18cells_each_59():
    """每個 cell 的 monthly active returns 必須 59 個（對應 60 rebalance）。"""
    data = utils.load_monthly_active_returns()
    assert data is not None
    assert len(data) == 18, f"預期 18 cells, 實際 {len(data)}"
    for cell, returns in data.items():
        assert len(returns) == 59, f"cell {cell} 預期 59 returns，實際 {len(returns)}"


def test_gate_pass_count_logic():
    full_pass = {
        "L1_ir_ge_0_20": True,
        "L2_mean_alpha_ge_0_005": True,
        "L3_te_in_range": True,
        "L4_max_dd_diff_le_0_05": True,
        "L5_a1_active_corr_le_0_50": True,
        "L6_bootstrap_ci_lower_gt_0": True,
    }
    assert utils.gate_pass_count(full_pass) == 6
    assert utils.gate_pass_count({}) == 0
    partial = full_pass.copy()
    partial["L6_bootstrap_ci_lower_gt_0"] = False
    assert utils.gate_pass_count(partial) == 5


def test_load_bootstrap_ci_lowers_18cells():
    ci = utils.load_bootstrap_ci_lowers()
    assert ci is not None
    assert "ci_lowers" in ci
    assert "L6_alpha" in ci
    ci_lowers = ci["ci_lowers"]
    assert len(ci_lowers) == 18
    # All cells should have CI lower ≤ 0 (NO-GO)
    n_pass = sum(1 for v in ci_lowers.values() if v > 0)
    assert n_pass == 0, f"預期 0 cell L6 pass，實際 {n_pass} → 違反 CONFIRM-NO-GO"


def test_load_audit_results_5_keys():
    results = utils.load_audit_results()
    expected = {
        "rolling_alpha",
        "regime_permutation",
        "friction_oddlot",
        "passive_evaluation",
        "factor_ic_recomputed",
    }
    assert set(results.keys()) == expected
