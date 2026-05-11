"""Phase A2 Step 2 tests: 5 new factor batch integration.

Covers:
- Group A: regression (new factors weight=0 unchanged behavior / guard interaction)
- Group B: per-factor functional (weight=1 dispatches correct batch entry)
- Group C: integration + Codex Round 14 P0 fixes
  (fetch_market_value no-symbol-arg bulk / issued_capital dtype + coverage /
  _safe_fetch BacktestCacheMissError propagation / available_metrics structural)
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.portfolio.tw_stock import (
    DEFAULT_PORTFOLIO_CONFIG,
    _batch_precompute_and_analyze,
    _bulk_fetch_latest_market_value,
    _compute_universe_batch_factors,
    _load_issued_capital_dict,
    _rank_analyses,
    _safe_fetch,
    get_portfolio_config,
)
from src.data.finmind import _BacktestCacheMissError


_UNIVERSE = [
    {"symbol": f"{2330+i}", "name": f"Stock{i}", "industry": "半導體業"}
    for i in range(3)
]
_AS_OF = pd.Timestamp("2026-04-21")


# ---------------------------------------------------------------------------
# Group A: Regression (4 tests)
# ---------------------------------------------------------------------------

def test_new_factors_default_zero_no_batch_fetch():
    """All 5 new factor weights default 0 → _compute_universe_batch_factors
    returns empty dict AND no fetcher is called. Cost optimization gate."""
    source = MagicMock()
    portfolio_config = {"score_weights": {
        "high_proximity": 0.0,
        "pead_eps": 0.0,
        "margin_short_ratio": 0.0,
        "revenue_momentum_v2": 0.0,
        "foreign_investor_v2": 0.0,
    }}
    out = _compute_universe_batch_factors(
        [s["symbol"] for s in _UNIVERSE], source, portfolio_config, _AS_OF,
    )
    assert out == {}
    # No fetcher attribute should have been accessed as a callable
    assert not source.fetch_ohlcv.called
    assert not source.fetch_quarterly_eps.called
    assert not source.fetch_margin_short.called
    assert not source.fetch_three_institutional.called
    assert not source.fetch_month_revenue.called
    assert not source.fetch_market_value.called


def test_new_factors_default_zero_identical_to_step15_behavior():
    """With all new factor weights=0, _batch_precompute_and_analyze output
    matches Step 1.5 behavior: dicts have no new *_raw keys set."""
    portfolio_config = {"score_weights": {}}  # empty → everything defaults to 0
    source = MagicMock()
    with patch("src.portfolio.tw_stock._analyze_symbol") as mock_analyze:
        # side_effect returns fresh dict per call — return_value would share
        # a single dict across iterations and inject-override corrupts results.
        mock_analyze.side_effect = lambda *a, **kw: {
            "symbol": "stub", "eligible": True, "filters": [],
        }
        out = _batch_precompute_and_analyze(
            _UNIVERSE, source, {}, portfolio_config, _AS_OF.to_pydatetime(), "caution",
        )
    assert len(out) == 3
    # No batch factor scores injected (because nothing computed)
    for a in out:
        assert "high_proximity_raw" not in a
        assert "pead_eps_raw" not in a
        assert "margin_short_ratio_raw" not in a
        assert "revenue_momentum_v2_raw" not in a
        assert "foreign_investor_v2_raw" not in a


def test_mixed_old_new_weights_active_set():
    """Mix old (price_momentum) + new (high_proximity) factors both >0.
    Both must appear in _rank_analyses output rank_components."""
    analyses = [
        {"symbol": f"000{i}", "name": f"S{i}", "industry": "半導體業",
         "eligible": True, "filters": [],
         "price_momentum_raw": 0.5 + 0.05 * i,
         "trend_quality_raw": None, "revenue_raw": None,
         "institutional_raw": None, "quality_raw": None,
         "high_proximity_raw": -0.02 + 0.005 * i}
        for i in range(5)
    ]
    config = {"score_weights": {"price_momentum": 0.6, "high_proximity": 0.4}}
    ranked = _rank_analyses(analyses, config)
    # Both factors appear in rank_components for eligible items
    for item in ranked:
        if item["eligible"]:
            assert "price_momentum" in item["rank_components"]
            assert "high_proximity" in item["rank_components"]


def test_backtest_context_guard_triggers_on_new_factor_missing_data():
    """Step 1.5.4 guard must fire when user enables new factor but data missing
    AND _backtest_context=True. Proves new factors participate in the guard."""
    analyses = [
        {"symbol": f"000{i}", "name": f"S{i}", "industry": "半導體業",
         "eligible": True, "filters": [],
         "price_momentum_raw": 0.5, "trend_quality_raw": 0.6,
         "revenue_raw": 0.1, "institutional_raw": None, "quality_raw": None,
         # high_proximity_raw intentionally missing → guard should catch
         }
        for i in range(10)
    ]
    config = {
        "_backtest_context": True,
        "score_weights": {"high_proximity": 1.0},
    }
    with pytest.raises(RuntimeError, match="high_proximity"):
        _rank_analyses(analyses, config)


# ---------------------------------------------------------------------------
# Group B: Per-factor functional (10 tests)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("factor_name,batch_func,source_attr,extra_kwargs", [
    ("high_proximity", "compute_high_proximity_universe", "fetch_ohlcv", {}),
    ("pead_eps", "compute_pead_eps_universe", "fetch_quarterly_eps", {}),
    ("revenue_momentum_v2", "compute_revenue_momentum_v2_universe", "fetch_month_revenue", {}),
])
def test_batch_entry_called_when_weight_positive(factor_name, batch_func, source_attr, extra_kwargs):
    """Each new factor with weight>0 triggers its batch entry call exactly once."""
    source = MagicMock()
    config = {"score_weights": {factor_name: 1.0}}
    with patch(f"src.portfolio.tw_stock.{batch_func}") as mock_batch:
        mock_batch.return_value = pd.Series({"2330": 0.5})
        out = _compute_universe_batch_factors(
            ["2330"], source, config, _AS_OF,
        )
    assert mock_batch.called
    assert factor_name in out
    # Fetcher for this factor was called per symbol
    assert getattr(source, source_attr).call_count >= 1


def test_margin_short_ratio_batch_with_issued_dict():
    """margin_short_ratio batch must pass issued_by_symbol dict from cache."""
    source = MagicMock()
    config = {"score_weights": {"margin_short_ratio": 1.0}}
    with patch("src.portfolio.tw_stock.compute_margin_short_ratio_universe") as mock_batch, \
         patch("src.portfolio.tw_stock._load_issued_capital_dict") as mock_issued:
        mock_issued.return_value = {"2330": 7e9}
        mock_batch.return_value = pd.Series({"2330": -0.3})
        out = _compute_universe_batch_factors(["2330"], source, config, _AS_OF)
    assert mock_batch.called
    call_kwargs = mock_batch.call_args.kwargs
    assert "issued_by_symbol" in call_kwargs
    assert call_kwargs["issued_by_symbol"] == {"2330": 7e9}
    assert out["margin_short_ratio"] == {"2330": -0.3}


def test_foreign_investor_v2_batch_with_market_value_dict():
    """foreign_investor_v2 batch must pass market_value_by_symbol dict from
    bulk fetch (NOT per-symbol fetch — Codex Round 14 P0-1 regression guard)."""
    source = MagicMock()
    config = {"score_weights": {"foreign_investor_v2": 1.0}}
    with patch("src.portfolio.tw_stock.compute_foreign_investor_v2_universe") as mock_batch, \
         patch("src.portfolio.tw_stock._bulk_fetch_latest_market_value") as mock_bulk:
        mock_bulk.return_value = {"2330": 1.5e13}
        mock_batch.return_value = pd.Series({"2330": 0.2})
        out = _compute_universe_batch_factors(["2330"], source, config, _AS_OF)
    assert mock_bulk.called
    # Confirm market_value_by_symbol passed as kwarg (not positional)
    call_kwargs = mock_batch.call_args.kwargs
    assert "market_value_by_symbol" in call_kwargs
    assert call_kwargs["market_value_by_symbol"] == {"2330": 1.5e13}


@pytest.mark.parametrize("factor_name", [
    "high_proximity", "pead_eps", "margin_short_ratio",
    "revenue_momentum_v2", "foreign_investor_v2",
])
def test_batch_missing_symbol_injects_none_raw(factor_name):
    """When batch Series drops a symbol (insufficient data), per-symbol
    analysis dict gets `<factor>_raw=None` — _rank_analyses treats as NaN."""
    portfolio_config = {"score_weights": {factor_name: 1.0}}
    source = MagicMock()
    with patch("src.portfolio.tw_stock._compute_universe_batch_factors") as mock_compute, \
         patch("src.portfolio.tw_stock._analyze_symbol") as mock_analyze:
        # Batch returns score only for 2330 — 2331/2332 dropped
        mock_compute.return_value = {factor_name: {"2330": 0.5}}
        # side_effect returns fresh dict per call; return_value would alias
        # across iterations and inject-override would corrupt the results.
        mock_analyze.side_effect = lambda *a, **kw: {
            "symbol": "stub", "eligible": True, "filters": [],
        }
        out = _batch_precompute_and_analyze(
            _UNIVERSE, source, {}, portfolio_config, _AS_OF.to_pydatetime(), "caution",
        )
    key = f"{factor_name}_raw"
    # First symbol in _UNIVERSE (2330) has score; 2331/2332 None
    assert out[0][key] == 0.5
    assert out[1][key] is None
    assert out[2][key] is None


# ---------------------------------------------------------------------------
# Group C: Integration + Codex Round 14 P0 fixes (6 tests)
# ---------------------------------------------------------------------------

def test_bulk_fetch_market_value_calls_with_no_symbol_arg():
    """Codex Round 14 P0-1 FIX regression: fetch_market_value must be called
    without a symbol argument (it takes days: int). Never per-symbol."""
    source = MagicMock()
    source.fetch_market_value.return_value = pd.DataFrame({
        "stock_id": ["2330"], "date": [pd.Timestamp("2026-04-21")],
        "market_value": [1.5e13],
    })
    _ = _bulk_fetch_latest_market_value(source)
    source.fetch_market_value.assert_called_once_with()  # no positional args


def test_bulk_fetch_market_value_groupby_latest():
    """fetch_market_value returns full-market panel; we must pick latest
    row per stock_id via groupby.tail(1)."""
    source = MagicMock()
    source.fetch_market_value.return_value = pd.DataFrame({
        "stock_id": ["2330", "2330", "2317"],
        "date": pd.to_datetime(["2026-04-19", "2026-04-21", "2026-04-21"]),
        "market_value": [1.4e13, 1.5e13, 2e12],
    })
    out = _bulk_fetch_latest_market_value(source)
    assert out["2330"] == 1.5e13  # later date wins
    assert out["2317"] == 2e12


def test_load_issued_capital_dtype_cast_to_str_float(tmp_path, monkeypatch):
    """Codex Round 14 P0-2 FIX: issued_capital pickle may have int64
    issued_shares; loader must cast stock_id→str, issued_shares→float."""
    # Build a small pickle with mixed dtypes mimicking the real cache
    df = pd.DataFrame({
        "stock_id": pd.Series(["1101", "1102"], dtype="str"),
        "issued_shares": pd.Series([7523181742, 3546562881], dtype="int64"),
    })
    cache_root = tmp_path / "cache"
    (cache_root / "issued_capital").mkdir(parents=True)
    df.to_pickle(cache_root / "issued_capital" / "_global.pkl")

    monkeypatch.setattr(
        "src.portfolio.tw_stock.resolve_cache_dir",
        lambda: cache_root,
    )
    out = _load_issued_capital_dict(["1101", "1102"])
    assert set(out.keys()) == {"1101", "1102"}
    assert all(isinstance(k, str) for k in out.keys())
    assert all(isinstance(v, float) for v in out.values())
    assert out["1101"] == 7523181742.0


def test_load_issued_capital_coverage_warning(tmp_path, monkeypatch, caplog):
    """Codex Round 14 P0-2 FIX: when universe has symbols not in cache
    (e.g. ETFs), log warning with missing list sample."""
    df = pd.DataFrame({
        "stock_id": ["1101"],
        "issued_shares": [7523181742],
    })
    cache_root = tmp_path / "cache"
    (cache_root / "issued_capital").mkdir(parents=True)
    df.to_pickle(cache_root / "issued_capital" / "_global.pkl")

    monkeypatch.setattr(
        "src.portfolio.tw_stock.resolve_cache_dir",
        lambda: cache_root,
    )
    with caplog.at_level(logging.WARNING, logger="src.portfolio.tw_stock"):
        _load_issued_capital_dict(["1101", "0050", "0056"])  # 0050/0056 missing
    assert any(
        "missing issued_shares" in rec.getMessage() and "0050" in rec.getMessage()
        for rec in caplog.records
    )


def test_safe_fetch_propagates_backtest_cache_miss():
    """Codex Round 14 P1-1 FIX: _safe_fetch MUST re-raise
    _BacktestCacheMissError (callers MUST NOT catch). Catching would
    silently fall through to live API in backtest mode."""
    def raising_fetch(symbol):
        raise _BacktestCacheMissError(f"cache miss for {symbol}")
    with pytest.raises(_BacktestCacheMissError):
        _safe_fetch(raising_fetch, "2330")


def test_safe_fetch_swallows_other_exceptions_returns_none():
    """Non-BacktestCacheMissError exceptions get logged and return None —
    lets batch factor drop the problematic symbol gracefully."""
    def raising_fetch(symbol):
        raise ValueError(f"transient API error for {symbol}")
    result = _safe_fetch(raising_fetch, "2330")
    assert result is None


def test_available_metrics_has_5_new_entries():
    """Structural regression: _rank_analyses must know about all 5 new factors.
    Tested via rank_components output when each weight > 0."""
    analyses = [
        {"symbol": f"000{i}", "name": f"S{i}", "industry": "半導體業",
         "eligible": True, "filters": [],
         "price_momentum_raw": 0.5 + 0.1 * i,
         "trend_quality_raw": None, "revenue_raw": None,
         "institutional_raw": None, "quality_raw": None,
         "high_proximity_raw": -0.02 + 0.002 * i,
         "pead_eps_raw": 0.3 * i,
         "margin_short_ratio_raw": -0.1 + 0.02 * i,
         "revenue_momentum_v2_raw": 0.1 * i,
         "foreign_investor_v2_raw": 0.05 * i}
        for i in range(10)
    ]
    config = {"score_weights": {
        "high_proximity": 0.2, "pead_eps": 0.2,
        "margin_short_ratio": 0.2, "revenue_momentum_v2": 0.2,
        "foreign_investor_v2": 0.2,
    }}
    ranked = _rank_analyses(analyses, config)
    for item in ranked:
        if item["eligible"]:
            assert set(item["rank_components"].keys()) == {
                "high_proximity", "pead_eps", "margin_short_ratio",
                "revenue_momentum_v2", "foreign_investor_v2",
            }


def test_default_portfolio_config_has_5_new_weight_zero():
    """Structural: DEFAULT_PORTFOLIO_CONFIG ships with all 5 new factors
    at weight=0 — user must explicitly opt in via settings.yaml."""
    sw = DEFAULT_PORTFOLIO_CONFIG["score_weights"]
    for factor in ["high_proximity", "pead_eps", "margin_short_ratio",
                   "revenue_momentum_v2", "foreign_investor_v2"]:
        assert factor in sw, f"{factor} missing from DEFAULT_PORTFOLIO_CONFIG"
        assert sw[factor] == 0.0, f"{factor} default weight != 0"


def test_tw_3m_stable_profile_has_5_new_weight_zero():
    """Structural: PORTFOLIO_PROFILES['tw_3m_stable'] also ships with all
    5 new factors at weight=0 (profile override should not accidentally
    re-enable them)."""
    merged = get_portfolio_config({"portfolio": {"profile": "tw_3m_stable"}})
    sw = merged["score_weights"]
    for factor in ["high_proximity", "pead_eps", "margin_short_ratio",
                   "revenue_momentum_v2", "foreign_investor_v2"]:
        assert sw.get(factor, None) == 0.0, f"{factor} not default-zero in tw_3m_stable"


def test_tw_6m_defensive_profile_has_5_new_weight_zero():
    """Structural: PORTFOLIO_PROFILES['tw_6m_defensive'] also ships with all
    5 new factors at weight=0. Codex Round 16 coverage gap fix — Step 2
    originally only locked tw_3m_stable profile; parallel lock for 6m too."""
    merged = get_portfolio_config({"portfolio": {"profile": "tw_6m_defensive"}})
    sw = merged["score_weights"]
    for factor in ["high_proximity", "pead_eps", "margin_short_ratio",
                   "revenue_momentum_v2", "foreign_investor_v2"]:
        assert sw.get(factor, None) == 0.0, f"{factor} not default-zero in tw_6m_defensive"
