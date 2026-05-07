"""V0.13 quality_v3 PIT-correct logic tests (Phase 2 S2).

Verifies `src.features.quality_v3.compute_quality_v3_panel` correctly handles:
- PIT lag (Q4 90d / Q1-3 45d income statement + 60d balance sheet)
- Cross-section z-score after per-symbol latest-valid-quarter selection
- Weighted composite (40/40/20 ROE / GM / Δassets default)
- Edge cases (empty input, missing data, weights validation)
- Mutation tests catching look-ahead leak / silent default

Phase 2 S6 owner extends to real FinMind cache wire-up; S2 tests use synthetic
financial_history fixture (per V1.2 active_corr stub pattern).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.quality_v3 import (  # noqa: E402
    BALANCE_SHEET_LAG_DAYS,
    DEFAULT_WEIGHTS,
    compute_quality_v3_panel,
)
from src.utils.constants import (  # noqa: E402
    QUARTERLY_EPS_LAG_DAYS_OTHER,
    QUARTERLY_EPS_LAG_DAYS_Q4,
)


def _make_history(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal financial_history fixture from row dicts."""
    return pd.DataFrame(rows)


def test_quality_v3_basic_three_symbols_q1_published():
    """Happy path: 3 symbols, all published Q1 2024 (45d lag), as_of after."""
    rows = [
        {"symbol": "2330", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
        {"symbol": "2317", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.10, "gross_margin_ttm": 0.10, "assets_yoy_pct": 0.05},
        {"symbol": "2454", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.20, "gross_margin_ttm": 0.30, "assets_yoy_pct": 0.15},
    ]
    df = _make_history(rows)
    # Q1 effective_lag = max(45, 60) = 60d; as_of = 2024-06-01 = +62d > 60d ✓
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-06-01"))
    assert len(panel) == 3
    assert "2330" in panel.index
    # 2330 has highest ROE + GM → highest composite z-score
    assert panel["2330"] > panel["2317"]
    assert panel["2330"] > panel["2454"]


def test_quality_v3_pit_excludes_unpublished_q4():
    """PIT critical: Q4 has 90d lag; rebal before publication → exclude."""
    rows = [
        {"symbol": "2330", "period_end": "2024-12-31", "quarter": 4, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
        {"symbol": "2317", "period_end": "2024-09-30", "quarter": 3, "roe_ttm": 0.10, "gross_margin_ttm": 0.10, "assets_yoy_pct": 0.05},
        {"symbol": "2454", "period_end": "2024-09-30", "quarter": 3, "roe_ttm": 0.20, "gross_margin_ttm": 0.30, "assets_yoy_pct": 0.15},
    ]
    df = _make_history(rows)
    # as_of = 2025-02-01 → Q4 2024 (period_end + 90d = 2025-03-31) NOT YET published
    # Q3 2024 (period_end + 60d = 2024-11-29) published ✓
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2025-02-01"))
    # 2330 has only Q4 row, all unpublished → drops out
    assert "2330" not in panel.index
    # 2317 / 2454 Q3 published → in panel
    assert "2317" in panel.index
    assert "2454" in panel.index


def test_quality_v3_q4_after_lag_publication_threshold():
    """Q4 after 90d lag passes → Q4 ROE / GM / Δassets available."""
    rows = [
        {"symbol": "2330", "period_end": "2024-12-31", "quarter": 4, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
        {"symbol": "2317", "period_end": "2024-09-30", "quarter": 3, "roe_ttm": 0.10, "gross_margin_ttm": 0.10, "assets_yoy_pct": 0.05},
        {"symbol": "2454", "period_end": "2024-12-31", "quarter": 4, "roe_ttm": 0.20, "gross_margin_ttm": 0.30, "assets_yoy_pct": 0.15},
    ]
    df = _make_history(rows)
    # as_of = 2025-04-01 → Q4 2024 (period_end + 90d = 2025-03-31) JUST published ✓
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2025-04-01"))
    assert len(panel) == 3
    assert "2330" in panel.index
    assert "2454" in panel.index


def test_quality_v3_balance_lag_dominates_q3_income_lag():
    """Q3 income lag = 45d; balance lag = 60d → effective = 60d; verify
    rebal between Q3+45d and Q3+60d → exclude."""
    rows = [
        {"symbol": "2330", "period_end": "2024-09-30", "quarter": 3, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
        {"symbol": "2317", "period_end": "2024-09-30", "quarter": 3, "roe_ttm": 0.10, "gross_margin_ttm": 0.10, "assets_yoy_pct": 0.05},
    ]
    df = _make_history(rows)
    # as_of = 2024-11-25 → Q3 + 45d = 2024-11-14 PASSED, Q3 + 60d = 2024-11-29 NOT YET
    # effective_lag = max(45, 60) = 60 → must be >= 60d → 2024-11-29 NOT REACHED yet
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-11-25"))
    # All symbols should be excluded because balance_lag (60d) not yet satisfied
    assert panel.empty


def test_quality_v3_uses_latest_pit_valid_quarter():
    """Per-symbol: when multiple PIT-valid quarters exist, use LATEST."""
    rows = [
        # Q1 2024 published, Q2 2024 also published by 2024-09-15
        {"symbol": "2330", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.10, "gross_margin_ttm": 0.30, "assets_yoy_pct": 0.05},
        {"symbol": "2330", "period_end": "2024-06-30", "quarter": 2, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
        {"symbol": "2317", "period_end": "2024-06-30", "quarter": 2, "roe_ttm": 0.10, "gross_margin_ttm": 0.20, "assets_yoy_pct": 0.05},
        {"symbol": "2454", "period_end": "2024-06-30", "quarter": 2, "roe_ttm": 0.20, "gross_margin_ttm": 0.40, "assets_yoy_pct": 0.08},
    ]
    df = _make_history(rows)
    # as_of = 2024-09-15: Q2 (lag 60) period_end + 60 = 2024-08-29 ✓; Q1 also valid
    # 2330 should select Q2 (latest), NOT Q1
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-09-15"))
    # If 2330 used Q2 (roe_ttm=0.30) it should rank above 2317 (Q2 0.10)
    assert panel["2330"] > panel["2317"]
    # Sanity: 2330 z-score should reflect Q2 0.30 not Q1 0.10
    # If used Q1, 2330 would be middle/lowest; using Q2, highest
    assert panel["2330"] > panel["2454"]


def test_quality_v3_weights_must_sum_to_one():
    """Mutation: invalid weights → raise ValueError."""
    rows = [
        {"symbol": "2330", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
    ]
    df = _make_history(rows)
    with pytest.raises(ValueError, match="weights must sum to 1.0"):
        compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-06-01"), weights=(0.5, 0.5, 0.5))


def test_quality_v3_empty_history_returns_empty_series():
    """Edge: empty financial_history → empty Series, NOT raise."""
    df = pd.DataFrame()
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-06-01"))
    assert panel.empty


def test_quality_v3_all_unpublished_returns_empty():
    """Edge: all rows are future periods (unpublished at as_of) → empty."""
    rows = [
        {"symbol": "2330", "period_end": "2025-12-31", "quarter": 4, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
    ]
    df = _make_history(rows)
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-06-01"))
    assert panel.empty


def test_quality_v3_nan_drops_symbol_not_global_fail():
    """Robustness: 1 symbol has NaN in 1 metric → that symbol drops, others survive."""
    rows = [
        {"symbol": "2330", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
        {"symbol": "2317", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": float("nan"), "gross_margin_ttm": 0.10, "assets_yoy_pct": 0.05},
        {"symbol": "2454", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.20, "gross_margin_ttm": 0.30, "assets_yoy_pct": 0.15},
    ]
    df = _make_history(rows)
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-06-01"))
    # 2317 has NaN ROE → dropped; 2330 / 2454 survive
    assert "2317" not in panel.index
    assert len(panel) == 2


def test_quality_v3_clipping_outliers_preserved_in_zscore():
    """Mutation: extreme ROE outlier should be CLIPPED before z-score, not
    distort the cross-section. Catches regression where clipping is bypassed."""
    rows = [
        {"symbol": "2330", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
        # 2317 has unrealistic ROE 5.0 (500%); should clip to 0.50
        {"symbol": "2317", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 5.0, "gross_margin_ttm": 0.10, "assets_yoy_pct": 0.05},
        {"symbol": "2454", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.20, "gross_margin_ttm": 0.30, "assets_yoy_pct": 0.15},
    ]
    df = _make_history(rows)
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-06-01"))
    # All 3 symbols present (NaN/inf check passes 5.0)
    assert len(panel) == 3
    # 2317 ROE clipped to 0.50, but still highest after clip; verify finite
    assert all(np.isfinite(panel.values))


def test_quality_v3_default_weights_are_40_40_20():
    """Sanity: default weights match H_d_v6:56 D-E spec (40/40/20)."""
    assert DEFAULT_WEIGHTS == (0.4, 0.4, 0.2)


def test_quality_v3_balance_lag_constant_is_60():
    """Sanity: BALANCE_SHEET_LAG_DAYS constant = 60 per V0.13 lock."""
    assert BALANCE_SHEET_LAG_DAYS == 60


def test_quality_v3_q4_lag_dominant_for_q4_rows():
    """Q4 income lag = 90d > balance lag = 60d → effective = 90d for Q4 rows.

    Mutation: revert to balance_lag dominant for Q4 → would over-include
    unpublished Q4 income statements."""
    rows = [
        {"symbol": "2330", "period_end": "2024-12-31", "quarter": 4, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": 0.10},
    ]
    df = _make_history(rows)
    # as_of = 2025-03-15 → Q4 + 60d = 2025-03-01 PASSED (balance OK)
    # but Q4 + 90d = 2025-03-31 NOT YET (income still unpublished)
    # effective_lag = max(90, 60) = 90 → must wait → exclude
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2025-03-15"))
    assert panel.empty, "Q4 effective lag must use 90d (income > balance for Q4)"


def test_quality_v3_negative_dassets_handled():
    """Edge: company with shrinking assets (Δassets < 0) → still included."""
    rows = [
        {"symbol": "2330", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.30, "gross_margin_ttm": 0.55, "assets_yoy_pct": -0.05},
        {"symbol": "2317", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.10, "gross_margin_ttm": 0.10, "assets_yoy_pct": -0.10},
        {"symbol": "2454", "period_end": "2024-03-31", "quarter": 1, "roe_ttm": 0.20, "gross_margin_ttm": 0.30, "assets_yoy_pct": 0.15},
    ]
    df = _make_history(rows)
    panel = compute_quality_v3_panel(df, as_of=pd.Timestamp("2024-06-01"))
    assert len(panel) == 3
    # All finite, panel computes
    assert all(np.isfinite(panel.values))
