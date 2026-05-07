"""V0.13 industry_momentum (D-F candidate factor): 6-month per Moskowitz-Grinblatt 1999.

Phase 2 Session 3 (2026-05-05) — H_d_v6 V0.13 §"3 New factor PIT lag spec":
- 6m formation period (NOT 12m; pre-commit #1 frozen per H_d_v6:57)
- monthly rebalance frequency
- per Moskowitz-Grinblatt 1999 "Do Industries Explain Momentum?"

Definition (per H_d_v6:57 D-F row + V0.13 §"industry label PIT strategy"):
    Per symbol score = own industry's average past-6m return.
    Cross-section z-scored across all symbols (not industries).

PIT discipline:
    - Past 6m return computed from ohlcv close < (as_of - 1d) [shift=1]
    - Industry label PIT strategy (V0.13 lock):
      - Option A (preferred): caller passes month-end snapshot @ (as_of - 30d);
        cache key `industry_label_<YYYY-MM-DD>` migration in Phase 2 S6
      - Option B (caveat fallback): caller passes current snapshot;
        D-F results 標 "industry-label PIT not enforced; D-F caveat"
        per V0.13 R14 risk register
      Caller controls strategy by passing appropriate `industry_label_map`.

Caller wires (Phase 2 S6 cache fresh-rerun + S5 cell sweep CLI):
    from src.features.industry_momentum import compute_industry_momentum_panel
    panel = compute_industry_momentum_panel(
        ohlcv_panel=ohlcv_dict,
        industry_label_map=label_map_at_t_minus_30d,  # Option A preferred
        as_of=rebalance_date,
        lookback_months=6,  # per MG1999, MUST be 6
    )

V0.13 enforcement: lookback_months parameter validates == 6 to prevent
silent regression to 12m (pre-commit #1 frozen per R24 §設計-5 fix).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_LOOKBACK_MONTHS: int = 6  # MG1999 per H_d_v6 V0.13; pre-commit #1 frozen
DEFAULT_PIT_INDUSTRY_LAG_DAYS: int = 30
DEFAULT_Z_CLIP: float = 3.0
DEFAULT_MIN_TRADING_DAYS: int = 60


def compute_industry_momentum_panel(
    ohlcv_panel: dict[str, pd.DataFrame],
    industry_label_map: dict[str, str],
    as_of: pd.Timestamp,
    *,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    z_clip: float = DEFAULT_Z_CLIP,
    min_trading_days: int = DEFAULT_MIN_TRADING_DAYS,
) -> pd.Series:
    """Compute D-F industry_momentum cross-section panel at rebalance date.

    Args:
        ohlcv_panel: dict[symbol -> OHLCV DataFrame with DatetimeIndex]; each
            DataFrame must contain a 'close' column.
        industry_label_map: dict[symbol -> industry name str]. PIT strategy
            (Option A vs B) controlled by caller. Symbols missing from map
            are excluded.
        as_of: rebalance date (typically month-end).
        lookback_months: MUST be 6 per H_d_v6 V0.13 + MG1999. Raises if != 6
            (pre-commit #1 frozen; change requires H_d_v7 reframe).
        z_clip: cross-section z-score outlier clip (default ±3σ).
        min_trading_days: per-symbol minimum OHLCV bars within lookback window
            to compute return; symbols below threshold dropped.

    Returns:
        pd.Series indexed by symbol, value = z-scored own industry's past-6m
        return. Empty Series when no valid symbols.

    Raises:
        ValueError: if lookback_months != 6 (V0.13 enforcement).

    PIT semantics:
        - Past 6m return: close at (as_of - 1d) / close at (as_of - 6m - 1d) - 1
          (shift=1 PIT mirroring high_proximity / low_vol_v2 semantics)
        - Industry label: caller's choice of snapshot date; Option A preferred
          but Option B caveat fallback acceptable per V0.13 R14
    """
    if lookback_months != DEFAULT_LOOKBACK_MONTHS:
        raise ValueError(
            f"lookback_months must be 6 per H_d_v6 V0.13 + MG1999; got "
            f"{lookback_months}. Pre-commit #1 frozen — change requires "
            f"H_d_v7 reframe (NOT in-place edit of v6/v7)."
        )

    # PIT cutoffs (shift=1 strict-before)
    cutoff_end = as_of - pd.Timedelta(days=1)
    cutoff_start = as_of - pd.Timedelta(days=lookback_months * 30 + 1)

    # Per-symbol past-6m total return
    sym_returns: dict[str, float] = {}
    for sym, df in ohlcv_panel.items():
        if df is None or df.empty:
            continue
        if "close" not in df.columns:
            continue
        # Strict-before cutoff_end; lower bound cutoff_start
        df_lookback = df[(df.index >= cutoff_start) & (df.index < cutoff_end)]
        if len(df_lookback) < min_trading_days:
            continue
        first_close = float(df_lookback["close"].iloc[0])
        last_close = float(df_lookback["close"].iloc[-1])
        if first_close <= 0 or not np.isfinite(first_close) or not np.isfinite(last_close):
            continue
        sym_returns[sym] = (last_close / first_close) - 1

    if not sym_returns:
        return pd.Series(dtype=float)

    # Aggregate to industry-level (equal-weight within industry)
    industry_returns: dict[str, list[float]] = {}
    for sym, ret in sym_returns.items():
        industry = industry_label_map.get(sym)
        if not industry:
            continue
        industry_returns.setdefault(industry, []).append(ret)

    if not industry_returns:
        return pd.Series(dtype=float)

    industry_avg_return = {
        ind: float(np.mean(rets)) for ind, rets in industry_returns.items()
    }

    # Per-symbol score = own industry's return
    sym_scores: dict[str, float] = {}
    for sym in sym_returns:
        industry = industry_label_map.get(sym)
        if industry and industry in industry_avg_return:
            sym_scores[sym] = industry_avg_return[industry]

    if not sym_scores:
        return pd.Series(dtype=float)

    # Cross-section z-score across symbols
    series = pd.Series(sym_scores)
    mu = float(series.mean())
    sd = float(series.std(ddof=1))
    if sd <= 1e-12 or not np.isfinite(sd):
        return pd.Series(0.0, index=series.index, dtype=float)
    z = (series - mu) / sd
    return z.clip(-z_clip, z_clip)
