"""Tests for src/analysis/ml_contextual.py — Step 4b contextual features.

Per pre-reg §6 LOCK:
- Sector dummies (one-hot, with _OTHER/_UNKNOWN pooling)
- Size decile (integer 1-N)
- Regime one-hot (3 labels)
- 5 LOCKED interactions
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis.ml_contextual import (  # noqa: E402
    LOCKED_INTERACTION_NAMES,
    REGIME_LABELS,
    ContextualConfig,
    _compute_size_decile_per_period,
    add_contextual_features,
)


# ===========================================================================
# Fixtures
# ===========================================================================
BASE_COLS = ["symbol", "as_of", "label_end", "idio_vol_max", "high_proximity",
             "value_ep_sn", "pead_eps_sn", "reversal_1m",
             "forward_return", "label_top_decile"]


def _make_training_df(n_symbols: int = 10, n_periods: int = 2,
                       seed: int = 0) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    rows = []
    as_ofs = [pd.Timestamp(f"2024-0{m}-15") for m in range(1, n_periods + 1)]
    for i, as_of in enumerate(as_ofs[:-1]):
        label_end = as_ofs[i + 1]
        for j in range(n_symbols):
            rows.append({
                "symbol": f"S{j}",
                "as_of": as_of,
                "label_end": label_end,
                "idio_vol_max": rng.randn(),
                "high_proximity": rng.uniform(0, 1),
                "value_ep_sn": rng.randn() * 0.05,
                "pead_eps_sn": rng.randn() * 0.5,
                "reversal_1m": rng.randn() * 0.02,
                "forward_return": rng.normal(0.01, 0.05),
                "label_top_decile": int(rng.random() > 0.9),
            })
    return pd.DataFrame(rows)


# ===========================================================================
# Helper: size decile
# ===========================================================================
def test_size_decile_basic_ascending():
    caps = pd.Series([1e6, 1e7, 1e8, 1e9, 1e10], index=["A", "B", "C", "D", "E"])
    deciles = _compute_size_decile_per_period(caps, n_buckets=5)
    # Smallest = 1, largest = 5
    assert deciles["A"] == 1
    assert deciles["E"] == 5


def test_size_decile_missing_stays_nan():
    caps = pd.Series([1e6, np.nan, 1e8], index=["A", "B", "C"])
    deciles = _compute_size_decile_per_period(caps, n_buckets=3)
    assert pd.isna(deciles["B"])


def test_size_decile_empty():
    caps = pd.Series(dtype=float)
    deciles = _compute_size_decile_per_period(caps, n_buckets=10)
    assert deciles.empty


# ===========================================================================
# Smoke
# ===========================================================================
def test_add_contextual_basic_shape():
    df = _make_training_df(n_symbols=10, n_periods=3, seed=0)
    industry_map = {f"S{i}": ("IND_A" if i < 5 else "IND_B") for i in range(10)}
    size_panel = {
        as_of: {f"S{i}": 10 ** (6 + i * 0.5) for i in range(10)}
        for as_of in df["as_of"].unique()
    }
    regime_by_date = {
        as_of: "trending_up" for as_of in df["as_of"].unique()
    }
    out, diag = add_contextual_features(df, industry_map, size_panel,
                                        regime_by_date)
    # 10 base + 2 sector + 1 size_decile + 3 regime + 5 interaction = 21 cols
    assert out.shape[1] == 10 + 2 + 1 + 3 + 5
    assert "size_decile" in out.columns
    for name in LOCKED_INTERACTION_NAMES:
        assert name in out.columns


def test_locked_interaction_names_are_exactly_5():
    """pre-reg §6 §13.1 — 5 LOCKED interactions, no more no less."""
    assert len(LOCKED_INTERACTION_NAMES) == 5


def test_regime_one_hot_three_columns():
    df = _make_training_df(n_symbols=5, n_periods=2)
    industry_map = {f"S{i}": "IND_A" for i in range(5)}
    size_panel = {as_of: {f"S{i}": 1e8 for i in range(5)}
                  for as_of in df["as_of"].unique()}
    regime_by_date = {as_of: "ranging" for as_of in df["as_of"].unique()}
    out, _ = add_contextual_features(df, industry_map, size_panel, regime_by_date)
    for r in REGIME_LABELS:
        assert f"regime_{r}" in out.columns
    # All ranging → ranging col is 1, others are 0
    assert out["regime_ranging"].all()
    assert (out["regime_trending_up"] == 0).all()
    assert (out["regime_trending_down"] == 0).all()


# ===========================================================================
# LOCKED interaction semantics
# ===========================================================================
def test_interact_value_ep_sn_x_trending_up():
    df = _make_training_df(n_symbols=3, n_periods=2)
    # Mix regime: first period trending_up, second period trending_down
    as_ofs = df["as_of"].unique()
    industry_map = {f"S{i}": "IND_A" for i in range(3)}
    size_panel = {as_of: {f"S{i}": 1e8 for i in range(3)} for as_of in as_ofs}
    regime_by_date = {as_ofs[0]: "trending_up"}
    out, _ = add_contextual_features(df, industry_map, size_panel, regime_by_date)
    # For rows where regime is trending_up: interact = value_ep_sn
    # For other rows: interact = 0
    trending_rows = out[out["regime_trending_up"] == 1]
    assert (trending_rows["interact_value_ep_sn_x_trending_up"]
            == trending_rows["value_ep_sn"]).all()
    non_trending = out[out["regime_trending_up"] == 0]
    assert (non_trending["interact_value_ep_sn_x_trending_up"] == 0).all()


def test_interact_pead_eps_sn_x_earnings_season_uses_month():
    # Build rows in March (earnings_season) and February (not)
    rows = [
        {"symbol": "A", "as_of": pd.Timestamp("2024-03-15"),
         "label_end": pd.Timestamp("2024-04-15"),
         "idio_vol_max": 0, "high_proximity": 0.5,
         "value_ep_sn": 0, "pead_eps_sn": 1.0,
         "reversal_1m": 0, "forward_return": 0, "label_top_decile": 0},
        {"symbol": "B", "as_of": pd.Timestamp("2024-02-15"),
         "label_end": pd.Timestamp("2024-03-15"),
         "idio_vol_max": 0, "high_proximity": 0.5,
         "value_ep_sn": 0, "pead_eps_sn": 1.0,
         "reversal_1m": 0, "forward_return": 0, "label_top_decile": 0},
    ]
    df = pd.DataFrame(rows)
    industry_map = {"A": "X", "B": "X"}
    size_panel = {pd.Timestamp("2024-03-15"): {"A": 1e8},
                  pd.Timestamp("2024-02-15"): {"B": 1e8}}
    regime_by_date = {pd.Timestamp("2024-03-15"): "ranging",
                      pd.Timestamp("2024-02-15"): "ranging"}
    out, _ = add_contextual_features(df, industry_map, size_panel, regime_by_date)
    # March row (3 in earnings_season {3,5,8,11}) → interaction = 1 * 1 = 1
    # Feb row (2 NOT in earnings_season)            → interaction = 1 * 0 = 0
    a_row = out[out["symbol"] == "A"].iloc[0]
    b_row = out[out["symbol"] == "B"].iloc[0]
    assert a_row["interact_pead_eps_sn_x_earnings_season"] == 1.0
    assert b_row["interact_pead_eps_sn_x_earnings_season"] == 0.0


def test_interact_value_ep_sn_x_size_decile_low_uses_threshold():
    df = _make_training_df(n_symbols=10, n_periods=2)
    industry_map = {f"S{i}": "X" for i in range(10)}
    # Linearly increasing market_cap so deciles are 1..10
    size_panel = {as_of: {f"S{i}": 10 ** (5 + i) for i in range(10)}
                  for as_of in df["as_of"].unique()}
    regime_by_date = {as_of: "ranging" for as_of in df["as_of"].unique()}
    out, _ = add_contextual_features(df, industry_map, size_panel, regime_by_date)
    # decile <= 3 means deciles 1,2,3 → 3 lowest stocks (S0,S1,S2)
    low_size = out[out["size_decile"] <= 3]
    assert (low_size["interact_value_ep_sn_x_size_decile_low"]
            == low_size["value_ep_sn"]).all()
    high_size = out[out["size_decile"] > 3]
    assert (high_size["interact_value_ep_sn_x_size_decile_low"] == 0).all()


# ===========================================================================
# Schema enforcement
# ===========================================================================
def test_raises_when_missing_required_column():
    df = pd.DataFrame({"symbol": ["A"], "as_of": [pd.Timestamp("2024-01-15")]})
    with pytest.raises(ValueError, match="missing required columns"):
        add_contextual_features(df, {}, {}, {})


def test_raises_when_empty():
    df = pd.DataFrame(columns=BASE_COLS)
    with pytest.raises(ValueError, match="empty"):
        add_contextual_features(df, {}, {}, {})


# ===========================================================================
# Sector pooling
# ===========================================================================
def test_sector_pooling_small_industries_become_other():
    """Industries with < 3 members get pooled into _OTHER."""
    df = _make_training_df(n_symbols=5, n_periods=2)
    # IND_A has 4 members, IND_B has 1 (below threshold 3) → IND_B → _OTHER
    industry_map = {"S0": "IND_A", "S1": "IND_A", "S2": "IND_A",
                    "S3": "IND_A", "S4": "IND_B"}
    size_panel = {as_of: {f"S{i}": 1e8 for i in range(5)}
                  for as_of in df["as_of"].unique()}
    regime_by_date = {as_of: "ranging" for as_of in df["as_of"].unique()}
    out, diag = add_contextual_features(df, industry_map, size_panel, regime_by_date)
    # IND_B (S4) → _OTHER bucket
    assert diag.n_pooled_other_sector_rows > 0


def test_sector_unknown_for_missing_symbol():
    df = _make_training_df(n_symbols=3, n_periods=2)
    # S2 not in industry_map
    industry_map = {"S0": "X", "S1": "X"}
    size_panel = {as_of: {f"S{i}": 1e8 for i in range(3)}
                  for as_of in df["as_of"].unique()}
    regime_by_date = {as_of: "ranging" for as_of in df["as_of"].unique()}
    out, diag = add_contextual_features(df, industry_map, size_panel, regime_by_date)
    assert diag.n_unknown_sector_rows > 0


# ===========================================================================
# Total dimensions sanity
# ===========================================================================
def test_total_column_count_sanity():
    """Verify pre-reg §6 ML 實際輸入維度 ~30-40 ballpark.
    With ~20 sectors realistic, total ≈ 10 + 20 + 1 + 3 + 5 = 39.
    Synthetic small test: 2 sectors → 10 + 2 + 1 + 3 + 5 = 21 (lower bound)."""
    df = _make_training_df(n_symbols=10, n_periods=3)
    industry_map = {f"S{i}": ("IND_A" if i < 5 else "IND_B") for i in range(10)}
    size_panel = {as_of: {f"S{i}": 10 ** (6 + i * 0.5) for i in range(10)}
                  for as_of in df["as_of"].unique()}
    regime_by_date = {as_of: "trending_up" for as_of in df["as_of"].unique()}
    out, _ = add_contextual_features(df, industry_map, size_panel, regime_by_date)
    # 10 base + 2 sector + 1 size_decile + 3 regime + 5 interaction = 21
    assert out.shape[1] == 21
    # Order: base cols come first
    for i, c in enumerate(BASE_COLS):
        assert out.columns[i] == c
