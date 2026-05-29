"""Active correlation (L5 sub-condition a).

L5 (a) threshold: active_corr <= 0.50 (high active corr means portfolio
"hugs" benchmark; not real active management).

Definition: Pearson correlation between monthly active returns
(portfolio_monthly - benchmark_monthly) and benchmark_monthly_returns.

Index alignment check enforced: same length but different date indexes →
raises ValueError. Caller MUST align by date index before calling.
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
            indexes (sanity check).

    Binding notes:
        - signature: monthly returns (NOT daily — frequency error)
        - definition: active = portfolio - benchmark; corr(active, benchmark)
        - NOT corr(portfolio, portfolio) or corr(portfolio, benchmark)
          directly (which would be different metric)
    """
    if len(portfolio_monthly_returns) != len(benchmark_monthly_returns):
        raise ValueError(
            f"Length mismatch: portfolio={len(portfolio_monthly_returns)} vs "
            f"benchmark={len(benchmark_monthly_returns)}. Caller must align "
            f"monthly periods before calling active_corr."
        )
    # Docstring promises non-aligned index check but length alone is not enough.
    # Same length ≠ same dates; pandas Series subtract auto-aligns by index which
    # silently produces wrong result if dates differ. Caller MUST align by date
    # index first.
    if not portfolio_monthly_returns.index.equals(benchmark_monthly_returns.index):
        raise ValueError(
            f"Index misalignment: portfolio[0]={portfolio_monthly_returns.index[0]} "
            f"vs benchmark[0]={benchmark_monthly_returns.index[0]}; lengths match "
            f"but date indexes differ. Caller must align by date index before "
            f"calling active_corr (V0.14 P0-4 fix per R25-mid 獨立 audit)."
        )
    active = portfolio_monthly_returns - benchmark_monthly_returns
    return float(active.corr(benchmark_monthly_returns))
