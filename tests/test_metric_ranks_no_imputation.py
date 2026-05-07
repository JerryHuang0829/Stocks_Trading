"""Audit 2026-05-02 A.1 fix tests — verify silent 0.5 imputation removed.

Background:
    Pre-fix: `_metric_ranks` filled missing factor values with 0.5 (median
    percentile), so a stock missing factor X silently competed in `top_n`
    with a "neutral" score on X. This was a Pattern 6 silent fallback that
    masked data-quality issues, particularly visible in Phase A3.1
    sector_neutral runs where small-sector stocks routinely lacked factor
    data and still claimed median rank.

Post-fix: missing factor → `None` sentinel; `_rank_analyses` per-symbol
    re-normalizes weight_sum and forces score=0 when factor coverage falls
    below `min_factor_coverage_per_symbol` (default 0.6).

Each test corresponds to one Pattern 0 attacker from the audit pre-design:
    1. All-NaN row should NOT win top_n on synthetic 0.5 score.
    2. Partial-NaN row scored fairly via per-symbol re-normalization.
    3. Cross-sectional path returns None for missing symbols (mutation test).
    4. Sector-neutral path returns None for missing symbols (mutation test).
    5. Full-universe NaN factor still triggers the existing >50% guard +
       backtest-context raise (no regression).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.tw_stock import (
    _metric_ranks,
    _metric_ranks_sector_neutral,
    _rank_analyses,
)
from tests.conftest import make_analysis


class TestMetricRanksReturnsNoneForMissing:
    """Pattern 11 mutation test: revert 0.5 → None and confirm caller
    behavior diverges (i.e. the test would fail under the old code)."""

    def test_cross_sectional_missing_symbol_is_none(self):
        """Attacker #3: factor None on one stock → _metric_ranks output[sym] is None."""
        items = [
            make_analysis("2330", pm=80),
            make_analysis("2317", pm=70),
            make_analysis("2454", pm=None),  # missing factor
        ]
        ranks, has_real_data = _metric_ranks(items, "price_momentum_raw")
        assert has_real_data is True
        # 2 of 3 have data → not >50% NaN, factor still trusted globally
        assert ranks["2454"] is None, (
            "Missing factor must yield None sentinel (was 0.5 pre-fix)"
        )
        assert ranks["2330"] is not None
        assert ranks["2317"] is not None

    def test_sector_neutral_missing_symbol_is_none(self):
        """Attacker #5: sector_neutral path also returns None for missing stocks."""
        items = [
            make_analysis("2330", industry="半導體業", pm=80),
            make_analysis("2317", industry="電子工業", pm=70),
            make_analysis("2454", industry="半導體業", pm=75),
            make_analysis("2308", industry="電子工業", pm=65),
            make_analysis("9999", industry="塑膠工業", pm=None),  # missing + lone-sector
        ]
        ranks, _ = _metric_ranks_sector_neutral(items, "price_momentum_raw")
        assert ranks["9999"] is None, (
            "Sector-neutral missing factor must yield None (was 0.5 pre-fix)"
        )

    def test_all_universe_nan_returns_unreliable(self):
        """Attacker #3-extreme: 100% NaN → has_real_data=False, all None."""
        items = [
            make_analysis("2330", pm=None),
            make_analysis("2317", pm=None),
        ]
        ranks, has_real_data = _metric_ranks(items, "price_momentum_raw")
        assert has_real_data is False
        assert all(v is None for v in ranks.values())

    def test_more_than_50pct_nan_returns_unreliable(self):
        """Attacker #4-corner: 60% NaN crosses the 50% guard → unreliable."""
        items = [
            make_analysis("2330", pm=80),
            make_analysis("2317", pm=None),
            make_analysis("2454", pm=None),
            make_analysis("2308", pm=None),
            make_analysis("1301", pm=70),
        ]
        ranks, has_real_data = _metric_ranks(items, "price_momentum_raw")
        assert has_real_data is False
        # Even valid-value symbols return None when factor is unreliable.
        assert all(v is None for v in ranks.values())


