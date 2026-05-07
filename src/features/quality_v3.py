"""V0.13 quality_v3 (D-E candidate factor): PIT-correct profitability composite.

Phase 2 Session 2 (2026-05-05) — H_d_v6 V0.13 §"3 New factor PIT lag spec":
quality_v3 = weighted composite of cross-section z-scored {ROE, gross_margin,
Δassets}, PIT-truncated per (Q4 90d / Q1-3 45d income statement + 60d balance
sheet) lag.

Definition (per H_d_v6:56 D-E row):
    quality_v3 = w_roe × z(ROE_TTM) + w_gm × z(gross_margin_TTM) + w_da × z(Δassets_YoY)
    weights default (0.4, 0.4, 0.2)

**NOT full QMJ**: covers ONLY profitability sub-component (ROE + gross_margin)
+ investment proxy (Δassets, FF investment factor — mislabeled as QMJ
profitability per multi-perspective Q7 P1 caveat). Growth / safety / payout
sub-components NOT included. Per R24 §設計-4 + V1.1c review: D-E spec
acknowledged as "QMJ profitability sub-component, NOT full QMJ".

**Supersedes quality_v2.py** (single-snapshot lookahead bias). quality_v2.py
retained for B0-Lite spike historical reference but DEPRECATED — DO NOT use
in PIT backtest context per V1.2 §"L5 binding" series紀律.

Caller wires (Phase 2 S6 cache fresh-rerun + S5 cell sweep CLI):
    from src.features.quality_v3 import compute_quality_v3_panel
    panel = compute_quality_v3_panel(
        financial_history=fhist_df,      # see schema below
        as_of=rebalance_date,
        income_lag_days_q4=QUARTERLY_EPS_LAG_DAYS_Q4,        # 90d
        income_lag_days_other=QUARTERLY_EPS_LAG_DAYS_OTHER,  # 45d
        balance_lag_days=BALANCE_SHEET_LAG_DAYS,             # 60d
    )

financial_history schema:
    DataFrame with columns:
        - 'symbol' (str): stock symbol (or set as DataFrame index)
        - 'period_end' (date-like): quarter-end date (e.g. 2024-03-31)
        - 'quarter' (int 1-4): quarter number
        - 'roe_ttm' (float): trailing-twelve-month ROE
        - 'gross_margin_ttm' (float): TTM gross margin
        - 'assets_yoy_pct' (float): YoY asset growth (1.0 = +100%)
    Each row = one (symbol, quarter) observation.

Phase 2 S6 owner: wire to FinMind cache (extend `cache_fill_new_factors.py`
to fetch `taiwan_stock_financial_statements` full + `taiwan_stock_balance_sheet`
history; aggregate to quality_v3 schema). V1.2 active_corr stub pattern.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils.constants import (
    BALANCE_SHEET_LAG_DAYS,
    QUARTERLY_EPS_LAG_DAYS_OTHER,
    QUARTERLY_EPS_LAG_DAYS_Q4,
)


DEFAULT_ROE_CLIP: tuple[float, float] = (-0.50, 0.50)
DEFAULT_GROSS_MARGIN_CLIP: tuple[float, float] = (0.0, 1.0)
DEFAULT_DASSETS_CLIP: tuple[float, float] = (-1.0, 1.0)
DEFAULT_Z_CLIP: float = 3.0
DEFAULT_WEIGHTS: tuple[float, float, float] = (0.4, 0.4, 0.2)


def _z_score(series: pd.Series, z_clip: float = DEFAULT_Z_CLIP) -> pd.Series:
    """Cross-sectional z-score with outlier clip."""
    if series.empty:
        return series
    mu = float(series.mean())
    sd = float(series.std(ddof=1))
    if sd <= 1e-12 or not np.isfinite(sd):
        return pd.Series(0.0, index=series.index, dtype=float)
    z = (series - mu) / sd
    return z.clip(-z_clip, z_clip)


def compute_quality_v3_panel(
    financial_history: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    income_lag_days_q4: int = QUARTERLY_EPS_LAG_DAYS_Q4,
    income_lag_days_other: int = QUARTERLY_EPS_LAG_DAYS_OTHER,
    balance_lag_days: int = BALANCE_SHEET_LAG_DAYS,
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
    roe_clip: tuple[float, float] = DEFAULT_ROE_CLIP,
    gross_margin_clip: tuple[float, float] = DEFAULT_GROSS_MARGIN_CLIP,
    dassets_clip: tuple[float, float] = DEFAULT_DASSETS_CLIP,
) -> pd.Series:
    """Compute quality_v3 cross-sectional panel at rebalance date `as_of`.

    Returns:
        pd.Series indexed by symbol, value = weighted composite z-score.
        Symbols with insufficient PIT-valid history (no quarter where all 3
        lags satisfied) are dropped.

    Raises:
        ValueError: if weights don't sum to 1.0.

    PIT semantics (per H_d_v6 V0.13 §"3 New factor PIT lag spec"):
        - ROE / gross_margin: per quarter use respective income lag
          (Q4=income_lag_days_q4=90d / Q1-Q3=income_lag_days_other=45d)
        - Δassets: balance_lag_days=60d (later than income statement)
        - Effective lag per row = max(income_lag, balance_lag) — most
          restrictive (all 3 metrics must be PIT-valid)
        - Per-symbol: latest quarter where (period_end + effective_lag <= as_of)
        - Cross-section: z-score AFTER per-symbol selection (avoid bias from
          mixing different-quarter selections in the z-score baseline)
    """
    if abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError(f"weights must sum to 1.0; got {sum(weights)}")

    if financial_history.empty:
        return pd.Series(dtype=float)

    df = financial_history.copy()
    if "symbol" in df.columns:
        df = df.set_index("symbol", drop=True)

    # Per-symbol PIT truncation: latest valid quarter
    pit_records: dict[str, dict[str, float]] = {}
    for sym in df.index.unique():
        sym_rows = df.loc[[sym]] if not isinstance(df.loc[sym], pd.Series) else df.loc[sym].to_frame().T

        # Filter rows where (period_end + effective_lag <= as_of)
        valid_rows = []
        for _, row in sym_rows.iterrows():
            period_end = pd.Timestamp(row["period_end"])
            quarter = int(row["quarter"])
            income_lag = income_lag_days_q4 if quarter == 4 else income_lag_days_other
            # Conservative: max lag because all 3 metrics needed simultaneously
            effective_lag = max(income_lag, balance_lag_days)
            if period_end + pd.Timedelta(days=effective_lag) <= as_of:
                valid_rows.append(row)

        if not valid_rows:
            continue

        # Latest PIT-valid quarter
        latest = max(valid_rows, key=lambda r: pd.Timestamp(r["period_end"]))
        pit_records[str(sym)] = {
            "roe": float(latest["roe_ttm"]),
            "gross_margin": float(latest["gross_margin_ttm"]),
            "dassets": float(latest["assets_yoy_pct"]),
        }

    if not pit_records:
        return pd.Series(dtype=float)

    # Cross-section assembly + clip + z-score + weighted aggregate
    roe = pd.Series({s: r["roe"] for s, r in pit_records.items()})
    gm = pd.Series({s: r["gross_margin"] for s, r in pit_records.items()})
    da = pd.Series({s: r["dassets"] for s, r in pit_records.items()})

    # Drop NaN/inf before clipping (NaN.clip stays NaN; need explicit drop)
    valid = roe.notna() & gm.notna() & da.notna()
    valid &= np.isfinite(roe) & np.isfinite(gm) & np.isfinite(da)
    roe, gm, da = roe[valid], gm[valid], da[valid]
    if len(roe) == 0:
        return pd.Series(dtype=float)

    roe = roe.clip(*roe_clip)
    gm = gm.clip(*gross_margin_clip)
    da = da.clip(*dassets_clip)

    w_roe, w_gm, w_da = weights
    composite = w_roe * _z_score(roe) + w_gm * _z_score(gm) + w_da * _z_score(da)
    return composite.dropna()
