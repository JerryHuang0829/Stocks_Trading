"""Quality factor (single-snapshot, B0-Lite spike only).

⚠️ **DEPRECATED (Phase 2 S2, 2026-05-05)**: use `src/features/quality_v3.py`
for PIT-correct profitability composite per H_d_v6 V0.13 D-E candidate spec.
This module retained for B0-Lite spike historical reference only — DO NOT
use in PIT backtest context (refusal gate `assert_not_in_pit_backtest`
remains active).

Replacement: `compute_quality_v3_panel(financial_history, as_of, ...)`
returns PIT-truncated cross-section z-score composite. See
`src/features/quality_v3.py` module docstring for migration.

Score per symbol = z-score-additive composite of:
    (a) annualised ROE = (net_income / equity) * 4   (single-quarter annualised)
    (b) gross_margin = gross_profit / revenue        (single-quarter)

Both ratios are clipped before z-scoring (cross-section) inside the
`compute_quality_v2_universe` batch helper to limit outlier influence.

============================================================================
**WARNING — LOOKAHEAD BIAS (B0-Lite spike scope)**
============================================================================
This factor reads from `FinMindSource.fetch_financial_quality(symbol)`, which
returns a SINGLE-SNAPSHOT dict (the latest quarter at cache build time). It
does NOT vary with the caller's `as_of`. Therefore:

    * Backtest at as_of=2020-01-31 still sees the most recent quarter's
      ROE / gross_margin (e.g. 2024-Q3) → look-ahead bias.
    * IC results from this factor are INFLATED. Expect 30-50% IC degradation
      after the PIT-correct rewrite (Phase B0 full version).

This module exists ONLY to:
    * Spike the IC magnitude before committing to the 8-12 hr PIT rewrite.
    * Confirm coverage / turnover / active-return characteristics.

Phase B0-Lite reject criteria treat this factor's IC ≤ 0.02 as a strong
signal that PIT-correct version will be ≤ 0 (no edge); IC > 0.02 only weakly
suggests Phase B0 full version is worth attempting.

DO NOT enable this factor in live `_rank_analyses` with backtest_context=True.
A `RuntimeError` refusal gate enforces this (mirrors R19 audit A.2 pattern
applied to fetch_financial_quality in src/portfolio/tw_stock.py:691).
============================================================================

Motivation: Asness-Frazzini-Pedersen 2014 ("Quality Minus Junk") documents
profitability + safety + growth + payout as sub-components of "quality".
This implementation covers only the PROFITABILITY sub-component (ROE +
gross_margin). It is NOT a faithful QMJ replication; growth, safety, and
payout dimensions are absent.

Retail-tractable: requires only cached fetch_financial_quality dict.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


DEFAULT_ROE_CLIP = (-0.50, 0.50)
DEFAULT_GROSS_MARGIN_CLIP = (0.0, 1.0)
DEFAULT_Z_CLIP = 3.0


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_quality_v2(
    fq_dict: dict | None,
    *,
    roe_clip: tuple[float, float] = DEFAULT_ROE_CLIP,
    gross_margin_clip: tuple[float, float] = DEFAULT_GROSS_MARGIN_CLIP,
) -> dict:
    """Per-symbol single-snapshot quality score (NOT z-scored — caller does it).

    Returns dict with keys: score (raw composite, equal-weight ROE + GM after
    clip), roe_clipped, gross_margin_clipped, detail.

    The raw `score` is the equal-weighted sum of clipped ROE and clipped
    gross_margin. Cross-sectional Z-score is applied by
    `compute_quality_v2_universe` before composite ranking — this scalar is
    only useful for per-symbol diagnostics or single-stock display.
    """
    if fq_dict is None:
        return {"score": None, "detail": "fq_missing", "icon": "➖"}
    roe = fq_dict.get("roe")
    gm = fq_dict.get("gross_margin")
    if roe is None or gm is None:
        return {"score": None, "detail": "fq_partial", "icon": "➖"}
    if not (np.isfinite(roe) and np.isfinite(gm)):
        return {"score": None, "detail": "fq_nonfinite", "icon": "➖"}

    roe_c = _clip(float(roe), *roe_clip)
    gm_c = _clip(float(gm), *gross_margin_clip)
    raw_score = roe_c + gm_c
    return {
        "score": raw_score,
        "roe_clipped": roe_c,
        "gross_margin_clipped": gm_c,
        "detail": "single_snapshot_lookahead",
        "icon": "🟢" if raw_score >= 0.5 else ("✅" if raw_score >= 0.2 else "⚠️"),
    }


def compute_quality_v2_universe(
    fq_by_symbol: Mapping[str, dict | None],
    *,
    roe_clip: tuple[float, float] = DEFAULT_ROE_CLIP,
    gross_margin_clip: tuple[float, float] = DEFAULT_GROSS_MARGIN_CLIP,
    z_clip: float = DEFAULT_Z_CLIP,
) -> pd.Series:
    """Batch quality_v2 score for all symbols (cross-section z-scored).

    Returns a Series indexed by symbol of the FINAL composite score:
        z(roe_clipped) + z(gross_margin_clipped)
    Symbols with missing/non-finite ROE or gross_margin are dropped.

    The final z-score is clipped at ±z_clip per dimension (default 3σ) to
    limit single-symbol outlier influence.

    NOTE: This factor uses single-snapshot `fetch_financial_quality` data
    that does NOT vary with `as_of`. IC from this factor is INFLATED by
    look-ahead bias. See module docstring.
    """
    raw_roe: dict[str, float] = {}
    raw_gm: dict[str, float] = {}
    for symbol, fq in fq_by_symbol.items():
        scored = score_quality_v2(
            fq, roe_clip=roe_clip, gross_margin_clip=gross_margin_clip,
        )
        if scored["score"] is None:
            continue
        raw_roe[symbol] = scored["roe_clipped"]
        raw_gm[symbol] = scored["gross_margin_clipped"]

    if not raw_roe or not raw_gm:
        return pd.Series(dtype=float)

    roe_series = pd.Series(raw_roe, dtype=float)
    gm_series = pd.Series(raw_gm, dtype=float)

    def _z(series: pd.Series) -> pd.Series:
        mu = float(series.mean())
        sd = float(series.std(ddof=1))
        if sd <= 1e-12 or not np.isfinite(sd):
            return pd.Series(0.0, index=series.index, dtype=float)
        z = (series - mu) / sd
        return z.clip(-z_clip, z_clip)

    composite = _z(roe_series) + _z(gm_series)
    return composite.dropna()


def assert_not_in_pit_backtest(portfolio_config: dict | None) -> None:
    """Refusal gate: forbid quality_v2 use inside `_backtest_context=True`.

    Mirrors src/portfolio/tw_stock.py:691 R19 audit A.2 refusal gate pattern.
    Phase B0-Lite spike runs as a standalone batch script (no
    `_backtest_context` marker), so the gate is informational here. If a
    future caller wires quality_v2 into `_rank_analyses` with backtest
    context, this guard MUST trigger because single-snapshot quality is
    NOT PIT-correct.
    """
    if portfolio_config is None:
        return
    if portfolio_config.get("_backtest_context", False):
        raise RuntimeError(
            "quality_v2 in backtest_context=True is forbidden: factor reads "
            "single-snapshot fetch_financial_quality (lookahead bias). "
            "Implement fetch_financial_quality_history (PIT-correct) before "
            "re-enabling. See src/features/quality_v2.py module docstring."
        )
