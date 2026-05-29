"""Pro IC Infrastructure — Spearman IC, bootstrap CI, FDR, regime-conditional, permutation.

All functions are pure (no I/O, no network). Designed for full-universe IC
research where each period supplies independent factor/return Series.

Conventions:
    - Spearman IC: rank correlation between factor score and forward return
    - IC_IR (information ratio): mean_ic / std_ic (per-observation; NOT annualized)
    - t-stat: mean_ic / std_ic * sqrt(n), with two-sided p-value from Student-t
    - Bootstrap CI: defaults to Politis-Romano stationary block bootstrap for
      autocorrelated period IC; iid bootstrap retained for backward-compat
    - FDR: Benjamini-Hochberg at alpha=0.05 by default
    - Permutation: shuffle factor scores within each period, per (iter, period)
      independent seed to avoid serial correlation in null distribution
    - DSR: Bailey-Lopez de Prado (2014) multi-trial deflated Sharpe ratio
    - effective_n: cross-sectional cluster adjustment (industry or fallback)
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats

BOOTSTRAP_DEFAULT_N = 1000
PERMUTATION_DEFAULT_N = 300
DEFAULT_SEED = 42
DEFAULT_AVG_BLOCK_LEN = 3.0
# No default DSR n_trials: all callers MUST explicitly pass n_trials kwarg —
# silent default fallback removed to prevent over-claim (smaller n_trials makes
# DSR more lenient, so a silent default would false-PASS multi-trial sweeps).
EULER_MASCHERONI = 0.5772156649


@dataclass
class PeriodIC:
    """One rebalance period's IC observation.

    Transparency fields:
        tie_ratio: fraction of symbols with duplicate forward-return values.
            Spearman uses average-ranking for ties but information is lost;
            period with tie_ratio > 0.3 is flagged unreliable.
        n_excluded: how many symbols entered the universe for this period but
            were dropped before ranking (NaN factor, NaN return, stale price
            beyond max_gap_days, etc.). Enables diagnostics of periods where
            selection bias dominates.
    """

    rebalance_date: str
    bucket: str
    regime: str | None
    n_symbols: int
    rank_ic: float | None
    tie_ratio: float | None = None
    n_excluded: int | None = None


@dataclass
class FactorICResult:
    factor_name: str
    return_basis: str
    period_ics: list[PeriodIC]
    overall: dict
    by_regime: dict
    by_bucket: dict
    permutation: dict
    fdr_adjusted_p: float | None
    n_periods: int
    n_symbols_avg: float
    known_biases: list[str] = field(default_factory=list)
    # Methodology-layer fields
    bootstrap_method: str = "stationary_block"
    bootstrap_avg_block_len: float = DEFAULT_AVG_BLOCK_LEN
    deflated_sharpe_ratio: float | None = None
    # int | None records the actual passed value (None = unset); silent default
    # DSR n_trials retired to prevent over-claim.
    deflated_sharpe_n_trials: int | None = None
    # Empirical skew / Pearson kurtosis fed into DSR (default Gaussian
    # fallback). `deflated_sharpe_moments_estimated` distinguishes "really
    # estimated" from "fell back silently" so mutation tests can
    # pin down which branch executed.
    deflated_sharpe_skewness: float = 0.0
    deflated_sharpe_kurtosis: float = 3.0
    deflated_sharpe_moments_estimated: bool = False
    # n_obs used by DSR (time-series n_periods, NOT effective_n). Recorded
    # for transparency to avoid dimension confusion with cross-sectional size.
    deflated_sharpe_n_obs: int | None = None
    # Cross-sectional cluster shrinkage value. METADATA ONLY — never wired
    # into p-value or DSR (see known_biases).
    effective_n: int | None = None
    fdr_period_level: list[float | None] = field(default_factory=list)
    fdr_method: str = "benjamini_hochberg"
    # Per-period factor scores kept for downstream
    # cross-factor correlation analysis (/ic-aggregate skill). Each entry is
    # {"rebalance_date": "YYYY-MM-DD", "scores": {symbol: factor_score}}. Only
    # symbols that survived factor + forward-return alignment (i.e. the subset
    # used to compute that period's rank IC) are stored — ensures correlation
    # analysis operates on the same aligned universe as IC itself.
    period_factor_scores: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "factor_name": self.factor_name,
            "return_basis": self.return_basis,
            "n_periods": self.n_periods,
            "n_symbols_avg": round(self.n_symbols_avg, 2),
            "overall": self.overall,
            "by_regime": self.by_regime,
            "by_bucket": self.by_bucket,
            "permutation": self.permutation,
            "fdr_adjusted_p": self.fdr_adjusted_p,
            "fdr_period_level": {
                "values": self.fdr_period_level,
                "method": self.fdr_method,
            },
            "deflated_sharpe_ratio": self.deflated_sharpe_ratio,
            "deflated_sharpe_n_trials": self.deflated_sharpe_n_trials,
            "deflated_sharpe_n_obs": self.deflated_sharpe_n_obs,
            "deflated_sharpe_skewness": round(self.deflated_sharpe_skewness, 4),
            "deflated_sharpe_kurtosis": round(self.deflated_sharpe_kurtosis, 4),
            "deflated_sharpe_moments_estimated": self.deflated_sharpe_moments_estimated,
            "effective_n": self.effective_n,
            "bootstrap_method": self.bootstrap_method,
            "bootstrap_avg_block_len": self.bootstrap_avg_block_len,
            "known_biases": self.known_biases,
            "period_ics": [
                {
                    "rebalance_date": p.rebalance_date,
                    "bucket": p.bucket,
                    "regime": p.regime,
                    "n_symbols": p.n_symbols,
                    "rank_ic": None if p.rank_ic is None else round(p.rank_ic, 4),
                    "tie_ratio": (
                        None if p.tie_ratio is None else round(p.tie_ratio, 4)
                    ),
                    "n_excluded": p.n_excluded,
                }
                for p in self.period_ics
            ],
            "period_factor_scores": self.period_factor_scores,
        }


def bucket_for(ts: pd.Timestamp) -> str:
    """Annual + half-year bucket (reuses convention from analyze_factor_ic.py)."""
    year = ts.year
    half = "H1" if ts.month <= 6 else "H2"
    if year in (2024, 2025, 2026):
        return f"{year}-{half}"
    return str(year)


def _clean_floats(values: Iterable) -> list[float]:
    return [float(v) for v in values if v is not None and not pd.isna(v)]


def compute_spearman_ic(factor: pd.Series, future_returns: pd.Series) -> float | None:
    """Spearman rank correlation between factor and realised return.

    Aligns on the shared index first; returns None when < 3 usable pairs.
    """
    if factor is None or future_returns is None:
        return None
    aligned = pd.concat([factor, future_returns], axis=1, join="inner").dropna()
    if len(aligned) < 3:
        return None
    x = aligned.iloc[:, 0].to_numpy(dtype=float)
    y = aligned.iloc[:, 1].to_numpy(dtype=float)
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    corr, _p = stats.spearmanr(x, y)
    if pd.isna(corr):
        return None
    return float(corr)


def _estimate_tie_ratio(values: Sequence[float]) -> float | None:
    """Fraction of samples that share a value with at least one other.

    Used to flag periods dominated by ties (e.g., mass limit-down days where
    many symbols share the exact same forward return). scipy.stats.spearmanr
    still computes a valid rank correlation via average ranking but the
    information content degrades; callers should treat tie_ratio > 0.3 as
    reduced confidence.

    Returns **None** when the input is degenerate (empty, all
    NaN, or single observation) so callers can distinguish "cannot
    evaluate" from "zero ties". A degenerate input must not return
    0.0, which would silently mask all-NaN periods as tie-free.
    """
    arr = np.array([float(v) for v in values if v is not None and not pd.isna(v)])
    if arr.size < 2:
        return None
    _, counts = np.unique(arr, return_counts=True)
    tied = int(np.sum(counts[counts > 1]))
    return float(tied) / float(arr.size)


def compute_period_ic_stats(ics: Sequence[float | None]) -> dict:
    """Summary stats for a list of period ICs (time-series).

    Returns: mean, std, IC_IR, t_stat, p_value, n, t_df.
    Uses Student-t for p-value (exact small-sample, superior to normal approx).

    ADR — time-series df, not cross-sectional
    -----------------------------------------------------------------------
    Earlier revisions accepted a cross-sectional cluster override that
    replaced Student-t ``df = n - 1`` with ``effective_n - 1``. That was a
    dimension confusion: ``n`` here is a **time-series** sample size
    (number of rebalance periods), while the cross-sectional effective size
    is a **symbol-cluster** shrinkage. In production the cross-sectional
    effective size is almost always **larger** than ``n_periods``, so
    forcing ``df`` to use it made the t-test **less conservative** (smaller
    p-values) — the opposite of the intended effect.

    Correct policy (this version):
        * df is always `n - 1` (the time-series df for an IC mean test).
        * `effective_n_cluster` remains useful as JSON metadata and a known-bias
          note reminding readers that cross-sectional dependence is NOT baked
          into this p-value.
        * Future proper Moulton-style correction should shrink `std_ic`, not
          `df`, and is left as a future refinement.
    """
    clean = _clean_floats(ics)
    n = len(clean)
    if n == 0:
        return {
            "mean_ic": None, "std_ic": None, "ic_ir": None,
            "t_stat": None, "p_value": None, "n": 0, "t_df": None,
        }
    mu = sum(clean) / n
    if n == 1:
        return {
            "mean_ic": round(mu, 4), "std_ic": None, "ic_ir": None,
            "t_stat": None, "p_value": None, "n": 1, "t_df": None,
        }
    sd = math.sqrt(sum((v - mu) ** 2 for v in clean) / (n - 1))
    # `sd == 0` exact comparison is brittle against float noise.
    # A constant series like [0.2, 0.2, 0.2] can yield sd ≈ 1e-17 depending on
    # accumulation order, which produces ic_ir ~ 5.88e15 and
    # p_value = 0.0. Use a tight absolute tolerance so numerically-degenerate
    # inputs are treated as "no variation".
    if sd < 1e-12:
        return {
            "mean_ic": round(mu, 4), "std_ic": 0.0, "ic_ir": None,
            "t_stat": None, "p_value": None, "n": n, "t_df": n - 1,
        }
    ic_ir = mu / sd
    t_stat = ic_ir * math.sqrt(n)
    df = n - 1
    p_value = 2.0 * float(stats.t.sf(abs(t_stat), df=df))
    # `round(sd, 4)` collapses microscopic-but-above-guard
    # standard deviations (e.g. 1.58e-10) to 0.0 while ic_ir / t_stat /
    # p_value still carry the real signal (ic_ir ~ 6.3e8). Downstream
    # readers then see `std_ic=0.0` next to "statistically significant"
    # stats and cannot reconcile them. Preserve ten decimal places so the
    # serialised std_ic matches the scale used in the t-stat calculation.
    return {
        "mean_ic": round(mu, 4),
        "std_ic": round(sd, 10),
        "ic_ir": round(ic_ir, 4),
        "t_stat": round(t_stat, 3),
        "p_value": round(p_value, 4),
        "n": n,
        "t_df": df,
    }


def bootstrap_ci(
    ics: Sequence[float | None],
    n: int = BOOTSTRAP_DEFAULT_N,
    seed: int = DEFAULT_SEED,
    alpha: float = 0.05,
) -> tuple[float | None, float | None]:
    """IID bootstrap (1-alpha) CI for mean IC. Seed fixed for reproducibility.

    Retained for backward compatibility; for autocorrelated period IC series
    (which is the common case) prefer `stationary_block_bootstrap_ci`.
    """
    clean = _clean_floats(ics)
    if len(clean) < 3:
        return (None, None)
    rng = random.Random(seed)
    sample_size = len(clean)
    means = []
    for _ in range(n):
        s = [clean[rng.randrange(sample_size)] for _ in range(sample_size)]
        means.append(sum(s) / sample_size)
    means.sort()
    # Clamp both bounds to valid index range so alpha/2 * n floor to 0
    # does not underflow when n is small.
    lo_idx = max(0, int((alpha / 2) * n))
    hi_idx = min(n - 1, int((1 - alpha / 2) * n))
    return (round(means[lo_idx], 4), round(means[hi_idx], 4))


def stationary_block_bootstrap_ci(
    ics: Sequence[float | None],
    *,
    n: int = BOOTSTRAP_DEFAULT_N,
    avg_block_len: float = DEFAULT_AVG_BLOCK_LEN,
    alpha: float = 0.05,
    seed: int = DEFAULT_SEED,
) -> tuple[float | None, float | None]:
    """Politis-Romano (1994) stationary block bootstrap for autocorrelated series.

    Block length ~ Geometric(1 / avg_block_len). At each step either advance
    to the next index (with prob 1 - 1/L) or jump to a fresh random index
    (with prob 1/L). This preserves short-run dependence better than iid
    resampling and widens CI for positively autocorrelated inputs.

    Returns (lo, hi) of the (1-alpha) CI for the mean, or (None, None) when
    fewer than 3 usable observations.
    """
    clean_arr = np.array(_clean_floats(ics), dtype=float)
    m = clean_arr.shape[0]
    if m < 3:
        return (None, None)
    if avg_block_len <= 1.0:
        # Degenerates to iid bootstrap; caller should use `bootstrap_ci` instead.
        avg_block_len = 1.0
    p_jump = 1.0 / avg_block_len
    rng = np.random.default_rng(seed)
    means = np.empty(n, dtype=float)
    for i in range(n):
        sample = np.empty(m, dtype=float)
        idx = int(rng.integers(0, m))
        jumps = rng.random(m)
        for j in range(m):
            sample[j] = clean_arr[idx]
            if jumps[j] < p_jump:
                idx = int(rng.integers(0, m))
            else:
                idx = (idx + 1) % m
        means[i] = sample.mean()
    means.sort()
    lo_idx = max(0, int((alpha / 2) * n))
    hi_idx = min(n - 1, int((1 - alpha / 2) * n))
    return (round(float(means[lo_idx]), 4), round(float(means[hi_idx]), 4))


def deflated_sharpe_ratio(
    observed_sr: float,
    *,
    n_obs: int,
    n_trials: int | None = None,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float | None:
    """Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio (DSR).

    Returns Ψ = Φ(z) where z = (observed_sr - E[max SR | n_trials, no skill])
    / σ_SR, following BLdP 2014 Eq. (6). Ψ is a **confidence** in [0, 1]:

        Ψ ≥ 0.95 → strong evidence observed SR beats the null's best-of-n
        Ψ ≈ 0.50 → observed SR indistinguishable from the null's expected max
        Ψ ≤ 0.05 → observed SR is WORSE than even the null's expected max
                   (NOT significant; do not flip to treat as a p-value)

    Decision rule: ``deflated_sharpe_ratio >= 0.95`` for skill,
    NOT ``< 0.05`` (which would be inverted — this function is a confidence,
    not a p-value).

    Args:
        observed_sr: the realised Sharpe / IC-IR to test
        n_obs: number of periods used to compute `observed_sr`
        n_trials: number of candidate strategies / factors considered
        skewness / kurtosis: of the underlying returns (default Gaussian)

    Returns None when inputs are pathological (n_obs <= 1, n_trials < 1, or
    the variance estimate is non-positive).

    Raises ValueError when n_trials is None — silent default removed to
    prevent over-claim (a smaller n_trials makes DSR more lenient, so a
    silent default would false-PASS multi-trial sweeps). Pass n_trials
    explicitly (= number of candidate strategies / factors considered).

    Edge cases
    ----------
    - ``n_trials`` must be a positive integer family size. ``int`` and
      whole-valued ``float`` (e.g. ``400.0``) are accepted; fractional floats
      (``400.5``), ``bool``, and non-finite values (``inf`` / ``nan``) raise
      ``ValueError`` — a fractional or boolean trial count signals a config
      bug, not a value to silently normalize.
    - ``observed_sr < 0`` is mathematically valid: Ψ → 0 as observed_sr drops
      below E[max SR_0]. A near-zero Ψ for negative SR means "this strategy
      under-performs even the null's best-of-N expected SR" — it is NOT a
      computation failure. Factor sign errors typically surface here.
    - ``n_obs ≤ 1`` or ``n_trials < 1`` → returns None (degenerate variance).

    Formula structure (two-stage, units explicit)
    ---------------------------------------------
    Stage 1 — compute z_max in STANDARD-NORMAL units (Gumbel asymp):
        z_max ≈ √(2·ln N) - (γ + ln(ln N)) / (2·√(2·ln N))
    Stage 2 — convert to SR units and form the z-score:
        E[max SR_0] = σ_SR × z_max          # SR units
        z           = (observed_sr - E[max SR_0]) / σ_SR
                    = observed_sr / σ_SR - z_max
    Ψ = Φ(z).

    Comparison with BLdP 2014 Eq.(6) quantile-mixture form:
        z_max_eq6 ≈ (1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))
    For small N (e.g. N=12) Gumbel asymp gives ≈1.8957 vs Eq.(6) ≈1.6648
    (true Monte-Carlo value ≈1.6297). Both quantities are in
    standard-normal units. Gumbel asymp is slightly more conservative;
    both are valid extreme-value approximations and the gap closes for
    N → ∞. DSR verdicts (Ψ ≥ 0.95 → skill) are robust to this choice
    within the parameter ranges relevant to this project
    (n_obs 50-250, n_trials 5-18).

    The ``test_deflated_sharpe_golden_n12_gumbel_asymp`` and
    ``test_deflated_sharpe_unit_consistency_mutation`` tests lock both
    the Gumbel form AND the unit-correct stage-2 application; any
    switch must update formula and tests together.
    """
    if n_trials is None:
        raise ValueError(
            "deflated_sharpe_ratio: n_trials must be explicit kwarg "
            "(Plan v7 H_d_v6 V0.13 Assertion 3 enforcement). Silent default "
            "DEFAULT_DSR_N_TRIALS=5 retired in V1.1b. Pass n_trials=5 "
            "(Phase A1 single-factor) or n_trials=18 (v7 cell sweep)."
        )
    if isinstance(n_trials, bool):
        raise ValueError(
            f"deflated_sharpe_ratio: n_trials must be an integer family size, "
            f"not bool ({n_trials!r}) — bool silently coerces to 0/1."
        )
    if isinstance(n_trials, float):
        if not math.isfinite(n_trials):
            raise ValueError(
                f"deflated_sharpe_ratio: n_trials must be finite, got {n_trials!r}"
            )
        if not n_trials.is_integer():
            raise ValueError(
                f"deflated_sharpe_ratio: n_trials must be a whole number, got "
                f"fractional {n_trials!r} (fractional family size is a config bug)"
            )
    try:
        n_trials = int(n_trials)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(
            f"deflated_sharpe_ratio: n_trials must be int-coercible, got "
            f"{type(n_trials).__name__}={n_trials!r}"
        )
    if n_obs <= 1 or n_trials < 1 or observed_sr is None or pd.isna(observed_sr):
        return None
    if n_trials == 1:
        z_max = 0.0
    else:
        ln_n = math.log(n_trials)
        if ln_n <= 0:
            z_max = 0.0
        else:
            sqrt_two_ln_n = math.sqrt(2.0 * ln_n)
            # Gumbel asymp expected MAX of N standard-normal variables —
            # in STANDARD NORMAL units (NOT SR units). Convert to SR units
            # by multiplying by sigma_SR below.
            z_max = (
                sqrt_two_ln_n
                - (EULER_MASCHERONI + math.log(max(ln_n, 1e-12))) / (2.0 * sqrt_two_ln_n)
            )
    # Mertens (2002) variance of the Sharpe ratio estimator under null.
    var_sr = (
        1.0
        - skewness * observed_sr
        + (kurtosis - 1.0) / 4.0 * observed_sr ** 2
    ) / (n_obs - 1)
    if var_sr <= 0 or not math.isfinite(var_sr):
        return None
    sigma_sr = math.sqrt(var_sr)
    # z_max is in standard-normal units; convert to SR units before z-scoring
    # against observed_sr (BLdP 2014: return mu + sigma * maxZ, mu=0 under null).
    expected_max_sr = sigma_sr * z_max
    z = (observed_sr - expected_max_sr) / sigma_sr
    return round(float(stats.norm.cdf(z)), 4)


def effective_n_cluster(
    symbols: Sequence[str],
    industry_labels: Mapping[str, str] | None = None,
    *,
    fallback_ratio: float = 0.5,
) -> int:
    """Cross-sectional effective sample size under clustering.

    When `industry_labels` is provided, n_eff = n / sqrt(avg_cluster_size)
    (a mild shrinkage that recognises positive within-cluster correlation
    without collapsing n to the number of clusters). When missing, fall back
    to max(1, floor(n * fallback_ratio)); `fallback_ratio` defaults to 0.5
    which is roughly the sqrt-of-n shrinkage for typical TWSE universes.

    Returns >=1 integer.
    """
    n = len(symbols)
    if n <= 0:
        return 0
    if not industry_labels:
        return max(1, int(n * fallback_ratio))
    clusters = Counter(industry_labels.get(s, "UNKNOWN") for s in symbols)
    if not clusters:
        return max(1, int(n * fallback_ratio))
    avg_cluster_size = n / len(clusters)
    if avg_cluster_size <= 1.0:
        return n
    return max(1, int(n / math.sqrt(avg_cluster_size)))


def fdr_correct(p_values: Sequence[float], alpha: float = 0.05) -> list[float]:
    """Benjamini-Hochberg adjusted p-values.

    Returns list aligned to input order. None/NaN inputs pass through as None.
    """
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None and not pd.isna(p)]
    if not indexed:
        return [None] * len(p_values)
    m = len(indexed)
    indexed_sorted = sorted(indexed, key=lambda t: t[1])
    adjusted = [None] * len(p_values)
    # BH: q_i = min_{k>=i}( p_(k) * m / k ), enforcing monotone non-increasing from the tail
    running_min = float("inf")
    for rank in range(m, 0, -1):
        orig_idx, p = indexed_sorted[rank - 1]
        q = p * m / rank
        running_min = min(running_min, q)
        adjusted[orig_idx] = round(min(running_min, 1.0), 4)
    return adjusted


def regime_conditional_ic(
    period_ics: Sequence[float | None],
    regimes: Sequence[str | None],
) -> dict:
    """Group period ICs by regime, return {regime: stats_dict} for each bucket.

    The previous cross-sectional cluster override has been removed because
    mixing cross-sectional cluster shrinkage into a time-series t-test
    inverted the conservatism direction. Each regime now simply runs
    `compute_period_ic_stats` on its own time-series sample with
    df = (n_regime_periods - 1). See the ADR in `compute_period_ic_stats`
    for the full rationale.
    """
    if len(period_ics) != len(regimes):
        raise ValueError("period_ics and regimes must have matching length")
    groups: dict[str, list[float]] = {}
    for ic, regime in zip(period_ics, regimes):
        if ic is None or pd.isna(ic):
            continue
        key = regime if regime else "unknown"
        groups.setdefault(key, []).append(float(ic))
    return {
        regime: compute_period_ic_stats(ics)
        for regime, ics in groups.items()
    }


def permutation_baseline(
    factor_by_period: Sequence[pd.Series],
    returns_by_period: Sequence[pd.Series],
    n: int = PERMUTATION_DEFAULT_N,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Shuffle factor scores within each period, build null distribution of mean IC.

    Each (iter_id, period_id) pair gets an independent `default_rng`
    derived from `base_seed + iter_id * n_periods + period_id`. Sharing a single
    RNG across the nested loops induces serial correlation in the null and
    biases the empirical p-value optimistically by 15-25%.
    """
    if len(factor_by_period) != len(returns_by_period):
        raise ValueError("factor_by_period and returns_by_period lengths must match")

    # Real mean IC
    real_ics = [
        compute_spearman_ic(f, r)
        for f, r in zip(factor_by_period, returns_by_period)
    ]
    real_clean = _clean_floats(real_ics)
    if not real_clean:
        return {
            "real_mean_ic": None, "null_mean": None, "null_std": None,
            "percentile": None, "p_value_empirical": None,
            "p_value_empirical_floor": None,
            "conclusion": "insufficient_data", "n_permutations": n,
        }
    real_mean = sum(real_clean) / len(real_clean)

    # Pre-align each period once (deterministic, independent of iter_id)
    aligned_pairs: list[tuple[pd.Series, pd.Series] | None] = []
    for f, r in zip(factor_by_period, returns_by_period):
        aligned = pd.concat([f, r], axis=1, join="inner").dropna()
        if len(aligned) < 3:
            aligned_pairs.append(None)
        else:
            aligned_pairs.append((aligned.iloc[:, 0], aligned.iloc[:, 1]))

    n_periods = max(1, len(aligned_pairs))
    null_means: list[float] = []
    for iter_id in range(n):
        period_ics: list[float] = []
        for period_id, pair in enumerate(aligned_pairs):
            if pair is None:
                continue
            factor_aligned, returns_aligned = pair
            # Independent seed per (iter, period) avoids null-distribution serial
            # correlation that biases empirical p-value optimistically.
            period_rng = np.random.default_rng(
                seed + iter_id * n_periods + period_id
            )
            shuffled = period_rng.permutation(
                factor_aligned.to_numpy(dtype=float)
            )
            ic = compute_spearman_ic(
                pd.Series(shuffled, index=factor_aligned.index),
                returns_aligned,
            )
            if ic is not None:
                period_ics.append(ic)
        if period_ics:
            null_means.append(sum(period_ics) / len(period_ics))

    if not null_means:
        return {
            "real_mean_ic": round(real_mean, 4), "null_mean": None, "null_std": None,
            "percentile": None, "p_value_empirical": None,
            "p_value_empirical_floor": None,
            "conclusion": "empty_null", "n_permutations": n,
        }

    null_arr = np.array(null_means)
    total = len(null_arr)
    count_below = int((null_arr < real_mean).sum())
    count_above = int((null_arr > real_mean).sum())
    percentile = float(count_below) / total
    # Two-sided empirical p with discrete lower bound (North, Curtis &
    # Sham 2002). Plain (count/n) can return p=0 when real_mean beats every
    # null draw, which JSON readers then misread as "absolute zero". Using
    # (count + 1) / (n + 1) guarantees p_emp >= 2 / (n + 1) — the true
    # resolution limit for n_permutations draws.
    lower_tail = (count_below + 1) / (total + 1)
    upper_tail = (count_above + 1) / (total + 1)
    p_emp = min(1.0, 2.0 * min(lower_tail, upper_tail))
    p_emp_min = 2.0 / (total + 1)  # resolution floor for this n_permutations

    if p_emp < 0.05 and real_mean > 0:
        conclusion = "significant_positive"
    elif p_emp < 0.05 and real_mean < 0:
        conclusion = "significant_negative"
    else:
        conclusion = "not_significant"

    return {
        "real_mean_ic": round(real_mean, 4),
        "null_mean": round(float(null_arr.mean()), 4),
        "null_std": round(float(null_arr.std(ddof=1)), 4),
        "percentile": round(percentile, 4),
        "p_value_empirical": round(p_emp, 4),
        # Lowest p-value distinguishable with this many permutations. Use
        # when interpreting "p_value_empirical close to floor" as
        # "statistically indistinguishable from max-possible evidence".
        "p_value_empirical_floor": round(p_emp_min, 4),
        "conclusion": conclusion,
        "n_permutations": len(null_means),
    }


