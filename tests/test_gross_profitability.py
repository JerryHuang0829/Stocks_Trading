"""Tests for src/features/gross_profitability.py — GP/Assets factor.

Covers:
- TTM GP sum (last 4 quarters)
- Q1-3 (+45d) vs Q4 (+90d) quarter-aware lag
- Latest assets from balance sheet (PIT-truncated)
- Filters: < 4 quarters / non-positive GP / missing assets / NaN
- Batch universe wrapper
- Empty inputs → empty Series
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.features.gross_profitability import (  # noqa: E402
    _earliest_asof_for_row,
    _latest_assets,
    _normalise_quarterly_frame,
    _ttm_gp,
    compute_gross_profitability,
    compute_gross_profitability_universe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_qf_frame(
    quarter_ends: list[str],
    values: list[float],
    type_value: str = "GrossProfit",
) -> pd.DataFrame:
    """Build a long-form FinMind financial DataFrame."""
    return pd.DataFrame({
        "date": pd.to_datetime(quarter_ends),
        "type": [type_value] * len(quarter_ends),
        "value": values,
    })


# ---------------------------------------------------------------------------
# _earliest_asof_for_row (quarter-aware lag)
# ---------------------------------------------------------------------------
def test_q1_lag_45_days():
    asof = _earliest_asof_for_row(pd.Timestamp("2024-03-31"))
    assert asof == pd.Timestamp("2024-03-31") + pd.Timedelta(days=45)


def test_q2_lag_45_days():
    asof = _earliest_asof_for_row(pd.Timestamp("2024-06-30"))
    assert asof == pd.Timestamp("2024-06-30") + pd.Timedelta(days=45)


def test_q3_lag_45_days():
    asof = _earliest_asof_for_row(pd.Timestamp("2024-09-30"))
    assert asof == pd.Timestamp("2024-09-30") + pd.Timedelta(days=45)


def test_q4_lag_90_days():
    asof = _earliest_asof_for_row(pd.Timestamp("2024-12-31"))
    assert asof == pd.Timestamp("2024-12-31") + pd.Timedelta(days=90)


# ---------------------------------------------------------------------------
# _normalise_quarterly_frame
# ---------------------------------------------------------------------------
def test_filter_by_type_value():
    """Only rows matching type_value should remain."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-03-31"] * 3),
        "type": ["GrossProfit", "Revenue", "EPS"],
        "value": [1.0, 2.0, 3.0],
    })
    out = _normalise_quarterly_frame(df, "GrossProfit", pd.Timestamp("2025-01-01"))
    assert len(out) == 1
    assert out["value"].iloc[0] == 1.0


def test_pit_truncate_excludes_undisclosed_q1():
    """Q1 2024-03-31 row not disclosed until 2024-05-15. as_of=2024-05-01 → drop."""
    df = _make_qf_frame(["2024-03-31"], [100.0])
    out = _normalise_quarterly_frame(df, "GrossProfit", pd.Timestamp("2024-05-01"))
    assert out is None


def test_pit_truncate_includes_disclosed_q1():
    """Q1 2024-03-31 disclosed by 2024-05-15. as_of=2024-05-20 → include."""
    df = _make_qf_frame(["2024-03-31"], [100.0])
    out = _normalise_quarterly_frame(df, "GrossProfit", pd.Timestamp("2024-05-20"))
    assert out is not None
    assert len(out) == 1


def test_pit_truncate_q4_needs_90_days():
    """Q4 2024-12-31 not disclosed until 2025-03-31. as_of=2025-02-01 → drop."""
    df = _make_qf_frame(["2024-12-31"], [100.0])
    out = _normalise_quarterly_frame(df, "GrossProfit", pd.Timestamp("2025-02-01"))
    assert out is None


def test_normalise_returns_none_for_empty():
    assert _normalise_quarterly_frame(None, "GrossProfit", pd.Timestamp("2024-01-01")) is None
    assert _normalise_quarterly_frame(pd.DataFrame(), "GrossProfit", pd.Timestamp("2024-01-01")) is None


# ---------------------------------------------------------------------------
# _ttm_gp
# ---------------------------------------------------------------------------
def test_ttm_gp_sums_last_four():
    frame = pd.DataFrame({"value": [10.0, 20.0, 30.0, 40.0, 50.0]})
    assert _ttm_gp(frame, min_quarters=4) == 140.0


def test_ttm_gp_none_for_too_few():
    frame = pd.DataFrame({"value": [10.0, 20.0, 30.0]})
    assert _ttm_gp(frame, min_quarters=4) is None


def test_ttm_gp_none_for_nan():
    frame = pd.DataFrame({"value": [10.0, np.nan, 30.0, 40.0]})
    assert _ttm_gp(frame, min_quarters=4) is None


