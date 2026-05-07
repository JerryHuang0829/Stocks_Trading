"""Tests for graceful degradation when factor data is missing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.tw_stock import _rank_analyses
from tests.conftest import make_analysis


class TestGracefulDegradation:
    """When factor data is None/missing, ranking should still work."""

    def test_missing_revenue_still_ranks(self, portfolio_config):
        """revenue_raw=None → stock still gets a score (from PM + TQ)."""
        analyses = [
            make_analysis("A", pm=80, tq=0.9, rev=None),
            make_analysis("B", pm=70, tq=0.7, rev=0.10),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        assert all(r["portfolio_score"] > 0 for r in result if r["eligible"])

    def test_all_revenue_none_uses_default(self, portfolio_config):
        """If ALL stocks have revenue=None, that metric gets default 0.5."""
        analyses = [
            make_analysis("A", pm=80, tq=0.9, rev=None),
            make_analysis("B", pm=70, tq=0.7, rev=None),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        # Both should still have valid scores
        assert len([r for r in result if r["portfolio_score"] > 0]) == 2

    def test_missing_institutional_no_impact(self, portfolio_config):
        """IF weight=0 → institutional_raw doesn't matter even if None."""
        analyses = [
            make_analysis("A", pm=80, tq=0.9, rev=0.15, inst=None),
            make_analysis("B", pm=80, tq=0.9, rev=0.15, inst=50),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        scores = {r["symbol"]: r["portfolio_score"] for r in result}
        assert abs(scores["A"] - scores["B"]) < 0.01

    def test_weight_renormalization(self):
        """If a non-zero-weight factor has no real data, remaining weights renormalize."""
        # Custom config where revenue_momentum has weight but all values are None
        config = {
            "score_weights": {
                "price_momentum": 0.50,
                "trend_quality": 0.25,
                "revenue_momentum": 0.25,
                "institutional_flow": 0.00,
            }
        }
        analyses = [
            make_analysis("A", pm=80, tq=0.9, rev=None),
            make_analysis("B", pm=40, tq=0.4, rev=None),
        ]
        result = _rank_analyses(analyses, config)
        # A should still beat B (higher PM and TQ)
        a = next(r for r in result if r["symbol"] == "A")
        b = next(r for r in result if r["symbol"] == "B")
        assert a["portfolio_score"] > b["portfolio_score"]
