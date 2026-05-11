"""Phase A3.1.1 tests: sector-neutral factor ranking.

Covers:
- Backward compat: sector_neutral=False preserves Phase A2 cross-sectional rank
- Within-industry rank when >= 3 members
- Small industries pool into _OTHER
- Unknown / empty industry maps to _UNKNOWN, pooled with other small groups
- has_real_data threshold still fires when >50% NaN
- _rank_analyses wires sector_neutral_metrics config correctly
"""

from __future__ import annotations

import pytest

from src.portfolio.tw_stock import (
    _metric_ranks,
    _metric_ranks_sector_neutral,
    _group_items_by_industry,
    _rank_analyses,
)


def _make_item(sym, industry, value):
    """Minimal analysis dict for rank testing."""
    return {
        "symbol": sym,
        "industry": industry,
        "eligible": True,
        "filters": [],
        "foo_raw": value,
    }


# ---------------------------------------------------------------------------
# Group 1: _group_items_by_industry unit tests
# ---------------------------------------------------------------------------

def test_group_pools_small_industries_into_OTHER():
    """Industries with fewer than min_size (3) members pool into _OTHER."""
    items = [
        _make_item("A1", "半導體業", 1.0),
        _make_item("A2", "半導體業", 1.0),
        _make_item("A3", "半導體業", 1.0),
        _make_item("B1", "金融業", 1.0),    # only 1 → OTHER
        _make_item("C1", "塑膠業", 1.0),    # only 2 → OTHER
        _make_item("C2", "塑膠業", 1.0),
    ]
    groups = _group_items_by_industry(items)
    assert "半導體業" in groups
    assert len(groups["半導體業"]) == 3
    assert "_OTHER" in groups
    assert len(groups["_OTHER"]) == 3  # B1 + C1 + C2
    # 金融業 / 塑膠業 should NOT exist as top-level keys
    assert "金融業" not in groups
    assert "塑膠業" not in groups


def test_group_maps_missing_industry_to_UNKNOWN():
    """Items with None / empty industry map to _UNKNOWN before pooling check."""
    items = [
        _make_item("X1", None, 1.0),
        _make_item("X2", "", 1.0),
        _make_item("X3", "   ", 1.0),
        _make_item("Y1", "半導體業", 1.0),
        _make_item("Y2", "半導體業", 1.0),
        _make_item("Y3", "半導體業", 1.0),
    ]
    groups = _group_items_by_industry(items)
    # _UNKNOWN has 3 members → stays as own group, NOT pooled into _OTHER
    assert "_UNKNOWN" in groups
    assert len(groups["_UNKNOWN"]) == 3
    # 半導體業 also has 3 → its own group
    assert len(groups["半導體業"]) == 3


# ---------------------------------------------------------------------------
# Group 2: _metric_ranks_sector_neutral direct tests
# ---------------------------------------------------------------------------

def test_sector_neutral_ranks_within_industry():
    """Within a single industry of 4 items, ranks should be [0.25, 0.50, 0.75, 1.00]."""
    items = [
        _make_item("S1", "半導體業", 1.0),
        _make_item("S2", "半導體業", 2.0),
        _make_item("S3", "半導體業", 3.0),
        _make_item("S4", "半導體業", 4.0),
    ]
    ranks, has_real = _metric_ranks_sector_neutral(items, "foo_raw")
    assert has_real
    assert ranks["S1"] == pytest.approx(0.25)
    assert ranks["S2"] == pytest.approx(0.50)
    assert ranks["S3"] == pytest.approx(0.75)
    assert ranks["S4"] == pytest.approx(1.00)


def test_sector_neutral_two_industries_independent_ranks():
    """Two industries rank independently — largest value in each is 1.0."""
    items = [
        _make_item("A1", "半導體業", 10.0),
        _make_item("A2", "半導體業", 20.0),
        _make_item("A3", "半導體業", 30.0),   # largest in 半導體業 → 1.0
        _make_item("B1", "金融業", 100.0),
        _make_item("B2", "金融業", 200.0),
        _make_item("B3", "金融業", 300.0),     # largest in 金融業 → 1.0
    ]
    ranks, has_real = _metric_ranks_sector_neutral(items, "foo_raw")
    assert has_real
    # Each industry's max value gets rank 1.0 despite different absolute values
    assert ranks["A3"] == pytest.approx(1.0)
    assert ranks["B3"] == pytest.approx(1.0)
    # Min value in each industry gets 1/3 ≈ 0.333
    assert ranks["A1"] == pytest.approx(1 / 3)
    assert ranks["B1"] == pytest.approx(1 / 3)


