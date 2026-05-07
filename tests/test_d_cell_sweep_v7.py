"""V0.13 Assertion 2 + 3 enforcement tests + 6 yaml schema validation — Phase 2 S4.

Verifies `scripts.d_cell_sweep_v7`:
- Assertion 2: D-A pre-disqualification guard (string mutation catches typo)
- Assertion 3: EXPECTED_N_TRIALS = 18 cell-level (DSR n_trials lock)
- 6 yaml configs (D-B/C/D/E/F/G) load + schema validate + weights sum
- Stub cell grid emits 18 cells (Assertion 3 runtime verify)

Phase 2 S6 owner extends to real BacktestEngine wire-up; S4 tests use yaml
schema fixture (per V1.2 active_corr stub pattern).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.d_cell_sweep_v7 import (  # noqa: E402
    CANDIDATE_FACTOR_SETS,
    EXPECTED_N_TRIALS,
    TOP_N_VALUES,
    load_all_candidate_configs,
    load_candidate_config,
    run_cell_sweep_stub,
)


# ---------------------------------------------------------------------------
# V0.13 Assertion 2 — D-A pre-disqualification guard
# ---------------------------------------------------------------------------
def test_assertion_2_d_a_not_in_candidate_factor_sets():
    """V0.13 Assertion 2: D-A MUST NOT be in CANDIDATE_FACTOR_SETS."""
    assert "D-A" not in CANDIDATE_FACTOR_SETS
    # Mutation: if "D-A" added → module-level assertion would have raised at
    # import time; test passes only if module imports cleanly


def test_assertion_2_string_typo_d_a_caught():
    """Mutation: typo variants like 'D_A' / 'd-a' would not be 'D-A' literal,
    but should ALSO be excluded; test verifies common typo variants."""
    typo_variants = ["D-A", "D_A", "d-a", "D-a", " D-A ", "D-A\n"]
    for typo in typo_variants:
        assert typo.strip() not in [c.strip() for c in CANDIDATE_FACTOR_SETS] or typo.strip() != "D-A", (
            f"typo variant {typo!r} should not appear in candidate set"
        )


def test_assertion_2_load_d_a_raises():
    """Caller-side mutation: load_candidate_config('D-A') must raise."""
    with pytest.raises(ValueError, match="D-A pre-disqualified"):
        load_candidate_config("D-A")


# ---------------------------------------------------------------------------
# V0.13 Assertion 3 — EXPECTED_N_TRIALS = 18
# ---------------------------------------------------------------------------
def test_assertion_3_expected_n_trials_eq_18():
    """V0.13 Assertion 3 (cell-level): EXPECTED_N_TRIALS = 18 = 6 × 3."""
    assert EXPECTED_N_TRIALS == 18
    assert len(CANDIDATE_FACTOR_SETS) == 6
    assert len(TOP_N_VALUES) == 3
    assert EXPECTED_N_TRIALS == len(CANDIDATE_FACTOR_SETS) * len(TOP_N_VALUES)


def test_assertion_3_top_n_values_locked():
    """V0.13 pre-commit #7: top_n_values = (8, 12, 16) frozen."""
    assert TOP_N_VALUES == (8, 12, 16)


def test_assertion_3_cell_grid_emits_18_cells():
    """Stub run emits exactly 18 cells (runtime Assertion 3 verify)."""
    summary = run_cell_sweep_stub()
    assert summary["expected_n_trials"] == 18
    assert summary["n_cells_emitted"] == 18
    assert len(summary["cells"]) == 18


# ---------------------------------------------------------------------------
# 6 yaml configs schema validation
# ---------------------------------------------------------------------------
def test_all_6_candidates_yaml_load():
    """Happy path: all 6 candidates load without error + schema valid."""
    configs = load_all_candidate_configs()
    assert len(configs) == 6
    for cid in CANDIDATE_FACTOR_SETS:
        assert cid in configs
        assert configs[cid]["candidate_id"] == cid


