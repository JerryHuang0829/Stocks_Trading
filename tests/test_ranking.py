"""Tests for _rank_analyses() in tw_stock.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.tw_stock import _rank_analyses
from tests.conftest import make_analysis


class TestRankAnalyses:
    """_rank_analyses: percentile ranking + weighted score."""

    def test_empty_analyses(self, portfolio_config):
        result = _rank_analyses([], portfolio_config)
        assert result == []

    def test_single_eligible(self, portfolio_config):
        analyses = [make_analysis("2330", pm=80, tq=0.9, rev=0.15)]
        result = _rank_analyses(analyses, portfolio_config)
        assert len(result) == 1
        assert result[0]["rank"] == 1
        assert result[0]["eligible"] is True
        assert result[0]["portfolio_score"] > 0

    def test_eligible_sorted_by_score(self, portfolio_config, ten_eligible_analyses):
        result = _rank_analyses(ten_eligible_analyses, portfolio_config)
        scores = [r["portfolio_score"] for r in result if r["eligible"]]
        assert scores == sorted(scores, reverse=True), "Eligible stocks should be sorted by score descending"

    def test_ineligible_get_zero_score(self, portfolio_config):
        analyses = [
            make_analysis("2330", eligible=True, pm=80, tq=0.9, rev=0.15),
            make_analysis("9999", eligible=False, pm=90, tq=0.95, rev=0.25),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        ineligible = [r for r in result if not r["eligible"]]
        assert len(ineligible) == 1
        assert ineligible[0]["portfolio_score"] == 0.0
        assert ineligible[0]["rank_components"] == {}

    def test_ineligible_ranked_after_eligible(self, portfolio_config):
        analyses = [
            make_analysis("2330", eligible=True, pm=50),
            make_analysis("9999", eligible=False, pm=90),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        assert result[0]["eligible"] is True
        assert result[1]["eligible"] is False
        assert result[0]["rank"] < result[1]["rank"]

    def test_rank_components_present(self, portfolio_config, ten_eligible_analyses):
        result = _rank_analyses(ten_eligible_analyses, portfolio_config)
        eligible = [r for r in result if r["eligible"]]
        for item in eligible:
            assert "rank_components" in item
            assert "price_momentum" in item["rank_components"]
            assert "trend_quality" in item["rank_components"]
            assert "revenue_momentum" in item["rank_components"]
            # institutional_flow weight=0, should NOT be in rank_components
            assert "institutional_flow" not in item["rank_components"]

    def test_zero_weight_factor_excluded(self, portfolio_config):
        """IF weight=0 → not in rank_components, doesn't affect score."""
        analyses = [
            make_analysis("A", pm=80, tq=0.9, rev=0.15, inst=100),
            make_analysis("B", pm=80, tq=0.9, rev=0.15, inst=-100),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        # Same PM/TQ/REV → same score regardless of institutional_raw
        assert abs(result[0]["portfolio_score"] - result[1]["portfolio_score"]) < 0.01

    def test_higher_pm_gets_higher_score(self, portfolio_config):
        """PM has highest weight (0.55), so higher PM → higher total score."""
        analyses = [
            make_analysis("HIGH", pm=90, tq=0.5, rev=0.10),
            make_analysis("LOW", pm=10, tq=0.5, rev=0.10),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        high = next(r for r in result if r["symbol"] == "HIGH")
        low = next(r for r in result if r["symbol"] == "LOW")
        assert high["portfolio_score"] > low["portfolio_score"]

    def test_all_none_scores(self, portfolio_config):
        """All raw scores are None → no active weights → score=0 (graceful degradation)."""
        analyses = [
            make_analysis("A", pm=None, tq=None, rev=None),
            make_analysis("B", pm=None, tq=None, rev=None),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        for r in result:
            if r["eligible"]:
                # All factors have no real data → has_real_data=False → not in active_weights
                # → score=0.0 (correct graceful degradation)
                assert r["portfolio_score"] == 0.0

    def test_ranks_are_sequential(self, portfolio_config, ten_eligible_analyses):
        result = _rank_analyses(ten_eligible_analyses, portfolio_config)
        ranks = [r["rank"] for r in result]
        assert ranks == list(range(1, len(result) + 1))