def test_sector_neutral_small_industries_pooled():
    """Industries with < 3 members pool into _OTHER and rank together."""
    items = [
        _make_item("M1", "半導體業", 1.0),
        _make_item("M2", "半導體業", 2.0),
        _make_item("M3", "半導體業", 3.0),
        _make_item("S1", "金融業", 10.0),      # pooled (only 1)
        _make_item("S2", "塑膠業", 20.0),      # pooled (only 1)
    ]
    ranks, has_real = _metric_ranks_sector_neutral(items, "foo_raw")
    assert has_real
    # 半導體業 ranks: M1=1/3, M2=2/3, M3=1.0 (its own group)
    assert ranks["M3"] == pytest.approx(1.0)
    # _OTHER pool: S1=10 / S2=20 → ranks 0.5 / 1.0
    assert ranks["S2"] == pytest.approx(1.0)
    assert ranks["S1"] == pytest.approx(0.5)


def test_sector_neutral_high_nan_ratio_flagged_unreliable():
    """If > 50% items have NaN foo_raw, has_real_data=False."""
    items = [
        _make_item("A1", "半導體業", 1.0),
        _make_item("A2", "半導體業", None),
        _make_item("A3", "半導體業", None),
        _make_item("A4", "半導體業", None),
        _make_item("B1", "金融業", 10.0),
        _make_item("B2", "金融業", None),
    ]
    ranks, has_real = _metric_ranks_sector_neutral(items, "foo_raw")
    assert not has_real  # only 2/6 = 33% have valid data


# ---------------------------------------------------------------------------
# Group 2b: Phase A3.1.4 second-pass pool (small-valid groups deferred)
# ---------------------------------------------------------------------------

def test_sector_neutral_large_group_low_valid_pooled_into_second_pass():
    """Phase A3.1.4: a group with len(group) >= min_size but < 2 valid factor
    values must NOT silently skip — items pool into a second-pass bucket.

    Phase A3.1.4: group skipped → items pooled into second-pass bucket so the
        intra-group invalid count does not trigger a false >50% NaN flag.

    Audit 2026-05-02 A.1 fix: invalid items now get `None` (not 0.5 median
        imputation), and the per-symbol `_rank_analyses` re-normalization
        excludes them from that symbol's weight_sum."""
    items = [
        # 金融業: 3 valid (ranks independently)
        _make_item("C1", "金融業", 10.0),
        _make_item("C2", "金融業", 20.0),
        _make_item("C3", "金融業", 30.0),
        # 半導體業: 3 items (>= min_size) but only 1 valid -> pool
        _make_item("A1", "半導體業", 100.0),
        _make_item("A2", "半導體業", None),
        _make_item("A3", "半導體業", None),
        # 塑膠業: 3 items (>= min_size) but only 1 valid -> pool
        _make_item("D1", "塑膠業", 50.0),
        _make_item("D2", "塑膠業", None),
        _make_item("D3", "塑膠業", None),
    ]
    ranks, has_real = _metric_ranks_sector_neutral(items, "foo_raw")
    # 9 items total, valid = 3 (金融) + 2 (A1 + D1 pool) = 5 → 5/9 = 55% > 50%
    assert has_real
    # 金融業 internal ranks: C3 max → 1.0
    assert ranks["C3"] == pytest.approx(1.0)
    # Pool (A1=100, D1=50): A1 max → 1.0, D1 min → 0.5
    assert ranks["A1"] == pytest.approx(1.0)
    assert ranks["D1"] == pytest.approx(0.5)
    # Invalid items now sentinel None (was 0.5 pre-A.1-fix)
    assert ranks["A2"] is None
    assert ranks["D3"] is None


