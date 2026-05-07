"""Unit tests for src.features.foreign_broker_v2."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.foreign_broker_v2 import (
    _pivot_long_to_wide,
    compute_foreign_broker_v2_universe,
)


def _make_inst_frame(
    start: str = "2024-01-02",
    n_days: int = 80,
    foreign_pattern: list[float] | None = None,
    trust_pattern: list[float] | None = None,
    stock_id: str = "9999",
) -> pd.DataFrame:
    """Build long-format institutional frame (date, stock_id, name, buy, sell).

    By default: foreign +/-100k alternating, trust 0, dealers 0.
    """
    dates = pd.bdate_range(start=start, periods=n_days)
    rows = []
    for i, d in enumerate(dates):
        f_net = foreign_pattern[i] if foreign_pattern and i < len(foreign_pattern) else 100_000 * ((-1) ** i)
        t_net = trust_pattern[i] if trust_pattern and i < len(trust_pattern) else 0
        # Encode net as buy - sell; use buy = max(net, 0), sell = max(-net, 0)
        for name, net in [
            ("Foreign_Investor", f_net),
            ("Investment_Trust", t_net),
            ("Dealer_self", 0),
            ("Dealer_Hedging", 0),
        ]:
            buy = max(net, 0)
            sell = max(-net, 0)
            rows.append({
                "date": d, "stock_id": stock_id, "name": name,
                "buy": buy, "sell": sell,
            })
    return pd.DataFrame(rows)


def test_pivot_long_to_wide_correct_net():
    df = _make_inst_frame(n_days=5)
    wide = _pivot_long_to_wide(df)
    assert wide is not None
    assert {"foreign_net", "trust_net", "dealer_self_net", "dealer_hedge_net"} <= set(wide.columns)
    assert len(wide) == 5


def test_empty_frame_returns_empty_series():
    out = compute_foreign_broker_v2_universe(
        {"AAA": None}, market_value_by_symbol={"AAA": 1e9},
        as_of=pd.Timestamp("2024-04-01"),
    )
    assert out.empty


def test_insufficient_history_drops_symbol():
    df = _make_inst_frame(n_days=30)  # < min_history=60
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_foreign_broker_v2_universe(
        {"SHORT": df}, market_value_by_symbol={"SHORT": 1e9},
        as_of=as_of,
    )
    assert out.empty


def test_pit_respects_lag_days():
    """Foreign inflow exclusively on the last 5 days should NOT influence the score
    if as_of's cutoff (as_of - lag_days) drops those days."""
    n = 100
    # A has huge foreign buy on last 5 days; B and C are all-zero peers
    a_foreign = [0.0] * (n - 5) + [10_000_000.0] * 5
    df_a = _make_inst_frame(n_days=n, foreign_pattern=a_foreign, stock_id="A")
    df_b = _make_inst_frame(n_days=n, foreign_pattern=[0.0]*n, stock_id="B")
    df_c = _make_inst_frame(n_days=n, foreign_pattern=[0.0]*n, stock_id="C")

    last_date = pd.Timestamp(df_a["date"].iloc[-1])

    # Case 1: cutoff captures all inflow days (as_of well past end) → A differs from B/C
    out_visible = compute_foreign_broker_v2_universe(
        {"A": df_a, "B": df_b, "C": df_c},
        market_value_by_symbol={"A": 1e9, "B": 1e9, "C": 1e9},
        as_of=last_date + pd.Timedelta(days=10), lag_days=2,
    )
    assert out_visible["A"] > out_visible["B"]

    # Case 2: cutoff drops the inflow window entirely (lag_days=14 pulls cutoff
    # well before the last 5 inflow days) → A should be equivalent to peers
    out_hidden = compute_foreign_broker_v2_universe(
        {"A": df_a, "B": df_b, "C": df_c},
        market_value_by_symbol={"A": 1e9, "B": 1e9, "C": 1e9},
        as_of=last_date, lag_days=14,
    )
    # All three symbols now see identical zero foreign_net histories →
    # composites should be equal (z-scores over identical values = 0)
    if "A" in out_hidden and "B" in out_hidden:
        assert abs(out_hidden["A"] - out_hidden["B"]) < 0.1


