"""Phase 2 S7 walk_forward_d_v7 tests.

Verifies:
- L6 80% CI lower bound computation correctness on synthetic monthly active returns
- Locked constants: alpha=0.20 / n=10000 / avg_block_len=3.0 / seed=42
- Determinism: same input + same seed → same CI lower bound
- Per-cell aggregation across 18 cells produces dict[(candidate_id, top_n) → ci_lower]
- JSON IO round-trip (write_bootstrap_ci_lowers + load_cell_monthly_active_returns)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.d_cell_sweep_v7 import CANDIDATE_FACTOR_SETS, TOP_N_VALUES  # noqa: E402
from scripts.walk_forward_d_v7 import (  # noqa: E402
    L6_ALPHA,
    L6_AVG_BLOCK_LEN,
    L6_BOOTSTRAP_N,
    L6_SEED,
    compute_bootstrap_ci_lowers,
    compute_cell_bootstrap_ci_lower,
    load_cell_monthly_active_returns,
    write_bootstrap_ci_lowers,
)


# ---------------------------------------------------------------------------
# Spec lock constants
# ---------------------------------------------------------------------------


def test_l6_locked_constants():
    """13 pre-commit #13 + V0.13 §L6 spec lock — constants must NOT drift."""
    assert L6_ALPHA == 0.20, "L6 alpha must be 0.20 (= 80% CI per pre-commit #13)"
    assert L6_BOOTSTRAP_N == 10000, "L6 bootstrap n must be 10000"
    assert L6_AVG_BLOCK_LEN == 3.0, "L6 avg_block_len must be 3.0"
    assert L6_SEED == 42, "L6 seed must be 42 (deterministic)"


# ---------------------------------------------------------------------------
# Per-cell CI lower bound
# ---------------------------------------------------------------------------


def test_compute_cell_bootstrap_ci_lower_positive_returns():
    """Strongly positive monthly returns → CI lower bound > 0."""
    # 60 months of strongly positive active returns (mean ~0.01, low noise)
    rng = _seeded_returns(mean=0.01, std=0.005, n=60, seed=123)
    ci_lower = compute_cell_bootstrap_ci_lower(rng)
    assert ci_lower is not None
    assert ci_lower > 0.0, f"Expected CI lower > 0 for strongly positive returns; got {ci_lower}"


def test_compute_cell_bootstrap_ci_lower_negative_returns():
    """Strongly negative monthly returns → CI lower bound < 0."""
    rng = _seeded_returns(mean=-0.01, std=0.005, n=60, seed=456)
    ci_lower = compute_cell_bootstrap_ci_lower(rng)
    assert ci_lower is not None
    assert ci_lower < 0.0, f"Expected CI lower < 0 for strongly negative returns; got {ci_lower}"


def test_compute_cell_bootstrap_ci_lower_insufficient_obs():
    """Fewer than 3 observations → None."""
    assert compute_cell_bootstrap_ci_lower([]) is None
    assert compute_cell_bootstrap_ci_lower([0.01, 0.02]) is None


def test_compute_cell_bootstrap_ci_lower_determinism():
    """Same input + locked seed → same CI lower bound (reproducibility)."""
    returns = _seeded_returns(mean=0.005, std=0.01, n=60, seed=999)
    ci1 = compute_cell_bootstrap_ci_lower(returns)
    ci2 = compute_cell_bootstrap_ci_lower(returns)
    assert ci1 == ci2, "Determinism violated: same input → different CI lowers"


# ---------------------------------------------------------------------------
# Aggregate across 18 cells
# ---------------------------------------------------------------------------


def test_compute_bootstrap_ci_lowers_18_cells():
    """compute_bootstrap_ci_lowers handles full 18-cell dict."""
    cell_returns: dict[tuple[str, int], list[float]] = {
        (candidate, top_n): _seeded_returns(
            mean=0.006, std=0.008, n=60, seed=hash((candidate, top_n)) & 0xFFFF
        )
        for candidate in CANDIDATE_FACTOR_SETS
        for top_n in TOP_N_VALUES
    }
    ci_lowers = compute_bootstrap_ci_lowers(cell_returns)
    assert len(ci_lowers) == 18, "Expected 18 cells (6 candidates × 3 top_n)"
    assert all(ci is not None for ci in ci_lowers.values()), (
        "All cells should produce numeric CI lowers (n=60 obs each)"
    )


# ---------------------------------------------------------------------------
# JSON IO round-trip
# ---------------------------------------------------------------------------


def test_write_and_load_round_trip(tmp_path):
    """write_bootstrap_ci_lowers + load_cell_monthly_active_returns round-trip."""
    cell_returns: dict[tuple[str, int], list[float]] = {
        ("D-B", 8): [0.01, 0.02, -0.005, 0.008, 0.015, -0.002, 0.011, 0.007, 0.003, 0.009],
        ("D-C", 12): [-0.005, 0.001, 0.002, -0.003, 0.004, 0.001, -0.001, 0.002, 0.003, 0.000],
    }
    monthly_path = tmp_path / "cell_monthly_active_returns.json"
    raw = {f"{c}|{t}": list(r) for (c, t), r in cell_returns.items()}
    monthly_path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_cell_monthly_active_returns(monthly_path)
    assert loaded == cell_returns, "Round-trip failed: load did not reconstruct input"

    ci_lowers = compute_bootstrap_ci_lowers(loaded)
    out_path = tmp_path / "cell_bootstrap_ci_lowers.json"
    write_bootstrap_ci_lowers(ci_lowers, out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["L6_alpha"] == L6_ALPHA
    assert payload["L6_bootstrap_n"] == L6_BOOTSTRAP_N
    assert payload["L6_avg_block_len"] == L6_AVG_BLOCK_LEN
    assert payload["L6_seed"] == L6_SEED
    assert "D-B|8" in payload["ci_lowers"]
    assert "D-C|12" in payload["ci_lowers"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seeded_returns(mean: float, std: float, n: int, seed: int) -> list[float]:
    """Deterministic Gaussian-like returns via stdlib random for test fixtures."""
    import random
    rng = random.Random(seed)
    return [rng.gauss(mean, std) for _ in range(n)]
