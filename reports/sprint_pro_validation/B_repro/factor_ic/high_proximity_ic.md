# high_proximity IC Report — Pro Sprint 2026-05-04 Reproducer

**Source JSON**: `reports/sprint_pro_validation/B_repro/factor_ic/high_proximity_ic.json`
**Reproducer commit**: `0d31572` (post 2 P0 fixes)
**Cache**: FinMind cache @ 2026-04-21
**Range**: 2020-01-01 → 2025-12-31, monthly rebalance day 12, intersection universe 1696 symbols

---

## Overall Metrics

| Metric | Value | Notes |
|--------|-------|-------|
| n_periods | 71 | warmup-adjusted (full range 72 → effective 71) |
| n_symbols_avg | 1635.8500 | cross-sectional cohort size |
| **mean_ic** | **0.0413** | Spearman rank IC, monthly average |
| std_ic | 0.1509 | period-level std |
| **ic_ir** | **0.2738** | mean / std |
| t_stat | 2.3070 | df = 70 |
| p_value | 0.0240 | two-sided |
| bootstrap CI 95% (block) | [0.013, 0.0709] | block_len=3, n_iter=10000, seed=42 |
| bootstrap CI 95% (iid) | [0.0059, 0.0759] | comparison only |

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
| real_mean_ic | 0.0413 |
| null_mean | 0.0002 |
| null_std | 0.0029 |
| percentile | 1.0000 |
| p_value_empirical | 0.0066 |
| p_value_empirical_floor | 0.0066 |
| conclusion | significant_positive |
| n_permutations | 300 |

## Regime Decomposition

| Regime | mean_ic | n |
|--------|---------|---|
| trending_up | 0.0732 | 23 |
| trending_down | -0.0478 | 14 |
| ranging | 0.0565 | 34 |

## Bucket Decomposition (yearly / half-year)

| Bucket | mean_ic | ic_ir | t_stat | p_value | n |
|--------|---------|-------|--------|---------|---|
| 2020 | 0.0567 | 0.4318 | 1.4960 | 0.1628 | 12 |
| 2021 | 0.0204 | 0.1358 | 0.4710 | 0.6471 | 12 |
| 2022 | 0.0495 | 0.2220 | 0.7690 | 0.4580 | 12 |
| 2023 | 0.0442 | 0.4943 | 1.7120 | 0.1149 | 12 |
| 2024-H1 | 0.1020 | 0.9230 | 2.2610 | 0.0733 | 6 |
| 2024-H2 | 0.0523 | 0.4647 | 1.1380 | 0.3066 | 6 |
| 2025-H1 | -0.0273 | -0.1110 | -0.2720 | 0.7966 | 6 |
| 2025-H2 | 0.0243 | 0.2633 | 0.5890 | 0.5877 | 5 |

## Reproducibility Note

Reproducer 重跑與 `reports/factor_ic/high_proximity_ic.json` 對齊：
- mean_ic / bootstrap_ci_95 / DSR / n_periods 全 within Pro tolerance (≤0.001 drift)
- effective_n 統一漂 +3-4（industry label cache 增量更新所致）
- 詳見 `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §3
