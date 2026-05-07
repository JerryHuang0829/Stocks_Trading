"""Phase A3.1.2 tests: regime-aware weighting in _rank_analyses.

Covers:
- Backward compat: market_view=None preserves Phase A2 behavior
- regime_score_weights present + matching regime -> use regime-specific weights
- regime_score_weights present but unknown regime -> fallback to flat weights
- regime_score_weights empty -> fallback to flat weights
- _resolve_regime_score_weights helper direct unit tests
"""

from __future__ import annotations

import pytest

from src.portfolio.tw_stock import (
    _rank_analyses,
    _resolve_regime_score_weights,
)


def _make_item(sym, industry, pm, hp):
    return {
        "symbol": sym, "name": sym, "industry": industry,
        "eligible": True, "filters": [],
        "price_momentum_raw": pm,
        "high_proximity_raw": hp,
        # rest None for compact fixture
        "trend_quality_raw": None, "revenue_raw": None,
        "institutional_raw": None, "quality_raw": None,
        "pead_eps_raw": None, "margin_short_ratio_raw": None,
        "revenue_momentum_v2_raw": None, "foreign_broker_v2_raw": None,
    }


# ---------------------------------------------------------------------------
# Group 1: _resolve_regime_score_weights helper tests
# ---------------------------------------------------------------------------

def test_resolve_no_market_view_returns_flat_weights():
    """market_view=None -> return flat score_weights."""
    config = {"score_weights": {"price_momentum": 1.0}}
    out = _resolve_regime_score_weights(config, None)
    assert out == {"price_momentum": 1.0}


def test_resolve_no_regime_score_weights_returns_flat_weights():
    """regime_score_weights missing -> return flat score_weights."""
    config = {"score_weights": {"price_momentum": 1.0}}
    market_view = {"regime": "trending_up", "signal": "risk_on"}
    out = _resolve_regime_score_weights(config, market_view)
    assert out == {"price_momentum": 1.0}


def test_resolve_empty_regime_score_weights_returns_flat_weights():
    """Explicit empty dict fallback."""
    config = {
        "score_weights": {"price_momentum": 1.0},
        "regime_score_weights": {},
    }
    market_view = {"regime": "trending_up"}
    out = _resolve_regime_score_weights(config, market_view)
    assert out == {"price_momentum": 1.0}


def test_resolve_regime_match_picks_regime_weights():
    """market_view.regime in regime_score_weights keys -> use that bucket."""
    config = {
        "score_weights": {"price_momentum": 0.5, "high_proximity": 0.5},
        "regime_score_weights": {
            "trending_up": {"price_momentum": 0.2, "high_proximity": 0.8},
            "ranging": {"price_momentum": 0.5, "high_proximity": 0.5},
            "trending_down": {"price_momentum": 0.8, "high_proximity": 0.2},
        },
    }
    market_view = {"regime": "trending_up", "signal": "risk_on"}
    out = _resolve_regime_score_weights(config, market_view)
    assert out == {"price_momentum": 0.2, "high_proximity": 0.8}


def test_resolve_regime_unknown_falls_back_to_flat():
    """If regime not in regime_score_weights keys -> warn and fallback flat."""
    config = {
        "score_weights": {"price_momentum": 1.0},
        "regime_score_weights": {"trending_up": {"price_momentum": 0.0}},
    }
    # regime="ranging" is not in the dict
    market_view = {"regime": "ranging"}
    out = _resolve_regime_score_weights(config, market_view)
    assert out == {"price_momentum": 1.0}  # fell back to flat


# ---------------------------------------------------------------------------
# Group 2: _rank_analyses end-to-end with regime switching
# ---------------------------------------------------------------------------

def test_rank_analyses_default_market_view_none_matches_phase_a2():
    """_rank_analyses called without market_view must match Phase A2 behavior
    (regression guard — existing tests call with 2 args)."""
    items = [
        _make_item("A1", "半導體業", pm=1, hp=10),
        _make_item("A2", "半導體業", pm=2, hp=20),
        _make_item("A3", "半導體業", pm=3, hp=30),
    ]
    config = {"score_weights": {"price_momentum": 1.0}}
    # No market_view kwarg — old signature
    ranked = _rank_analyses(items, config)
    # pm=3 should be rank 1.0 (max) → score 100
    a3 = next(r for r in ranked if r["symbol"] == "A3")
    assert a3["rank_components"]["price_momentum"] == pytest.approx(100.0)


def test_rank_analyses_trending_up_uses_regime_weights():
    """With regime_score_weights + market_view=trending_up, only the
    trending_up factor weights are active."""
    items = [
        _make_item("A1", "半導體業", pm=1, hp=30),   # hp max → should dominate under trending_up
        _make_item("A2", "半導體業", pm=2, hp=20),
        _make_item("A3", "半導體業", pm=3, hp=10),   # pm max → dominates only if pm weighted
    ]
    config = {
        "score_weights": {"price_momentum": 0.5, "high_proximity": 0.5},
        "regime_score_weights": {
            "trending_up": {"price_momentum": 0.0, "high_proximity": 1.0},  # pure hp
            "ranging": {"price_momentum": 1.0, "high_proximity": 0.0},      # pure pm
        },
    }
    market_view = {"regime": "trending_up", "signal": "risk_on"}
    ranked = _rank_analyses(items, config, market_view=market_view)

    # Under trending_up → 100% high_proximity weight → A1 (hp=30 max) gets highest portfolio_score
    rank_by_score = sorted(ranked, key=lambda r: r["portfolio_score"], reverse=True)
    assert rank_by_score[0]["symbol"] == "A1"


def test_rank_analyses_ranging_uses_different_regime_weights():
    """Same items, same config, different regime -> different winner."""
    items = [
        _make_item("A1", "半導體業", pm=1, hp=30),
        _make_item("A2", "半導體業", pm=2, hp=20),
        _make_item("A3", "半導體業", pm=3, hp=10),   # pm max → dominates under ranging (pure pm)
    ]
    config = {
        "score_weights": {"price_momentum": 0.5, "high_proximity": 0.5},
        "regime_score_weights": {
            "trending_up": {"price_momentum": 0.0, "high_proximity": 1.0},
            "ranging": {"price_momentum": 1.0, "high_proximity": 0.0},
        },
    }
    market_view = {"regime": "ranging", "signal": "caution"}
    ranked = _rank_analyses(items, config, market_view=market_view)
    # Under ranging → 100% pm → A3 (pm=3 max) highest
    rank_by_score = sorted(ranked, key=lambda r: r["portfolio_score"], reverse=True)
    assert rank_by_score[0]["symbol"] == "A3"


def test_rank_analyses_unknown_regime_falls_back_to_flat():
    """When market_view.regime is not in regime_score_weights, flat score_weights
    used (logged warning, no raise)."""
    items = [
        _make_item("A1", "半導體業", pm=1, hp=30),
        _make_item("A2", "半導體業", pm=3, hp=10),
    ]
    config = {
        "score_weights": {"price_momentum": 1.0},  # flat: pm-only
        "regime_score_weights": {
            "trending_up": {"price_momentum": 0.0, "high_proximity": 1.0},  # not this regime
        },
    }
    # Regime not in map → should use flat (pm-only)
    market_view = {"regime": "ranging", "signal": "caution"}
    ranked = _rank_analyses(items, config, market_view=market_view)
    # Pure pm → A2 (pm=3) wins
    rank_by_score = sorted(ranked, key=lambda r: r["portfolio_score"], reverse=True)
    assert rank_by_score[0]["symbol"] == "A2"