# ---------------------------------------------------------------------------
# _latest_assets
# ---------------------------------------------------------------------------
def test_latest_assets_returns_last_row_value():
    frame = pd.DataFrame({"value": [1000.0, 1100.0, 1200.0]})
    assert _latest_assets(frame) == 1200.0


def test_latest_assets_none_for_zero():
    frame = pd.DataFrame({"value": [0.0]})
    assert _latest_assets(frame) is None


def test_latest_assets_none_for_negative():
    frame = pd.DataFrame({"value": [-100.0]})
    assert _latest_assets(frame) is None


def test_latest_assets_none_for_empty():
    assert _latest_assets(None) is None
    assert _latest_assets(pd.DataFrame({"value": []})) is None


# ---------------------------------------------------------------------------
# compute_gross_profitability — happy path
# ---------------------------------------------------------------------------
def test_happy_path_score_is_ttm_over_assets():
    qf = _make_qf_frame(
        ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
        [25.0, 25.0, 25.0, 25.0],   # TTM = 100
    )
    bs = _make_qf_frame(
        ["2023-12-31"], [1000.0], type_value="TotalAssets",
    )
    # as_of = 2024-04-01 — Q4 2023-12-31 disclosed by 2024-03-31 ✅
    result = compute_gross_profitability(qf, bs, pd.Timestamp("2024-04-01"))
    assert result["score"] == pytest.approx(0.1)   # 100 / 1000
    assert result["ttm_gp"] == 100.0
    assert result["assets"] == 1000.0
    assert result["reason"] == "ok"


def test_filter_non_positive_ttm():
    qf = _make_qf_frame(
        ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
        [-25.0, -25.0, -25.0, -25.0],  # all losses
    )
    bs = _make_qf_frame(["2023-12-31"], [1000.0], type_value="TotalAssets")
    result = compute_gross_profitability(qf, bs, pd.Timestamp("2024-04-01"))
    assert result["score"] is None
    assert result["reason"] == "non_positive_gp"


def test_filter_insufficient_history():
    qf = _make_qf_frame(
        ["2023-09-30", "2023-12-31"], [25.0, 25.0],  # only 2 quarters
    )
    bs = _make_qf_frame(["2023-12-31"], [1000.0], type_value="TotalAssets")
    result = compute_gross_profitability(qf, bs, pd.Timestamp("2024-04-01"))
    assert result["score"] is None
    assert result["reason"] == "insufficient_gp_history"


def test_filter_missing_assets():
    qf = _make_qf_frame(
        ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
        [25.0, 25.0, 25.0, 25.0],
    )
    bs = None
    result = compute_gross_profitability(qf, bs, pd.Timestamp("2024-04-01"))
    assert result["score"] is None
    assert result["reason"] == "assets_unavailable"


def test_pit_excludes_future_quarter():
    """If Q4 2023-12-31 not yet disclosed, only 3 quarters available → insufficient."""
    qf = _make_qf_frame(
        ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
        [25.0, 25.0, 25.0, 25.0],
    )
    bs = _make_qf_frame(["2023-12-31"], [1000.0], type_value="TotalAssets")
    # as_of = 2024-02-01 — Q4 not yet disclosed (needs +90d → 2024-03-31)
    result = compute_gross_profitability(qf, bs, pd.Timestamp("2024-02-01"))
    assert result["score"] is None
    assert result["reason"] == "insufficient_gp_history"


# ---------------------------------------------------------------------------
# compute_gross_profitability_universe
# ---------------------------------------------------------------------------
def test_universe_batch():
    qf = _make_qf_frame(
        ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
        [25.0, 25.0, 25.0, 25.0],
    )
    bs = _make_qf_frame(["2023-12-31"], [1000.0], type_value="TotalAssets")
    qf_by_sym = {"A": qf, "B": qf, "MISSING": qf}
    bs_by_sym = {"A": bs, "B": bs}  # MISSING has no balance sheet
    result = compute_gross_profitability_universe(
        qf_by_sym, bs_by_sym, as_of=pd.Timestamp("2024-04-01"),
    )
    assert "A" in result.index
    assert "B" in result.index
    assert "MISSING" not in result.index
    assert result["A"] == pytest.approx(0.1)


def test_universe_empty_bs_returns_empty():
    qf_by_sym = {"A": _make_qf_frame(["2023-03-31"], [25.0])}
    result = compute_gross_profitability_universe(
        qf_by_sym, None, as_of=pd.Timestamp("2024-04-01"),
    )
    assert result.empty


def test_universe_as_of_required():
    with pytest.raises(ValueError, match="as_of is required"):
        compute_gross_profitability_universe({}, {}, as_of=None)