def test_sector_neutral_second_pass_pool_itself_too_small_returns_none():
    """Phase A3.1.4: if pool itself has < 2 valid values, single-observation
    items cannot rank meaningfully.

    Audit 2026-05-02 A.1 fix: such items return `None` (not 0.5 median);
    `_rank_analyses` per-symbol normalization drops them from that symbol's
    factor weight."""
    items = [
        # 金融業 ranks fine (all valid)
        _make_item("C1", "金融業", 10.0),
        _make_item("C2", "金融業", 20.0),
        _make_item("C3", "金融業", 30.0),
        # 半導體業: 1 valid only; pool also contains single-valid entry
        _make_item("A1", "半導體業", 100.0),
        _make_item("A2", "半導體業", None),
        _make_item("A3", "半導體業", None),
    ]
    ranks, has_real = _metric_ranks_sector_neutral(items, "foo_raw")
    # Pool has only A1 valid → can't rank with 1 observation
    # has_real_data: 3/6 = 50% NOT > 50% → False
    assert not has_real  # 3/6 = 50%, threshold is strict >
    # A1 cannot rank from a single-element pool → None (was 0.5 pre-A.1-fix)
    assert ranks["A1"] is None
    # 金融業 items still ranked
    assert ranks["C3"] == pytest.approx(1.0)


