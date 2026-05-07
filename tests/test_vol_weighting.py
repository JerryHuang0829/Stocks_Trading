"""Tests for vol_weighted (risk parity lite) position sizing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.tw_stock import _rank_analyses, _select_positions, _calculate_position_weights
from tests.conftest import make_analysis


def _rank_and_select(analyses, portfolio_config, market_view, current_positions=None):
    ranked = _rank_analyses(analyses, portfolio_config)
    return _select_positions(ranked, current_positions or {}, portfolio_config, market_view)


class TestVolWeighted:
    """vol_weighted: weights ∝ 1/volatility (risk parity lite)."""

    def test_low_vol_gets_higher_weight(self, portfolio_config, market_view_risk_on):
        """Lower volatility stock should get higher weight than high volatility stock."""
        cfg = {**portfolio_config, "weight_mode": "vol_weighted", "min_holdings": 2,
               "top_n": 2, "max_same_industry": 99, "max_position_weight": 0.80}
        analyses = [
            make_analysis("LOW", "低波動", "A業", pm=60, vol=0.01),   # low vol → high weight
            make_analysis("HIGH", "高波動", "B業", pm=60, vol=0.04),  # high vol → low weight
        ]
        result = _rank_and_select(analyses, cfg, market_view_risk_on)
        weights = {p["symbol"]: p["target_weight"] for p in result["positions"]}
        assert weights["LOW"] > weights["HIGH"], "Low vol stock should get higher weight"

    def test_total_exposure_correct(self, portfolio_config, ten_eligible_analyses, market_view_caution):
        """Total weight should match target exposure regardless of vol distribution."""
        cfg = {**portfolio_config, "weight_mode": "vol_weighted"}
        result = _rank_and_select(ten_eligible_analyses, cfg, market_view_caution)
        total = sum(p["target_weight"] for p in result["positions"])
        expected = cfg["exposure"]["caution"]
        assert abs(total - expected) < 0.01

    def test_max_position_weight_respected(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        cfg = {**portfolio_config, "weight_mode": "vol_weighted"}
        result = _rank_and_select(ten_eligible_analyses, cfg, market_view_risk_on)
        cap = cfg["max_position_weight"]
        for p in result["positions"]:
            assert p["target_weight"] <= cap + 0.001

    def test_missing_vol_uses_median(self, portfolio_config, market_view_risk_on):
        """Stock with vol=None should get median-like weight, not 0 or extreme."""
        cfg = {**portfolio_config, "weight_mode": "vol_weighted", "min_holdings": 3, "top_n": 3, "max_same_industry": 99}
        analyses = [
            make_analysis("A", "A股", "X業", pm=70, vol=0.01),
            make_analysis("B", "B股", "Y業", pm=65, vol=None),   # missing vol
            make_analysis("C", "C股", "Z業", pm=60, vol=0.04),
        ]
        result = _rank_and_select(analyses, cfg, market_view_risk_on)
        weights = {p["symbol"]: p["target_weight"] for p in result["positions"]}
        # B should NOT have 0 weight (median fallback should work)
        assert weights.get("B", 0) > 0, "Missing vol stock should still get weight via median fallback"

    def test_all_same_vol_equals_equal_weight(self, portfolio_config, market_view_risk_on):
        """If all stocks have same volatility, vol_weighted ≈ equal weight."""
        cfg = {**portfolio_config, "weight_mode": "vol_weighted", "min_holdings": 3, "top_n": 3, "max_same_industry": 99}
        analyses = [
            make_analysis("A", "A股", "X業", pm=70, vol=0.02),
            make_analysis("B", "B股", "Y業", pm=65, vol=0.02),
            make_analysis("C", "C股", "Z業", pm=60, vol=0.02),
        ]
        result = _rank_and_select(analyses, cfg, market_view_risk_on)
        weights = [p["target_weight"] for p in result["positions"]]
        assert max(weights) - min(weights) < 0.001, "Same vol → same weight"


class TestCalculatePositionWeightsDirect:
    """Direct tests for _calculate_position_weights function."""

    def test_score_weighted_sums_to_exposure(self):
        selected = [
            {"symbol": "A", "portfolio_score": 80},
            {"symbol": "B", "portfolio_score": 60},
        ]
        result = _calculate_position_weights(selected, 0.96, 0.50, "score_weighted")
        assert abs(sum(result.values()) - 0.96) < 0.01

    def test_vol_weighted_sums_to_exposure(self):
        selected = [
            {"symbol": "A", "portfolio_score": 80, "volatility_20d": 0.01},
            {"symbol": "B", "portfolio_score": 60, "volatility_20d": 0.03},
        ]
        result = _calculate_position_weights(selected, 0.70, 0.50, "vol_weighted")
        assert abs(sum(result.values()) - 0.70) < 0.01

    def test_equal_weighted_uniform(self):
        selected = [
            {"symbol": "A", "portfolio_score": 80},
            {"symbol": "B", "portfolio_score": 20},
        ]
        result = _calculate_position_weights(selected, 0.96, 0.50, "equal")
        assert abs(result["A"] - result["B"]) < 0.001

    def test_empty_selected(self):
        result = _calculate_position_weights([], 0.96, 0.12, "vol_weighted")
        assert result == {}
