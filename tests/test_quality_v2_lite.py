"""Phase B0-Lite tests for quality_v2 (single-snapshot lookahead version).

5 tests covering:
    1. ROE + gross_margin clip + composite math sanity
    2. fq_dict missing/None → score None
    3. fq_dict partial (only ROE, no gross_margin) → score None
    4. universe z-score normalize + drop missing symbols
    5. Refusal gate: backtest_context=True + quality_v2 → RuntimeError raise
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.quality_v2 import (
    assert_not_in_pit_backtest,
    compute_quality_v2_universe,
    score_quality_v2,
)


def test_score_quality_v2_clip_and_composite():
    """ROE clipped to [-0.5, 0.5]; gross_margin clipped to [0, 1]; sum is raw composite."""
    fq = {"date": "2024-09-30", "roe": 0.80, "gross_margin": 1.50}  # both above clip
    result = score_quality_v2(fq)
    assert result["roe_clipped"] == 0.50  # ROE clipped to ceiling
    assert result["gross_margin_clipped"] == 1.0  # GM clipped to ceiling
    assert result["score"] == 1.50  # 0.5 + 1.0


def test_missing_fq_returns_none():
    """fq_dict None → score None + detail fq_missing."""
    assert score_quality_v2(None)["score"] is None
    assert score_quality_v2(None)["detail"] == "fq_missing"


def test_partial_fq_returns_none():
    """fq_dict missing roe or gross_margin key → score None + detail fq_partial."""
    only_roe = {"date": "2024-09-30", "roe": 0.20, "gross_margin": None}
    only_gm = {"date": "2024-09-30", "roe": None, "gross_margin": 0.45}
    assert score_quality_v2(only_roe)["score"] is None
    assert score_quality_v2(only_roe)["detail"] == "fq_partial"
    assert score_quality_v2(only_gm)["score"] is None


def test_universe_zscore_drops_missing():
    """compute_quality_v2_universe z-scores valid symbols + drops missing."""
    fq_by_sym = {
        "2330": {"date": "2024-09-30", "roe": 0.30, "gross_margin": 0.55},
        "2317": {"date": "2024-09-30", "roe": 0.10, "gross_margin": 0.10},
        "2454": {"date": "2024-09-30", "roe": 0.20, "gross_margin": 0.40},
        "2308": {"date": "2024-09-30", "roe": 0.15, "gross_margin": 0.30},
        "9999": None,  # missing — must be dropped
        "8888": {"date": "2024-09-30", "roe": None, "gross_margin": 0.50},  # partial
    }
    series = compute_quality_v2_universe(fq_by_sym)
    assert "9999" not in series.index, "missing fq must be dropped"
    assert "8888" not in series.index, "partial fq must be dropped"
    assert len(series) == 4  # 4 valid symbols
    # 2330 has highest both ROE & GM → should have highest composite z-score
    assert series.idxmax() == "2330"
    assert series.idxmin() == "2317"  # lowest both
    # Z-score sum: mean ≈ 0 (composite = z(roe)+z(gm), each centered at 0)
    assert abs(float(series.mean())) < 1e-10


def test_refusal_gate_in_backtest_context():
    """quality_v2 with backtest_context=True must raise RuntimeError."""
    # No config or no _backtest_context → no raise
    assert_not_in_pit_backtest(None)
    assert_not_in_pit_backtest({})
    assert_not_in_pit_backtest({"_backtest_context": False})

    # _backtest_context=True → raise (mirrors tw_stock.py:691 R19 audit gate)
    with pytest.raises(RuntimeError, match="lookahead bias"):
        assert_not_in_pit_backtest({"_backtest_context": True})
