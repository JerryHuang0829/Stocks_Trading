"""Active correlation (L5 sub-condition a) — V1.2 binding (S5 落地完成).

Phase 2 Session 1 落地 stub + signature; **Phase 2 Session 5 (2026-05-05)
落地完整 V1.2 binding requirements**:
  1. ✅ commit (S1 c023d0b stub + V0.14 index alignment + S5 docstring update)
  2. ✅ e2e test (test_active_correlation.py 7 tests including A10 mutation
        3 範例: self-corr / port-vs-bench / daily-frequency)
  3. ✅ Cell sweep CLI integration (d_cell_sweep_v7.py per-cell active_corr
        output scaffolding @ S5; real wire-up @ S6 cache fresh-rerun)
  4. ✅ A10 mutation test cover (3 mutation 範例 per V1.2 §"L5 active_corr
        binding (V1.2 lock)" A10 attacker connection)
  5. ✅ tag `phase-d-v7-implementation-start` @ S5 commit

L5 (a) threshold: active_corr <= 0.50 (high active corr means portfolio
"hugs" benchmark; not real active management). Per H_d_v6:29 6 hard gates
table.

Definition: Pearson correlation between monthly active returns
(portfolio_monthly - benchmark_monthly) and benchmark_monthly_returns.

V0.14 (R25-mid Codex audit P0-4 fix): index alignment check enforced; same
length but different date indexes → raises ValueError. Caller MUST align by
date index before calling.
"""
from __future__ import annotations

import pandas as pd


def active_corr(
    portfolio_monthly_returns: pd.Series,
    benchmark_monthly_returns: pd.Series,
) -> float:
    """Compute active correlation: corr(portfolio - benchmark, benchmark).

    Args:
        portfolio_monthly_returns: Monthly portfolio returns indexed by date.
        benchmark_monthly_returns: Monthly benchmark (typically 0050) returns
            indexed by same date.

    Returns:
        Pearson correlation between active returns and benchmark returns.
        Range [-1, 1]. L5 (a) gate: must be <= 0.50.

    Raises:
        ValueError: if input Series have different lengths or non-aligned
            indexes (sanity check; full alignment handling at Phase 2 S5).

    V1.2 binding (stub-level):
        - signature: monthly returns (NOT daily — frequency error per V1.2 P0)
        - definition: active = portfolio - benchmark; corr(active, benchmark)
        - NOT corr(portfolio, portfolio) or corr(portfolio, benchmark)
          directly (which would be different metric)

    V1.2 Phase 2 S5 expansion (full implementation):
        - Cell sweep CLI integration: 18-cell sweep each cell outputs
          active_corr value + L5 (a) PASS/FAIL flag
        - A10 attacker mutation test cover (3 mutation: self-corr / daily /
          移除 active = port - bench)
        - Tag commit `phase-d-v7-implementation-start`
    """
    if len(portfolio_monthly_returns) != len(benchmark_monthly_returns):
        raise ValueError(
            f"Length mismatch: portfolio={len(portfolio_monthly_returns)} vs "
            f"benchmark={len(benchmark_monthly_returns)}. Caller must align "
            f"monthly periods before calling active_corr."
        )
    # V0.14 fix per R25-mid Codex audit P0-4: docstring promised non-aligned
    # index check but original code only verified length. Same length ≠ same
    # dates; pandas Series subtract auto-aligns by index which silently produces
    # wrong result if dates differ. Caller MUST align by date index first.
    if not portfolio_monthly_returns.index.equals(benchmark_monthly_returns.index):
        raise ValueError(
            f"Index misalignment: portfolio[0]={portfolio_monthly_returns.index[0]} "
            f"vs benchmark[0]={benchmark_monthly_returns.index[0]}; lengths match "
            f"but date indexes differ. Caller must align by date index before "
            f"calling active_corr (V0.14 P0-4 fix per R25-mid Codex audit)."
        )
    active = portfolio_monthly_returns - benchmark_monthly_returns
    return float(active.corr(benchmark_monthly_returns))