def test_sector_neutral_regression_d1_v3_scenario_global_coverage_above_50pct():
    """Regression guard for D1_v3 failure:
    Global coverage is ~58% (above 50% threshold), but sector split has many
    thin sectors with < 2 valid → old behavior false-flagged has_real=False
    and raised the >50% NaN guard in backtest context.

    After A3.1.4 fix, pool aggregates the thin-sector items so has_real=True."""
    items = [
        # 半導體業: 3 valid
        _make_item("S1", "半導體業", 1.0),
        _make_item("S2", "半導體業", 2.0),
        _make_item("S3", "半導體業", 3.0),
        # 金融業: 4 valid
        _make_item("F1", "金融業", 10.0),
        _make_item("F2", "金融業", 20.0),
        _make_item("F3", "金融業", 30.0),
        _make_item("F4", "金融業", 40.0),
        # 4 thin sectors each >= min_size=3 but only 1 valid each
        _make_item("P1", "塑膠業", 100.0),
        _make_item("P2", "塑膠業", None),
        _make_item("P3", "塑膠業", None),
        _make_item("R1", "電子零組件業", 200.0),
        _make_item("R2", "電子零組件業", None),
        _make_item("R3", "電子零組件業", None),
        _make_item("T1", "紡織業", 300.0),
        _make_item("T2", "紡織業", None),
        _make_item("T3", "紡織業", None),
        _make_item("U1", "運輸業", 400.0),
        _make_item("U2", "運輸業", None),
        _make_item("U3", "運輸業", None),
    ]
    ranks, has_real = _metric_ranks_sector_neutral(items, "foo_raw")
    # total items = 19, valid globally = 3+4+4 = 11 → 11/19 = 58%
    # Old: 半導 3 + 金融 4 + 4 sectors skip → total_valid = 7, 7/19 = 37% (FALSE positive)
    # New: 半導 3 + 金融 4 + pool(P1,R1,T1,U1) 4 → total_valid = 11, 11/19 = 58% OK
    assert has_real
    # Pool of 4 items (P1=100, R1=200, T1=300, U1=400) → ranks 0.25/0.5/0.75/1.0
    assert ranks["P1"] == pytest.approx(0.25)
    assert ranks["U1"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Group 3: backward compat — default path preserves Phase A2 behavior
# ---------------------------------------------------------------------------

def test_metric_ranks_default_cross_sectional_unchanged():
    """Default call (sector_neutral=False) must match Phase A2 exact behavior:
    industry is ignored, rank is global."""
    items = [
        _make_item("A1", "半導體業", 1.0),
        _make_item("A2", "半導體業", 2.0),
        _make_item("B1", "金融業", 3.0),
        _make_item("B2", "金融業", 4.0),
    ]
    ranks, has_real = _metric_ranks(items, "foo_raw")  # no flag
    assert has_real
    # Cross-sectional: 1→0.25, 2→0.50, 3→0.75, 4→1.00 (ignore industry)
    assert ranks["A1"] == pytest.approx(0.25)
    assert ranks["A2"] == pytest.approx(0.50)
    assert ranks["B1"] == pytest.approx(0.75)
    assert ranks["B2"] == pytest.approx(1.00)


def test_metric_ranks_sector_neutral_via_kwarg():
    """Same data but sector_neutral=True → per-industry ranks."""
    items = [
        _make_item("A1", "半導體業", 1.0),
        _make_item("A2", "半導體業", 2.0),
        _make_item("B1", "金融業", 3.0),
        _make_item("B2", "金融業", 4.0),
    ]
    ranks, _ = _metric_ranks(items, "foo_raw", sector_neutral=True)
    # Both industries have only 2 members → all pool into _OTHER
    # _OTHER ranks: 1→0.25, 2→0.50, 3→0.75, 4→1.00 (same as cross-sectional)
    # because pooling 2+2 = 4 items all in one bucket
    assert ranks["A1"] == pytest.approx(0.25)
    assert ranks["B2"] == pytest.approx(1.00)


# ---------------------------------------------------------------------------
# Group 4: _rank_analyses wires sector_neutral_metrics config
# ---------------------------------------------------------------------------

def _make_ranked_item(sym, industry, pm_raw, pead_raw):
    return {
        "symbol": sym, "name": sym, "industry": industry,
        "eligible": True, "filters": [],
        "price_momentum_raw": pm_raw,
        "pead_eps_raw": pead_raw,
        "trend_quality_raw": None, "revenue_raw": None,
        "institutional_raw": None, "quality_raw": None,
        "high_proximity_raw": None, "margin_short_ratio_raw": None,
        "revenue_momentum_v2_raw": None, "foreign_investor_v2_raw": None,
    }


def test_rank_analyses_sector_neutral_metrics_config_read():
    """_rank_analyses reads `sector_neutral_metrics` list and applies it
    per factor. Factors in the list use within-industry rank; others cross-sectional."""
    # 6 items across 2 industries, pead_raw ordered 1..6 globally
    items = [
        _make_ranked_item("A1", "半導體業", pm_raw=5, pead_raw=1.0),
        _make_ranked_item("A2", "半導體業", pm_raw=4, pead_raw=2.0),
        _make_ranked_item("A3", "半導體業", pm_raw=3, pead_raw=3.0),
        _make_ranked_item("B1", "金融業", pm_raw=2, pead_raw=4.0),
        _make_ranked_item("B2", "金融業", pm_raw=1, pead_raw=5.0),
        _make_ranked_item("B3", "金融業", pm_raw=0, pead_raw=6.0),
    ]
    config = {
        "score_weights": {"price_momentum": 0.5, "pead_eps": 0.5},
        "sector_neutral_metrics": ["pead_eps"],  # only pead is sector-neutral
    }
    ranked = _rank_analyses(items, config)
    # price_momentum: cross-sectional, A1 (pm=5) gets rank 1.0
    # pead_eps: sector-neutral, within 半導體業: A3 pead=3 is max → 1.0 / A1 pead=1 is min → 1/3
    # So A1's rank_components['pead_eps'] = 1/3 * 100 ≈ 33.33
    a1 = next(r for r in ranked if r["symbol"] == "A1")
    assert a1["rank_components"]["price_momentum"] == pytest.approx(100.0)  # global max for pm
    assert a1["rank_components"]["pead_eps"] == pytest.approx(33.33, abs=0.1)  # within-industry


def test_rank_analyses_empty_sector_neutral_metrics_identical_to_phase_a2():
    """Default config (no sector_neutral_metrics key) must produce identical
    rank_components as Phase A2 behavior — regression guard."""
    items = [
        _make_ranked_item("X1", "半導體業", pm_raw=1, pead_raw=10),
        _make_ranked_item("X2", "金融業", pm_raw=2, pead_raw=20),
        _make_ranked_item("X3", "半導體業", pm_raw=3, pead_raw=30),
    ]
    config = {"score_weights": {"price_momentum": 1.0}}  # no sector_neutral_metrics key
    ranked = _rank_analyses(items, config)
    # Cross-sectional: pm=1→1/3, pm=2→2/3, pm=3→1.0
    x3 = next(r for r in ranked if r["symbol"] == "X3")
    assert x3["rank_components"]["price_momentum"] == pytest.approx(100.0)
    x1 = next(r for r in ranked if r["symbol"] == "X1")
    assert x1["rank_components"]["price_momentum"] == pytest.approx(33.33, abs=0.1)
