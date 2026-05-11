"""V0.13 d_cell_sweep_v7 — 18-cell sweep generic engine entrypoint (S4 stub).

Phase 2 Session 4 (2026-05-05) — H_d_v6 V0.13 §"Code-level enforcement"
Assertion 2 (D-A guard) + Assertion 3 (DSR n_trials=18 verify) 落地。

Spec source:
- H_d_v6:51-58 — 6 candidate factor sets (D-B/C/D/E/F/G); D-A pre-disqualified
- H_d_v6:118-130 — Assertion 2: `assert "D-A" not in CANDIDATE_FACTOR_SETS`
- H_d_v6:142 — Assertion 3 (cell-level): `assert EXPECTED_N_TRIALS == 18`
  (= 6 candidates × 3 top_n; deflated_sharpe_ratio level enforce 已 V1.1b 落地)
- V0.13 §"Cell sweep adjust pipeline" — d_cell_sweep_v7 必經 BacktestEngine
  (real wire-up @ Phase 2 Session 6 cache fresh-rerun + 18 cell sweep run)

S4 stub-level scope (per V1.2 active_corr stub pattern):
- 6 yaml configs at `config/d_v7/D-{B,C,D,E,F,G}.yaml` 已建
- CANDIDATE_FACTOR_SETS module-level constant + Assertion 2 module-level enforce
- TOP_N_VALUES module-level constant + Assertion 3 enforce (EXPECTED_N_TRIALS=18)
- `load_candidate_config(candidate_id)` yaml loader stub
- `run_cell_sweep_stub()` placeholder — real BacktestEngine wire-up @ S6

Phase 2 Session 6 owner: extend `run_cell_sweep_stub()` to:
1. Load each candidate yaml + each top_n combination
2. Instantiate `BacktestEngine(config=settings.yaml + factor_overrides)`
3. Run backtest per cell; aggregate via `d_cell_aggregate_v7.py` (S6 owns)
4. Pre-flight 3 件 gate (cache coverage / lookback prereq / smoke 1-fold)
   per V0.13 P1 #18

Usage (Phase 2 S6 wire-up):
    python scripts/d_cell_sweep_v7.py --output-dir reports/phase_d/cell_sweep_v6_<date>/
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# V0.13 Assertion 2 — D-A pre-disqualification guard (module-level enforce)
# ---------------------------------------------------------------------------
CANDIDATE_FACTOR_SETS: tuple[str, ...] = ("D-B", "D-C", "D-D", "D-E", "D-F", "D-G")

assert "D-A" not in CANDIDATE_FACTOR_SETS, (
    "V0.13 Assertion 2 FAIL: D-A pre-disqualified per H_d_v6 §D-A "
    "pre-disqualification record + D6 OOS evidence (IR 0.9238 → 0.0058, "
    "99.4% collapse). Reintroducing D-A requires H_d_v7 reframe + new "
    "commit-hash anchor, NOT in-place edit of v6/v7."
)


# ---------------------------------------------------------------------------
# V0.14 Assertion 2 強化 (R25-mid 獨立 audit P0-1 fix, 2026-05-05):
# D-A pre-disqualification 不僅 string-level (CANDIDATE_FACTOR_SETS check)，
# 更要 composition-level — 防 candidate id 換名字但 factor weights 仍等價 D-A。
# ---------------------------------------------------------------------------
D_A_FORBIDDEN_COMPOSITIONS: tuple[dict[str, float], ...] = (
    {"high_proximity": 0.50, "pead_eps": 0.50},  # D-A = D1_v2 baseline (52W + PEAD 50/50)
)


def _composition_equals_forbidden(factors: dict[str, float]) -> bool:
    """V0.14 Assertion 2 helper: check if factors dict matches any D-A forbidden
    composition (rounded to 4 decimals to handle float weight 0.5000001 edge).

    Catches the regression where a candidate (e.g. D-C 50/50) is mathematically
    equivalent to D-A even though the candidate_id string differs.
    """
    factors_normalized = {k: round(float(v), 4) for k, v in factors.items()}
    for forbidden in D_A_FORBIDDEN_COMPOSITIONS:
        forbidden_normalized = {k: round(float(v), 4) for k, v in forbidden.items()}
        if factors_normalized == forbidden_normalized:
            return True
    return False


# ---------------------------------------------------------------------------
# V0.13 Assertion 3 — DSR n_trials = 18 verify (cell-level)
# ---------------------------------------------------------------------------
TOP_N_VALUES: tuple[int, ...] = (8, 12, 16)

EXPECTED_N_TRIALS: int = len(CANDIDATE_FACTOR_SETS) * len(TOP_N_VALUES)

assert EXPECTED_N_TRIALS == 18, (
    f"V0.13 Assertion 3 FAIL: cell count drift; expected 18 = 6 candidates "
    f"× 3 top_n, got {EXPECTED_N_TRIALS} (= {len(CANDIDATE_FACTOR_SETS)} × "
    f"{len(TOP_N_VALUES)}). Per H_d_v6 V0.13 §13 pre-commit discipline #2: "
    f"DSR n_trials=18 must match cell count. Per V1.1b ic_analysis.py: "
    f"deflated_sharpe_ratio() raises on missing n_trials kwarg."
)


# ---------------------------------------------------------------------------
# Yaml config loader (S4 stub level)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 2 Session 5 — Cell Sweep CLI 3 件 pre-flight gate
# (per V1.1c P1 #18 + V1.2 §"L5 active_corr binding")
# ---------------------------------------------------------------------------
# 3 gates 防 18 cell sweep 開跑前未準備好就 full run；S6 cache fresh-rerun
# 階段 owner enforce real numbers，S5 是 spec lock + scaffolding。

# Maximum factor lookback in trading days (高_proximity 252d max dominates)
MAX_FACTOR_LOOKBACK_DAYS: int = 252

# Cache coverage threshold per V0.13 §"S6 fresh-rerun 範圍與時程"
DEFAULT_CACHE_COVERAGE_THRESHOLD: float = 0.95


def check_cache_coverage_gate(
    cache_dir: pathlib.Path,
    top_n_universe_size: int = 80,
    threshold: float = DEFAULT_CACHE_COVERAGE_THRESHOLD,
) -> tuple[bool, dict[str, Any]]:
    """V1.1c P1 #18 pre-flight gate 1: cache coverage ≥ 95% before cell sweep run.

    Counts existing OHLCV pkls in cache vs top-N universe size; raises if
    coverage < threshold. S6 fresh-rerun owner enforces this gate's TRUE
    return value before invoking cell sweep.

    Returns: (passed: bool, diagnostic: dict)
    """
    ohlcv_dir = cache_dir / "ohlcv"
    if not ohlcv_dir.exists():
        return False, {
            "passed": False,
            "reason": f"OHLCV cache dir missing: {ohlcv_dir}",
            "coverage_pct": 0.0,
            "threshold_pct": threshold * 100,
        }
    existing_pkls = list(ohlcv_dir.glob("*.pkl"))
    valid_pkls = [p for p in existing_pkls if p.stem.isdigit() and len(p.stem) == 4]
    coverage = len(valid_pkls) / max(1, top_n_universe_size)
    passed = coverage >= threshold
    return passed, {
        "passed": passed,
        "coverage_pct": coverage * 100,
        "threshold_pct": threshold * 100,
        "existing_pkls_count": len(valid_pkls),
        "universe_target": top_n_universe_size,
    }


def check_lookback_prereq_gate(
    cache_dir: pathlib.Path,
    backtest_start: pd.Timestamp,
    required_lookback_days: int = MAX_FACTOR_LOOKBACK_DAYS,
) -> tuple[bool, dict[str, Any]]:
    """V1.1c P1 #18 pre-flight gate 2: lookback prereq before cell sweep run.

    Verifies OHLCV cache extends ≥ required_lookback_days BEFORE backtest_start
    (high_proximity 52W needs 252d; idio_vol_max 60d / industry_momentum 6m ≈
    132d are subsumed by 252d). Without this buffer, factors silently produce
    NaN at cell sweep run start.

    Returns: (passed: bool, diagnostic: dict)
    """
    ohlcv_dir = cache_dir / "ohlcv"
    if not ohlcv_dir.exists():
        return False, {
            "passed": False,
            "reason": f"OHLCV cache dir missing: {ohlcv_dir}",
        }
    required_earliest = backtest_start - pd.Timedelta(days=int(required_lookback_days * 1.5))
    # Sample 5 pkls (deterministic order) check earliest OHLCV row date
    sample_pkls = sorted(ohlcv_dir.glob("*.pkl"))[:5]
    if not sample_pkls:
        return False, {
            "passed": False,
            "reason": "no OHLCV pkls found for sampling",
        }
    earliest_dates: list[pd.Timestamp] = []
    for p in sample_pkls:
        try:
            df = pd.read_pickle(p)
            if df is None or df.empty:
                continue
            earliest_dates.append(pd.Timestamp(df.index[0]).tz_localize(None))
        except Exception:
            continue
    if not earliest_dates:
        return False, {
            "passed": False,
            "reason": "all sampled OHLCV pkls failed to read",
        }
    sample_earliest = max(earliest_dates)  # worst case among sampled
    passed = sample_earliest <= required_earliest
    return passed, {
        "passed": passed,
        "backtest_start": str(backtest_start.date()),
        "required_lookback_days": required_lookback_days,
        "required_earliest_date": str(required_earliest.date()),
        "sample_earliest_observed": str(sample_earliest.date()),
        "sample_count": len(earliest_dates),
    }


def check_smoke_1_fold_gate(
    candidate_id: str = "D-C",
    top_n: int = 8,
) -> tuple[bool, dict[str, Any]]:
    """V1.1c P1 #18 pre-flight gate 3: smoke 1-fold gate.

    S5 stub-level: validate yaml load + Assertion 2 composition check + cell
    descriptor emission for the smoke candidate. NOT a real backtest run (S6
    owns real backtest). Catches catastrophic failure modes before triggering
    18-cell ~10 hr sweep.

    Returns: (passed: bool, diagnostic: dict)
    """
    if candidate_id not in CANDIDATE_FACTOR_SETS:
        return False, {
            "passed": False,
            "reason": f"smoke candidate {candidate_id!r} not in CANDIDATE_FACTOR_SETS",
        }
    if top_n not in TOP_N_VALUES:
        return False, {
            "passed": False,
            "reason": f"smoke top_n {top_n} not in TOP_N_VALUES",
        }
    try:
        cfg = load_candidate_config(candidate_id)
    except ValueError as exc:
        return False, {
            "passed": False,
            "reason": f"smoke yaml load failed: {exc}",
        }
    return True, {
        "passed": True,
        "smoke_candidate": candidate_id,
        "smoke_top_n": top_n,
        "factors_loaded": dict(cfg["factors"]),
    }


def run_pre_flight_gates(
    cache_dir: pathlib.Path,
    backtest_start: pd.Timestamp,
) -> tuple[bool, dict[str, Any]]:
    """Phase 2 S5 cell sweep CLI pre-flight orchestration: run 3 gates and
    return aggregated verdict. S6 cell sweep entrypoint MUST call this before
    18-cell run; abort if any gate fails."""
    coverage_ok, coverage_diag = check_cache_coverage_gate(cache_dir)
    lookback_ok, lookback_diag = check_lookback_prereq_gate(cache_dir, backtest_start)
    smoke_ok, smoke_diag = check_smoke_1_fold_gate()
    all_passed = coverage_ok and lookback_ok and smoke_ok
    return all_passed, {
        "all_gates_passed": all_passed,
        "gate_1_cache_coverage": coverage_diag,
        "gate_2_lookback_prereq": lookback_diag,
        "gate_3_smoke_1_fold": smoke_diag,
    }


def _candidate_yaml_path(candidate_id: str) -> pathlib.Path:
    return PROJECT_ROOT / "config" / "d_v7" / f"{candidate_id}.yaml"


def load_candidate_config(candidate_id: str) -> dict[str, Any]:
    """Load and validate a candidate yaml config.

    Raises:
        ValueError: if candidate_id not in CANDIDATE_FACTOR_SETS, yaml missing,
            schema malformed, weights don't sum to ~1.0, or top_n_values
            mismatch [8, 12, 16].
    """
    if candidate_id not in CANDIDATE_FACTOR_SETS:
        raise ValueError(
            f"candidate_id {candidate_id!r} not in CANDIDATE_FACTOR_SETS. "
            f"Allowed: {CANDIDATE_FACTOR_SETS}. (D-A pre-disqualified per V0.13 Assertion 2.)"
        )
    yaml_path = _candidate_yaml_path(candidate_id)
    if not yaml_path.exists():
        raise ValueError(f"yaml config missing: {yaml_path}")
    with yaml_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Schema validation
    required_keys = {"candidate_id", "description", "factors", "top_n_values", "spec_source"}
    missing = required_keys - set(cfg.keys())
    if missing:
        raise ValueError(f"yaml schema missing keys for {candidate_id}: {missing}")
    if cfg["candidate_id"] != candidate_id:
        raise ValueError(
            f"yaml candidate_id mismatch: file={candidate_id} vs payload="
            f"{cfg['candidate_id']}"
        )
    # Weights sum sanity (allow ±0.01 tolerance for IR-weighted rounding)
    total_weight = sum(float(w) for w in cfg["factors"].values())
    if abs(total_weight - 1.0) > 0.01:
        raise ValueError(
            f"yaml {candidate_id} factor weights sum {total_weight} ≠ 1.0 "
            f"(tolerance ±0.01)"
        )
    # top_n_values must equal [8, 12, 16] per V0.13 #7
    if list(cfg["top_n_values"]) != list(TOP_N_VALUES):
        raise ValueError(
            f"yaml {candidate_id} top_n_values {cfg['top_n_values']} ≠ "
            f"{list(TOP_N_VALUES)} (pre-commit #7 frozen)"
        )
    # V0.14 Assertion 2 強化 (R25-mid 獨立 audit P0-1): composition-level
    # D-A pre-disqualification check; catches D-C 50/50 ≡ D-A regression.
    if _composition_equals_forbidden(dict(cfg["factors"])):
        raise ValueError(
            f"V0.14 Assertion 2 FAIL: candidate {candidate_id} composition "
            f"{dict(cfg['factors'])} matches D-A forbidden composition (D1_v2 "
            f"50/50 baseline pre-disqualified per H_d_v6 §D-A pre-disqualification "
            f"record + D6 OOS evidence). R25-mid 獨立 audit P0-1 fix — D-C "
            f"V0.14 已 redesign 為 0.40/0.60 PEAD-weighted variant."
        )
    return cfg


def load_all_candidate_configs() -> dict[str, dict[str, Any]]:
    """Load all 6 candidate yaml configs; raises on any malformed."""
    return {cid: load_candidate_config(cid) for cid in CANDIDATE_FACTOR_SETS}


# ---------------------------------------------------------------------------
# Cell sweep stub (S4 placeholder; S6 owns real BacktestEngine wire-up)
# ---------------------------------------------------------------------------
def run_cell_sweep_stub(output_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """S4 stub: validates 18-cell config grid; emits cell descriptors only.

    Phase 2 S6 expansion (real run):
    - Load each (candidate, top_n) cell as BacktestEngine config
    - Run backtest per cell + aggregate via d_cell_aggregate_v7.py
    - DSR n_trials=18 explicit pass per V1.1b enforcement
    - Pre-flight 3 件 gate (cache coverage / lookback prereq / smoke 1-fold)
    """
    cells: list[dict[str, Any]] = []
    configs = load_all_candidate_configs()
    for candidate_id, cfg in configs.items():
        for top_n in TOP_N_VALUES:
            cells.append({
                "candidate_id": candidate_id,
                "top_n": top_n,
                "factors": cfg["factors"],
                "spec_source": cfg["spec_source"],
            })
    assert len(cells) == EXPECTED_N_TRIALS, (
        f"Cell count drift in stub: {len(cells)} ≠ {EXPECTED_N_TRIALS}. "
        f"V0.13 Assertion 3 violation."
    )
    summary = {
        "candidate_factor_sets": list(CANDIDATE_FACTOR_SETS),
        "top_n_values": list(TOP_N_VALUES),
        "expected_n_trials": EXPECTED_N_TRIALS,
        "n_cells_emitted": len(cells),
        "cells": cells,
        "stub_status": "S4 stub: yaml grid validated; S6 owns BacktestEngine wire-up",
    }
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "cell_grid_stub.yaml").write_text(
            yaml.safe_dump(summary, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("S4 stub cell grid written: %s/cell_grid_stub.yaml", output_dir)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V0.13 d_cell_sweep_v7 (S4 stub)")
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        default=None,
        help="Optional: emit cell grid descriptor to this dir (S6 wire-up scope)",
    )
    args = parser.parse_args()
    summary = run_cell_sweep_stub(output_dir=args.output_dir)
    logger.info(
        "S4 stub complete: %d candidates × %d top_n = %d cells (Assertion 2/3 enforced)",
        len(CANDIDATE_FACTOR_SETS), len(TOP_N_VALUES), summary["n_cells_emitted"],
    )


if __name__ == "__main__":
    main()