def test_bullish_persistent_foreign_scores_higher():
    """Symbol with consistent foreign buying should rank above the rest."""
    n = 80
    # BULL: foreign +100k every last 20 days; BEAR: foreign -100k; NEUTRAL: 0
    bull = [0.0] * (n - 20) + [100_000.0] * 20
    bear = [0.0] * (n - 20) + [-100_000.0] * 20
    neutral = [0.0] * n
    df_bull = _make_inst_frame(n_days=n, foreign_pattern=bull, stock_id="BULL")
    df_bear = _make_inst_frame(n_days=n, foreign_pattern=bear, stock_id="BEAR")
    df_neu = _make_inst_frame(n_days=n, foreign_pattern=neutral, stock_id="NEU")

    as_of = pd.Timestamp(df_bull["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_foreign_broker_v2_universe(
        {"BULL": df_bull, "BEAR": df_bear, "NEU": df_neu},
        market_value_by_symbol={"BULL": 1e9, "BEAR": 1e9, "NEU": 1e9},
        as_of=as_of,
    )
    assert {"BULL", "BEAR", "NEU"} <= set(out.index)
    assert out["BULL"] > out["NEU"] > out["BEAR"]


def test_foreign_and_trust_alignment_boosts_consistency():
    """Foreign + Trust both bullish → consistency sub-signal should score well."""
    n = 80
    foreign_both = [0.0] * (n - 20) + [100_000.0] * 20
    trust_both = [0.0] * (n - 20) + [50_000.0] * 20
    only_foreign = [0.0] * (n - 20) + [100_000.0] * 20  # trust stays 0
    neither = [0.0] * n

    df_both = _make_inst_frame(n_days=n, foreign_pattern=foreign_both,
                                trust_pattern=trust_both, stock_id="BOTH")
    df_onef = _make_inst_frame(n_days=n, foreign_pattern=only_foreign, stock_id="ONEF")
    df_none = _make_inst_frame(n_days=n, foreign_pattern=neither, stock_id="NONE")

    as_of = pd.Timestamp(df_both["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_foreign_broker_v2_universe(
        {"BOTH": df_both, "ONEF": df_onef, "NONE": df_none},
        market_value_by_symbol={"BOTH": 1e9, "ONEF": 1e9, "NONE": 1e9},
        as_of=as_of,
    )
    # BOTH should score strictly higher than ONEF (consistency boost)
    assert out["BOTH"] > out["ONEF"] > out["NONE"]


def test_zero_market_value_drops_symbol():
    df = _make_inst_frame(n_days=80)
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_foreign_broker_v2_universe(
        {"ZERO": df}, market_value_by_symbol={"ZERO": 0},
        as_of=as_of,
    )
    assert "ZERO" not in out


def test_aux_panel_accepted_for_market_value():
    """CLI will pass market_value as aux_panel."""
    n = 80
    dfs = {s: _make_inst_frame(n_days=n, stock_id=s) for s in ("A", "B", "C")}
    as_of = pd.Timestamp(dfs["A"]["date"].iloc[-1]) + pd.Timedelta(days=3)
    mv = {s: 1e9 for s in dfs}
    out_kw = compute_foreign_broker_v2_universe(dfs, market_value_by_symbol=mv, as_of=as_of)
    out_aux = compute_foreign_broker_v2_universe(dfs, aux_panel=mv, as_of=as_of)
    assert set(out_kw.index) == set(out_aux.index)


def test_as_of_required_raises():
    with pytest.raises(ValueError):
        compute_foreign_broker_v2_universe({}, market_value_by_symbol={}, as_of=None)


def test_pivot_drops_duplicate_date_name_rows():
    """P1-4: duplicate (date, name) rows must not double-count via aggfunc='sum'.

    Simulates FinMind publishing two revisions for the same (date, name).
    Under the old 'sum' aggregation net would be 150 instead of the latest 50.
    """
    base = [
        {"date": pd.Timestamp("2024-04-10"), "stock_id": "1", "name": "Foreign_Investor",
         "buy": 100_000, "sell": 0},
        {"date": pd.Timestamp("2024-04-10"), "stock_id": "1", "name": "Foreign_Investor",
         "buy": 50_000, "sell": 0},
        {"date": pd.Timestamp("2024-04-11"), "stock_id": "1", "name": "Foreign_Investor",
         "buy": 30_000, "sell": 0},
    ]
    wide = _pivot_long_to_wide(pd.DataFrame(base))
    assert wide is not None
    # Last-wins: keep 50_000, not sum 150_000
    assert float(wide.loc[pd.Timestamp("2024-04-10"), "foreign_net"]) == 50_000.0
    assert float(wide.loc[pd.Timestamp("2024-04-11"), "foreign_net"]) == 30_000.0


def test_rank_stability_skips_small_universe_days():
    """P1-新7: days with < min_universe_size positive-net symbols must be skipped.

    Build a day-level panel where only 10 symbols have positive foreign net — fewer
    than MIN_UNIVERSE_FOR_RANK_STABILITY=50. The rank_stability sub-signal should
    remain unavailable for every symbol instead of giving a noisy 1.0 to the tiny
    positive subset.
    """
    from src.features.foreign_broker_v2 import _compute_rank_stability

    # Fabricate a wide frame for 10 symbols with 70 days of +1 foreign net each.
    n_days = 70
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    wide_by_symbol = {}
    for i in range(10):
        wide_by_symbol[f"S{i}"] = pd.DataFrame({
            "foreign_net": [1.0] * n_days,
            "trust_net": [0.0] * n_days,
            "dealer_self_net": [0.0] * n_days,
            "dealer_hedge_net": [0.0] * n_days,
        }, index=dates)
    market_value_by_symbol = {f"S{i}": 1e9 for i in range(10)}
    stability = _compute_rank_stability(
        wide_by_symbol,
        market_value_by_symbol,
        as_of=dates[-1] + pd.Timedelta(days=3),
        lag_days=2,
        min_history=60,
        lookback_days=60,
        top_pct=0.20,
    )
    # Every day has only 10 positive symbols → below default min_universe=50.
    # Because no day satisfies the threshold, day_counts stays at 0 for each
    # symbol and the returned dict must be empty (no noisy 1.0 assignment).
    assert stability == {}


def test_rank_stability_min_universe_yaml_override(monkeypatch):
    """follow-up-2: min_universe_size default flows from factor_thresholds.yaml.

    Pre-fix: module-level constant MIN_UNIVERSE_FOR_RANK_STABILITY=50 was
    bound into the function signature, so yaml edits had no effect. This
    test stubs the thresholds cache and verifies the helper respects it.
    """
    from src.features.foreign_broker_v2 import _compute_rank_stability
    from src.utils import thresholds

    fake = {
        "factor_ic": {
            "min_universe_size": {"rank_stability": 5},
        },
    }
    monkeypatch.setattr(thresholds, "_cache", fake, raising=False)
    monkeypatch.setattr(thresholds, "_cache_source", "test-stub", raising=False)

    n_days = 70
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    wide_by_symbol = {}
    for i in range(10):
        wide_by_symbol[f"S{i}"] = pd.DataFrame({
            "foreign_net": [1.0] * n_days,
            "trust_net": [0.0] * n_days,
            "dealer_self_net": [0.0] * n_days,
            "dealer_hedge_net": [0.0] * n_days,
        }, index=dates)
    market_value_by_symbol = {f"S{i}": 1e9 for i in range(10)}
    # Default (None) now reads yaml → 5, so 10 symbols/day > 5 → no skip.
    stability = _compute_rank_stability(
        wide_by_symbol,
        market_value_by_symbol,
        as_of=dates[-1] + pd.Timedelta(days=3),
        lag_days=2,
        min_history=60,
        lookback_days=60,
        top_pct=0.20,
        min_universe_size=None,
    )
    # With threshold relaxed to 5, at least some symbols get a positive score.
    assert stability
    assert all(0 <= v <= 1 for v in stability.values())


def test_rank_stability_responds_to_mv_normalisation():
    """Same raw foreign_net but different market values → the SMALLER-cap symbol
    (net / mv higher) should rank higher in rank_stability."""
    n = 80
    foreign = [0.0] * (n - 20) + [100_000.0] * 20
    dfs = {f"S{i}": _make_inst_frame(n_days=n, foreign_pattern=foreign, stock_id=f"S{i}")
           for i in range(3)}
    as_of = pd.Timestamp(dfs["S0"]["date"].iloc[-1]) + pd.Timedelta(days=3)
    out = compute_foreign_broker_v2_universe(
        dfs,
        # S0 has smallest market cap, so (net/mv) ratio largest → ranks top
        market_value_by_symbol={"S0": 1e8, "S1": 1e9, "S2": 1e10},
        as_of=as_of,
    )
    assert "S0" in out and "S2" in out
    assert out["S0"] > out["S2"]


# Codex R8-1 mutation-proof test ----------------------------------------------


def test_zscore_with_tolerance_fires_on_sub_tolerance_std():
    """R8-1 (rewrite after Codex Round 7 showed R7-2 wasn't mutation-proof):
    directly verify that `_zscore_with_tolerance` fires on std that is
    **greater than zero but below 1e-12** — the exact failure mode the
    R6-3 fix was supposed to catch.

    Codex's R7 mutation showed the previous R7-2 test's input collapsed
    to *exactly identical* values (`1e-15` step is below float epsilon of
    `1.0`, `1e-17` step is below epsilon of `10000.0`), so std was 0 and
    even the old `std == 0` exact-compare guard fired — the test passed
    under mutation, i.e. was useless.

    This version uses `1e-13` step which survives float representation
    at the 1.0 scale (cross-section std ≈ 1.00e-13, strictly > 0 and
    strictly < 1e-12). The old exact-compare would NOT fire here, and
    would emit pathological z-scores (±1.0 range). The new tolerance
    guard DOES fire → all 0.0.
    """
    from src.features.foreign_broker_v2 import _zscore_with_tolerance

    # Construction: 3 values where std ≈ 1e-13 (verified empirically).
    col = pd.Series([1.0, 1.0 + 1e-13, 1.0 + 2e-13], index=["A", "B", "C"])

    # Input must be provably in the "float-noise near-constant" band.
    std = col.std(ddof=1)
    assert 0 < std < 1e-12, (
        f"test setup invalid: std={std} not in (0, 1e-12). "
        f"Either Python float changed behaviour, or step is wrong."
    )

    # Under the R6-3 fix, guard fires → all zeros.
    result = _zscore_with_tolerance(col)
    assert all(abs(v) < 1e-9 for v in result), (
        f"tolerance guard did NOT fire for sub-tolerance std: {result.tolist()}"
    )

    # Mutation harness (pinned inline): a regression to exact-compare would
    # produce ±1.0-range z-scores that this assertion catches.
    def _zscore_mutated_exact_compare(col: pd.Series) -> pd.Series:
        clean = col.dropna()
        if len(clean) < 3:
            return pd.Series(0.0, index=col.index)
        std = clean.std(ddof=1)
        if std == 0 or pd.isna(std):  # OLD exact compare
            return pd.Series(0.0, index=col.index)
        return (col - clean.mean()) / std

    mutated = _zscore_mutated_exact_compare(col)
    # The regression's output must differ from the tolerance version so
    # this test would fail if someone reverts the guard in production.
    assert any(abs(v) > 0.5 for v in mutated), (
        "mutation harness is wrong: exact-compare mutation did not "
        "produce pathological z-scores for std=1e-13 input. Test is "
        "not mutation-proof against the regression it was designed to catch."
    )
    # Production output must be strictly flatter than the mutated version.
    assert result.abs().max() < mutated.abs().max(), (
        "production zscore is not distinct from mutation harness"
    )
