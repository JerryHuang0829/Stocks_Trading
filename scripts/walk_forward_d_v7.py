"""S7 walk-forward bootstrap CI shell for L6 80% CI gate.

Phase 2 Session 7 (2026-05-05) — H_d_v6 13 pre-commit #13 + V0.13 §"L6 80% CI"
- 80% CI lower bound > 0 → L6 PASS (per H_d_v6:204)
- Stationary block bootstrap on per-cell monthly active returns (Politis-Romano 1994)
- alpha=0.20 (80% CI), avg_block_len=3.0, n=10000, seed=42

Stub-real split (per Plan v7.1 + S6.1 Path B):
- S7 STUB: function shell + tests against synthetic monthly active return fixtures
  (no real BacktestEngine — relies on cell_metrics.json from S6.1 d_cell_sweep_v7
  real run as input)
- S7 REAL run: post-S6.1 cache fill + 18-cell run; reads
  reports/phase_d/cell_sweep_v6_2026_05/cell_monthly_active_returns.json
  → produces cell_bootstrap_ci_lowers.json for d_cell_aggregate_v7 L6 wire-up

Caller flow:
    # 1. d_cell_sweep_v7.run_cell_sweep_real() emits monthly active returns per cell
    # 2. walk_forward_d_v7.compute_bootstrap_ci_lowers() → dict[(candidate_id, top_n) → ci_lower]
    # 3. d_cell_aggregate_v7.aggregate_cell_results(cell_metrics, bootstrap_ci_lowers=...)
       → L6 gate evaluated per cell
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Sequence

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.ic_analysis import (  # noqa: E402
    DEFAULT_AVG_BLOCK_LEN,
    DEFAULT_SEED,
    stationary_block_bootstrap_ci,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# V0.13 + 13 pre-commit #13 LOCKED constants
# ---------------------------------------------------------------------------
L6_ALPHA: float = 0.20  # 80% CI per pre-commit #13 (H_d_v6:204)
L6_BOOTSTRAP_N: int = 10000  # n_iterations for cell-level monthly active returns
L6_AVG_BLOCK_LEN: float = DEFAULT_AVG_BLOCK_LEN  # = 3.0
L6_SEED: int = DEFAULT_SEED  # = 42


def compute_cell_bootstrap_ci_lower(
    monthly_active_returns: Sequence[float],
    *,
    alpha: float = L6_ALPHA,
    n: int = L6_BOOTSTRAP_N,
    avg_block_len: float = L6_AVG_BLOCK_LEN,
    seed: int = L6_SEED,
) -> float | None:
    """Stationary block bootstrap 80% CI lower bound for one cell's monthly active returns.

    Wraps `stationary_block_bootstrap_ci` from src/analysis/ic_analysis.py with
    L6-locked params (alpha=0.20, n=10000, avg_block_len=3.0, seed=42).

    Args:
        monthly_active_returns: per-cell IS 60 monthly active returns
                                (= portfolio_monthly - benchmark_monthly)
        alpha: defaults to L6_ALPHA=0.20 (80% CI)
        n: defaults to L6_BOOTSTRAP_N=10000
        avg_block_len: defaults to L6_AVG_BLOCK_LEN=3.0
        seed: defaults to L6_SEED=42 (deterministic across reruns)

    Returns:
        lower bound of 80% CI for mean monthly active return,
        or None if fewer than 3 usable observations.
    """
    lo, _hi = stationary_block_bootstrap_ci(
        monthly_active_returns,
        n=n,
        avg_block_len=avg_block_len,
        alpha=alpha,
        seed=seed,
    )
    return lo


def compute_bootstrap_ci_lowers(
    cell_monthly_active_returns: dict[tuple[str, int], Sequence[float]],
    *,
    alpha: float = L6_ALPHA,
    n: int = L6_BOOTSTRAP_N,
    avg_block_len: float = L6_AVG_BLOCK_LEN,
    seed: int = L6_SEED,
) -> dict[tuple[str, int], float | None]:
    """Compute 80% CI lower bounds for all 18 cells.

    Args:
        cell_monthly_active_returns: dict[(candidate_id, top_n) → monthly active returns]
        alpha/n/avg_block_len/seed: see compute_cell_bootstrap_ci_lower

    Returns:
        dict[(candidate_id, top_n) → ci_lower (None if insufficient data)]
    """
    return {
        cell_id: compute_cell_bootstrap_ci_lower(
            returns,
            alpha=alpha,
            n=n,
            avg_block_len=avg_block_len,
            seed=seed,
        )
        for cell_id, returns in cell_monthly_active_returns.items()
    }


def write_bootstrap_ci_lowers(
    ci_lowers: dict[tuple[str, int], float | None],
    output_path: pathlib.Path,
) -> None:
    """Persist CI lowers to JSON for d_cell_aggregate_v7 consumption.

    JSON schema:
        {
            "L6_alpha": 0.20,
            "L6_bootstrap_n": 10000,
            "L6_avg_block_len": 3.0,
            "L6_seed": 42,
            "ci_lowers": {
                "D-B|8": 0.0123,
                "D-B|12": -0.0045,
                ...
            }
        }
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "L6_alpha": L6_ALPHA,
        "L6_bootstrap_n": L6_BOOTSTRAP_N,
        "L6_avg_block_len": L6_AVG_BLOCK_LEN,
        "L6_seed": L6_SEED,
        "ci_lowers": {
            f"{candidate_id}|{top_n}": ci
            for (candidate_id, top_n), ci in ci_lowers.items()
        },
    }
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("L6 bootstrap CI lowers written: %s", output_path)


