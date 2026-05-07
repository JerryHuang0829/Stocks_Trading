# revenue_momentum_v2 IC Report — Pro Sprint 2026-05-04 Reproducer

**Source JSON**: `reports/sprint_pro_validation/B_repro/factor_ic/revenue_momentum_v2_ic.json`
**Reproducer commit**: `0d31572` (post 2 P0 fixes)
**Cache**: FinMind cache @ 2026-04-21
**Range**: 2020-01-01 → 2025-12-31, monthly rebalance day 12, intersection universe 1696 symbols

---

## Overall Metrics

| Metric | Value | Notes |
|--------|-------|-------|
| n_periods | 71 | warmup-adjusted (full range 72 → effective 71) |
| n_symbols_avg | 1633.8500 | cross-sectional cohort size |
| **mean_ic** | **0.0145** | Spearman rank IC, monthly average |
| std_ic | 0.0759 | period-level std |
| **ic_ir** | **0.1906** | mean / std |
| t_stat | 1.6060 | df = 70 |
| p_value | 0.1128 | two-sided |
| bootstrap CI 95% (block) | [-0.0013, 0.0305] | block_len=3, n_iter=10000, seed=42 |
| bootstrap CI 95% (iid) | [-0.0045, 0.0302] | comparison only |

## Pro Methodology Diagnostics

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **DSR** (Deflated Sharpe) | **0.0000** | BLdP 2014: ≥0.95 significant. FAIL (single-factor signal weakness) |
| n_trials (DSR haircut) | 5 | conservative |
| effective_n | 270 | industry-clustered (Pre-sprint -3~-4 due to industry label cache 增量) |
| FDR adjusted p | — | only set after /ic-aggregate |

## Permutation Test

| Field | Value |
|-------|-------|
| real_mean_ic | 0.0145 |
| null_mean | 0.0002 |
| null_std | 0.0030 |
| percentile | 1.0000 |
| p_value_empirical | 0.0066 |
| p_value_empirical_floor | 0.0066 |
| conclusion | significant_positive |
| n_permutations | 300 |

## Regime Decomposition

| Regime | mean_ic | n |
|--------|---------|---|
| trending_up | 0.0002 | 23 |
| trending_down | 0.0491 | 14 |
| ranging | 0.0098 | 34 |

## Bucket Decomposition (yearly / half-year)

| Bucket | mean_ic | ic_ir | t_stat | p_value | n |
|--------|---------|-------|--------|---------|---|
| 2020 | 0.0144 | 0.2051 | 0.7110 | 0.4921 | 12 |
| 2021 | 0.0122 | 0.0967 | 0.3350 | 0.7439 | 12 |
| 2022 | 0.0134 | 0.2297 | 0.7960 | 0.4431 | 12 |
| 2023 | 0.0085 | 0.1093 | 0.3780 | 0.7123 | 12 |
| 2024-H1 | 0.0215 | 0.3419 | 0.8380 | 0.4405 | 6 |
| 2024-H2 | -0.0049 | -0.0958 | -0.2350 | 0.8238 | 6 |
| 2025-H1 | 0.0311 | 0.4076 | 0.9990 | 0.3639 | 6 |
| 2025-H2 | 0.0321 | 1.0849 | 2.4260 | 0.0723 | 5 |

## Reproducibility Note

Reproducer 重跑與 `reports/factor_ic/revenue_momentum_v2_ic.json` 對齊：
- mean_ic / bootstrap_ci_95 / DSR / n_periods 全 within Pro tolerance (≤0.001 drift)
- effective_n 統一漂 +3-4（industry label cache 增量更新所致）
- 詳見 `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §3
