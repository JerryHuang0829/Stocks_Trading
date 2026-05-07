"""Phase 2 S8 sole_survivor_v7 tests.

Verifies:
- 13 pre-commit #9 tie-break invariant (highest IR > highest mean α)
- 13 pre-commit #11 D-A pre-disqualification guard (raises if D-A appears)
- candidate_id ∈ CANDIDATE_FACTOR_SETS validation
- internally inconsistent input (sole_survivor set but no all_l1_l6_passed cell) raises
- malformed input (missing 'cells' field) raises
- emit_phase_d_v7_complete_tag_command returns None on NO-GO, command string on GO
- write_sole_survivor JSON schema correctness
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.d_cell_sweep_v7 import CANDIDATE_FACTOR_SETS  # noqa: E402
from scripts.sole_survivor_v7 import (  # noqa: E402
    DA_PREDISQUALIFIED_ID,
    emit_phase_d_v7_complete_tag_command,
    lock_sole_survivor,
    write_sole_survivor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_passing_cell(candidate_id: str, top_n: int, ir: float = 0.25, mean_a: float = 0.008):
    return {
        "candidate_id": candidate_id,
        "top_n": top_n,
        "metrics": {"ir": ir, "mean_alpha_monthly": mean_a},
        "all_l1_l6_passed": True,
    }


def _make_failing_cell(candidate_id: str, top_n: int):
    return {
        "candidate_id": candidate_id,
        "top_n": top_n,
        "metrics": {"ir": 0.10, "mean_alpha_monthly": 0.001},
        "all_l1_l6_passed": False,
    }


def _make_outcome_1_summary():
    """Build cell_summary mimicking d_cell_aggregate_v7 output: Outcome-1, 1 winner."""
    cells = [
        _make_passing_cell("D-B", 8, ir=0.30, mean_a=0.010),
        _make_passing_cell("D-C", 12, ir=0.25, mean_a=0.012),
        _make_failing_cell("D-D", 16),
    ]
    return {
        "outcome_classification": "Outcome-1 Full Pass",
        "cells": cells,
        "sole_survivor": cells[0],  # D-B 8 has highest IR 0.30
    }


# ---------------------------------------------------------------------------
# Spec lock
# ---------------------------------------------------------------------------


def test_da_predisqualified_constant():
    """Pre-commit #11 spec lock — D-A constant must NOT drift."""
    assert DA_PREDISQUALIFIED_ID == "D-A"


# ---------------------------------------------------------------------------
# lock_sole_survivor — happy path
# ---------------------------------------------------------------------------


def test_lock_outcome_1_returns_validated_winner():
    summary = _make_outcome_1_summary()
    result = lock_sole_survivor(summary)
    assert result is not None
    assert result["candidate_id"] == "D-B"
    assert result["top_n"] == 8


def test_lock_no_outcome_1_returns_none():
    summary = {
        "outcome_classification": "Outcome-4 Full Fail",
        "cells": [_make_failing_cell("D-B", 8), _make_failing_cell("D-C", 12)],
        "sole_survivor": None,
    }
    result = lock_sole_survivor(summary)
    assert result is None


# ---------------------------------------------------------------------------
# lock_sole_survivor — pre-commit #11 D-A guard (mutation-equivalent test)
# ---------------------------------------------------------------------------


def test_lock_da_candidate_raises():
    """Pre-commit #11: D-A as sole_survivor must raise."""
    summary = {
        "outcome_classification": "Outcome-1 Full Pass",
        "cells": [
            _make_passing_cell("D-A", 8, ir=0.30),
        ],
        "sole_survivor": _make_passing_cell("D-A", 8, ir=0.30),
    }
    with pytest.raises(ValueError, match="pre-commit #11"):
        lock_sole_survivor(summary)


# ---------------------------------------------------------------------------
# lock_sole_survivor — defensive validation
# ---------------------------------------------------------------------------


def test_lock_unknown_candidate_raises():
    """candidate_id not in CANDIDATE_FACTOR_SETS → raise."""
    summary = {
        "outcome_classification": "Outcome-1 Full Pass",
        "cells": [_make_passing_cell("D-Z", 8)],
        "sole_survivor": _make_passing_cell("D-Z", 8),
    }
    with pytest.raises(ValueError, match="not in CANDIDATE_FACTOR_SETS"):
        lock_sole_survivor(summary)


def test_lock_missing_cells_field_raises():
    summary = {"outcome_classification": "Outcome-1 Full Pass", "sole_survivor": None}
    with pytest.raises(ValueError, match="missing 'cells' field"):
        lock_sole_survivor(summary)


def test_lock_internally_inconsistent_raises():
    """sole_survivor set but no cell has all_l1_l6_passed=True → raise."""
    summary = {
        "outcome_classification": "Outcome-1 Full Pass",
        "cells": [_make_failing_cell("D-B", 8)],
        "sole_survivor": _make_passing_cell("D-B", 8),
    }
    with pytest.raises(ValueError, match="internally inconsistent"):
        lock_sole_survivor(summary)


def test_lock_tie_break_invariant_violation_raises():
    """Pre-commit #9: sole_survivor IR ≠ max IR among Outcome-1 cells → raise."""
    cells = [
        _make_passing_cell("D-B", 8, ir=0.30),  # actual max IR
        _make_passing_cell("D-C", 12, ir=0.25),
    ]
    summary = {
        "outcome_classification": "Outcome-1 Full Pass",
        "cells": cells,
        "sole_survivor": cells[1],  # D-C has IR 0.25, NOT max → invariant violated
    }
    with pytest.raises(ValueError, match="pre-commit #9"):
        lock_sole_survivor(summary)


# ---------------------------------------------------------------------------
# emit_phase_d_v7_complete_tag_command
# ---------------------------------------------------------------------------


def test_emit_tag_command_on_go():
    sole_survivor = _make_passing_cell("D-B", 8)
    cmd = emit_phase_d_v7_complete_tag_command(sole_survivor)
    assert cmd is not None
    assert "phase-d-v7-complete" in cmd


def test_emit_tag_command_on_no_go():
    cmd = emit_phase_d_v7_complete_tag_command(None)
    assert cmd is None


# ---------------------------------------------------------------------------
# write_sole_survivor JSON schema
# ---------------------------------------------------------------------------


def test_write_sole_survivor_go_schema(tmp_path):
    sole_survivor = _make_passing_cell("D-B", 8)
    out = tmp_path / "sole_survivor_v6.json"
    write_sole_survivor(sole_survivor, "Outcome-1 Full Pass", out)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["outcome"] == "Outcome-1 Full Pass"
    assert payload["sole_survivor"]["candidate_id"] == "D-B"
    assert payload["tag_emit_command"] is not None
    assert "phase-d-v7-complete" in payload["tag_emit_command"]


def test_write_sole_survivor_no_go_schema(tmp_path):
    out = tmp_path / "sole_survivor_v6.json"
    write_sole_survivor(None, "Outcome-4 Full Fail", out)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["outcome"] == "Outcome-4 Full Fail"
    assert payload["sole_survivor"] is None
    assert payload["tag_emit_command"] is None