def load_cell_monthly_active_returns(
    input_path: pathlib.Path,
) -> dict[tuple[str, int], list[float]]:
    """Load per-cell monthly active returns from S6.1 cell_sweep output JSON.

    Expected schema (produced by d_cell_sweep_v7.run_cell_sweep_real):
        {
            "D-B|8": [0.012, -0.003, ...],
            "D-B|12": [...],
            ...
        }
    """
    with input_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[tuple[str, int], list[float]] = {}
    for key, returns in raw.items():
        candidate_id, top_n_str = key.split("|")
        out[(candidate_id, int(top_n_str))] = list(returns)
    return out


def main() -> None:
    """CLI entrypoint for S7 walk-forward bootstrap CI.

    Stub-level invocation prints L6 spec lock summary; real invocation requires
    --input-monthly-returns <cell_monthly_active_returns.json> from S6.1 real run.
    """
    parser = argparse.ArgumentParser(description="S7 walk-forward bootstrap CI shell")
    parser.add_argument(
        "--input-monthly-returns",
        type=pathlib.Path,
        default=None,
        help="Path to cell_monthly_active_returns.json (S6.1 output). "
             "If omitted, prints spec lock summary only.",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("reports/phase_d/cell_sweep_v6_2026_05/cell_bootstrap_ci_lowers.json"),
        help="Output path for cell_bootstrap_ci_lowers.json",
    )
    args = parser.parse_args()

    if args.input_monthly_returns is None:
        print("walk_forward_d_v7 V0.13 + 13 pre-commit #13 spec lock summary:")
        print(f"  L6 alpha (1 - CI): {L6_ALPHA} (= 80% CI)")
        print(f"  L6 bootstrap n: {L6_BOOTSTRAP_N}")
        print(f"  L6 avg_block_len: {L6_AVG_BLOCK_LEN}")
        print(f"  L6 seed: {L6_SEED} (deterministic)")
        print(f"  Method: stationary_block_bootstrap_ci (Politis-Romano 1994)")
        print(f"  S7 stub: real input from d_cell_sweep_v7.run_cell_sweep_real()")
        print(f"            cell_monthly_active_returns.json (S6.1 user 端 18-cell run)")
        return

    logger.info("Loading monthly active returns from %s", args.input_monthly_returns)
    cell_returns = load_cell_monthly_active_returns(args.input_monthly_returns)
    logger.info("Loaded %d cells", len(cell_returns))

    ci_lowers = compute_bootstrap_ci_lowers(cell_returns)
    write_bootstrap_ci_lowers(ci_lowers, args.output)

    n_pass = sum(1 for ci in ci_lowers.values() if ci is not None and ci > 0.0)
    logger.info("L6 PASS (CI lower > 0): %d/%d cells", n_pass, len(ci_lowers))


if __name__ == "__main__":
    main()
