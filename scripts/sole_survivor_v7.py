"""S8 sole_survivor lock + tag emit shell.

Phase 2 Session 8 (2026-05-05) — H_d_v6 §"Candidate factor sets" + 13 pre-commit #9
- Sole-survivor tie-break: highest IR > highest mean α (per pre-commit #9)
- D-A pre-disqualification: NOT a fallback (per pre-commit #11)
- Tag `phase-d-v7-complete` emit on Outcome-1 lock (per Plan v7.1 closeout flow)

Stub-real split:
- S8 STUB: function shell + tests against synthetic cell_summary fixtures
- S8 REAL run: post-S6.1 + S7 wire-up; reads cell_summary_v6.json (= aggregate output)
  → emits sole_survivor_v6.json + git tag command output

Caller flow:
    # 1. d_cell_aggregate_v7 produces cell_summary (with sole_survivor field via tie-break)
    # 2. sole_survivor_v7.lock_sole_survivor() validates pre-commit #9 + #11 invariants
    # 3. emit_phase_d_v7_complete_tag() prints `git tag` command for user to run
       (deliberately NOT auto-tagging — user owns commit/tag boundary)
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.d_cell_sweep_v7 import CANDIDATE_FACTOR_SETS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 13 pre-commit #11 LOCKED constant — D-A is NOT a fallback (re-enforce)
# ---------------------------------------------------------------------------
DA_PREDISQUALIFIED_ID: str = "D-A"


def lock_sole_survivor(
    cell_summary: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate sole_survivor against pre-commit #9 (tie-break) + #11 (D-A guard).

    Args:
        cell_summary: aggregate result from d_cell_aggregate_v7.aggregate_cell_results
                      (contains 'sole_survivor' field — None if no Outcome-1 cell)

    Returns:
        validated sole_survivor dict, or None if no Outcome-1 cell.

    Raises:
        ValueError: if sole_survivor.candidate_id == "D-A" (pre-commit #11 violation)
        ValueError: if cell_summary has no 'cells' field (malformed input)
    """
    if "cells" not in cell_summary:
        raise ValueError(
            "S8 lock FAIL: cell_summary missing 'cells' field; not a valid "
            "aggregate output from d_cell_aggregate_v7."
        )

    sole_survivor = cell_summary.get("sole_survivor")
    if sole_survivor is None:
        logger.info(
            "S8 lock: no sole_survivor (Outcome-2 Partial or Outcome-4 Full Fail). "
            "Plan v7.1 closeout: NO-GO (pre-commit #9 tie-break has no candidate)."
        )
        return None

    # Pre-commit #11: D-A is NOT a fallback
    if sole_survivor.get("candidate_id") == DA_PREDISQUALIFIED_ID:
        raise ValueError(
            f"S8 lock FAIL (13 pre-commit #11): sole_survivor candidate_id "
            f"== '{DA_PREDISQUALIFIED_ID}' (pre-disqualified). D-A cannot be "
            f"reopened mid-experiment per H_d_v6:184 + V0.13 Assertion 2."
        )

    # Defensive: validate candidate_id is in the locked candidate pool
    candidate_id = sole_survivor.get("candidate_id")
    if candidate_id not in CANDIDATE_FACTOR_SETS:
        raise ValueError(
            f"S8 lock FAIL: sole_survivor candidate_id '{candidate_id}' not in "
            f"CANDIDATE_FACTOR_SETS {CANDIDATE_FACTOR_SETS}."
        )

    # Defensive: confirm tie-break invariant (sole_survivor IR is max among Outcome-1 cells)
    outcome_1_cells = [c for c in cell_summary["cells"] if c.get("all_l1_l6_passed")]
    if not outcome_1_cells:
        raise ValueError(
            "S8 lock FAIL: cell_summary.sole_survivor is set but no cell has "
            "all_l1_l6_passed=True; aggregate output is internally inconsistent."
        )
    max_ir = max(c["metrics"].get("ir", 0.0) for c in outcome_1_cells)
    if abs(sole_survivor["metrics"].get("ir", 0.0) - max_ir) > 1e-9:
        raise ValueError(
            f"S8 lock FAIL (13 pre-commit #9): sole_survivor IR "
            f"{sole_survivor['metrics'].get('ir')} ≠ max IR {max_ir} among "
            f"Outcome-1 cells; tie-break invariant violated."
        )

    logger.info(
        "S8 sole_survivor locked: candidate_id=%s top_n=%s IR=%s mean_α=%s",
        candidate_id,
        sole_survivor.get("top_n"),
        sole_survivor["metrics"].get("ir"),
        sole_survivor["metrics"].get("mean_alpha_monthly"),
    )
    return sole_survivor


