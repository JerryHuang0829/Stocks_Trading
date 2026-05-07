"""quality_v3 history aggregator tests (S6.1 Path B).

Verifies `aggregate_quality_v3_history()` correctly computes TTM rolling + YoY
Δassets from raw FinMind long-format DataFrames using synthetic fixtures.

Real FinMind cache wire-up + cache fill 由 user 端跑（per Path B 規劃）；S6.1
Path B tests 用 synthetic fixture verify 邏輯正確。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.quality_v3_aggregator import (  # noqa: E402
    _pivot_long_to_wide,
    _quarter_from_date,
    aggregate_quality_v3_history,
)


def _make_fs_long(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Build long-format income statement DataFrame from (date, type, value) tuples."""
    return pd.DataFrame(
        rows, columns=["date", "type", "value"]
    ).assign(stock_id="2330")


def _make_bs_long(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Build long-format balance sheet DataFrame from (date, type, value) tuples."""
    return pd.DataFrame(
        rows, columns=["date", "type", "value"]
    ).assign(stock_id="2330")


def test_quarter_from_date_q1_q2_q3_q4():
    """Map period_end date → correct quarter number."""
    assert _quarter_from_date(pd.Timestamp("2024-03-31")) == 1
    assert _quarter_from_date(pd.Timestamp("2024-06-30")) == 2
    assert _quarter_from_date(pd.Timestamp("2024-09-30")) == 3
    assert _quarter_from_date(pd.Timestamp("2024-12-31")) == 4


def test_pivot_long_to_wide_basic():
    """Long format → wide format with one column per type."""
    long_df = _make_fs_long([
        ("2024-03-31", "Revenue", 100.0),
        ("2024-03-31", "GrossProfit", 30.0),
        ("2024-06-30", "Revenue", 110.0),
        ("2024-06-30", "GrossProfit", 35.0),
    ])
    wide = _pivot_long_to_wide(long_df)
    assert "Revenue" in wide.columns
    assert "GrossProfit" in wide.columns
    assert wide.loc[pd.Timestamp("2024-03-31"), "Revenue"] == 100.0
    assert wide.loc[pd.Timestamp("2024-06-30"), "GrossProfit"] == 35.0


def test_pivot_long_to_wide_empty_returns_empty():
    """Empty input → empty DataFrame, NOT raise."""
    assert _pivot_long_to_wide(pd.DataFrame()).empty
    assert _pivot_long_to_wide(None).empty


def test_aggregate_basic_5_quarters_produces_1_ttm_row():
    """Need 5 quarters minimum for TTM (4Q rolling) + YoY (5Q ago) — 4Q rolling
    starts at Q4 2023; YoY needs Q4 2022 too. With exactly 5 quarters input
    (e.g. Q1 2023 through Q1 2024), only Q1 2024 has both TTM and YoY computable."""
    # Need 5 consecutive quarters
    rows_fs = []
    rows_bs = []
    for q_idx, q_end in enumerate([
        "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31",
    ]):
        rows_fs.append((q_end, "Revenue", 100.0 + q_idx * 10))
        rows_fs.append((q_end, "GrossProfit", 30.0 + q_idx * 3))
        rows_fs.append((q_end, "IncomeAfterTaxes", 10.0 + q_idx))
        rows_bs.append((q_end, "Equity", 200.0))
        rows_bs.append((q_end, "TotalAssets", 500.0 + q_idx * 50))
    fs_full = _make_fs_long(rows_fs)
    bs_history = _make_bs_long(rows_bs)
    out = aggregate_quality_v3_history("2330", fs_full, bs_history)
    # Only Q1 2024 has 4Q TTM + 4Q YoY (need 5 quarters total; first 4 used)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["symbol"] == "2330"
    assert pd.Timestamp(row["period_end"]) == pd.Timestamp("2024-03-31")
    assert row["quarter"] == 1
    # TTM revenue = 110+120+130+140 = 500; gross = 33+36+39+42 = 150
    # gross_margin_ttm = 150/500 = 0.30
    assert abs(row["gross_margin_ttm"] - 0.30) < 1e-6


def test_aggregate_yoy_assets_growth():
    """Δassets YoY: TotalAssets_Q1_2024 / TotalAssets_Q1_2023 - 1."""
    rows_fs = []
    rows_bs = []
    # Synthetic: assets grow 50/quarter from 500
    for q_idx, q_end in enumerate([
        "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31",
    ]):
        rows_fs.append((q_end, "Revenue", 100.0))
        rows_fs.append((q_end, "GrossProfit", 30.0))
        rows_fs.append((q_end, "IncomeAfterTaxes", 10.0))
        rows_bs.append((q_end, "Equity", 200.0))
        rows_bs.append((q_end, "TotalAssets", 500.0 + q_idx * 50))
    fs_full = _make_fs_long(rows_fs)
    bs_history = _make_bs_long(rows_bs)
    out = aggregate_quality_v3_history("2330", fs_full, bs_history)
    row = out.iloc[0]
    # TotalAssets Q1 2024 = 700; Q1 2023 = 500; YoY = 0.40
    assert abs(row["assets_yoy_pct"] - 0.40) < 1e-6


def test_aggregate_missing_required_columns_returns_empty():
    """Missing 'IncomeAfterTaxes' in fs_full → return empty DataFrame."""
    rows_fs = [
        ("2024-03-31", "Revenue", 100.0),
        ("2024-03-31", "GrossProfit", 30.0),
        # missing IncomeAfterTaxes
    ]
    rows_bs = [
        ("2024-03-31", "Equity", 200.0),
        ("2024-03-31", "TotalAssets", 500.0),
    ]
    out = aggregate_quality_v3_history(
        "2330", _make_fs_long(rows_fs), _make_bs_long(rows_bs),
    )
    assert out.empty


def test_aggregate_missing_balance_sheet_returns_empty():
    """Missing balance sheet → return empty."""
    rows_fs = []
    for q_end in ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31"]:
        rows_fs.append((q_end, "Revenue", 100.0))
        rows_fs.append((q_end, "GrossProfit", 30.0))
        rows_fs.append((q_end, "IncomeAfterTaxes", 10.0))
    out = aggregate_quality_v3_history("2330", _make_fs_long(rows_fs), None)
    assert out.empty


def test_aggregate_zero_revenue_handled():
    """Zero revenue → gross_margin_ttm should not divide by zero (NaN drop)."""
    rows_fs = []
    rows_bs = []
    for q_end in ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31"]:
        rows_fs.append((q_end, "Revenue", 0.0))  # zero revenue
        rows_fs.append((q_end, "GrossProfit", 0.0))
        rows_fs.append((q_end, "IncomeAfterTaxes", 0.0))
        rows_bs.append((q_end, "Equity", 200.0))
        rows_bs.append((q_end, "TotalAssets", 500.0))
    out = aggregate_quality_v3_history(
        "2330", _make_fs_long(rows_fs), _make_bs_long(rows_bs),
    )
    # All NaN due to zero revenue → drop_subset removes
    assert out.empty


def test_aggregate_only_4q_input_no_ttm():
    """Only 4 quarters → TTM computable for last quarter but no YoY (need 5Q) → empty."""
    rows_fs = []
    rows_bs = []
    for q_end in ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"]:
        rows_fs.append((q_end, "Revenue", 100.0))
        rows_fs.append((q_end, "GrossProfit", 30.0))
        rows_fs.append((q_end, "IncomeAfterTaxes", 10.0))
        rows_bs.append((q_end, "Equity", 200.0))
        rows_bs.append((q_end, "TotalAssets", 500.0))
    out = aggregate_quality_v3_history(
        "2330", _make_fs_long(rows_fs), _make_bs_long(rows_bs),
    )
    # 4 quarters: TTM at Q4 OK but YoY 4Q ago = Q1 2022 missing → drop
    assert out.empty


def test_aggregate_8_quarters_4_ttm_rows():
    """8 consecutive quarters → 4 valid (Q4 2023 onwards have YoY base 4Q ago)."""
    rows_fs = []
    rows_bs = []
    quarters = [
        "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
        "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    ]
    for q_idx, q_end in enumerate(quarters):
        rows_fs.append((q_end, "Revenue", 100.0))
        rows_fs.append((q_end, "GrossProfit", 30.0))
        rows_fs.append((q_end, "IncomeAfterTaxes", 10.0))
        rows_bs.append((q_end, "Equity", 200.0))
        rows_bs.append((q_end, "TotalAssets", 500.0 + q_idx * 25))
    out = aggregate_quality_v3_history(
        "2330", _make_fs_long(rows_fs), _make_bs_long(rows_bs),
    )
    # Q4 2022 has 4Q TTM (Q1-Q4 2022) but no YoY (Q4 2021 missing)
    # Q1 2023 has TTM (Q2 2022 - Q1 2023) + YoY (Q1 2022) ✓
    # → 4 valid rows: Q1, Q2, Q3, Q4 2023
    assert len(out) == 4
    # All quarters must be 1-4
    assert set(out["quarter"]) == {1, 2, 3, 4}