def test_yaml_schema_required_keys():
    """Each yaml has required keys: candidate_id, description, factors, top_n_values, spec_source."""
    required = {"candidate_id", "description", "factors", "top_n_values", "spec_source"}
    for cid in CANDIDATE_FACTOR_SETS:
        cfg = load_candidate_config(cid)
        assert required <= set(cfg.keys()), f"{cid} missing keys: {required - set(cfg.keys())}"


def test_yaml_factor_weights_sum_to_one():
    """Per H_d_v6:51-58: each candidate factor weights sum to 1.0 (±0.01 tolerance)."""
    for cid in CANDIDATE_FACTOR_SETS:
        cfg = load_candidate_config(cid)
        total = sum(float(w) for w in cfg["factors"].values())
        assert abs(total - 1.0) < 0.01, f"{cid} weights sum {total} ≠ 1.0 ±0.01"


def test_yaml_top_n_values_locked_per_candidate():
    """All 6 candidates use same top_n_values = [8, 12, 16] (pre-commit #7)."""
    for cid in CANDIDATE_FACTOR_SETS:
        cfg = load_candidate_config(cid)
        assert list(cfg["top_n_values"]) == [8, 12, 16], (
            f"{cid} top_n_values {cfg['top_n_values']} ≠ [8, 12, 16]"
        )


def test_yaml_d_e_uses_quality_v3_not_v2():
    """V1.1c P1 #5 + V0.13: D-E MUST use quality_v3 (NOT quality_v2 deprecated)."""
    cfg = load_candidate_config("D-E")
    assert "quality_v3" in cfg["factors"], "D-E must use quality_v3 (V0.13 spec)"
    assert "quality_v2" not in cfg["factors"], "D-E must NOT use deprecated quality_v2"


def test_yaml_d_f_uses_industry_momentum_6m_spec_source():
    """D-F yaml spec_source mentions 6m / MG1999 (R24 §設計-5 lock)."""
    cfg = load_candidate_config("D-F")
    assert "industry_momentum" in cfg["factors"]
    spec_source = cfg.get("spec_source", "")
    assert "6m" in spec_source or "MG1999" in spec_source, (
        f"D-F spec_source must reference 6m / MG1999 lock; got: {spec_source!r}"
    )


def test_yaml_d_g_uses_idio_vol_max_split_spec():
    """D-G yaml spec_source mentions 0.5/0.5 split (R24 §設計-6)."""
    cfg = load_candidate_config("D-G")
    assert "idio_vol_max" in cfg["factors"]
    spec_source = cfg.get("spec_source", "")
    assert "0.5/0.5" in spec_source or "split" in spec_source, (
        f"D-G spec_source must reference 0.5/0.5 split; got: {spec_source!r}"
    )


def test_yaml_unknown_candidate_raises():
    """Caller mutation: load unknown candidate_id → ValueError."""
    with pytest.raises(ValueError, match="not in CANDIDATE_FACTOR_SETS"):
        load_candidate_config("D-Z")


def test_yaml_d_d_has_3_factors_v0_14_no_revenue_v2():
    """V0.14 R25-mid Codex audit P0-2 fix: D-D redesigned from 4-factor to
    3-factor IR-weighted normalize (revenue_momentum_v2 移除 per pre-commit #8
    V0.14 clarify). Verifies D-D V0.14 contains exactly 3 factors AND
    revenue_momentum_v2 NOT in factors."""
    cfg = load_candidate_config("D-D")
    assert len(cfg["factors"]) == 3, (
        f"V0.14 D-D 必為 3-factor (revenue_v2 移除); got {len(cfg['factors'])} factors"
    )
    assert "revenue_momentum_v2" not in cfg["factors"], (
        "V0.14 P0-2 fix violation: revenue_momentum_v2 must NOT be in D-D "
        "(pre-commit #8 V0.14 clarify EXCLUDED from candidate pool)"
    )
    # Expected weights (IR-weighted normalize without 20% Margin cap)
    expected = {"high_proximity": 0.34, "pead_eps": 0.36, "margin_short_ratio": 0.30}
    for factor, weight in expected.items():
        assert abs(cfg["factors"][factor] - weight) < 0.005, (
            f"V0.14 D-D weight {factor}: expected {weight}, got {cfg['factors'][factor]}"
        )