def write_sole_survivor(
    sole_survivor: dict[str, Any] | None,
    cell_summary_outcome: str,
    output_path: pathlib.Path,
) -> None:
    """Persist sole_survivor lock result to JSON for R25-final 獨立 audit.

    Output schema:
        {
            "outcome": "Outcome-1 Full Pass" | "Outcome-2 Partial" | "Outcome-4 Full Fail",
            "sole_survivor": <cell dict> | None,
            "tag_emit_command": "git tag phase-d-v7-complete <SHA>" | null
        }
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "outcome": cell_summary_outcome,
        "sole_survivor": sole_survivor,
        "tag_emit_command": (
            "git tag phase-d-v7-complete <commit-SHA>"
            if sole_survivor is not None
            else None
        ),
    }
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info("S8 sole_survivor lock written: %s", output_path)


def emit_phase_d_v7_complete_tag_command(
    sole_survivor: dict[str, Any] | None,
) -> str | None:
    """Emit (NOT execute) git tag command for `phase-d-v7-complete`.

    Deliberately returns command string for user to run; does NOT auto-tag.
    User owns commit/tag boundary per the dev guide "git safety protocol".

    Args:
        sole_survivor: validated sole_survivor dict, or None

    Returns:
        `git tag phase-d-v7-complete <SHA>` command string, or None if NO-GO
    """
    if sole_survivor is None:
        return None
    return "git tag phase-d-v7-complete <commit-SHA>"


def main() -> None:
    """CLI entrypoint for S8 sole_survivor lock."""
    parser = argparse.ArgumentParser(description="S8 sole_survivor lock + tag emit")
    parser.add_argument(
        "--cell-summary",
        type=pathlib.Path,
        default=None,
        help="Path to cell_summary_v6.json (d_cell_aggregate_v7 output). "
             "If omitted, prints S8 spec lock summary only.",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("reports/phase_d/cell_sweep_v6_2026_05/sole_survivor_v6.json"),
        help="Output path for sole_survivor_v6.json",
    )
    args = parser.parse_args()

    if args.cell_summary is None:
        print("sole_survivor_v7 V0.13 + 13 pre-commit #9/#11 spec lock summary:")
        print(f"  CANDIDATE_FACTOR_SETS: {CANDIDATE_FACTOR_SETS}")
        print(f"  D-A pre-disqualified (pre-commit #11): {DA_PREDISQUALIFIED_ID}")
        print(f"  Tie-break (pre-commit #9): highest IR > highest mean α")
        print(f"  Tag emit (Plan v7.1 closeout): phase-d-v7-complete (user runs)")
        print(f"  S8 stub: real input from d_cell_aggregate_v7.aggregate_cell_results()")
        return

    logger.info("Loading cell_summary from %s", args.cell_summary)
    with args.cell_summary.open("r", encoding="utf-8") as f:
        cell_summary = json.load(f)

    sole_survivor = lock_sole_survivor(cell_summary)
    write_sole_survivor(
        sole_survivor,
        cell_summary.get("outcome_classification", "unknown"),
        args.output,
    )

    tag_command = emit_phase_d_v7_complete_tag_command(sole_survivor)
    if tag_command:
        print(f"\nUser action required: {tag_command}")
    else:
        print("\nNO-GO outcome: no phase-d-v7-complete tag to emit.")


if __name__ == "__main__":
    main()