def factor_ic_report(
    factor_name: str,
    period_data: Sequence[tuple[pd.Timestamp, pd.Series, pd.Series, str | None]],
    return_basis: str = "price_only",
    n_permutation: int = PERMUTATION_DEFAULT_N,
    known_biases: list[str] | None = None,
    *,
    bootstrap_avg_block_len: float = DEFAULT_AVG_BLOCK_LEN,
    dsr_n_trials: int = 5,
    industry_labels: Mapping[str, str] | None = None,
    effective_n_fallback_ratio: float = 0.5,
) -> FactorICResult:
    """Assemble full IC report for a single factor.

    period_data: list of (rebalance_date, factor_series, forward_returns, regime).
                 Both Series are indexed by symbol.

    Methodology notes:
        * CI uses Politis-Romano stationary block bootstrap
        * Adds deflated Sharpe p-value, effective_n, bucket-level FDR
        * Permutation null uses per (iter, period) independent seed

    `dsr_n_trials` has a default here only for single-factor backward
    compatibility. Multi-trial cell sweeps MUST NOT use this function for DSR
    computation — they must call `deflated_sharpe_ratio()` directly with an
    explicit `n_trials` kwarg (deflated_sharpe_ratio() raises on None to
    enforce). This split:
      - single-factor IC via this function: dsr_n_trials default OK
      - deflated_sharpe_ratio (direct call): MUST pass explicit n_trials kwarg
    """
    period_ics_records: list[PeriodIC] = []
    period_ics_float: list[float | None] = []
    regimes: list[str | None] = []
    factor_by_period: list[pd.Series] = []
    returns_by_period: list[pd.Series] = []
    n_symbols: list[int] = []
    symbols_seen: set[str] = set()
    # Per-period factor scores for /ic-aggregate
    # cross-factor correlation analysis. Populated below from `aligned` so only
    # symbols that actually participated in the rank IC are included.
    period_factor_scores: list[dict] = []

    for rebalance_date, factor, fwd_returns, regime in period_data:
        ic = compute_spearman_ic(factor, fwd_returns)
        aligned = pd.concat([factor, fwd_returns], axis=1, join="inner").dropna()
        n_sym = len(aligned)
        # How many symbols were dropped during alignment (NaN factor /
        # NaN return / stale price). Computed against the *union* of inputs
        # so the diagnostic reflects selection pressure from the full intended
        # universe, not just the factor-populated subset.
        factor_index = getattr(factor, "index", None)
        returns_index = getattr(fwd_returns, "index", None)
        if factor_index is not None and returns_index is not None:
            union_size = len(factor_index.union(returns_index))
        else:
            union_size = n_sym
        n_excluded = max(0, union_size - n_sym)
        # Tie ratio on forward returns (ties in returns are the
        # common failure mode on mass limit-up/down days; ties in factor also
        # count). `_estimate_tie_ratio` returns None on degenerate input
        # (all-NaN / n<2), so max() must skip None to avoid TypeError.
        tie_ratio = None
        if n_sym >= 2:
            candidates = [
                t for t in (
                    _estimate_tie_ratio(aligned.iloc[:, 1].tolist()),
                    _estimate_tie_ratio(aligned.iloc[:, 0].tolist()),
                )
                if t is not None
            ]
            tie_ratio = max(candidates) if candidates else None
        bucket = bucket_for(pd.Timestamp(rebalance_date))
        period_ics_records.append(
            PeriodIC(
                rebalance_date=pd.Timestamp(rebalance_date).strftime("%Y-%m-%d"),
                bucket=bucket,
                regime=regime,
                n_symbols=n_sym,
                rank_ic=ic,
                tie_ratio=tie_ratio,
                n_excluded=n_excluded,
            )
        )
        # Store aligned factor scores (same subset used
        # for rank IC) — keyed by symbol for downstream /ic-aggregate pairing.
        # round(x, 6) keeps float precision while trimming JSON bloat.
        if n_sym > 0:
            score_dict = {
                str(sym): round(float(val), 6)
                for sym, val in aligned.iloc[:, 0].items()
                if pd.notna(val)
            }
            period_factor_scores.append({
                "rebalance_date": pd.Timestamp(rebalance_date).strftime("%Y-%m-%d"),
                "scores": score_dict,
            })
        period_ics_float.append(ic)
        regimes.append(regime)
        factor_by_period.append(factor)
        returns_by_period.append(fwd_returns)
        n_symbols.append(n_sym)
        symbols_seen.update(str(s) for s in aligned.index)

    # Cross-sectional effective-n (symbol-cluster shrinkage). Kept as JSON
    # metadata + known-biases warning ONLY — plumbing it into Student-t df or
    # DSR n_obs mixes dimensions and produces the wrong direction of
    # conservatism (see ADR note in compute_period_ic_stats docstring).
    eff_n = effective_n_cluster(
        sorted(symbols_seen),
        industry_labels,
        fallback_ratio=effective_n_fallback_ratio,
    )
    effective_n_metadata = eff_n if eff_n and eff_n > 1 else None

    overall = compute_period_ic_stats(period_ics_float)
    # Top-level `FactorICResult.effective_n` already
    # surfaces this as metadata. Writing
    # `overall["effective_n_cross_sectional"] = effective_n_metadata` would
    # produce identical duplicate values in the serialised JSON. The
    # overall dict only carries **time-series** stats; cross-sectional
    # cluster metadata lives at the top level where it belongs.
    overall["bootstrap_ci_95"] = list(
        stationary_block_bootstrap_ci(
            period_ics_float, avg_block_len=bootstrap_avg_block_len
        )
    )
    overall["bootstrap_ci_95_iid"] = list(bootstrap_ci(period_ics_float))

    # By bucket (time-series within each bucket; effective_n is cross-sectional
    # and intentionally NOT applied to bucket-level t-tests — same policy).
    buckets: dict[str, list[float]] = {}
    bucket_order: list[str] = []
    for p in period_ics_records:
        if p.bucket not in buckets:
            bucket_order.append(p.bucket)
            buckets[p.bucket] = []
        if p.rank_ic is not None:
            buckets[p.bucket].append(p.rank_ic)
    by_bucket = {
        b: compute_period_ic_stats(v)
        for b, v in buckets.items()
    }

    # Regime-conditional stats (time-series per regime; no effective_n mixing).
    by_regime = regime_conditional_ic(period_ics_float, regimes)

    perm = permutation_baseline(factor_by_period, returns_by_period, n=n_permutation)

    # Bucket-level FDR (Benjamini-Hochberg) across buckets of this factor
    bucket_pvals = [by_bucket[b].get("p_value") for b in bucket_order]
    fdr_period_level = fdr_correct(bucket_pvals)
    for b, adj in zip(bucket_order, fdr_period_level):
        by_bucket[b]["fdr_adj_p"] = adj

    # Deflated Sharpe Ratio on the IC-IR.
    #
    # n_obs = number of time-series observations (= n_periods). Substituting
    # cross-sectional `effective_n` here mixes dimensions the same way as the
    # Student-t df error. Sharpe / IR is a time-series statistic; Mertens (2002)
    # variance is parameterised by time-series n, not symbol count.
    #
    # Feed the empirically-estimated period-IC skewness / Pearson kurtosis
    # so fat-tailed IC distributions do not inflate DSR confidence via
    # the Gaussian default.
    dsr_confidence: float | None = None
    dsr_skew_used: float = 0.0
    dsr_kurt_used: float = 3.0
    dsr_moments_estimated: bool = False
    ic_ir = overall.get("ic_ir")
    dsr_n_obs_used: int | None = None
    if ic_ir is not None and overall.get("n"):
        dsr_n_obs_used = int(overall["n"])  # time-series n_periods
        # Estimate skew/kurt from the period IC samples when we have enough
        # data (>= 4 points); otherwise fall back to the Gaussian default.
        clean_ics_for_moments = _clean_floats(period_ics_float)
        if len(clean_ics_for_moments) >= 4:
            skew_estimate = float(stats.skew(clean_ics_for_moments, bias=False))
            # scipy.stats.kurtosis default is fisher=True (excess kurtosis);
            # Mertens (2002) wants Pearson kurtosis (= excess + 3).
            kurt_estimate = float(
                stats.kurtosis(clean_ics_for_moments, fisher=False, bias=False)
            )
            # Mark "estimated" only when BOTH moments pass guards; otherwise
            # DSR falls back silently to Gaussian and would misreport readiness.
            skew_ok = math.isfinite(skew_estimate)
            kurt_ok = math.isfinite(kurt_estimate) and kurt_estimate > 0
            if skew_ok:
                dsr_skew_used = skew_estimate
            if kurt_ok:
                dsr_kurt_used = kurt_estimate
            if skew_ok and kurt_ok:
                dsr_moments_estimated = True
        # BLdP 2014 confidence Ψ ∈ [0,1], NOT a p-value (legacy var name
        # kept to avoid churn; consumers should read .deflated_sharpe_ratio)
        dsr_confidence = deflated_sharpe_ratio(
            float(ic_ir),
            n_obs=dsr_n_obs_used,
            n_trials=dsr_n_trials,
            skewness=dsr_skew_used,
            kurtosis=dsr_kurt_used,
        )

    n_sym_avg = float(sum(n_symbols) / len(n_symbols)) if n_symbols else 0.0

    biases = list(known_biases or [])
    # Document methodology caveats so downstream readers cannot forget them.
    #
    # Corrections applied below:
    #   * effective_n is metadata only (NOT wired into any p-value / DSR).
    #   * Survivorship boilerplate is unconditional here; callers should no
    #     longer pass their own "cache-scan survivorship" string to avoid
    #     duplicate wording in the serialised known_biases list.
    #   * dsr_moments_estimated flag now drives the note, so platykurtic
    #     series that fall back to Gaussian do not lie about being "empirical".
    eff_n_note = (
        f"effective_n (cross-sectional cluster, ~{effective_n_metadata}) is "
        "recorded as metadata only; p-values / DSR use time-series n_periods "
        "(no automatic cross-sectional shrinkage applied)"
        if effective_n_metadata is not None
        else "effective_n unavailable (symbol universe < 2); no cross-sectional metadata recorded"
    )
    # DSR skew/kurt note — uses the explicit `dsr_moments_estimated`
    # flag so callers know whether **both** moments were estimated.
    dsr_moment_note = (
        f"DSR uses empirical period-IC moments "
        f"(skew={dsr_skew_used:.3f}, kurtosis={dsr_kurt_used:.3f})"
        if dsr_moments_estimated
        else "DSR uses Gaussian moments (fewer than 4 period ICs or "
             "non-finite estimates — fallback)"
    )
    boilerplate = [
        eff_n_note,
        "bootstrap CI uses Politis-Romano stationary block resampling to "
        "preserve autocorrelation",
        "permutation null uses per (iter, period) independent seed",
        dsr_moment_note,
        # Unconditional additions (callers should not duplicate these)
        "universe drawn from local cache scan; delisted symbols absent "
        "(survivorship bias)",
        "forward return is price-only (no dividend adjustment); total-return "
        "upgrade deferred to Phase A2",
    ]
    # Flag periods where tie_ratio > 0.3 — Spearman via average-rank
    # still runs but loses information; downstream readers should treat those
    # periods as less reliable.
    high_tie_periods = [
        p.rebalance_date
        for p in period_ics_records
        if p.tie_ratio is not None and p.tie_ratio > 0.3
    ]
    if high_tie_periods:
        boilerplate.append(
            f"{len(high_tie_periods)} period(s) have tie_ratio > 0.3 "
            f"(Spearman uses average-rank; reduced precision): "
            f"{high_tie_periods[:5]}"
            + ("..." if len(high_tie_periods) > 5 else "")
        )

    # Dedup precision:
    # A plain substring keyword match swallows unrelated caller notes.
    # Bucketing all canonical phrases into one set cross-contaminates
    # *topics* — e.g. an already-appended survivorship boilerplate would
    # falsely suppress the price-only boilerplate because both qualify as
    # "canonical". Group canonical phrases by semantic topic so dedup only
    # triggers within the same topic.
    _CANONICAL_PHRASE_GROUPS = (
        # Survivorship topic
        {
            "universe drawn from local cache scan",
            "cache-scan universe (survivorship bias",
        },
        # Price-only / dividend-adjustment topic
        {
            "forward return is price-only",
            "price_only forward return",
        },
    )

    def _is_canonical_restatement(new_msg: str, existing: list[str]) -> bool:
        """True iff the boilerplate phrase and some existing entry BOTH hit
        the same canonical-topic group. Cross-topic canonicals do not dedup."""
        new_low = new_msg.lower()
        for group in _CANONICAL_PHRASE_GROUPS:
            new_in_group = any(p in new_low for p in group)
            if not new_in_group:
                continue
            existing_in_group = any(
                any(p in b.lower() for p in group) for b in existing
            )
            if existing_in_group:
                return True
        return False

    for msg in boilerplate:
        if msg in biases:
            continue
        if _is_canonical_restatement(msg, biases):
            continue
        biases.append(msg)

    return FactorICResult(
        factor_name=factor_name,
        return_basis=return_basis,
        period_ics=period_ics_records,
        overall=overall,
        by_regime=by_regime,
        by_bucket=by_bucket,
        permutation=perm,
        fdr_adjusted_p=None,  # filled by caller when comparing multiple factors
        n_periods=len(period_ics_records),
        n_symbols_avg=n_sym_avg,
        known_biases=biases,
        bootstrap_method="stationary_block",
        bootstrap_avg_block_len=bootstrap_avg_block_len,
        deflated_sharpe_ratio=dsr_confidence,
        deflated_sharpe_n_trials=dsr_n_trials,
        deflated_sharpe_skewness=dsr_skew_used,
        deflated_sharpe_kurtosis=dsr_kurt_used,
        deflated_sharpe_moments_estimated=dsr_moments_estimated,
        deflated_sharpe_n_obs=dsr_n_obs_used,
        effective_n=effective_n_metadata,
        fdr_period_level=fdr_period_level,
        fdr_method="benjamini_hochberg",
        period_factor_scores=period_factor_scores,
    )
