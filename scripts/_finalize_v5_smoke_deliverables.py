"""Finalize v5.0 smoke deliverables from existing cell_summary JSON.

The smoke run wrote v5_ml_cell_summary_smoke.json successfully but failed at
to_markdown() (missing tabulate). This script regenerates the vs_baseline.md
and outcome.md WITHOUT re-running the 25-minute panel builder.

SHAP not regenerated here — requires model retraining which needs the IS
matrix. Production run will produce SHAP fresh.
"""
from __future__ import annotations

import json
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

OUT_DIR = pathlib.Path("reports/phase_d_v5")


def main():
    cell_summary_path = OUT_DIR / "v5_ml_cell_summary_smoke.json"
    if not cell_summary_path.exists():
        raise FileNotFoundError(f"{cell_summary_path} missing — run smoke first")
    d = json.loads(cell_summary_path.read_text(encoding="utf-8"))

    # Build vs_baseline DataFrame
    baseline_by_top_n = {b["top_n"]: b["oos_sharpe"] for b in d["baseline_cells"]}
    rows = []
    for c in d["ml_cells"]:
        base_sh = baseline_by_top_n.get(c["top_n"], float("nan"))
        diff = c["oos_sharpe"] - base_sh
        rows.append({
            "model_name": c["model_name"],
            "top_n": c["top_n"],
            "ml_oos_sharpe": round(c["oos_sharpe"], 4),
            "baseline_oos_sharpe": round(base_sh, 4),
            "sharpe_diff": round(diff, 4),
            "ml_beats_baseline_by_threshold": diff >= 0.05,
        })
    vs_df = pd.DataFrame(rows)

    # vs_baseline.md
    vs_md_path = OUT_DIR / "v5_ml_vs_baseline_smoke.md"
    vs_md = ["# v5.0 ML vs Baseline OOS Comparison",
             f"**Mode**: {d['mode']}",
             f"**Run**: {d['run_date']}",
             f"**OOS Window**: {d['oos_window'][0]} ~ {d['oos_window'][1]}",
             f"**Pre-reg §1 threshold**: Sharpe diff ≥ +0.05",
             "",
             vs_df.to_markdown(index=False)]
    vs_md_path.write_text("\n".join(vs_md), encoding="utf-8")
    print(f"wrote {vs_md_path}")

    # outcome.md
    best_diff = max((r["sharpe_diff"] for r in rows), default=0.0)
    n_pass_l1_proxy = sum(int(c["oos_sharpe"] >= 0.2) for c in d["ml_cells"])
    n_beat_baseline = sum(int(r["ml_beats_baseline_by_threshold"]) for r in rows)
    outcome_md = [
        f"# v5.0 Outcome (preliminary, mode={d['mode']})",
        f"**Run**: {d['run_date']}",
        "",
        "## Headline",
        f"- ML cells: {len(d['ml_cells'])}",
        f"- Baseline cells: {len(d['baseline_cells'])}",
        f"- Best ML vs baseline Sharpe diff: {best_diff:+.4f}",
        f"  (pre-reg §1 requires ≥ +0.05)",
        f"- ML cells with OOS Sharpe ≥ 0.20 (rough L1 proxy): "
        f"{n_pass_l1_proxy} / {len(d['ml_cells'])}",
        f"- ML cells beating baseline by ≥ +0.05: "
        f"{n_beat_baseline} / {len(rows)}",
        "",
        "## Detail",
        vs_df.to_markdown(index=False),
        "",
        "## Caveats",
        f"- mode={d['mode']}; n_trials={d['n_trials']} per cell "
        "(production: 50)",
        f"- inner_cv_n_splits={d['inner_cv_n_splits']} (production: 5)",
        f"- models={d['models']} (production includes lambdamart)",
        f"- top_n_values={d['top_n_values']} (production: {{15,20,25,30}})",
        "- No DSR n_trials correction applied (production: n_trials=400)",
        "- L1-L6 hard gates not evaluated (Self-Audit pending)",
        "- SHAP not regenerated (needs full IS matrix; production will produce)",
    ]
    outcome_path = OUT_DIR / "v5_outcome_smoke.md"
    outcome_path.write_text("\n".join(outcome_md), encoding="utf-8")
    print(f"wrote {outcome_path}")

    print()
    print("Smoke deliverables finalized (SHAP deferred to production run).")


if __name__ == "__main__":
    main()