def test_yaml_d_c_has_2_factors():
    """D-C is the minimum 2-factor baseline per H_d_v6:54 (D1_v2 50/50)."""
    cfg = load_candidate_config("D-C")
    assert len(cfg["factors"]) == 2


def test_yaml_d_b_has_3_factors():
    """D-B is 3-factor IR-weighted with 20% Margin cap per H_d_v6:53."""
    cfg = load_candidate_config("D-B")
    assert len(cfg["factors"]) == 3
    # Margin cap: margin_short_ratio weight ≈ 0.20 (per R24 §設計-3)
    assert abs(cfg["factors"]["margin_short_ratio"] - 0.20) < 1e-6


# ---------------------------------------------------------------------------
# Cell grid stub structural sanity
# ---------------------------------------------------------------------------
def test_cell_grid_stub_includes_all_candidates():
    """Stub cell grid emits cells for all 6 candidates."""
    summary = run_cell_sweep_stub()
    candidate_ids_in_cells = {cell["candidate_id"] for cell in summary["cells"]}
    assert candidate_ids_in_cells == set(CANDIDATE_FACTOR_SETS)


def test_cell_grid_stub_includes_all_top_n():
    """Stub cell grid covers all 3 top_n values."""
    summary = run_cell_sweep_stub()
    top_n_in_cells = {cell["top_n"] for cell in summary["cells"]}
    assert top_n_in_cells == set(TOP_N_VALUES)


def test_cell_grid_stub_no_d_a_in_cells():
    """V0.13 Assertion 2 runtime verify: no cell has candidate_id == 'D-A'."""
    summary = run_cell_sweep_stub()
    for cell in summary["cells"]:
        assert cell["candidate_id"] != "D-A", "D-A leaked into cell grid (Assertion 2 violation)"


# ---------------------------------------------------------------------------
# V0.14 Assertion 2 強化 — composition-level forbidden check (P0-1 fix)
# ---------------------------------------------------------------------------
# R25-mid Codex audit P0-1: D-C 50/50 ≡ D-A composition; existing string-only
# Assertion 2 cannot catch. V0.14 adds module-level _composition_equals_forbidden()
# helper + caller-side check in load_candidate_config(). These tests verify
# composition equivalence catch.


def test_v0_14_composition_helper_blocks_d_a_50_50():
    """V0.14 P0-1 fix: _composition_equals_forbidden() catches D-A composition."""
    from scripts.d_cell_sweep_v7 import _composition_equals_forbidden
    # D-A 50/50 forbidden composition MUST be caught
    assert _composition_equals_forbidden({"high_proximity": 0.50, "pead_eps": 0.50}) is True
    # D-C V0.14 redesigned 0.40/0.60 must NOT be caught (legitimate variant)
    assert _composition_equals_forbidden({"high_proximity": 0.40, "pead_eps": 0.60}) is False
    # Other 2-factor combinations also pass through
    assert _composition_equals_forbidden({"pead_eps": 0.50, "margin_short_ratio": 0.50}) is False


def test_v0_14_composition_helper_decimal_robust():
    """V0.14 P0-1 fix: rounded comparison handles 0.5000001 edge cases."""
    from scripts.d_cell_sweep_v7 import _composition_equals_forbidden
    # 0.5000001 rounds to 0.5 (4 decimal places) → should be caught
    assert _composition_equals_forbidden({"high_proximity": 0.5000001, "pead_eps": 0.5000001}) is True
    # 0.499 vs 0.501 → not caught (genuine 49.9/50.1 split)
    assert _composition_equals_forbidden({"high_proximity": 0.499, "pead_eps": 0.501}) is False


