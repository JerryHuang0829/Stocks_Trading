"""Unit tests for src.features.margin_short_ratio."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.margin_short_ratio import (
    compute_margin_short_ratio_universe,
    score_margin_short,
)


def _make_margin_frame(
    start: str = "2024-01-02",
    n: int = 60,
    margin_balance: float | list[float] = 10000.0,
    short_balance: float | list[float] = 100.0,
) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=n)
    mb = margin_balance if isinstance(margin_balance, list) else [margin_balance] * n
    sb = short_balance if isinstance(short_balance, list) else [short_balance] * n
    return pd.DataFrame({
        "date": dates,
        "stock_id": ["9999"] * n,
        "MarginPurchaseBuy": [100] * n,
        "MarginPurchaseSell": [80] * n,
        "MarginPurchaseCashRepayment": [5] * n,
        "MarginPurchaseTodayBalance": mb,
        "MarginPurchaseYesterdayBalance": [m - 5 for m in mb],
        "MarginPurchaseLimit": [6000000] * n,
        "ShortSaleBuy": [10] * n,
        "ShortSaleSell": [12] * n,
        "ShortSaleCashRepayment": [1] * n,
        "ShortSaleTodayBalance": sb,
        "ShortSaleYesterdayBalance": [s - 2 for s in sb],
        "ShortSaleLimit": [6000000] * n,
        "OffsetLoanAndShort": [0] * n,
        "Note": [" "] * n,
    })


def test_pit_filters_unpublished_days():
    # Margin records extend up to today; with lag_days=2 cutoff, latest 2 days should drop
    df = _make_margin_frame(start="2024-01-01", n=60)
    as_of = pd.Timestamp(df["date"].iloc[-1])
    out = compute_margin_short_ratio_universe(
        {"AAA": df}, issued_by_symbol={"AAA": 1_000_000_000}, as_of=as_of,
        lag_days=2, min_history=40,
    )
    # Single-symbol zscore → 0.0 (std=0 protection). Just verify non-empty.
    assert "AAA" in out


def test_short_history_drops_symbol():
    df = _make_margin_frame(n=30)  # < min_history=40
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_margin_short_ratio_universe(
        {"SHORT": df}, issued_by_symbol={"SHORT": 1_000_000_000}, as_of=as_of,
    )
    assert "SHORT" not in out


def test_zero_issued_shares_drops_symbol():
    df = _make_margin_frame(n=60)
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_margin_short_ratio_universe(
        {"ZERO": df}, issued_by_symbol={"ZERO": 0}, as_of=as_of,
    )
    assert "ZERO" not in out


def test_margin_increase_score_reversed():
    """Stock whose margin balance surged 50% in 20 days should score LOW (reversed)."""
    # Need ≥ 3 symbols for cross-sectional zscore
    stable = [10000.0] * 60
    decline = [10000.0] * 40 + list(np.linspace(10000, 8000, 20))
    surge = [10000.0] * 40 + list(np.linspace(10000, 15000, 20))
    df_stable = _make_margin_frame(n=60, margin_balance=stable)
    df_decline = _make_margin_frame(n=60, margin_balance=decline)
    df_surge = _make_margin_frame(n=60, margin_balance=surge)
    as_of = pd.Timestamp(df_stable["date"].iloc[-1]) + pd.Timedelta(days=3)

    out = compute_margin_short_ratio_universe(
        {"STABLE": df_stable, "DECLINE": df_decline, "SURGE": df_surge},
        issued_by_symbol={
            "STABLE": 1_000_000_000, "DECLINE": 1_000_000_000, "SURGE": 1_000_000_000,
        },
        as_of=as_of, min_history=40,
    )
    # Reversed: SURGE (融資暴增) score lowest, DECLINE (融資萎縮) score highest
    assert {"STABLE", "DECLINE", "SURGE"} <= set(out.index)
    assert out["SURGE"] < out["STABLE"] < out["DECLINE"]


def test_missing_issued_shares_drops_symbol():
    df = _make_margin_frame(n=60)
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_margin_short_ratio_universe(
        {"NOMETA": df}, issued_by_symbol={}, as_of=as_of,
    )
    assert "NOMETA" not in out


def test_higher_margin_ratio_scores_lower():
    """For three symbols with different margin balance, higher margin/issued scores lower."""
    df_low = _make_margin_frame(n=60, margin_balance=5000.0)
    df_mid = _make_margin_frame(n=60, margin_balance=25000.0)
    df_high = _make_margin_frame(n=60, margin_balance=50000.0)
    as_of = pd.Timestamp(df_low["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_margin_short_ratio_universe(
        {"LOW": df_low, "MID": df_mid, "HIGH": df_high},
        issued_by_symbol={
            "LOW": 1_000_000_000, "MID": 1_000_000_000, "HIGH": 1_000_000_000,
        },
        as_of=as_of, min_history=40,
    )
    # Reversed: HIGH (融資高) → 低分；LOW (融資低) → 高分
    assert out["HIGH"] < out["MID"] < out["LOW"]


def test_aux_panel_as_fallback_for_issued_shares():
    """CLI passes issued shares as aux_panel; factor should accept either kwarg."""
    df = _make_margin_frame(n=60)
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    out_via_kw = compute_margin_short_ratio_universe(
        {"AAA": df}, issued_by_symbol={"AAA": 1_000_000_000}, as_of=as_of,
    )
    out_via_aux = compute_margin_short_ratio_universe(
        {"AAA": df}, aux_panel={"AAA": 1_000_000_000}, as_of=as_of,
    )
    assert set(out_via_kw.index) == set(out_via_aux.index)


def test_score_wrapper_returns_dict_with_icon():
    df = _make_margin_frame(n=60)
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    res = score_margin_short(df, 1_000_000_000, as_of=as_of)
    assert res["score"] is not None
    assert res["icon"] in {"🔻", "✅", "➖", "⚠️"}


def test_partial_coverage_symbols_excluded_via_intersection():
    """P1-3: symbols missing one sub-signal must be dropped, not fillna(0.0)'d.

    Simulates 5 symbols where 2 lack enough history for margin_change_20d. Under
    the old union-based reindex they received change_z=0.0 and therefore a
    partial composite score — that contaminates the cross-sectional ranking.
    With the intersection fix, only the 3 fully-covered symbols remain.
    """
    # 3 fully-covered symbols (n=60 → margin_change_20d computable)
    full_a = _make_margin_frame(n=60, margin_balance=10_000.0)
    full_b = _make_margin_frame(n=60, margin_balance=25_000.0)
    full_c = _make_margin_frame(n=60, margin_balance=50_000.0)
    # 2 partial-coverage symbols: n=20 trading days. `_compute_raw_signals`
    # requires len(frame) >= 21 for change_20d, so these produce only the
    # ratio signal — change_20d stays None. Under old union+fillna(0) they
    # would still land in the composite with a synthetic change_z=0.
    partial_d = _make_margin_frame(n=20, margin_balance=30_000.0)
    partial_e = _make_margin_frame(n=20, margin_balance=40_000.0)
    as_of = pd.Timestamp(full_a["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_margin_short_ratio_universe(
        {
            "A": full_a, "B": full_b, "C": full_c,
            "D": partial_d, "E": partial_e,
        },
        issued_by_symbol={s: 1_000_000_000 for s in ("A", "B", "C", "D", "E")},
        as_of=as_of,
        min_history=15,
    )
    # Only fully-covered symbols survive the intersection.
    assert set(out.index) == {"A", "B", "C"}


def test_score_wrapper_insufficient_returns_none():
    df = _make_margin_frame(n=10)
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    res = score_margin_short(df, 1_000_000_000, as_of=as_of)
    assert res["score"] is None
    assert res["detail"] == "insufficient"


# R8-1 mutation-proof test ----------------------------------------------


def test_zscore_with_tolerance_fires_on_sub_tolerance_std():
    """R8-1 (rewrite after external audit Round 7 showed R7-2 wasn't mutation-proof):
    directly verify that `_zscore_with_tolerance` fires on std that is
    **greater than zero but below 1e-12** — the exact failure mode the
    R6-3 fix was supposed to catch.

    external audit's R7 mutation showed the previous R7-2 test's input (`1e-17`
    symbol offset on 10000.0 base) was silently flattened to *identical*
    values because 1e-17 is below float epsilon at 1e4 scale. Std was
    exactly 0, even the old `std == 0` exact-compare guard fired, so
    the test passed under mutation — i.e. was useless.

    This version uses `1e-13` step at scale 1.0 (cross-section std
    ≈ 1.00e-13, strictly > 0 and strictly < 1e-12). Old guard would NOT
    fire and would produce pathological z-scores (±1.0 range). New
    tolerance guard DOES fire → all 0.0.
    """
    from src.features.margin_short_ratio import _zscore_with_tolerance

    col = pd.Series([1.0, 1.0 + 1e-13, 1.0 + 2e-13], index=["A", "B", "C"])

    # Input must be provably in the float-noise band.
    std = col.std(ddof=1)
    assert 0 < std < 1e-12, (
        f"test setup invalid: std={std} not in (0, 1e-12)"
    )

    result = _zscore_with_tolerance(col)
    assert all(abs(v) < 1e-9 for v in result), (
        f"tolerance guard did NOT fire for sub-tolerance std: {result.tolist()}"
    )

    # Mutation harness: exact-compare version must produce ±1.0 range.
    def _zscore_mutated_exact_compare(s: pd.Series) -> pd.Series:
        if len(s) < 3:
            return pd.Series(0.0, index=s.index)
        std = s.std(ddof=1)
        if std == 0 or pd.isna(std):  # OLD exact compare
            return pd.Series(0.0, index=s.index)
        return (s - s.mean()) / std

    mutated = _zscore_mutated_exact_compare(col)
    assert any(abs(v) > 0.5 for v in mutated), (
        "mutation harness failed: exact-compare did not emit pathological "
        "z-scores for std=1e-13 input. Test is not mutation-proof."
    )
    assert result.abs().max() < mutated.abs().max(), (
        "production zscore indistinguishable from mutated version"
    )