class TestRankAnalysesPerSymbolNormalization:
    """A.1 fix integration tests: _rank_analyses uses per-symbol weight_sum."""

    def test_partial_factor_coverage_uses_per_symbol_weight(self, portfolio_config):
        """Attacker #2: stock missing 1/3 active factors should be scored only
        on the 2 factors it has, with weight_sum re-normalized per symbol."""
        # Use 3 active factors: pm=0.55, rev=0.25, tq=0.20 (default config)
        analyses = [
            make_analysis("2330", pm=80, tq=0.9, rev=0.15),
            make_analysis("2317", pm=70, tq=0.7, rev=0.12),
            # 2454 missing rev → should be scored on pm + tq only
            make_analysis("2454", pm=75, tq=0.8, rev=None),
            make_analysis("2308", pm=65, tq=0.6, rev=0.10),
            make_analysis("1301", pm=40, tq=0.4, rev=0.05),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        target = next(r for r in result if r["symbol"] == "2454")
        assert target["score_dropped_factors"] == ["revenue_momentum"]
        # Coverage = (0.55 + 0.20) / (0.55 + 0.25 + 0.20) = 0.75 ≥ 0.6 default
        assert target["score_below_coverage"] is False
        assert target["portfolio_score"] > 0
        # Score must be on 0-100 scale, not below_coverage 0
        assert 0 < target["portfolio_score"] <= 100

    def test_all_factors_missing_forces_zero_score(self, portfolio_config):
        """Attacker #1: stock with all factors NaN gets portfolio_score=0
        and score_below_coverage=True (cannot win top_n on synthetic 0.5)."""
        analyses = [
            make_analysis("2330", pm=80, tq=0.9, rev=0.15),
            make_analysis("2317", pm=70, tq=0.7, rev=0.12),
            # 9999 missing all 3 weighted factors
            make_analysis("9999", pm=None, tq=None, rev=None),
            make_analysis("2454", pm=75, tq=0.8, rev=0.20),
            make_analysis("2308", pm=65, tq=0.6, rev=0.10),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        target = next(r for r in result if r["symbol"] == "9999")
        assert target["portfolio_score"] == 0.0
        assert target["score_below_coverage"] is True
        assert set(target["score_dropped_factors"]) == {
            "price_momentum", "trend_quality", "revenue_momentum"
        }

    def test_below_min_factor_coverage_forces_zero(self, portfolio_config):
        """Stock with only 1/3 factors (coverage 0.55 < 0.6 default) → score 0."""
        # Default weights: pm=0.55, rev=0.25, tq=0.20 → sum=1.00
        # Only pm (0.55) → coverage = 0.55 < 0.6 default → forced to 0
        analyses = [
            make_analysis("2330", pm=80, tq=0.9, rev=0.15),
            make_analysis("2317", pm=70, tq=0.7, rev=0.12),
            # 5566 missing tq + rev → coverage = 0.55
            make_analysis("5566", pm=85, tq=None, rev=None),
            make_analysis("2454", pm=75, tq=0.8, rev=0.20),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        target = next(r for r in result if r["symbol"] == "5566")
        assert target["portfolio_score"] == 0.0
        assert target["score_below_coverage"] is True

    def test_above_min_factor_coverage_keeps_score(self, portfolio_config):
        """Stock with 2/3 factors (coverage 0.75 ≥ 0.6) → score preserved."""
        analyses = [
            make_analysis("2330", pm=80, tq=0.9, rev=0.15),
            make_analysis("2317", pm=70, tq=0.7, rev=0.12),
            # 5566 missing only revenue → coverage = 0.75
            make_analysis("5566", pm=85, tq=0.85, rev=None),
            make_analysis("2454", pm=75, tq=0.8, rev=0.20),
            make_analysis("2308", pm=65, tq=0.6, rev=0.10),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        target = next(r for r in result if r["symbol"] == "5566")
        assert target["portfolio_score"] > 0
        assert target["score_below_coverage"] is False

    def test_custom_min_factor_coverage_threshold(self, portfolio_config):
        """User can tighten / loosen the threshold via portfolio_config."""
        config = dict(portfolio_config)
        config["min_factor_coverage_per_symbol"] = 0.9  # tightened
        analyses = [
            make_analysis("2330", pm=80, tq=0.9, rev=0.15),
            make_analysis("2317", pm=70, tq=0.7, rev=0.12),
            # 5566 missing rev → coverage = 0.75 < 0.9 → forced to 0
            make_analysis("5566", pm=85, tq=0.85, rev=None),
            make_analysis("2454", pm=75, tq=0.8, rev=0.20),
        ]
        result = _rank_analyses(analyses, config)
        target = next(r for r in result if r["symbol"] == "5566")
        assert target["portfolio_score"] == 0.0
        assert target["score_below_coverage"] is True

    def test_zero_coverage_attacker_does_not_win_top_n(self, portfolio_config):
        """Attacker #1 hard variant: ensure missing-everywhere stock ranks AFTER
        any partial-coverage stock (was the actual silent-bug payload pre-fix)."""
        analyses = [
            make_analysis("9999", pm=None, tq=None, rev=None),  # all-missing
            make_analysis("2330", pm=20, tq=0.2, rev=0.01),  # weak but present
            make_analysis("2317", pm=15, tq=0.15, rev=0.02),
        ]
        result = _rank_analyses(analyses, portfolio_config)
        # all-missing stock must NOT outrank weak-but-present stocks
        assert result[-1]["symbol"] == "9999" or result[-2]["symbol"] == "9999"
        rank_9999 = next(r for r in result if r["symbol"] == "9999")["rank"]
        rank_2330 = next(r for r in result if r["symbol"] == "2330")["rank"]
        assert rank_9999 > rank_2330, (
            "All-missing-factor stock must rank AFTER any stock with real data"
        )


class TestSilentRenormalizeGuardStillRaises:
    """Attacker #6: ensure existing silent-renormalize guard still raises in
    backtest_context when an entire factor is unreliable."""

    def test_full_universe_nan_factor_raises_in_backtest(self, portfolio_config):
        """Pattern 17(c) integration: A.1 fix must NOT regress the existing
        Phase A2 Step 1.5.4 silent-renormalize raise behavior."""
        config = dict(portfolio_config)
        config["_backtest_context"] = True
        # All revenue None → factor unreliable, must raise (not silently rebalance)
        analyses = [
            make_analysis("2330", pm=80, tq=0.9, rev=None),
            make_analysis("2317", pm=70, tq=0.7, rev=None),
            make_analysis("2454", pm=75, tq=0.8, rev=None),
            make_analysis("2308", pm=65, tq=0.6, rev=None),
        ]
        with pytest.raises(RuntimeError, match="Silent renormalization"):
            _rank_analyses(analyses, config)
