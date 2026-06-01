"""Tests for src/analysis/ml_features.py — 5-feature panel provider.

Per pre-reg §4 + §13 conditions 1 / 4 / 12:
- LOCKED_FEATURE_NAMES exact match (5 features, fixed order)
- PIT discipline delegated to each factor function (those have own tests)
- This module's tests cover: assembly + column order + universe filter

Heavy IC validation belongs in each factor's own test file; this file only
verifies the orchestration / glue layer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_features import (  # noqa: E402
    LOCKED_FEATURE_NAMES,
    compute_feature_panel,
)


# ===========================================================================
# Lock contract
# ===========================================================================
def test_locked_feature_names_exact_5():
    """pre-reg §4 / §13.1 — feature set 鎖定 5 個,順序固定."""
    assert LOCKED_FEATURE_NAMES == [
        "idio_vol_max",
        "high_proximity",
        "value_ep_sn",
        "pead_eps_sn",
        "reversal_1m",
    ]
    assert len(LOCKED_FEATURE_NAMES) == 5


# ===========================================================================
# Orchestration smoke (heavy compute deferred — small synthetic inputs)
# ===========================================================================
def _make_ohlcv(symbol_seed: int, n_days: int = 300,
                 start: str = "2023-06-01") -> pd.DataFrame:
    rng = np.random.RandomState(symbol_seed)
    rets = rng.normal(0.0005, 0.015, n_days)
    closes = 100.0 * np.exp(np.cumsum(rets))
    idx = pd.date_range(start, periods=n_days, freq="B")
    return pd.DataFrame({
        "open":  closes * 0.99,
        "high":  closes * 1.02,
        "low":   closes * 0.97,
        "close": closes,
        "volume": rng.randint(1000, 100000, n_days),
    }, index=idx)


def _make_eps(symbol_seed: int) -> pd.DataFrame:
    """Quarterly EPS DataFrame in FinMind long format."""
    rng = np.random.RandomState(symbol_seed * 7)
    quarter_ends = pd.date_range("2019-03-31", "2024-09-30", freq="QE")
    eps_values = rng.uniform(0.5, 5.0, len(quarter_ends))
    return pd.DataFrame({
        "date": quarter_ends,
        "type": ["EPS"] * len(quarter_ends),
        "value": eps_values,
    })


def test_compute_feature_panel_returns_correct_schema():
    """Panel must have exactly LOCKED_FEATURE_NAMES as columns, in order."""
    symbols = [f"S{i}" for i in range(8)]
    ohlcv = {s: _make_ohlcv(i) for i, s in enumerate(symbols)}
    eps = {s: _make_eps(i) for i, s in enumerate(symbols)}
    market_idx = pd.date_range("2023-06-01", periods=300, freq="B")
    market_returns = pd.Series(
        np.random.RandomState(0).normal(0.0003, 0.012, 300),
        index=market_idx,
    )
    industry_map = {s: f"IND_{i % 3}" for i, s in enumerate(symbols)}

    panel = compute_feature_panel(
        as_of=pd.Timestamp("2024-08-15"),
        ohlcv_by_symbol=ohlcv,
        eps_by_symbol=eps,
        market_returns=market_returns,
        industry_map=industry_map,
    )

    assert isinstance(panel, pd.DataFrame)
    assert list(panel.columns) == LOCKED_FEATURE_NAMES


def test_adjusted_ohlcv_routes_ratio_factors_not_value_ep():
    """adjusted_ohlcv_by_symbol must drive ratio/return features (high_proximity)
    while value_ep keeps the RAW ohlcv (price-level E/P must stay unit-consistent
    with EPS)."""
    from scripts._factor_ic_helpers import split_adjust_ohlcv_panel

    symbols = [f"S{i}" for i in range(8)]
    raw = {s: _make_ohlcv(i) for i, s in enumerate(symbols)}
    # Inject a raw (unadjusted) 1:2 split ~100 bars before end on every symbol.
    for df in raw.values():
        split_day = df.index[-100]
        df.loc[df.index < split_day, ["open", "high", "low", "close"]] *= 2.0
    adjusted = split_adjust_ohlcv_panel(raw)

    eps = {s: _make_eps(i) for i, s in enumerate(symbols)}
    market_idx = raw["S0"].index
    market_returns = pd.Series(
        np.random.RandomState(0).normal(0.0003, 0.012, len(market_idx)),
        index=market_idx,
    )
    industry_map = {s: f"IND_{i % 3}" for i, s in enumerate(symbols)}
    as_of = pd.Timestamp("2024-06-15")
    kw = dict(eps_by_symbol=eps, market_returns=market_returns,
              industry_map=industry_map)

    raw_only = compute_feature_panel(as_of=as_of, ohlcv_by_symbol=raw, **kw)
    with_adj = compute_feature_panel(
        as_of=as_of, ohlcv_by_symbol=raw, adjusted_ohlcv_by_symbol=adjusted, **kw,
    )

    # high_proximity differs: raw has the fake split discontinuity in its 52w
    # rolling-max window; adjusted removes it.
    hp_raw = raw_only["high_proximity"].dropna()
    hp_adj = with_adj["high_proximity"].reindex(hp_raw.index)
    assert (hp_raw - hp_adj).abs().max() > 0.05
    # value_ep_sn identical: value_ep always uses the RAW ohlcv, ignores adjusted.
    ve_raw = raw_only["value_ep_sn"].dropna()
    ve_adj = with_adj["value_ep_sn"].reindex(ve_raw.index)
    assert len(ve_raw) > 0 and np.allclose(ve_raw.values, ve_adj.values)


def test_compute_feature_panel_universe_filter_applied():
    """Universe filter must restrict output to specified symbols."""
    symbols = [f"S{i}" for i in range(8)]
    ohlcv = {s: _make_ohlcv(i) for i, s in enumerate(symbols)}
    eps = {s: _make_eps(i) for i, s in enumerate(symbols)}
    market_idx = pd.date_range("2023-06-01", periods=300, freq="B")
    market_returns = pd.Series(
        np.random.RandomState(0).normal(0.0003, 0.012, 300),
        index=market_idx,
    )
    industry_map = {s: f"IND_{i % 3}" for i, s in enumerate(symbols)}

    filter_set = {"S0", "S1", "S2"}
    panel = compute_feature_panel(
        as_of=pd.Timestamp("2024-08-15"),
        ohlcv_by_symbol=ohlcv,
        eps_by_symbol=eps,
        market_returns=market_returns,
        industry_map=industry_map,
        universe_filter=filter_set,
    )
    assert set(panel.index).issubset(filter_set)


def test_compute_feature_panel_idio_vol_max_requires_market_returns():
    """Without market_returns, compute_idio_vol_max_panel returns empty Series;
    panel.idio_vol_max column should be all-NaN but column itself MUST exist."""
    symbols = ["S0", "S1"]
    ohlcv = {s: _make_ohlcv(i) for i, s in enumerate(symbols)}
    eps = {s: _make_eps(i) for i, s in enumerate(symbols)}
    # market returns shorter than residual lookback (60d) → idio_vol_max returns empty
    short_market = pd.Series(
        [0.001, 0.002, 0.003],
        index=pd.date_range("2024-08-01", periods=3, freq="B"),
    )
    industry_map = {s: "IND_0" for s in symbols}

    panel = compute_feature_panel(
        as_of=pd.Timestamp("2024-08-15"),
        ohlcv_by_symbol=ohlcv,
        eps_by_symbol=eps,
        market_returns=short_market,
        industry_map=industry_map,
    )
    # idio_vol_max column exists but all-NaN (because empty source Series)
    assert "idio_vol_max" in panel.columns
    assert panel["idio_vol_max"].isna().all()


def test_compute_feature_panel_sn_subtracts_industry_mean():
    """value_ep_sn / pead_eps_sn must NOT equal raw value_ep / pead_eps
    when there are multiple symbols in same industry with different raw values.
    Sanity test that sector_neutralize is actually being applied."""
    # 4 symbols, all same industry IND_X with diverse EPS history → diverse raw value_ep
    symbols = ["A", "B", "C", "D"]
    ohlcv = {s: _make_ohlcv(i) for i, s in enumerate(symbols)}
    eps = {s: _make_eps(i * 13) for i, s in enumerate(symbols)}  # different seeds
    market_returns = pd.Series(
        np.random.RandomState(0).normal(0.0003, 0.012, 300),
        index=pd.date_range("2023-06-01", periods=300, freq="B"),
    )
    industry_map = {s: "IND_X" for s in symbols}  # all in same industry

    panel = compute_feature_panel(
        as_of=pd.Timestamp("2024-08-15"),
        ohlcv_by_symbol=ohlcv,
        eps_by_symbol=eps,
        market_returns=market_returns,
        industry_map=industry_map,
    )
    # value_ep_sn column should sum to ~0 over the same-industry group
    # (sector neutralization subtracts group mean)
    sn = panel["value_ep_sn"].dropna()
    if len(sn) >= 2:   # need at least 2 to test group-demean
        assert abs(sn.sum()) < 1e-6, (
            f"value_ep_sn for same-industry group must sum to ~0; got {sn.sum()}"
        )
