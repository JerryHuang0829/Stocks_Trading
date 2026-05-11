"""Phase A2 Step 1.5 tests: shared _batch_precompute_and_analyze helper +
_rank_analyses silent renormalize guard + BacktestEngine _backtest_context marker.

Addresses external audit Round 14-plan-review P0-1 (duplicate analyze loop) and P0-2
(silent renormalize false-positive). These tests exist purely to lock in the
refactored behavior — no new business logic.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from src.portfolio.tw_stock import (
    _batch_precompute_and_analyze,
    _rank_analyses,
)


# ---------------------------------------------------------------------------
# Fixtures local to this file
# ---------------------------------------------------------------------------

def _fake_universe(n=3):
    return [
        {"symbol": f"{2330+i}", "name": f"Stock{i}", "industry": "半導體業"}
        for i in range(n)
    ]


def _fake_source():
    return MagicMock(name="FakeSource")


_DEFAULT_STRATEGY: dict = {}
_MIN_PORTFOLIO_CONFIG = {"score_weights": {}}
_AS_OF = datetime(2026, 4, 21)


# ---------------------------------------------------------------------------
# Group 1: helper structural tests
# ---------------------------------------------------------------------------

def test_helper_returns_list_same_length_as_universe():
    """Helper returns one analysis dict per universe symbol."""
    universe = _fake_universe(5)
    with patch("src.portfolio.tw_stock._analyze_symbol") as mock_analyze:
        mock_analyze.return_value = {"symbol": "stub", "eligible": True, "filters": []}
        out = _batch_precompute_and_analyze(
            universe, _fake_source(), _DEFAULT_STRATEGY, _MIN_PORTFOLIO_CONFIG,
            _AS_OF, "caution",
        )
    assert len(out) == 5
    assert mock_analyze.call_count == 5


def test_helper_error_stub_has_exactly_five_keys():
    """When _analyze_symbol raises, stub dict must have the canonical 5 keys
    (symbol/name/eligible/filters/industry) — same keys the pre-1.5 inline loops
    produced in both callers. Lock-in via exact set comparison."""
    universe = [{"symbol": "2330", "name": "台積電", "industry": "半導體業"}]
    with patch("src.portfolio.tw_stock._analyze_symbol", side_effect=ValueError("boom")):
        out = _batch_precompute_and_analyze(
            universe, _fake_source(), _DEFAULT_STRATEGY, _MIN_PORTFOLIO_CONFIG,
            _AS_OF, "caution",
        )
    assert len(out) == 1
    stub = out[0]
    assert set(stub.keys()) == {"symbol", "name", "eligible", "filters", "industry"}
    assert stub["eligible"] is False
    assert stub["filters"] == ["analysis_error:boom"]
    assert stub["symbol"] == "2330"
    assert stub["industry"] == "半導體業"


def test_helper_preserves_as_of_and_market_signal():
    """_analyze_symbol must receive the exact as_of + market_signal passed in,
    not defaults, not datetime.now(). Regression against accidental shadowing."""
    universe = _fake_universe(1)
    specific_as_of = datetime(2024, 7, 15, 14, 30)
    with patch("src.portfolio.tw_stock._analyze_symbol") as mock_analyze:
        mock_analyze.return_value = {"symbol": "stub", "eligible": True, "filters": []}
        _batch_precompute_and_analyze(
            universe, _fake_source(), _DEFAULT_STRATEGY, _MIN_PORTFOLIO_CONFIG,
            specific_as_of, "risk_off",
        )
    # positional args: sym_config, source, default_strategy, portfolio_config, as_of
    # kwarg: market_signal
    call_args = mock_analyze.call_args
    assert call_args.args[4] == specific_as_of
    assert call_args.kwargs["market_signal"] == "risk_off"


# ---------------------------------------------------------------------------
# Group 2: silent renormalize guard — _backtest_context=True → raise
# ---------------------------------------------------------------------------

def _make_analyses_missing_quality(n=10):
    """Build eligible analyses with all factor_raw set EXCEPT quality_raw.
    quality is in available_metrics but make_analysis() does not populate
    quality_raw, so every row gets None → _metric_ranks reports has_real_data=False.
    """
    from tests.conftest import make_analysis
    return [
        make_analysis(f"000{i}", industry="半導體業", pm=50+i, tq=0.5, rev=0.1, inst=0.0)
        for i in range(n)
    ]


def test_guard_backtest_raise_when_weight_gt_zero_but_no_data():
    """Backtest context: factor with weight>0 but no real data → RuntimeError
    naming the offending factor."""
    analyses = _make_analyses_missing_quality()
    config = {
        "_backtest_context": True,
        "score_weights": {"quality": 1.0},
    }
    with pytest.raises(RuntimeError, match="quality"):
        _rank_analyses(analyses, config)


def test_guard_live_warn_when_weight_gt_zero_but_no_data(caplog):
    """Live context (no _backtest_context): only logs warning, does not raise.
    Existing 14 direct _rank_analyses tests rely on this lenient behavior."""
    analyses = _make_analyses_missing_quality()
    config = {
        # No _backtest_context key == live path
        "score_weights": {"quality": 1.0},
    }
    with caplog.at_level(logging.WARNING, logger="src.portfolio.tw_stock"):
        ranked = _rank_analyses(analyses, config)
    # Must not raise; must return the ranked list intact
    assert isinstance(ranked, list)
    assert len(ranked) == len(analyses)
    # The specific warning must be present
    assert any(
        "no real data" in rec.getMessage() and "quality" in rec.getMessage()
        for rec in caplog.records
    )


def test_guard_no_trigger_when_all_factor_data_present():
    """Factor weight>0 AND all items have that factor_raw → no guard trigger,
    no raise, no warn (in either context)."""
    analyses = _make_analyses_missing_quality()  # price_momentum_raw IS set
    config = {
        "_backtest_context": True,
        "score_weights": {"price_momentum": 1.0},
    }
    # Must not raise
    ranked = _rank_analyses(analyses, config)
    assert len(ranked) == len(analyses)


def test_guard_no_trigger_when_weight_zero_even_with_missing_data():
    """weight=0 factor is skipped before _metric_ranks runs → never counted
    as silent_dropped, even if the factor_raw is missing from every item."""
    analyses = _make_analyses_missing_quality()
    config = {
        "_backtest_context": True,
        "score_weights": {
            "price_momentum": 1.0,
            "quality": 0.0,  # weight=0, despite missing data
        },
    }
    # Must not raise (quality has weight=0, skipped entirely)
    ranked = _rank_analyses(analyses, config)
    assert len(ranked) == len(analyses)


# ---------------------------------------------------------------------------
# Group 3: BacktestEngine _backtest_context marker
# ---------------------------------------------------------------------------

def test_backtest_context_marker_is_set_in_init():
    """BacktestEngine.__init__ sets _backtest_context=True on self._portfolio_config.
    This is what enables the raising branch of the guard during backtest runs."""
    from src.backtest.engine import BacktestEngine
    source = MagicMock()
    config = {
        "portfolio": {
            "profile": None,
            "score_weights": {"price_momentum": 1.0},
        },
    }
    eng = BacktestEngine(source, config)
    assert eng._portfolio_config.get("_backtest_context") is True


def test_backtest_context_marker_does_not_mutate_input_config():
    """Init must not mutate the input config dict (incl. nested portfolio section)
    — otherwise live code path sharing the same config would accidentally flip
    into backtest-strict mode. Verified via deep equality on a deep-copied snapshot.
    """
    from src.backtest.engine import BacktestEngine
    source = MagicMock()
    config = {
        "portfolio": {
            "profile": None,
            "score_weights": {"price_momentum": 1.0},
        },
    }
    snapshot = copy.deepcopy(config)
    _ = BacktestEngine(source, config)
    assert config == snapshot, "BacktestEngine.__init__ mutated input config dict"


# ---------------------------------------------------------------------------
# Group 4: engine module imports the shared helper (structural regression)
# ---------------------------------------------------------------------------

def test_engine_module_imports_shared_helper():
    """Ensure src.backtest.engine really picks up the shared helper (import
    identity — not just some local re-definition). If engine ever reintroduces
    its own inline loop, this test keeps the fix visible."""
    import src.backtest.engine as engine_mod
    import src.portfolio.tw_stock as tw_stock_mod

    assert hasattr(engine_mod, "_batch_precompute_and_analyze")
    assert engine_mod._batch_precompute_and_analyze is tw_stock_mod._batch_precompute_and_analyze