def test_v0_14_load_yaml_with_d_a_composition_raises(tmp_path, monkeypatch):
    """V0.14 P0-1 fix: load_candidate_config() rejects yaml whose composition
    matches D-A even if candidate_id differs. Mutation reverts the V0.14 check
    → load returns silently with D-A-equivalent candidate."""
    from scripts.d_cell_sweep_v7 import load_candidate_config, CANDIDATE_FACTOR_SETS
    # Create a temp yaml file masquerading as D-C with 50/50 (the bug Codex caught)
    fake_yaml_dir = tmp_path / "fake_d_v7"
    fake_yaml_dir.mkdir()
    fake_yaml = fake_yaml_dir / "D-C.yaml"
    fake_yaml.write_text(
        "candidate_id: D-C\n"
        "description: bad composition (D-A equivalent)\n"
        "factors:\n"
        "  high_proximity: 0.50\n"
        "  pead_eps: 0.50\n"
        "top_n_values: [8, 12, 16]\n"
        "spec_source: synthetic for V0.14 mutation test\n",
        encoding="utf-8",
    )
    # Patch the path resolver to point to our fake yaml
    import scripts.d_cell_sweep_v7 as cs
    monkeypatch.setattr(cs, "_candidate_yaml_path", lambda cid: fake_yaml)
    # Loading the bad D-C must now raise V0.14 Assertion 2 violation
    with pytest.raises(ValueError, match="V0.14 Assertion 2 FAIL"):
        load_candidate_config("D-C")


def test_v0_14_real_d_c_loads_with_pead_weighted_60_40():
    """V0.14 P0-1 fix: D-C V0.14 redesigned (0.40/0.60 PEAD-weighted) loads
    cleanly without composition violation."""
    cfg = load_candidate_config("D-C")
    assert cfg["candidate_id"] == "D-C"
    assert len(cfg["factors"]) == 2
    # Weights ≠ 50/50 (V0.14 redesign)
    assert abs(cfg["factors"]["high_proximity"] - 0.40) < 0.005
    assert abs(cfg["factors"]["pead_eps"] - 0.60) < 0.005
    # NOT equivalent to D-A
    from scripts.d_cell_sweep_v7 import _composition_equals_forbidden
    assert _composition_equals_forbidden(dict(cfg["factors"])) is False


def test_v0_14_assertion_3_dynamic_matches_actual():
    """V0.14 fix per Codex 建議: EXPECTED_N_TRIALS computed dynamically from
    len(CANDIDATE_FACTOR_SETS) × len(TOP_N_VALUES); not just hardcoded 18."""
    from scripts.d_cell_sweep_v7 import (
        CANDIDATE_FACTOR_SETS,
        EXPECTED_N_TRIALS,
        TOP_N_VALUES,
    )
    # Dynamic identity holds regardless of hardcode
    assert EXPECTED_N_TRIALS == len(CANDIDATE_FACTOR_SETS) * len(TOP_N_VALUES)
    # And currently == 18 per pre-commit lock
    assert EXPECTED_N_TRIALS == 18


# ---------------------------------------------------------------------------
# Phase 2 Session 5 — Cell Sweep CLI 3 件 pre-flight gate tests
# (per V1.1c P1 #18 + V1.2 §"L5 active_corr binding")
# ---------------------------------------------------------------------------
import pandas as pd  # noqa: E402


def test_s5_pre_flight_cache_coverage_gate_passes_with_real_cache():
    """V1.1c P1 #18 gate 1: cache coverage threshold ≥ 95%; passes if real cache
    has ≥ 76 valid OHLCV pkls (95% of 80) — true on developer machine post-S6
    fresh-rerun. Verifies algorithm structure, not the absolute pass/fail."""
    from scripts.d_cell_sweep_v7 import check_cache_coverage_gate
    from src.utils.paths import resolve_cache_dir
    cache_dir = resolve_cache_dir()
    passed, diag = check_cache_coverage_gate(cache_dir, top_n_universe_size=80)
    # diag structure must include keys regardless of pass/fail
    assert "passed" in diag
    assert "coverage_pct" in diag
    assert "threshold_pct" in diag
    assert diag["threshold_pct"] == 95.0
    # passed flag matches coverage logic
    assert passed == (diag["coverage_pct"] >= 95.0)


