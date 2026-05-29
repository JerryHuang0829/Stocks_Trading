"""Tests for src/utils/factor_neutralize.py — sector / size demean helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.utils.factor_neutralize import (  # noqa: E402
    sector_neutralize,
    size_neutralize,
)


# ---------------------------------------------------------------------------
# sector_neutralize
# ---------------------------------------------------------------------------
def test_sector_neutralize_subtracts_industry_mean():
    """3 Semi stocks (mean 5) + 3 Finance stocks (mean 11) → neutralized = deviation from group mean."""
    panel = pd.Series(
        {"2330": 4.0, "2454": 5.0, "2317": 6.0,        # Semi mean = 5
         "2882": 10.0, "2891": 11.0, "2884": 12.0},    # Finance mean = 11
    )
    industry = {"2330": "Semi", "2454": "Semi", "2317": "Semi",
                "2882": "Finance", "2891": "Finance", "2884": "Finance"}
    out = sector_neutralize(panel, industry)
    # Semi
    assert out["2330"] == pytest.approx(-1.0)
    assert out["2454"] == pytest.approx(0.0)
    assert out["2317"] == pytest.approx(1.0)
    # Finance
    assert out["2882"] == pytest.approx(-1.0)
    assert out["2891"] == pytest.approx(0.0)
    assert out["2884"] == pytest.approx(1.0)


def test_sector_neutralize_pools_small_industries():
    """Industries with < min_industry_size members go to _OTHER."""
    panel = pd.Series(
        {"A": 10.0, "B": 12.0, "C": 14.0,    # Semi (3 → meets default min=3)
         "X": 100.0, "Y": 200.0,             # Niche (2 → pooled to _OTHER)
         "Z": 300.0},                         # Another (1 → pooled to _OTHER)
    )
    industry = {"A": "Semi", "B": "Semi", "C": "Semi",
                "X": "Niche", "Y": "Niche", "Z": "Another"}
    out = sector_neutralize(panel, industry, min_industry_size=3)
    # Semi mean = 12 → A=-2 B=0 C=2
    assert out["A"] == pytest.approx(-2.0)
    assert out["B"] == pytest.approx(0.0)
    assert out["C"] == pytest.approx(2.0)
    # _OTHER pool: X 100, Y 200, Z 300 → mean 200
    assert out["X"] == pytest.approx(-100.0)
    assert out["Y"] == pytest.approx(0.0)
    assert out["Z"] == pytest.approx(100.0)


def test_sector_neutralize_unknown_label():
    """Symbols without industry label → _UNKNOWN group, demeaned within it."""
    panel = pd.Series({"A": 5.0, "B": 7.0, "C": 9.0})
    industry = {"A": "Semi"}     # only A labeled; B/C → _UNKNOWN
    out = sector_neutralize(panel, industry, min_industry_size=2)
    # Semi (only A) becomes _OTHER (count 1 < 2);
    # _OTHER pool = A; _UNKNOWN pool = B, C
    # _OTHER: A alone → mean 5 → A=0
    # _UNKNOWN: B 7, C 9 → mean 8 → B=-1, C=1
    assert out["A"] == pytest.approx(0.0)
    assert out["B"] == pytest.approx(-1.0)
    assert out["C"] == pytest.approx(1.0)


def test_sector_neutralize_empty_panel():
    out = sector_neutralize(pd.Series(dtype=float), {})
    assert out.empty


def test_sector_neutralize_preserves_scale():
    """Output scale should match input (just shifted by mean)."""
    panel = pd.Series({f"S{i}": float(i * 10) for i in range(10)})
    industry = {f"S{i}": "X" for i in range(10)}   # all in one industry
    out = sector_neutralize(panel, industry)
    # mean = 45 → values become -45, -35, ..., 45
    assert out.min() == pytest.approx(-45.0)
    assert out.max() == pytest.approx(45.0)
    assert out.mean() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# size_neutralize
# ---------------------------------------------------------------------------
def test_size_neutralize_subtracts_decile_mean():
    """20 stocks across 4 buckets (5 stocks each); values = 1, 2, ... 20.
    Each bucket of 5 has mean (5k+3), so demeaned within bucket = -2,-1,0,1,2."""
    syms = [f"S{i:02d}" for i in range(20)]
    panel = pd.Series({s: float(i + 1) for i, s in enumerate(syms)})
    # market cap monotone increasing — ensures qcut buckets align with index
    caps = {s: float(i + 1) * 1000 for i, s in enumerate(syms)}
    out = size_neutralize(panel, caps, n_buckets=4)
    # Bucket 0 contains S00..S04 (values 1..5, mean 3) → -2,-1,0,1,2
    for i in range(5):
        assert out[syms[i]] == pytest.approx(float(i + 1) - 3.0)
    # Bucket 1 contains S05..S09 (values 6..10, mean 8) → -2,-1,0,1,2
    for i in range(5):
        assert out[syms[5 + i]] == pytest.approx(float(5 + i + 1) - 8.0)


def test_size_neutralize_missing_cap_pooled():
    """Symbols missing from market_cap_map → _UNKNOWN_SIZE bucket."""
    panel = pd.Series({"A": 10.0, "B": 12.0, "C": 14.0, "D": 100.0, "E": 200.0})
    caps = {"A": 1e9, "B": 2e9, "C": 3e9}   # D, E missing
    out = size_neutralize(panel, caps, n_buckets=3)
    # D, E pooled into _UNKNOWN_SIZE, mean = 150 → D=-50, E=50
    assert out["D"] == pytest.approx(-50.0)
    assert out["E"] == pytest.approx(50.0)


def test_size_neutralize_all_identical_caps():
    """Degenerate: all caps equal → 1 bucket → demean to 0 relative within."""
    panel = pd.Series({"A": 10.0, "B": 20.0, "C": 30.0})
    caps = {s: 1e9 for s in panel.index}      # all same
    out = size_neutralize(panel, caps, n_buckets=10)
    # Single bucket, mean = 20 → -10, 0, 10
    assert out.mean() == pytest.approx(0.0)
    assert out["A"] == pytest.approx(-10.0)
    assert out["C"] == pytest.approx(10.0)


def test_size_neutralize_empty_panel():
    assert size_neutralize(pd.Series(dtype=float), {}).empty


def test_size_neutralize_few_unique_caps():
    """3 unique caps → only 3 buckets possible even if n_buckets=10 requested."""
    panel = pd.Series({f"S{i}": float(i) for i in range(9)})
    caps = {f"S{i}": float((i // 3) * 1000) for i in range(9)}   # 3 cap levels
    out = size_neutralize(panel, caps, n_buckets=10)
    # 3 buckets of 3 stocks each; within each bucket mean = middle value
    # bucket0: 0,1,2 → mean 1 → -1,0,1
    # bucket1: 3,4,5 → mean 4 → -1,0,1
    # bucket2: 6,7,8 → mean 7 → -1,0,1
    assert out["S0"] == pytest.approx(-1.0)
    assert out["S1"] == pytest.approx(0.0)
    assert out["S2"] == pytest.approx(1.0)
    assert out["S5"] == pytest.approx(1.0)
    assert out["S6"] == pytest.approx(-1.0)
