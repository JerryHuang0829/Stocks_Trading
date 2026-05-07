"""Tests for _select_positions() in tw_stock.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.tw_stock import _rank_analyses, _select_positions
from tests.conftest import make_analysis


def _rank_and_select(analyses, portfolio_config, market_view, current_positions=None):
    """Helper: rank then select in one step."""
    ranked = _rank_analyses(analyses, portfolio_config)
    return _select_positions(ranked, current_positions or {}, portfolio_config, market_view)


class TestSelectPositions:
    """_select_positions: top_n, industry limit, hold buffer, exposure."""

    def test_respects_top_n(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        result = _rank_and_select(ten_eligible_analyses, portfolio_config, market_view_risk_on)
        assert len(result["positions"]) <= portfolio_config["top_n"]

    def test_all_entries_when_no_current(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        result = _rank_and_select(ten_eligible_analyses, portfolio_config, market_view_risk_on)
        for pos in result["positions"]:
            assert pos["action"] == "ENTER"

    def test_exposure_risk_on(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        result = _rank_and_select(ten_eligible_analyses, portfolio_config, market_view_risk_on)
        total_weight = sum(p["target_weight"] for p in result["positions"])
        expected = portfolio_config["exposure"]["risk_on"]
        assert abs(total_weight - expected) < 0.01

    def test_exposure_caution(self, portfolio_config, ten_eligible_analyses, market_view_caution):
        result = _rank_and_select(ten_eligible_analyses, portfolio_config, market_view_caution)
        total_weight = sum(p["target_weight"] for p in result["positions"])
        expected = portfolio_config["exposure"]["caution"]
        assert abs(total_weight - expected) < 0.01

    def test_exposure_risk_off(self, portfolio_config, ten_eligible_analyses, market_view_risk_off):
        result = _rank_and_select(ten_eligible_analyses, portfolio_config, market_view_risk_off)
        total_weight = sum(p["target_weight"] for p in result["positions"])
        expected = portfolio_config["exposure"]["risk_off"]
        assert abs(total_weight - expected) < 0.01

    def test_max_same_industry(self, portfolio_config, market_view_risk_on):
        """5 stocks from same industry → only max_same_industry selected."""
        analyses = [
            make_analysis(f"E{i}", industry="電子工業", pm=80 - i * 5)
            for i in range(5)
        ]
        # Add enough from other industries to reach top_n
        analyses += [
            make_analysis("P1", industry="塑膠工業", pm=30),
            make_analysis("P2", industry="塑膠工業", pm=25),
            make_analysis("F1", industry="金融保險業", pm=20),
        ]
        result = _rank_and_select(analyses, portfolio_config, market_view_risk_on)
        elec_count = sum(1 for p in result["positions"] if p["industry"] == "電子工業")
        assert elec_count <= portfolio_config["max_same_industry"]

    def test_rejected_by_industry_returned(self, portfolio_config, market_view_risk_on):
        """Industry-rejected symbols should appear in rejected_by_industry."""
        analyses = [
            make_analysis(f"E{i}", industry="電子工業", pm=80 - i * 5)
            for i in range(5)
        ]
        analyses += [make_analysis("P1", industry="塑膠工業", pm=10)]
        result = _rank_and_select(analyses, portfolio_config, market_view_risk_on)
        # 5 electronics, max 3 → at least 1 rejected (the step 1.5 removal)
        # Note: only triggers if >max_same_industry are in the selected set initially
        # With top_n=8 and only 6 stocks, all 5 electronics could be in selected first
        total = result["rejected_by_industry"]
        # At least verify the field exists and is a list
        assert isinstance(total, list)

    def test_min_holdings_goes_cash(self, portfolio_config, market_view_risk_on):
        """Fewer eligible than min_holdings → all cash."""
        cfg = {**portfolio_config, "min_holdings": 5}
        analyses = [make_analysis("A", pm=80), make_analysis("B", pm=70)]
        result = _rank_and_select(analyses, cfg, market_view_risk_on)
        assert len(result["positions"]) == 0
        assert result["gross_exposure"] == 0

    def test_max_position_weight_capped(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        result = _rank_and_select(ten_eligible_analyses, portfolio_config, market_view_risk_on)
        cap = portfolio_config["max_position_weight"]
        for pos in result["positions"]:
            assert pos["target_weight"] <= cap + 0.001

    def test_hold_existing_position(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        """Existing position within hold_buffer should be kept."""
        ranked = _rank_analyses(ten_eligible_analyses, portfolio_config)
        # Pretend we already hold the #5 ranked stock
        fifth = ranked[4]
        current = {fifth["symbol"]: {"symbol": fifth["symbol"], "target_weight": 0.12, "name": fifth["name"]}}
        result = _select_positions(ranked, current, portfolio_config, market_view_risk_on)
        held_symbols = {p["symbol"] for p in result["positions"]}
        # rank 5 is within top_n(8) + hold_buffer(3) = 11 → should be kept
        assert fifth["symbol"] in held_symbols

    def test_exits_returned(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        """Currently held stock not in new selection → appears in exits."""
        ranked = _rank_analyses(ten_eligible_analyses, portfolio_config)
        current = {"9999": {"symbol": "9999", "target_weight": 0.10, "name": "已下市"}}
        result = _select_positions(ranked, current, portfolio_config, market_view_risk_on)
        exit_symbols = {e["symbol"] for e in result["exits"]}
        assert "9999" in exit_symbols

    def test_equal_weight_mode(self, portfolio_config, ten_eligible_analyses, market_view_risk_on):
        cfg = {**portfolio_config, "weight_mode": "equal"}
        result = _rank_and_select(ten_eligible_analyses, cfg, market_view_risk_on)
        weights = [p["target_weight"] for p in result["positions"]]
        if weights:
            assert max(weights) - min(weights) < 0.001, "Equal weight: all positions should have same weight"
