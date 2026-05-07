"""Unit tests for src.features.revenue_momentum_v2."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.revenue_momentum_v2 import (
    SUBSIGNAL_WEIGHTS,
    compute_revenue_momentum_v2,
    compute_revenue_momentum_v2_universe,
)


def _make_revenue(
    start: str = "2022-01-01",
    n: int = 36,
    base: float = 1_000_000.0,
    growth_per_month: float = 0.0,
    seasonality: list[float] | None = None,
    noise: float = 0.0,
    seed: int = 0,
) -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=n, freq="MS")
    rng = np.random.default_rng(seed)
    revenue = []
    for i, d in enumerate(dates):
        value = base * (1 + growth_per_month) ** i
        if seasonality:
            value *= seasonality[d.month - 1]
        if noise:
            value *= 1 + rng.normal(0, noise)
        revenue.append(max(value, 1.0))
    return pd.DataFrame({"date": dates, "revenue": revenue})


# ---------------------------- PIT ----------------------------


def test_pit_filters_unpublished_months():
    # Frame includes the month that would only be legally published *after*
    # as_of - 45 days. The latest usable month should be older than as_of - 45d.
    df = _make_revenue(start="2023-01-01", n=30)
    # as_of = 2025-03-01 → cutoff = 2025-01-15 → 2025-01-01 just barely fits, 2025-02-01 must not
    as_of = pd.Timestamp("2025-03-01")
    out = compute_revenue_momentum_v2(df, as_of=as_of)
    # Sanity: composite computed, but n_months should not include post-cutoff rows
    # df dates extend 2023-01 to 2025-06 (30 months)
    # cutoff = 2025-01-15 → allows 2023-01 through 2025-01 (25 months)
    assert out["n_months"] == 25


def test_pit_empty_when_all_post_cutoff():
    df = _make_revenue(start="2026-04-01", n=12)
    as_of = pd.Timestamp("2026-04-16")  # cutoff = 2026-03-02 → drops everything
    out = compute_revenue_momentum_v2(df, as_of=as_of)
    assert out["score"] is None
    assert out["n_months"] == 0


# ---------------------------- YoY / Accel ----------------------------


def test_yoy_positive_growth():
    # 1% monthly growth → YoY ≈ (1.01^12 - 1) ≈ 0.1268
    df = _make_revenue(start="2023-01-01", n=30, growth_per_month=0.01)
    as_of = pd.Timestamp("2025-12-15")  # cutoff ≈ 2025-10-31, allows 2025-06
    out = compute_revenue_momentum_v2(df, as_of=as_of)
    assert out["yoy"] is not None
    assert out["yoy"] > 0.10  # decisively positive
    assert out["yoy"] < 0.20


def test_accel_positive_when_recent_beats_prior_3m():
    # Step-up in last 3 months
    base_values = [1_000_000.0] * 9 + [2_000_000.0] * 3  # 12 months, last 3 doubled
    df = pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=12, freq="MS"),
        "revenue": base_values,
    })
    as_of = pd.Timestamp("2024-06-15")  # all 12 months are pre-cutoff
    out = compute_revenue_momentum_v2(df, as_of=as_of, min_months=12)
    assert out["accel"] is not None
    # recent 3m = 2M, prev 3m = 1M → accel = 1.0
    assert out["accel"] == pytest.approx(1.0, abs=0.01)


# ---------------------------- Percentile ----------------------------


def test_percentile_rank_at_all_time_high():
    # Strictly increasing → latest is highest → percentile = +1
    df = _make_revenue(start="2021-01-01", n=36, growth_per_month=0.02)
    as_of = pd.Timestamp("2025-03-01")  # cutoff allows 2024-12 onwards
    out = compute_revenue_momentum_v2(df, as_of=as_of)
    assert out["percentile"] is not None
    assert out["percentile"] > 0.9


# ---------------------------- Seasonal z-score ----------------------------


def test_seasonal_zscore_strong_positive_surprise():
    """Recent months break above their own calendar-month distribution."""
    # 36 months with Q4 seasonality, then inflate last 3 months substantially
    seasonality = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1, 1.1, 1.3, 1.4, 1.5]
    dates = pd.date_range("2022-01-01", periods=36, freq="MS")
    vals = []
    rng = np.random.default_rng(42)
    for i, d in enumerate(dates):
        v = 1_000_000.0 * seasonality[d.month - 1] * (1 + rng.normal(0, 0.02))
        vals.append(v)
    # Bump the latest 3 months way above their peers
    vals[-1] *= 1.5
    vals[-2] *= 1.5
    vals[-3] *= 1.5
    df = pd.DataFrame({"date": dates, "revenue": vals})
    as_of = pd.Timestamp("2024-12-31")  # ensure last ~33 months are pre-cutoff
    out = compute_revenue_momentum_v2(df, as_of=as_of)
    assert out["seasonal_z"] is not None
    assert out["seasonal_z"] > 2.0


def test_seasonal_zscore_none_when_history_too_short():
    df = _make_revenue(start="2024-01-01", n=18)
    as_of = pd.Timestamp("2025-08-15")
    out = compute_revenue_momentum_v2(df, as_of=as_of)
    # n_months ~ 18 - 1 (cutoff) → seasonal needs 24 + 3; should be None
    assert out["seasonal_z"] is None


# ---------------------------- Composite ----------------------------


def test_composite_weighted_average_respects_weights():
    # Construct a frame where all 4 subsignals are computable and predictable
    # Strictly increasing growth → yoy, accel, percentile all positive
    df = _make_revenue(start="2020-01-01", n=60, growth_per_month=0.02, noise=0.01, seed=7)
    as_of = pd.Timestamp("2025-06-30")  # cutoff 2025-05-16 → allows up to 2025-04 or so
    out = compute_revenue_momentum_v2(df, as_of=as_of)
    # All subsignals should be non-None given 58+ months of data
    assert out["score"] is not None
    assert all(out[k] is not None for k in ("yoy", "accel", "percentile", "seasonal_z"))
    # Composite must lie within the range of the subsignals
    vals = [out["yoy"], out["accel"], out["percentile"], out["seasonal_z"]]
    assert min(vals) - 1e-9 <= out["score"] <= max(vals) + 1e-9


def test_composite_handles_partial_none_subsignals():
    # Only 15 months of history: yoy, accel possible; percentile & seasonal None
    df = _make_revenue(start="2024-01-01", n=15, growth_per_month=0.01)
    as_of = pd.Timestamp("2025-05-01")
    out = compute_revenue_momentum_v2(df, as_of=as_of, min_months=13)
    # percentile needs 24+3; seasonal needs 24+3 → both None here
    assert out["percentile"] is None
    assert out["seasonal_z"] is None
    # Score should still be computable from yoy/accel alone
    if out["yoy"] is not None and out["accel"] is not None:
        expected = (
            out["yoy"] * SUBSIGNAL_WEIGHTS["yoy"]
            + out["accel"] * SUBSIGNAL_WEIGHTS["accel"]
        ) / (SUBSIGNAL_WEIGHTS["yoy"] + SUBSIGNAL_WEIGHTS["accel"])
        assert out["score"] == pytest.approx(expected, abs=1e-9)


# ---------------------------- Universe batch ----------------------------


def test_universe_batch_drops_insufficient_history():
    rich = _make_revenue(start="2020-01-01", n=60, growth_per_month=0.01)
    short = _make_revenue(start="2025-01-01", n=5)  # way under min_history
    series = compute_revenue_momentum_v2_universe(
        {"LONG": rich, "SHORT": short},
        as_of=pd.Timestamp("2025-12-01"),
    )
    assert "LONG" in series
    assert "SHORT" not in series


def test_yoy_strict_month_matching_no_fallback():
    """P1-新6: latest month 2026-02 with missing 2025-02 must NOT fall back to 2025-01."""
    # Construct a frame including 2025-01 (which is ±45 days of 2025-02) but
    # EXCLUDE 2025-02 itself. Old tolerance logic would substitute 2025-01.
    # Strict year/month must return None for YoY.
    dates_kept = [
        pd.Timestamp(d)
        for d in pd.date_range("2024-01-01", "2026-02-01", freq="MS")
        if d != pd.Timestamp("2025-02-01")
    ]
    revenue = [1_000_000.0 * (1.01 ** i) for i in range(len(dates_kept))]
    df = pd.DataFrame({"date": dates_kept, "revenue": revenue})
    as_of = pd.Timestamp("2026-04-20")  # cutoff ≈ 2026-03-06, so 2026-02 is kept
    out = compute_revenue_momentum_v2(df, as_of=as_of, min_months=13)
    # Latest usable month is 2026-02; same-month prior year 2025-02 is missing
    # → strict matching returns None for YoY sub-signal.
    assert out["yoy"] is None


def test_yoy_strict_month_matches_exact_prior_year():
    """P1-新6 positive: when 2025-02 IS present, YoY must pair with it, not an adjacent month."""
    dates = pd.date_range("2024-01-01", "2026-02-01", freq="MS")
    # Make 2025-02 an outlier low (so if YoY erroneously used 2025-01 or 2025-03,
    # the result would be dramatically different).
    revenue = []
    for d in dates:
        if d == pd.Timestamp("2025-02-01"):
            revenue.append(500_000.0)  # half of the trend level
        else:
            revenue.append(1_000_000.0)
    df = pd.DataFrame({"date": dates, "revenue": revenue})
    as_of = pd.Timestamp("2026-04-20")
    out = compute_revenue_momentum_v2(df, as_of=as_of, min_months=13)
    # Latest (2026-02) = 1_000_000; strict prior-year same-month (2025-02) = 500_000
    # → YoY = 1.0 exactly. Any fallback to adjacent month would give 0.0.
    assert out["yoy"] == pytest.approx(1.0, abs=1e-9)


def test_zero_revenue_base_handled_gracefully():
    # Construct a frame where the yoy base revenue is zero — should not crash,
    # yoy subsignal becomes None, other subsignals may still compute.
    dates = pd.date_range("2023-01-01", periods=30, freq="MS")
    revenue = [1_000_000.0] * len(dates)
    revenue[17] = 0.0  # would be the "12 months ago" base for latest
    df = pd.DataFrame({"date": dates, "revenue": revenue})
    as_of = pd.Timestamp("2025-09-01")  # latest usable month ~ 2025-06
    out = compute_revenue_momentum_v2(df, as_of=as_of, min_months=12)
    # Should not raise; score may be None if all subsignals ended up None
    assert isinstance(out, dict)
    assert "yoy" in out


# Codex R6-3 companion --------------------------------------------------


def test_seasonal_z_handles_near_constant_peer_window():
    """R6-3: `seasonal_z` with near-constant peer revenue (float noise std
    ~ 1e-14) must return None, not seasonal_z=7e13.

    Pre-fix used `sd == 0` exact compare which missed float accumulation
    noise. The R6-3 tolerance (sd < 1e-12) catches it.
    """
    # 36 months where every same-calendar-month peer is near-constant
    # (base + microscopic noise), then a huge jump in the latest row.
    # The seasonal z-score on the target month must refuse to emit a z.
    dates = pd.date_range("2022-01-01", periods=36, freq="MS")
    values = []
    for i in range(36):
        # Peers for month M across years are base + tiny noise; latest row
        # (month 36, i.e. Dec 2024) is identical.
        values.append(1_000_000.0 + i * 1e-12)
    # Replace the last row to force seasonal_z to attempt computation
    values[-1] = 2_000_000.0
    df = pd.DataFrame({"date": dates, "revenue": values})
    out = compute_revenue_momentum_v2(
        df, as_of=pd.Timestamp("2025-01-15"), min_months=12
    )
    # With peer std ~ 1e-14, guard must fire → seasonal_z is None
    assert out["seasonal_z"] is None, (
        f"near-constant seasonal peers produced seasonal_z={out['seasonal_z']} "
        f"instead of None; float tolerance guard not applied"
    )
