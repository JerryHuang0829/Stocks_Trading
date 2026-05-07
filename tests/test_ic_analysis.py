"""Unit tests for src.analysis.ic_analysis (Phase A1 Pro IC Infrastructure)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.ic_analysis import (
    FactorICResult,
    bootstrap_ci,
    compute_period_ic_stats,
    compute_spearman_ic,
    deflated_sharpe_ratio,
    effective_n_cluster,
    factor_ic_report,
    fdr_correct,
    permutation_baseline,
    regime_conditional_ic,
    stationary_block_bootstrap_ci,
)


# ---------------------------- Spearman IC ----------------------------


def test_spearman_ic_perfect_positive():
    symbols = [f"s{i}" for i in range(10)]
    factor = pd.Series(range(10), index=symbols, dtype=float)
    returns = pd.Series([i * 0.01 for i in range(10)], index=symbols)
    ic = compute_spearman_ic(factor, returns)
    assert ic is not None
    assert ic == pytest.approx(1.0, abs=1e-9)


def test_spearman_ic_perfect_negative():
    symbols = [f"s{i}" for i in range(10)]
    factor = pd.Series(range(10), index=symbols, dtype=float)
    returns = pd.Series([-i * 0.01 for i in range(10)], index=symbols)
    ic = compute_spearman_ic(factor, returns)
    assert ic == pytest.approx(-1.0, abs=1e-9)


def test_spearman_ic_noisy():
    rng = np.random.default_rng(0)
    symbols = [f"s{i}" for i in range(50)]
    factor_vals = np.arange(50, dtype=float)
    noise = rng.normal(0, 5, 50)
    returns_vals = factor_vals + noise
    factor = pd.Series(factor_vals, index=symbols)
    returns = pd.Series(returns_vals, index=symbols)
    ic = compute_spearman_ic(factor, returns)
    assert ic is not None
    assert 0.4 < ic < 1.0  # noisy but still strongly positive


def test_spearman_ic_insufficient_samples():
    factor = pd.Series([1.0, 2.0], index=["a", "b"])
    returns = pd.Series([0.01, 0.02], index=["a", "b"])
    assert compute_spearman_ic(factor, returns) is None


def test_spearman_ic_misaligned_index_inner_join():
    factor = pd.Series([1.0, 2.0, 3.0, 4.0], index=["a", "b", "c", "d"])
    returns = pd.Series([0.01, 0.02, 0.03], index=["b", "c", "d"])  # drops 'a'
    ic = compute_spearman_ic(factor, returns)
    assert ic == pytest.approx(1.0, abs=1e-9)


# ---------------------------- Period stats ----------------------------


def test_period_ic_stats_basic():
    ics = [0.05, 0.03, 0.07, 0.02, 0.08, -0.01, 0.04, 0.06, 0.05, 0.03]
    stats = compute_period_ic_stats(ics)
    assert stats["n"] == 10
    assert stats["mean_ic"] == pytest.approx(0.042, abs=1e-3)
    assert stats["std_ic"] is not None and stats["std_ic"] > 0
    assert stats["ic_ir"] is not None and stats["ic_ir"] > 0
    assert stats["t_stat"] is not None
    assert stats["p_value"] is not None and 0 <= stats["p_value"] <= 1
    # t = mean/std * sqrt(n); here mean>0 → t positive
    assert stats["t_stat"] > 0


def test_period_ic_stats_empty_and_single():
    assert compute_period_ic_stats([])["n"] == 0
    single = compute_period_ic_stats([0.05])
    assert single["n"] == 1
    assert single["mean_ic"] == 0.05
    assert single["ic_ir"] is None


# ---------------------------- Bootstrap CI ----------------------------


def test_bootstrap_ci_seed_reproducible():
    ics = [0.02, -0.01, 0.05, 0.03, 0.04, -0.02, 0.06, 0.01, 0.00, 0.07]
    ci1 = bootstrap_ci(ics, n=500, seed=42)
    ci2 = bootstrap_ci(ics, n=500, seed=42)
    assert ci1 == ci2
    assert ci1[0] is not None and ci1[1] is not None
    assert ci1[0] < ci1[1]


def test_bootstrap_ci_insufficient_samples():
    assert bootstrap_ci([0.05, 0.03], n=100) == (None, None)


def test_bootstrap_ci_clamps_lower_bound_for_small_n():
    """P1-新3B: with small n_bootstrap, lo_idx = int(alpha/2 * n) floors to 0.

    Codex (follow-up-5) noted the previous version of this test only asserted
    `ci[0] <= ci[1]` which the un-fixed code would also satisfy. This version
    specifically exercises the under-flow scenario: n=20 bootstrap iterations
    with alpha=0.05 yields lo_idx = int(0.025*20) = 0, which happens to be a
    valid index; but a buggy `bootstrap_ci` that did `means[lo_idx-1]` would
    wrap around to the largest sample and produce CI inversion. We pin the
    deterministic output so a regression is caught.
    """
    ics = [0.01, -0.01, 0.02, 0.03, 0.0, -0.02, 0.04, 0.01, 0.0, 0.02]
    # Small n_bootstrap forces the clamp onto the hot path
    ci = bootstrap_ci(ics, n=20, seed=42)
    assert ci != (None, None)
    assert ci[0] is not None and ci[1] is not None
    # Lower bound must be <= upper bound even after floor-to-zero
    assert ci[0] <= ci[1]
    # Lower bound must correspond to the lowest bootstrap mean (index 0).
    # Since alpha/2 * 20 = 0.5 → int() = 0, and clamp max(0, 0) = 0 keeps it,
    # lo_idx = 0 regardless. A regression that used `-1` would wrap to the
    # upper tail and ci[0] would become >= ci[1].
    assert ci[0] < ci[1]  # strict: inversion would break this
    # Determinism check: same seed yields identical output
    ci2 = bootstrap_ci(ics, n=20, seed=42)
    assert ci == ci2


def test_bootstrap_ci_clamps_upper_bound_for_tiny_n():
    """Complement to the lower clamp: ensure `min(n-1, ...)` doesn't over-index.

    With n=5 bootstrap iterations and alpha=0.05:
      hi_idx = int(0.975 * 5) = 4 = n-1 (at the boundary, safe)
    Pre-fix: `hi_idx = int(0.975 * n)` without clamp would be 4 here too, so
    this test is a canary for future regressions where an off-by-one pushes
    hi_idx = n (IndexError).
    """
    ics = [0.01, -0.01, 0.02, 0.03, 0.0, -0.02, 0.04, 0.01, 0.0, 0.02]
    ci = bootstrap_ci(ics, n=5, seed=42)
    # Must not raise IndexError
    assert ci != (None, None)
    assert ci[0] is not None and ci[1] is not None
    assert ci[0] <= ci[1]


# ---------------------------- Stationary block bootstrap (P1-新3A) ----------------------------


def test_stationary_block_bootstrap_wider_than_iid_for_ar1():
    """Block bootstrap should produce wider CI than iid for autocorrelated series."""
    rng = np.random.default_rng(0)
    n_obs = 120
    rho = 0.5
    # Generate AR(1) series
    eps = rng.normal(0, 1, n_obs)
    x = np.empty(n_obs)
    x[0] = eps[0]
    for i in range(1, n_obs):
        x[i] = rho * x[i - 1] + eps[i]
    # Scale to typical IC magnitudes
    ics = (x * 0.02 + 0.03).tolist()

    iid_lo, iid_hi = bootstrap_ci(ics, n=2000, seed=42)
    block_lo, block_hi = stationary_block_bootstrap_ci(
        ics, n=2000, seed=42, avg_block_len=4.0,
    )
    assert None not in (iid_lo, iid_hi, block_lo, block_hi)
    iid_width = iid_hi - iid_lo
    block_width = block_hi - block_lo
    # Block width strictly wider by >=5% for moderate AR(1) serial correlation
    assert block_width > iid_width * 1.05


def test_stationary_block_bootstrap_reproducible():
    ics = [0.01, -0.01, 0.02, 0.03, 0.0, -0.02, 0.04, 0.01, 0.0, 0.02, 0.03]
    a = stationary_block_bootstrap_ci(ics, n=500, seed=7)
    b = stationary_block_bootstrap_ci(ics, n=500, seed=7)
    assert a == b


def test_stationary_block_bootstrap_insufficient_samples():
    assert stationary_block_bootstrap_ci([0.05, 0.03], n=100) == (None, None)


# ---------------------------- FDR ----------------------------


def test_fdr_correct_benjamini_hochberg():
    # Classic BH example: p = [0.01, 0.04, 0.03, 0.005]
    # Sorted: 0.005, 0.01, 0.03, 0.04 → ranks 1..4, m=4
    # adj: p_k * m / k → 0.02, 0.02, 0.04, 0.04 (with monotone enforcement)
    ps = [0.01, 0.04, 0.03, 0.005]
    adj = fdr_correct(ps)
    assert len(adj) == 4
    # Each adjusted p must be >= original unless capped at 1.0
    for orig, a in zip(ps, adj):
        assert a is not None
        assert a >= orig - 1e-9
    # Sorted by rank: smallest original p should have smallest adjusted p
    # 0.005 (min orig) → adj should be min
    min_idx = ps.index(min(ps))
    assert adj[min_idx] == min(adj)


def test_fdr_correct_handles_none():
    ps = [0.01, None, 0.03]
    adj = fdr_correct(ps)
    assert adj[1] is None
    assert adj[0] is not None and adj[2] is not None


# ---------------------------- Regime-conditional ----------------------------


def test_regime_conditional_ic_grouping():
    period_ics = [0.05, 0.03, 0.08, -0.01, 0.02, 0.06]
    regimes = [
        "trending_up", "trending_up", "trending_up",
        "ranging", "ranging", "trending_down",
    ]
    result = regime_conditional_ic(period_ics, regimes)
    assert "trending_up" in result
    assert result["trending_up"]["n"] == 3
    assert result["trending_up"]["mean_ic"] == pytest.approx(0.0533, abs=1e-3)
    assert result["ranging"]["n"] == 2
    assert result["trending_down"]["n"] == 1


def test_regime_conditional_ic_none_regime():
    period_ics = [0.05, None, 0.03]
    regimes = ["trending_up", "trending_up", None]
    result = regime_conditional_ic(period_ics, regimes)
    # None IC dropped; None regime keyed as "unknown"
    assert result["trending_up"]["n"] == 1
    assert result.get("unknown", {}).get("n") == 1


def test_regime_conditional_ic_length_mismatch_raises():
    with pytest.raises(ValueError):
        regime_conditional_ic([0.05, 0.03], ["trending_up"])


# ---------------------------- Permutation ----------------------------


def test_permutation_baseline_null_distribution():
    """Random factor (seed-independent) should have null mean IC near 0."""
    rng = np.random.default_rng(123)
    symbols = [f"s{i}" for i in range(30)]
    periods_factor = []
    periods_return = []
    for _ in range(5):
        factor = pd.Series(rng.normal(0, 1, 30), index=symbols)
        returns = pd.Series(rng.normal(0, 0.01, 30), index=symbols)
        periods_factor.append(factor)
        periods_return.append(returns)
    result = permutation_baseline(periods_factor, periods_return, n=100, seed=42)
    assert result["real_mean_ic"] is not None
    assert result["null_mean"] is not None
    assert abs(result["null_mean"]) < 0.1  # null centred near 0
    assert result["n_permutations"] > 0
    assert result["conclusion"] in (
        "significant_positive", "significant_negative", "not_significant",
    )


def test_permutation_per_period_independent_seed_reproducibility():
    """P1-新4: same base seed must yield deterministic null; different seed must shift it."""
    rng = np.random.default_rng(7)
    symbols = [f"s{i}" for i in range(20)]
    periods_factor = []
    periods_return = []
    for _ in range(4):
        periods_factor.append(pd.Series(rng.normal(0, 1, 20), index=symbols))
        periods_return.append(pd.Series(rng.normal(0, 0.01, 20), index=symbols))
    a = permutation_baseline(periods_factor, periods_return, n=30, seed=42)
    b = permutation_baseline(periods_factor, periods_return, n=30, seed=42)
    c = permutation_baseline(periods_factor, periods_return, n=30, seed=43)
    # Reproducible within a seed
    assert a["null_mean"] == b["null_mean"]
    assert a["null_std"] == b["null_std"]
    # But a different seed must realise a different null distribution
    assert a["null_mean"] != c["null_mean"]


def test_permutation_baseline_strong_signal_flagged_significant():
    """When factor == returns, real IC ≈ 1.0 and should dominate the null."""
    symbols = [f"s{i}" for i in range(30)]
    factor = pd.Series(np.arange(30, dtype=float), index=symbols)
    returns = pd.Series(np.arange(30, dtype=float) * 0.01, index=symbols)
    result = permutation_baseline([factor, factor, factor],
                                   [returns, returns, returns],
                                   n=100, seed=42)
    assert result["real_mean_ic"] == pytest.approx(1.0, abs=1e-6)
    assert result["percentile"] > 0.95
    assert result["conclusion"] == "significant_positive"


# R3-2 -----------------------------------------------------------------


def test_permutation_p_value_never_exactly_zero():
    """R3-2: `(count + 1) / (n + 1)` gives a discrete lower floor, even when
    the real IC beats every null draw. JSON consumers were previously
    misreading p_value_empirical=0 as 'absolute zero'."""
    symbols = [f"s{i}" for i in range(40)]
    # Construct a perfect-signal universe: factor exactly equals return each period
    periods_factor = []
    periods_return = []
    rng = np.random.default_rng(0)
    for _ in range(5):
        x = rng.normal(0, 1, 40)
        periods_factor.append(pd.Series(x, index=symbols))
        periods_return.append(pd.Series(x * 0.01, index=symbols))
    result = permutation_baseline(periods_factor, periods_return, n=50, seed=42)
    # Real IC dominates null — but p must stay ≥ floor = 2/(n+1)
    assert result["p_value_empirical"] is not None
    assert result["p_value_empirical"] > 0
    assert result["p_value_empirical"] >= result["p_value_empirical_floor"]
    assert result["p_value_empirical_floor"] == pytest.approx(2.0 / 51, abs=1e-4)


# ---------------------------- DSR + effective_n (P1-新5) ----------------------------


def test_deflated_sharpe_more_trials_lower_confidence():
    """More candidate strategies should make a given SR look less impressive."""
    low = deflated_sharpe_ratio(0.5, n_obs=60, n_trials=1)
    high = deflated_sharpe_ratio(0.5, n_obs=60, n_trials=20)
    assert low is not None and high is not None
    # n_trials=1 → SR_max_expected=0, so p(observed SR > 0) is high
    assert low >= high


def test_deflated_sharpe_degenerate_inputs_return_none():
    assert deflated_sharpe_ratio(0.5, n_obs=1, n_trials=5) is None
    assert deflated_sharpe_ratio(None, n_obs=60, n_trials=5) is None  # type: ignore[arg-type]
    assert deflated_sharpe_ratio(float("nan"), n_obs=60, n_trials=5) is None


def test_dsr_n_trials_required_explicit_v0_13_v1_1b():
    """V1.1b mutation test (Plan v7 H_d_v6 V0.13 Assertion 3): n_trials must be
    explicit kwarg. Silent default DEFAULT_DSR_N_TRIALS=5 retired to prevent
    over-claim in v7 cell sweep (n_trials越小 DSR越寬, silent default 5 用於
    18-cell sweep會 false PASS)."""
    import pytest
    # Reverting V1.1b enforcement (re-adding silent default = 5) → this test
    # would PASS without raise → catches the regression.
    with pytest.raises(ValueError, match="n_trials must be explicit"):
        deflated_sharpe_ratio(0.5, n_obs=60)  # type: ignore[call-arg]
    # n_trials=None explicit also raises (sentinel handling)
    with pytest.raises(ValueError, match="n_trials must be explicit"):
        deflated_sharpe_ratio(0.5, n_obs=60, n_trials=None)  # type: ignore[arg-type]


def test_effective_n_without_industry_uses_fallback():
    symbols = [f"s{i}" for i in range(100)]
    assert effective_n_cluster(symbols) == 50
    assert effective_n_cluster(symbols, fallback_ratio=0.3) == 30


def test_effective_n_with_industry_clustering_shrinks_n():
    # 100 symbols in 5 industries (20 per cluster)
    symbols = [f"s{i}" for i in range(100)]
    industry = {s: f"ind_{i // 20}" for i, s in enumerate(symbols)}
    eff = effective_n_cluster(symbols, industry)
    assert 0 < eff < 100
    # n / sqrt(avg_cluster) = 100 / sqrt(20) ≈ 22
    assert 15 <= eff <= 30


def test_effective_n_empty_universe_returns_zero():
    assert effective_n_cluster([]) == 0


# ---------------------------- effective_n as metadata (Codex Round 3.5) ----------------------------


def test_compute_period_ic_stats_df_is_always_n_minus_one():
    """After Codex Round 3.5: df = n_periods - 1 always (no override).

    Previously we wired `effective_n_override` into Student-t df but that
    mixed a cross-sectional symbol-cluster metric into a time-series t-test
    and, because effective_n > n_periods in practice, actually LOWERED the
    p-value (wrong direction of conservatism). Codex caught this with an
    8-period / effective_n=40 example: p dropped from 0.0331 to 0.0117.

    The correct policy is: df is always (n - 1). effective_n is recorded
    as JSON metadata only.
    """
    ics = [0.10, 0.08, 0.12, 0.09, 0.11, 0.07, 0.10, 0.08]
    r = compute_period_ic_stats(ics)
    assert r["t_df"] == len(ics) - 1
    # API should no longer accept `effective_n_override`
    with pytest.raises(TypeError):
        compute_period_ic_stats(ics, effective_n_override=3)  # type: ignore[call-arg]


def test_factor_ic_report_effective_n_is_metadata_only():
    """Industry-cluster shrinkage must NOT change the overall p-value.

    This is the direct antidote to Codex's Round 3.5 finding: compute two
    factor reports on the same period ICs, one with a heavily-clustered
    industry map and one with scattered (near-iid) labels, and verify that
    `overall.p_value` is identical between the two. Only the JSON metadata
    (top-level `effective_n`, `known_biases` wording) should differ.
    """
    rng = np.random.default_rng(42)
    symbols = [f"s{i}" for i in range(40)]
    periods = []
    dates = pd.date_range("2024-01-15", periods=8, freq="ME")
    for date in dates:
        factor_vals = rng.normal(0, 1, 40)
        returns_vals = 0.25 * factor_vals + rng.normal(0, 1, 40)
        periods.append((
            date,
            pd.Series(factor_vals, index=symbols),
            pd.Series(returns_vals * 0.01, index=symbols),
            "trending_up",
        ))
    clustered = {s: "same_industry" for s in symbols}
    scattered = {s: f"ind_{i}" for i, s in enumerate(symbols)}
    r_clustered = factor_ic_report("one_cluster", periods, n_permutation=30,
                                    industry_labels=clustered)
    r_scattered = factor_ic_report("unique_clusters", periods, n_permutation=30,
                                    industry_labels=scattered)
    # effective_n metadata differs (cross-sectional shrinkage)
    assert r_clustered.effective_n is not None
    assert r_scattered.effective_n is not None
    assert r_clustered.effective_n < r_scattered.effective_n
    # But p-value is IDENTICAL — cluster is metadata only, not wired into inference
    assert r_clustered.overall["p_value"] == r_scattered.overall["p_value"]
    assert r_clustered.overall["t_df"] == r_scattered.overall["t_df"] == 7
    # JSON echoes the metadata for post-hoc interpretation
    d = r_clustered.to_dict()
    assert d["effective_n"] is not None
    # R5-5: cross-sectional metadata is top-level only, not duplicated in overall
    assert "effective_n_cross_sectional" not in d["overall"]
    assert d["effective_n"] is not None
    # known_biases says "metadata only"
    biases = " | ".join(r_clustered.known_biases)
    assert "metadata only" in biases
    assert "no automatic cross-sectional shrinkage" in biases


def test_factor_ic_report_dsr_n_obs_is_time_series_n():
    """DSR `n_obs` must be the time-series period count, never effective_n.

    Mertens (2002) variance is parameterised by time-series observations;
    swapping in a cross-sectional symbol count (as the pre-Codex-3.5 version
    did) made var_SR shrink and DSR p drop — the same dimension confusion.
    """
    rng = np.random.default_rng(7)
    symbols = [f"s{i}" for i in range(30)]
    periods = []
    dates = pd.date_range("2024-01-15", periods=10, freq="ME")
    for date in dates:
        f = rng.normal(0, 1, 30)
        r = 0.2 * f + rng.normal(0, 1, 30)
        periods.append((
            date,
            pd.Series(f, index=symbols),
            pd.Series(r * 0.01, index=symbols),
            "trending_up",
        ))
    # Any industry labelling must not change dsr_n_obs
    dense = {s: "tech" for s in symbols}
    sparse = {s: f"i_{i}" for i, s in enumerate(symbols)}
    r_dense = factor_ic_report("dense", periods, n_permutation=30,
                                industry_labels=dense)
    r_sparse = factor_ic_report("sparse", periods, n_permutation=30,
                                 industry_labels=sparse)
    assert r_dense.deflated_sharpe_n_obs == len(periods)
    assert r_sparse.deflated_sharpe_n_obs == len(periods)
    # Therefore DSR (confidence, BLdP 2014) identical too
    assert r_dense.deflated_sharpe_ratio == r_sparse.deflated_sharpe_ratio


# ---------------------------- End-to-end ----------------------------


def test_factor_ic_report_stores_per_period_factor_scores():
    """Phase A2 Step 4-prep canonical fix: FactorICResult must retain
    per-period factor scores (symbol -> score dict) so that /ic-aggregate
    can compute cross-factor correlation downstream. Pre-fix Phase A1
    discarded the score vectors and only kept the scalar rank IC per period
    (documented as skipped in phase_a1_summary.md's correlation section)."""
    from src.analysis.ic_analysis import factor_ic_report

    rng = np.random.default_rng(11)
    symbols = [f"sym{i:03d}" for i in range(30)]
    periods = []
    dates = pd.date_range("2024-01-31", periods=3, freq="ME")
    for date in dates:
        factor_vals = rng.normal(0.2, 1.0, 30)
        returns_vals = rng.normal(0.0, 0.02, 30)
        factor = pd.Series(factor_vals, index=symbols)
        returns = pd.Series(returns_vals, index=symbols)
        periods.append((date, factor, returns, "ranging"))

    result = factor_ic_report("test_factor", periods, n_permutation=20)

    # Must store one dict per rebalance period, structurally aligned with period_ics
    assert len(result.period_factor_scores) == len(result.period_ics)
    for slot, period_ic in zip(result.period_factor_scores, result.period_ics):
        assert slot["rebalance_date"] == period_ic.rebalance_date
        assert isinstance(slot["scores"], dict)
        # n_symbols in period_ics is the *aligned* count — scores dict should match
        assert len(slot["scores"]) == period_ic.n_symbols
        # Keys are str symbol IDs, values are finite floats
        for sym, val in slot["scores"].items():
            assert isinstance(sym, str)
            assert isinstance(val, float)


def test_factor_ic_report_per_period_scores_round_trip_json():
    """Schema must serialize cleanly through JSON round-trip — critical
    for /ic-aggregate reading from reports/factor_ic/*.json."""
    from src.analysis.ic_analysis import factor_ic_report
    import json

    rng = np.random.default_rng(13)
    symbols = [f"stk{i:03d}" for i in range(20)]
    periods = []
    dates = pd.date_range("2024-06-30", periods=2, freq="ME")
    for date in dates:
        factor_vals = rng.normal(0, 1, 20)
        returns_vals = rng.normal(0, 0.02, 20)
        periods.append((
            date,
            pd.Series(factor_vals, index=symbols),
            pd.Series(returns_vals, index=symbols),
            "trending_up",
        ))

    result = factor_ic_report("rt_factor", periods, n_permutation=20)
    serialized = result.to_dict()
    round_tripped = json.loads(json.dumps(serialized))

    assert "period_factor_scores" in round_tripped
    assert len(round_tripped["period_factor_scores"]) == 2
    # After JSON round-trip scores dict keys are always str (JSON objects)
    first = round_tripped["period_factor_scores"][0]
    assert "rebalance_date" in first and "scores" in first
    assert all(isinstance(k, str) for k in first["scores"].keys())
    # Scores preserved to 6 decimal rounding (not truncated to int)
    assert any(v != int(v) for v in first["scores"].values())


def test_factor_ic_report_end_to_end():
    rng = np.random.default_rng(7)
    symbols = [f"s{i}" for i in range(40)]
    periods = []
    # Construct 6 rebalance periods across 2024-2025 with moderate positive signal
    dates = pd.date_range("2024-01-15", periods=6, freq="ME")
    regimes_cycle = ["trending_up", "ranging", "trending_down",
                     "trending_up", "trending_up", "ranging"]
    for date, regime in zip(dates, regimes_cycle):
        factor_vals = rng.normal(0, 1, 40)
        # Moderate positive relationship
        returns_vals = 0.3 * factor_vals + rng.normal(0, 1, 40)
        factor = pd.Series(factor_vals, index=symbols)
        returns = pd.Series(returns_vals * 0.01, index=symbols)
        periods.append((date, factor, returns, regime))

    result = factor_ic_report("synthetic_pos", periods, n_permutation=50)
    assert isinstance(result, FactorICResult)
    assert result.n_periods == 6
    assert result.overall["n"] == 6
    assert result.overall["mean_ic"] is not None and result.overall["mean_ic"] > 0
    assert "bootstrap_ci_95" in result.overall
    assert len(result.period_ics) == 6
    # Regime grouping populated
    assert set(result.by_regime.keys()) >= {"trending_up", "ranging", "trending_down"}
    # Permutation dict has required keys
    assert result.permutation["real_mean_ic"] is not None
    # Dict serialisation round-trips
    d = result.to_dict()
    assert d["factor_name"] == "synthetic_pos"
    assert d["return_basis"] == "price_only"
    assert len(d["period_ics"]) == 6
    # Phase A1 methodology-layer fields must be present in JSON schema
    assert d["bootstrap_method"] == "stationary_block"
    assert d["bootstrap_avg_block_len"] == pytest.approx(3.0)
    assert "deflated_sharpe_ratio" in d
    assert "effective_n" in d
    assert "fdr_period_level" in d
    assert d["fdr_period_level"]["method"] == "benjamini_hochberg"
    # Known-biases boilerplate auto-appended
    biases_joined = " | ".join(d["known_biases"])
    assert "effective_n" in biases_joined
    assert "stationary block" in biases_joined
    # R3-3 boilerplate must always be present
    assert "survivorship bias" in biases_joined
    assert "price-only" in biases_joined
    # R3-1 boilerplate: DSR moments announced (either empirical or fallback)
    assert ("DSR uses empirical" in biases_joined) or ("Gaussian moments" in biases_joined)
    # R3-4 / R3-5: per-period transparency fields present in serialised period_ics
    for p in d["period_ics"]:
        assert "tie_ratio" in p
        assert "n_excluded" in p
    # R3-1 DSR moments echoed in top-level JSON for reproducibility
    assert "deflated_sharpe_skewness" in d
    assert "deflated_sharpe_kurtosis" in d
    # Codex Round 3.5: new transparency fields
    assert "deflated_sharpe_n_obs" in d
    assert d["deflated_sharpe_n_obs"] == d["n_periods"]  # time-series n, NOT effective_n
    assert "deflated_sharpe_moments_estimated" in d
    assert d["overall"]["t_df"] == d["n_periods"] - 1  # time-series df
    # Codex R5-5: cross-sectional effective_n lives at top level only;
    # `overall` is time-series metadata. No duplicate serialisation.
    assert "effective_n_cross_sectional" not in d["overall"]
    assert "effective_n" in d
    # No duplicate survivorship wording in the serialised bias list
    lowered = [b.lower() for b in d["known_biases"]]
    assert sum(1 for b in lowered if "survivorship" in b) == 1


# R3-1 -----------------------------------------------------------------


def test_factor_ic_report_dsr_falls_back_to_gaussian_for_small_n():
    """R3-1: fewer than 4 period IC samples must fall back to (skew=0, kurt=3)."""
    symbols = [f"s{i}" for i in range(40)]
    periods = []
    dates = pd.date_range("2024-01-15", periods=3, freq="ME")
    rng = np.random.default_rng(0)
    for date in dates:
        factor_vals = rng.normal(0, 1, 40)
        returns_vals = 0.3 * factor_vals + rng.normal(0, 1, 40)
        periods.append((
            date,
            pd.Series(factor_vals, index=symbols),
            pd.Series(returns_vals * 0.01, index=symbols),
            "trending_up",
        ))
    r = factor_ic_report("tiny_series", periods, n_permutation=20)
    assert r.deflated_sharpe_skewness == 0.0
    assert r.deflated_sharpe_kurtosis == 3.0
    # Explicit flag so mutation tests can distinguish "fallback" from
    # "empirical with coincidentally Gaussian-like numbers".
    assert r.deflated_sharpe_moments_estimated is False
    biases = " | ".join(r.known_biases)
    assert "Gaussian moments" in biases


def test_factor_ic_report_dsr_uses_empirical_moments_when_available():
    """R3-1: >= 4 period ICs must yield empirically-estimated skew/kurtosis.

    We insert extreme period ICs to inflate the empirical kurtosis so it
    differs measurably from the Gaussian fallback (3.0).
    """
    symbols = [f"s{i}" for i in range(40)]
    dates = pd.date_range("2024-01-15", periods=12, freq="ME")
    rng = np.random.default_rng(0)
    # Build period IC distribution with 10 moderate + 2 outliers → leptokurtic
    target_ics = [0.2] * 10 + [1.0, -0.5]
    periods = []
    for date, target_ic in zip(dates, target_ics):
        factor_vals = rng.normal(0, 1, 40)
        signal = target_ic * factor_vals
        noise = rng.normal(0, abs(1 - abs(target_ic)) + 0.1, 40)
        returns_vals = signal + noise
        periods.append((
            date,
            pd.Series(factor_vals, index=symbols),
            pd.Series(returns_vals * 0.01, index=symbols),
            "trending_up",
        ))
    r = factor_ic_report("leptokurtic", periods, n_permutation=20)
    # Kurtosis must differ from Gaussian fallback (3.0) — empirical moments were used
    assert r.deflated_sharpe_kurtosis != 3.0
    # Explicit estimated flag so Codex-style mutation ("dsr_skew_used = 0.0")
    # and ("dsr_kurt_used = 3.0") are both detectable via a single assertion
    assert r.deflated_sharpe_moments_estimated is True
    biases = " | ".join(r.known_biases)
    assert "DSR uses empirical" in biases


def test_factor_ic_report_dsr_skew_mutation_caught(monkeypatch):
    """Codex C2 follow-up: the skew branch must be independently verifiable.

    Constructs a strongly skewed period IC distribution so that
    `r.deflated_sharpe_skewness` is **unambiguously non-zero**. A mutation
    like `dsr_skew_used = 0.0` (Codex's Round 3.5 attack) would make this
    assertion fail, closing the gap the earlier test left open.
    """
    symbols = [f"s{i}" for i in range(40)]
    dates = pd.date_range("2024-01-15", periods=12, freq="ME")
    rng = np.random.default_rng(0)
    # 10 near-zero IC periods + 2 strongly positive outliers → right-skewed
    target_ics = [0.02] * 10 + [0.8, 0.9]
    periods = []
    for date, target_ic in zip(dates, target_ics):
        factor_vals = rng.normal(0, 1, 40)
        # Put signal with chosen strength and very little noise in outliers
        # so the realised period IC tracks `target_ic` closely.
        signal_weight = target_ic
        noise_sd = 0.05 if target_ic > 0.5 else 1.0
        returns_vals = signal_weight * factor_vals + rng.normal(0, noise_sd, 40)
        periods.append((
            date,
            pd.Series(factor_vals, index=symbols),
            pd.Series(returns_vals * 0.01, index=symbols),
            "trending_up",
        ))
    r = factor_ic_report("right_skewed", periods, n_permutation=20)
    # Strong right-skew period ICs → empirical skew must be clearly > 0
    assert r.deflated_sharpe_skewness > 0.5, (
        f"expected empirical skew > 0.5 but got {r.deflated_sharpe_skewness}"
    )
    # And DSR (confidence) should differ from the Gaussian-default path on
    # the same IR because skew * SR term in Mertens variance is non-zero.
    # Recompute DSR with zero skew to confirm the two paths are distinguishable.
    dsr_empirical = r.deflated_sharpe_ratio
    dsr_zero_skew = deflated_sharpe_ratio(
        r.overall["ic_ir"],
        n_obs=r.deflated_sharpe_n_obs,
        n_trials=r.deflated_sharpe_n_trials,
        skewness=0.0,
        kurtosis=r.deflated_sharpe_kurtosis,
    )
    assert dsr_empirical != dsr_zero_skew, (
        "DSR collapses to the same value when skew is zeroed — test "
        "cannot distinguish empirical skew from Gaussian default."
    )


# R3-4 / R3-5 ----------------------------------------------------------


def test_factor_ic_report_records_tie_ratio_and_exclusion():
    """R3-4 + R3-5: high tie period surfaces tie_ratio>0.3 and n_excluded reflects alignment losses."""
    symbols = [f"s{i}" for i in range(20)]
    # Period 1: all returns tied at +1% (limit-up) — tie_ratio should be 1.0
    factor1 = pd.Series(range(20), index=symbols, dtype=float)
    returns1 = pd.Series([0.01] * 20, index=symbols)
    # Period 2: normal distribution, low tie ratio
    rng = np.random.default_rng(0)
    factor2 = pd.Series(rng.normal(0, 1, 20), index=symbols)
    returns2 = pd.Series(rng.normal(0, 0.01, 20), index=symbols)
    # Drop 5 symbols' returns in period 2 → n_excluded should reflect the drop
    returns2_dropped = returns2.iloc[:15]

    periods = [
        (pd.Timestamp("2024-01-15"), factor1, returns1, "ranging"),
        (pd.Timestamp("2024-02-15"), factor2, returns2_dropped, "ranging"),
    ]
    result = factor_ic_report("tie_bias_check", periods, n_permutation=20)
    # Period 1: tie_ratio near 1.0 (every return is identical)
    assert result.period_ics[0].tie_ratio is not None
    assert result.period_ics[0].tie_ratio >= 0.9
    # Period 2: low tie_ratio (continuous returns)
    assert result.period_ics[1].tie_ratio is not None
    assert result.period_ics[1].tie_ratio < 0.3
    # Period 2: 5 symbols excluded via inner-join alignment
    assert result.period_ics[1].n_excluded == 5
    # boilerplate mentions high tie warning
    biases = " | ".join(result.known_biases)
    assert "tie_ratio > 0.3" in biases


def test_factor_ic_report_respects_industry_labels_for_effective_n():
    rng = np.random.default_rng(11)
    symbols = [f"s{i}" for i in range(30)]
    industry_labels = {s: f"ind_{i // 6}" for i, s in enumerate(symbols)}  # 5 clusters
    periods = []
    for date in pd.date_range("2024-01-15", periods=4, freq="ME"):
        factor = pd.Series(rng.normal(0, 1, 30), index=symbols)
        returns = pd.Series(rng.normal(0, 0.01, 30), index=symbols)
        periods.append((date, factor, returns, "trending_up"))

    baseline = factor_ic_report("f", periods, n_permutation=20)
    clustered = factor_ic_report(
        "f", periods, n_permutation=20, industry_labels=industry_labels,
    )
    # With industry clustering effective_n is smaller than the fallback 0.5 * n
    assert clustered.effective_n is not None and baseline.effective_n is not None
    assert clustered.effective_n < baseline.effective_n


# ---------------------------- Codex Round 5 mutation-proof tests ----------------------------


def test_zero_variance_guard_tolerates_float_noise():
    """R5-2: a series of identical-value ICs must be flagged as zero-variance.

    Pre-R5 used `sd == 0` exact comparison. Codex showed that [0.2,0.2,0.2]
    still produced ic_ir ≈ 5.88e15 because of accumulation noise. The fix
    uses `sd < 1e-12` so float round-off falls into the guard branch.

    Self-audit note (Claude Round 5.5): an earlier version of this test used
    `step = 1e-18` which is below the float epsilon of 0.1 (~1e-17), so the
    Python runtime silently collapsed the list back to exact-constant. The
    test was only exercising the exact-zero branch — the tolerance branch
    was never actually reached. Step widened to `1e-14` so the list has
    measurable variance (std ≈ 1.6e-14) that still lies below the 1e-12
    guard threshold.
    """
    # Three identical values
    r = compute_period_ic_stats([0.2, 0.2, 0.2])
    assert r["std_ic"] == 0.0
    assert r["ic_ir"] is None
    assert r["t_stat"] is None
    assert r["p_value"] is None

    # A near-constant series with measurable but below-tolerance variance.
    # 1e-14 step → std ≈ 1.6e-14 > 0 but < 1e-12 tolerance → guard fires.
    near_constant = [0.1 + i * 1e-14 for i in range(5)]
    # Sanity-check the construction: the list must actually differ to
    # exercise the tolerance branch (not the exact-zero branch).
    assert len(set(near_constant)) > 1, (
        "near_constant collapsed to exact-constant; pick a larger step"
    )
    r2 = compute_period_ic_stats(near_constant)
    assert r2["std_ic"] == 0.0  # tolerance kicks in → reported as exact 0
    assert r2["ic_ir"] is None
    assert r2["p_value"] is None


def test_std_ic_preserves_precision_when_sd_is_tiny():
    """Codex R6-2: `round(sd, 4)` would collapse sd=1.58e-10 to 0.0, creating
    a serialised contradiction (std_ic=0.0 next to ic_ir=6.3e8). The fix
    uses `round(sd, 10)` so microscopic-but-above-guard standard deviations
    remain visible and downstream readers can reconcile the statistics.
    """
    # sd ≈ 1.58e-10 — above the 1e-12 guard, so ic_ir is real
    vals = [0.1 + i * 1e-10 for i in range(5)]
    r = compute_period_ic_stats(vals)
    assert r["ic_ir"] is not None
    # std_ic must be non-zero (Codex's failure mode): rounding to 10 digits
    # preserves the 1.58e-10 scale instead of showing 0.0.
    assert r["std_ic"] != 0.0, (
        f"std_ic collapsed to 0.0 despite sd being above the guard threshold; "
        f"this creates a serialised contradiction with ic_ir={r['ic_ir']}"
    )
    # Must be strictly positive at the actual scale
    assert 0 < r["std_ic"] < 1e-9


def test_zero_variance_guard_allows_small_but_measurable_sd():
    """Boundary companion to the tolerance test: std just ABOVE 1e-12 must
    produce a real ic_ir / p_value, not be swallowed by the guard.

    This pins the upper end of the tolerance window so a future regression
    that widens the guard (e.g. `sd < 1e-6`) is caught.

    Self-audit note: `std_ic` in the output dict is `round(sd, 4)`, so tiny
    sd values (~1e-10) are DISPLAYED as 0.0 even when the guard does not
    fire. Correctness therefore lives in `ic_ir` and `p_value` being
    non-None (the guard branch sets them to None).
    """
    # Step 1e-10 → std on [0.1, 0.1+1e-10, ...] is ~1.58e-10, comfortably
    # above the 1e-12 guard.
    above_tol = [0.1 + i * 1e-10 for i in range(5)]
    assert len(set(above_tol)) > 1  # measurable variance
    r = compute_period_ic_stats(above_tol)
    # Guard MUST NOT fire — ic_ir and p_value must be real numbers.
    # (std_ic is rounded to 4 decimals in the output, so it appears 0.0.)
    assert r["ic_ir"] is not None, "guard incorrectly fired: ic_ir is None"
    assert r["p_value"] is not None, "guard incorrectly fired: p_value is None"
    assert r["t_stat"] is not None, "guard incorrectly fired: t_stat is None"


def test_dedup_preserves_caller_custom_survivorship_note():
    """R5-3: caller's unrelated note containing 'survivorship' must NOT
    suppress the canonical boilerplate.

    Pre-R5 used substring keyword match → "my custom survivorship note" would
    swallow the standard boilerplate. Post-R5 dedup only triggers when BOTH
    sides match a canonical phrase (e.g. 'universe drawn from local cache
    scan').
    """
    symbols = [f"s{i}" for i in range(20)]
    periods = []
    for date in pd.date_range("2024-01-15", periods=5, freq="ME"):
        rng = np.random.default_rng(0)
        f = pd.Series(rng.normal(0, 1, 20), index=symbols)
        r = pd.Series(rng.normal(0, 0.01, 20), index=symbols)
        periods.append((date, f, r, "trending_up"))

    result = factor_ic_report(
        "custom_bias", periods, n_permutation=20,
        known_biases=["my custom survivorship note from the caller"],
    )
    biases = " || ".join(result.known_biases)
    # Caller note survives
    assert "my custom survivorship note from the caller" in biases
    # Standard boilerplate also present (substring match didn't suppress it)
    assert "universe drawn from local cache scan" in biases


def test_dedup_suppresses_canonical_duplicate():
    """R5-3 companion: if caller passes a canonical-phrase survivorship
    string (copy-paste from older version of this module), the boilerplate
    version IS correctly suppressed."""
    symbols = [f"s{i}" for i in range(20)]
    periods = []
    for date in pd.date_range("2024-01-15", periods=5, freq="ME"):
        rng = np.random.default_rng(1)
        f = pd.Series(rng.normal(0, 1, 20), index=symbols)
        r = pd.Series(rng.normal(0, 0.01, 20), index=symbols)
        periods.append((date, f, r, "trending_up"))

    result = factor_ic_report(
        "canonical_bias", periods, n_permutation=20,
        known_biases=["cache-scan universe (survivorship bias: delisted tickers absent)"],
    )
    # Exactly one survivorship bias entry
    survivorship_entries = [b for b in result.known_biases
                             if "survivorship" in b.lower()]
    assert len(survivorship_entries) == 1


def test_tie_ratio_none_on_degenerate_input():
    """R5-4: _estimate_tie_ratio returns None (not 0.0) on degenerate input.

    Previously empty / single-value / all-NaN inputs returned 0.0, silently
    masking 'unknown' as 'no ties'.
    """
    from src.analysis.ic_analysis import _estimate_tie_ratio
    assert _estimate_tie_ratio([]) is None
    assert _estimate_tie_ratio([np.nan, np.nan, np.nan]) is None
    assert _estimate_tie_ratio([0.5]) is None  # single observation

    # Two distinct values → 0.0 (proper zero-ties result)
    assert _estimate_tie_ratio([0.1, 0.2]) == 0.0
    # Two identical → 1.0 (all tied)
    assert _estimate_tie_ratio([0.1, 0.1]) == 1.0


def test_per_panel_min_obs_yaml_dict_is_respected():
    """R5-1: production YAML ships a per-panel dict under
    universe.min_obs_per_symbol. Quarterly panels must get the intended
    small threshold (12) rather than the daily default (250)."""
    from src.utils import thresholds

    # Force a reload from the real repo yaml (not test monkeypatch)
    thresholds._cache = None
    thresholds._cache_source = None
    data = thresholds.load_factor_thresholds(reload=True)
    quarterly_bar = thresholds.per_panel_min_obs("quarterly_eps")
    revenue_bar = thresholds.per_panel_min_obs("revenue")
    ohlcv_bar = thresholds.per_panel_min_obs("ohlcv")
    # Post-R5-1 per-panel values
    assert quarterly_bar == 12, f"quarterly_eps expected 12, got {quarterly_bar}"
    assert revenue_bar == 24, f"revenue expected 24, got {revenue_bar}"
    assert ohlcv_bar == 250


def test_per_panel_min_obs_scalar_override_has_safety_net(monkeypatch):
    """R5-1 defensive fallback: if someone writes
    `universe.min_obs_per_symbol: 250` as a scalar (Codex-observed
    regression mode), per_panel_min_obs must still work and fall back to
    the scalar rather than raising."""
    from src.utils import thresholds

    fake = {
        "universe": {
            "min_obs_per_symbol": 250,  # scalar regression
        },
    }
    monkeypatch.setattr(thresholds, "_cache", fake, raising=False)
    # Reset the one-shot warning flag so the test is idempotent
    if hasattr(thresholds.per_panel_min_obs, "_warned_scalar"):
        monkeypatch.setattr(
            thresholds.per_panel_min_obs, "_warned_scalar", False, raising=False,
        )
    # Scalar fallback treats 250 as default for every panel (undesirable but
    # doesn't crash; the warning log is emitted once to surface the misuse).
    assert thresholds.per_panel_min_obs("ohlcv") == 250
    assert thresholds.per_panel_min_obs("quarterly_eps") == 250
    assert thresholds.per_panel_min_obs("revenue") == 250
