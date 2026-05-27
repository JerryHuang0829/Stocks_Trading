"""v5.0 Step 6 — Run audit on production cell_summary.

Reads:
  reports/phase_d_v5/v5_ml_cell_summary.json
Writes:
  reports/phase_d_v5/v5_dsr_audit.json
  reports/phase_d_v5/v5_final_outcome.md
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys
from datetime import datetime

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from scripts._factor_ic_helpers import REGIME_SYMBOL, _load_ohlcv  # noqa: E402
from src.analysis.ml_audit import DSR_N_TRIALS, run_audit_from_cell_summary  # noqa: E402
from src.backtest.metrics import adjust_splits_ohlc  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logger = logging.getLogger("v5_step6_audit")


def _build_benchmark_returns_at_as_of(
    benchmark_ohlcv, as_of_dates: list[pd.Timestamp],
    next_rebalance_offset_days: int = 30,
) -> pd.Series:
    """0050 returns keyed at as_of[i], computed as close[as_of[i+1]]/close[as_of[i]] - 1.

    For the last as_of (where no next exists in list), use close at as_of + ~30d.
    Matches portfolio_monthly_returns_from_scores keying.
    """
    adjusted = adjust_splits_ohlc(benchmark_ohlcv)
    close = adjusted["close"].dropna()
    sorted_dates = sorted(pd.Timestamp(d) for d in as_of_dates)
    rets: dict[pd.Timestamp, float] = {}
    for i, t in enumerate(sorted_dates):
        if i + 1 < len(sorted_dates):
            t_next = sorted_dates[i + 1]
        else:
            t_next = t + pd.Timedelta(days=next_rebalance_offset_days)
        # close at or before t / t_next
        c_t_view = close[close.index <= t]
        c_next_view = close[close.index <= t_next]
        if c_t_view.empty or c_next_view.empty:
            continue
        c_t = float(c_t_view.iloc[-1])
        c_next = float(c_next_view.iloc[-1])
        if c_t > 0:
            rets[t] = c_next / c_t - 1.0
    return pd.Series(rets).sort_index()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    cache_dir = resolve_cache_dir()
    bench_ohlcv = _load_ohlcv(cache_dir, REGIME_SYMBOL)

    cell_summary_path = "reports/phase_d_v5/v5_ml_cell_summary.json"
    cs = json.loads(pathlib.Path(cell_summary_path).read_text(encoding="utf-8"))
    # Extract OOS as_of dates from first ML cell (all cells share same as_ofs)
    sample_cell = cs["ml_cells"][0]
    oos_dates = [pd.Timestamp(d) for d in sample_cell["oos_dates"]]
    logger.info("OOS as_of dates from cell_summary: %d", len(oos_dates))

    bench_monthly = _build_benchmark_returns_at_as_of(bench_ohlcv, oos_dates)
    logger.info("benchmark 0050 returns aligned to OOS as_of: %d", len(bench_monthly))

    audit_path = "reports/phase_d_v5/v5_dsr_audit.json"
    audit = run_audit_from_cell_summary(
        cell_summary_path=cell_summary_path,
        bench_monthly=bench_monthly,
        out_path=audit_path,
        sharpe_diff_threshold=0.05,
        dsr_n_trials=DSR_N_TRIALS,
    )

    # Write final outcome.md
    outcome_md = [
        "# v5.0 Final Outcome (production)",
        f"**Audit run**: {audit['audit_date']}",
        f"**Source**: {audit['source_cell_summary']}",
        f"**DSR n_trials**: {audit['dsr_n_trials']} (pre-reg §8.4 Option A)",
        f"**Sharpe diff threshold (pre-reg §1)**: +{audit['sharpe_diff_threshold']}",
        "",
        "## Verdict",
        f"**{audit['verdict']}**",
        f"- Cells pass ALL 6 gates: {audit['n_cells_pass_all_gates']} / {audit['n_cells']}",
        f"- Cells beating baseline by threshold: {audit['n_cells_beat_baseline']} / {audit['n_cells']}",
        "",
        "## Per-cell L1-L6 + DSR",
        "",
        "| Cell | OOS Sharpe | L1 IR | L2 net α | L3 TE | L4 DD-diff | L5 combo | L6 CI80 | DSR Ψ | Gates |",
        "|---|---:|:---:|:---:|:---:|:---:|:---:|:---:|---:|---:|",
    ]
    for c in audit["cell_audits"]:
        g = {x["name"]: x for x in c["gates"]}
        cell_id = c["cell_id"]
        sh = c["oos_sharpe"]
        psi = c["dsr_psi"]
        psi_str = f"{psi:.3f}" if psi is not None else "—"
        def _flag(name):
            return "✅" if g.get(name, {}).get("passed", False) else "❌"
        outcome_md.append(
            f"| {cell_id} | {sh:+.4f} | "
            f"{_flag('L1_IR')} | {_flag('L2_net_alpha_monthly')} | "
            f"{_flag('L3_TE_annual')} | {_flag('L4_max_dd_diff')} | "
            f"{_flag('L5_active_corr_te_t')} | {_flag('L6_bootstrap_ci80_lower')} | "
            f"{psi_str} | {c['n_gates_pass']}/6 |"
        )

    outcome_md.extend([
        "",
        "## vs Baseline",
        "",
        "| Cell | ML Sharpe | Baseline Sharpe | Diff | Beats by ≥+0.05 |",
        "|---|---:|---:|---:|:---:|",
    ])
    for v in audit["vs_baseline"]:
        beats = "✅" if v["beats_baseline_by_threshold"] else "❌"
        outcome_md.append(
            f"| {v['cell_id']} | {v['ml_sharpe']:+.4f} | "
            f"{v['baseline_sharpe']:+.4f} | {v['sharpe_diff']:+.4f} | {beats} |"
        )

    outcome_md.extend([
        "",
        "## L1-L6 gate values (detail)",
        "",
    ])
    for c in audit["cell_audits"]:
        outcome_md.append(f"### {c['cell_id']}  (Sharpe {c['oos_sharpe']:+.4f}, DSR Ψ={c['dsr_psi']})")
        for g in c["gates"]:
            check = "✅" if g["passed"] else "❌"
            val = "—" if g["value"] is None else f"{g['value']:+.4f}"
            outcome_md.append(f"- {check} **{g['name']}** = {val} ; expects {g['threshold']}")
        outcome_md.append("")

    final_path = pathlib.Path("reports/phase_d_v5/v5_final_outcome.md")
    final_path.write_text("\n".join(outcome_md), encoding="utf-8")
    print(f"wrote {final_path}")
    print()
    print("=" * 60)
    print(f"v5.0 STEP 6 AUDIT VERDICT: {audit['verdict']}")
    print("=" * 60)
    print(f"  cells passing ALL gates : {audit['n_cells_pass_all_gates']} / {audit['n_cells']}")
    print(f"  cells beating baseline  : {audit['n_cells_beat_baseline']} / {audit['n_cells']}")
    print(f"  DSR n_trials            : {audit['dsr_n_trials']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
