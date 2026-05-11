"""Mutation tests for P0-B (cum_ratio dollar-denominated) + P1-C (stale guard) +
P1-D (consistency weight=0) + P1-E (covered-weight rescale).

R26 audit established:
  - foreign_net unit = shares (TWSE T86 + FinMind 同款)
  - market_value unit = NTD (= TWSE shares × close per finmind.py:1033)
  - shares ÷ NTD = 1/price → cross-section price/scale bias
  - Fix: cum_dollar = (foreign_net × close).sum(); ratio = cum_dollar / mv (dimensionless)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.foreign_investor_v2 import (
    LAST20_MAX_CALENDAR_SPAN_DAYS,
    SUBSIGNAL_WEIGHTS,
    _compute_symbol_signals,
    compute_foreign_investor_v2_universe,
)


def _make_inst_frame(
    n_days: int,
    foreign_pattern: list[float] | None = None,
    trust_pattern: list[float] | None = None,
    stock_id: str = "9999",
    start: str = "2024-01-02",
) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=n_days)
    rows = []
    for i, d in enumerate(dates):
        f_net = foreign_pattern[i] if foreign_pattern and i < len(foreign_pattern) else 100_000 * ((-1) ** i)
        t_net = trust_pattern[i] if trust_pattern and i < len(trust_pattern) else 0
        for name, net in [
            ("Foreign_Investor", f_net),
            ("Investment_Trust", t_net),
            ("Dealer_self", 0),
            ("Dealer_Hedging", 0),
        ]:
            buy = max(net, 0)
            sell = max(-net, 0)
            rows.append({"date": d, "stock_id": stock_id, "name": name, "buy": buy, "sell": sell})
    return pd.DataFrame(rows)


def _make_close(n_days: int, close: float = 100.0, start: str = "2024-01-02") -> pd.Series:
    dates = pd.bdate_range(start=start, periods=n_days)
    return pd.Series([close] * n_days, index=dates)


# -----------------------------------------------------------------------------
# P0-B dollar denomination
# -----------------------------------------------------------------------------


def test_p0b_dollar_ratio_dimensionless():
    """foreign_cum_ratio = (Σ net_shares × close) / mv → dimensionless.

    Sanity: with foreign +1000 shares × 20 days × close 100 → cum_dollar = 2_000_000.
    mv = 1e9 → ratio = 0.002.
    """
    n = 80
    foreign_const_buy = [0.0] * (n - 20) + [1000.0] * 20
    df = _make_inst_frame(n_days=n, foreign_pattern=foreign_const_buy, stock_id="A")
    close = _make_close(n_days=n)
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)

    signals = _compute_symbol_signals(
        long_df=df, market_value=1e9, close_panel=close,
        as_of=as_of, lag_days=2, min_history=60,
    )
    assert "foreign_cum_ratio" in signals
    expected_dollar = 1000.0 * 100.0 * 20  # 2_000_000
    expected_ratio = expected_dollar / 1e9
    assert signals["foreign_cum_ratio"] == pytest.approx(expected_ratio, rel=1e-9)


def test_p0b_higher_close_yields_higher_ratio_same_shares():
    """Same net shares but different close → dollar ratio differs (higher close = higher ratio).

    Pre-P0-B (legacy shares/NTD): ratio identical regardless of close — stocks
    with tiny prices got artificially high scores. Post-P0-B: higher close means
    larger dollar amount in the numerator, mv same → bigger ratio.
    """
    n = 80
    foreign = [0.0] * (n - 20) + [1000.0] * 20
    df = _make_inst_frame(n_days=n, foreign_pattern=foreign, stock_id="A")
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)
    close_low = _make_close(n_days=n, close=10.0)
    close_high = _make_close(n_days=n, close=1000.0)

    sig_low = _compute_symbol_signals(df, 1e9, close_low, as_of, 2, 60)
    sig_high = _compute_symbol_signals(df, 1e9, close_high, as_of, 2, 60)
    assert sig_high["foreign_cum_ratio"] > sig_low["foreign_cum_ratio"], (
        f"P0-B regression: high-close ratio {sig_high['foreign_cum_ratio']} "
        f"not > low-close ratio {sig_low['foreign_cum_ratio']}"
    )


def test_p0b_close_panel_missing_skips_cum_ratio():
    """If close_panel=None, cum_ratio sub-signal must NOT be computed (would
    fall back to legacy shares/NTD which is dimensionally wrong)."""
    n = 80
    df = _make_inst_frame(n_days=n, stock_id="A")
    as_of = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=3)

    signals = _compute_symbol_signals(
        long_df=df, market_value=1e9, close_panel=None,
        as_of=as_of, lag_days=2, min_history=60,
    )
    assert "foreign_cum_ratio" not in signals
    assert "persistence" in signals  # other signals unaffected


# -----------------------------------------------------------------------------
# P1-C last20 stale guard
# -----------------------------------------------------------------------------


def test_p1c_stale_last20_drops_symbol():
    """If last 20 institutional rows span > LAST20_MAX_CALENDAR_SPAN_DAYS,
    symbol must be dropped entirely (not pollute cross-section)."""
    # Construct a frame where last 20 rows span ~ 100 calendar days (extreme stale)
    rows = []
    # 60 dense daily rows for min_history
    dense_dates = pd.bdate_range("2023-01-02", periods=60)
    sparse_dates = pd.date_range("2024-01-02", periods=20, freq="5D")  # 20 rows over 100 cal days
    for d in dense_dates:
        for name in ("Foreign_Investor", "Investment_Trust", "Dealer_self", "Dealer_Hedging"):
            rows.append({"date": d, "stock_id": "A", "name": name, "buy": 0, "sell": 0})
    for d in sparse_dates:
        for name in ("Foreign_Investor", "Investment_Trust", "Dealer_self", "Dealer_Hedging"):
            rows.append({"date": d, "stock_id": "A", "name": name, "buy": 100, "sell": 0})
    df = pd.DataFrame(rows)
    close = pd.Series([100.0] * (len(dense_dates) + len(sparse_dates)),
                       index=list(dense_dates) + list(sparse_dates))

    signals = _compute_symbol_signals(
        long_df=df, market_value=1e9, close_panel=close,
        as_of=pd.Timestamp("2024-04-01"), lag_days=2, min_history=60,
    )
    assert signals == {}, (
        f"P1-C regression: stale last20 (span > {LAST20_MAX_CALENDAR_SPAN_DAYS} days) "
        f"should drop symbol but got signals: {signals}"
    )


# -----------------------------------------------------------------------------
# P1-D consistency weight = 0
# -----------------------------------------------------------------------------


def test_p1d_consistency_weight_zero():
    """SUBSIGNAL_WEIGHTS['consistency'] must be 0 per H_a1 amendment 2026-05-10."""
    assert SUBSIGNAL_WEIGHTS["consistency"] == 0.0


def test_p1d_weight_sum_unchanged():
    """Total active weight after consistency drop should still equal 1.0
    (foreign_cum_ratio + persistence + rank_stability redistributed)."""
    active_total = sum(w for w in SUBSIGNAL_WEIGHTS.values() if w > 0)
    assert active_total == pytest.approx(1.0)


def test_subsignal_weights_yaml_in_sync_with_constant():
    """2026-05-11 R31 finding 4: the module now reads weights from
    `config/factor_thresholds.yaml :: factor_specific.foreign_investor_v2.weights`
    via `_subsignal_weights()`. Guard that the yaml and the module fallback
    constant stay in sync (so editing one without the other surfaces here).
    """
    from src.features.foreign_investor_v2 import _subsignal_weights
    yaml_weights = _subsignal_weights()
    assert yaml_weights == SUBSIGNAL_WEIGHTS, (
        "config/factor_thresholds.yaml weights drifted from SUBSIGNAL_WEIGHTS "
        f"constant: yaml={yaml_weights} vs constant={SUBSIGNAL_WEIGHTS}"
    )
    # consistency must be 0 in the LIVE source too (not just the constant)
    assert yaml_weights["consistency"] == 0.0


def test_last20_span_and_top_pct_yaml_resolved():
    """2026-05-11 R31 finding 4: last20 stale-guard span + rank_stability
    top-pct are yaml-driven; verify they resolve to the expected values."""
    from src.features.foreign_investor_v2 import (
        _last20_max_calendar_span_days,
        _rank_stability_top_pct,
    )
    assert _last20_max_calendar_span_days() == LAST20_MAX_CALENDAR_SPAN_DAYS == 35
    assert _rank_stability_top_pct() == pytest.approx(0.20)


# -----------------------------------------------------------------------------
# P1-E covered-weight rescale
# -----------------------------------------------------------------------------


def test_p1e_missing_subsignal_drops_below_50pct_coverage():
    """2026-05-10 P1-E: symbol with covered_weight < 0.5 must be DROPPED
    from output (not silently scored).

    R27 found previous version of this test only checked finite/no
    crash — too weak. This rewrite explicitly creates a symbol whose
    covered_weight = persistence (0.25) + consistency (0.0 weight) only,
    by withholding its close panel entry. cum_ratio + rank_stability both
    require close panel and are skipped → covered_weight = 0.25 < 0.5 → drop.
    """
    n = 80
    df_full = _make_inst_frame(n_days=n, stock_id="FULL")
    df_full2 = _make_inst_frame(n_days=n, stock_id="FULL2")
    df_missing = _make_inst_frame(n_days=n, stock_id="MISSING_CLOSE")

    # Provide close panel for FULL/FULL2 only — MISSING_CLOSE explicitly
    # absent so cum_ratio + rank_stability cannot be computed for it.
    close_panel = {
        "FULL": _make_close(n_days=n),
        "FULL2": _make_close(n_days=n),
        # MISSING_CLOSE intentionally omitted
    }

    as_of = pd.Timestamp(df_full["date"].iloc[-1]) + pd.Timedelta(days=3)

    out = compute_foreign_investor_v2_universe(
        {"FULL": df_full, "FULL2": df_full2, "MISSING_CLOSE": df_missing},
        market_value_by_symbol={"FULL": 1e9, "FULL2": 1e9, "MISSING_CLOSE": 1e9},
        as_of=as_of,
        close_by_symbol=close_panel,
    )

    # MISSING_CLOSE covered_weight = 0.25 (persistence only) < 0.5 → MUST be dropped
    assert "MISSING_CLOSE" not in out.index, (
        f"P1-E regression: MISSING_CLOSE has covered_weight=0.25 < 0.5 threshold "
        f"but appeared in output: composite={out.get('MISSING_CLOSE')}. "
        f"Old fillna(0) without rescale would silently include it."
    )
    # FULL/FULL2 should still be in output with finite composite
    for sym in ["FULL", "FULL2"]:
        assert sym in out.index, f"{sym} (full coverage) missing from output"
        assert np.isfinite(out[sym]), f"{sym} composite non-finite: {out[sym]}"
