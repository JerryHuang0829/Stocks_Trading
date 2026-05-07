# margin_short_ratio IC Report — Pro Sprint 2026-05-04 Reproducer

**Source JSON**: `reports/sprint_pro_validation/B_repro/factor_ic/margin_short_ratio_ic.json`
**Reproducer commit**: `0d31572` (post 2 P0 fixes)
**Cache**: FinMind cache @ 2026-04-21
**Range**: 2020-01-01 → 2025-12-31, monthly rebalance day 12, intersection universe 1696 symbols

---

## Overall Metrics

| Metric | Value | Notes |
|--------|-------|-------|
| n_periods | 71 | warmup-adjusted (full range 72 → effective 71) |
| n_symbols_avg | 1485.7200 | cross-sectional cohort size |
| **mean_ic** | **0.0387** | Spearman rank IC, monthly average |
| std_ic | 0.1674 | period-level std |
| **ic_ir** | **0.2314** | mean / std |
| t_stat | 1.9500 | df = 70 |
| p_value | 0.0552 | two-sided |
| bootstrap CI 95% (block) | [0.0121, 0.0668] | block_len=3, n_iter=10000, seed=42 |
| bootstrap CI 95% (iid) | [0.002, 0.0769] | comparison only |

## Pro Methodology Diagnostics

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **DSR** (Deflated Sharpe) | **0.0000** | BLdP 2014: ≥0.95 significant. FAIL (single-factor signal weakness) |
| n_trials (DSR haircut) | 5 | conservative |
| effective_n | 266 | industry-clustered (Pre-sprint -3~-4 due to industry label cache 增量) |
| FDR adjusted p | — | only set after /ic-aggregate |

## Permutation Test

| Field | Value |
|-------|-------|
| real_mean_ic | 0.0387 |
| null_mean | 0.0000 |
| null_std | 0.0032 |
| percentile | 1.0000 |
| p_value_empirical | 0.0066 |
| p_value_empirical_floor | 0.0066 |
| conclusion | significant_positive |
| n_permutations | 300 |

## Regime Decomposition

| Regime | mean_ic | n |
|--------|---------|---|
| trending_up | 0.0820 | 23 |
| trending_down | -0.0251 | 14 |
| ranging | 0.0358 | 34 |

## Bucket Decomposition (yearly / half-year)

| Bucket | mean_ic | ic_ir | t_stat | p_value | n |
|--------|---------|-------|--------|---------|---|
| 2020 | 0.0418 | 0.2974 | 1.0300 | 0.3250 | 12 |
| 2021 | 0.0290 | 0.1333 | 0.4620 | 0.6532 | 12 |
| 2022 | 0.1008 | 0.5223 | 1.8090 | 0.0978 | 12 |
| 2023 | -0.0009 | -0.0060 | -0.0210 | 0.9839 | 12 |
| 2024-H1 | -0.0146 | -0.0967 | -0.2370 | 0.8222 | 6 |
| 2024-H2 | 0.0906 | 0.6885 | 1.6870 | 0.1525 | 6 |
| 2025-H1 | 0.0525 | 0.2565 | 0.6280 | 0.5574 | 6 |
| 2025-H2 | -0.0136 | -0.1201 | -0.2690 | 0.8016 | 5 |

## Reproducibility Note

Reproducer 重跑與 `reports/factor_ic/margin_short_ratio_ic.json` 對齊：
- mean_ic / bootstrap_ci_95 / DSR / n_periods 全 within Pro tolerance (≤0.001 drift)
- effective_n 統一漂 +3-4（industry label cache 增量更新所致）
- 詳見 `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §3