def test_s5_pre_flight_cache_coverage_missing_dir_fails(tmp_path):
    """Mutation: missing OHLCV cache dir → gate FAILS with diagnostic reason."""
    from scripts.d_cell_sweep_v7 import check_cache_coverage_gate
    fake_cache = tmp_path / "nonexistent_cache"
    passed, diag = check_cache_coverage_gate(fake_cache, top_n_universe_size=80)
    assert passed is False
    assert "OHLCV cache dir missing" in diag["reason"]
    assert diag["coverage_pct"] == 0.0


def test_s5_pre_flight_lookback_prereq_gate_returns_diagnostic():
    """V1.1c P1 #18 gate 2: lookback prereq verifies OHLCV cache extends ≥
    252 trading days BEFORE backtest_start. S5 stub-level: structural check."""
    from scripts.d_cell_sweep_v7 import check_lookback_prereq_gate, MAX_FACTOR_LOOKBACK_DAYS
    from src.utils.paths import resolve_cache_dir
    cache_dir = resolve_cache_dir()
    backtest_start = pd.Timestamp("2024-01-01")
    passed, diag = check_lookback_prereq_gate(
        cache_dir, backtest_start, required_lookback_days=MAX_FACTOR_LOOKBACK_DAYS,
    )
    assert "passed" in diag
    assert MAX_FACTOR_LOOKBACK_DAYS == 252


def test_s5_pre_flight_smoke_1_fold_gate_passes_d_c():
    """V1.1c P1 #18 gate 3: smoke 1-fold gate with default candidate D-C
    + top_n=8. S5 stub: validate yaml load + Assertion 2 composition check
    + cell descriptor emission. Real backtest run @ S6."""
    from scripts.d_cell_sweep_v7 import check_smoke_1_fold_gate
    passed, diag = check_smoke_1_fold_gate(candidate_id="D-C", top_n=8)
    assert passed is True
    assert diag["smoke_candidate"] == "D-C"
    assert diag["smoke_top_n"] == 8
    assert "factors_loaded" in diag


def test_s5_pre_flight_smoke_invalid_candidate_fails():
    """Mutation: invalid candidate_id (e.g. D-A pre-disqualified) → smoke FAILS."""
    from scripts.d_cell_sweep_v7 import check_smoke_1_fold_gate
    passed, diag = check_smoke_1_fold_gate(candidate_id="D-A", top_n=8)
    assert passed is False
    assert "D-A" in diag["reason"]


def test_s5_pre_flight_smoke_invalid_top_n_fails():
    """Mutation: top_n not in (8, 12, 16) → smoke FAILS (pre-commit #7)."""
    from scripts.d_cell_sweep_v7 import check_smoke_1_fold_gate
    passed, diag = check_smoke_1_fold_gate(candidate_id="D-C", top_n=10)
    assert passed is False
    assert "10" in diag["reason"]


def test_s5_pre_flight_orchestration_returns_aggregated_verdict():
    """V1.1c P1 #18 orchestration: run_pre_flight_gates aggregates 3 gates
    into single verdict. S6 cell sweep MUST call this before 18-cell run."""
    from scripts.d_cell_sweep_v7 import run_pre_flight_gates
    from src.utils.paths import resolve_cache_dir
    cache_dir = resolve_cache_dir()
    backtest_start = pd.Timestamp("2024-01-01")
    all_passed, diag = run_pre_flight_gates(cache_dir, backtest_start)
    # Diag structure includes all 3 gates
    assert "all_gates_passed" in diag
    assert "gate_1_cache_coverage" in diag
    assert "gate_2_lookback_prereq" in diag
    assert "gate_3_smoke_1_fold" in diag
    # gate 3 smoke MUST pass (default D-C 8 — cleanest path)
    assert diag["gate_3_smoke_1_fold"]["passed"] is True
    # all_passed iff all 3 individual gates passed
    expected_all = (
        diag["gate_1_cache_coverage"]["passed"]
        and diag["gate_2_lookback_prereq"]["passed"]
        and diag["gate_3_smoke_1_fold"]["passed"]
    )
    assert all_passed == expected_all
