# pead_eps IC Report — Pro Sprint 2026-05-04 Reproducer

**Source JSON**: `reports/sprint_pro_validation/B_repro/factor_ic/pead_eps_ic.json`
**Reproducer commit**: `0d31572` (post 2 P0 fixes)
**Cache**: FinMind cache @ 2026-04-21
**Range**: 2020-01-01 → 2025-12-31, monthly rebalance day 12, intersection universe 1696 symbols

---

## Overall Metrics

| Metric | Value | Notes |
|--------|-------|-------|
| n_periods | 71 | warmup-adjusted (full range 72 → effective 71) |
| n_symbols_avg | 1608.9600 | cross-sectional cohort size |
| **mean_ic** | **0.0219** | Spearman rank IC, monthly average |
| std_ic | 0.0753 | period-level std |
| **ic_ir** | **0.2907** | mean / std |
| t_stat | 2.4490 | df = 70 |
| p_value | 0.0168 | two-sided |
| bootstrap CI 95% (block) | [0.0075, 0.0369] | block_len=3, n_iter=10000, seed=42 |
| bootstrap CI 95% (iid) | [0.0037, 0.0404] | comparison only |

## Pro Methodology Diagnostics

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **DSR** (Deflated Sharpe) | **0.0000** | BLdP 2014: ≥0.95 significant. FAIL (single-factor signal weakness) |
| n_trials (DSR haircut) | 5 | conservative |
| effective_n | 269 | industry-clustered (Pre-sprint -3~-4 due to industry label cache 增量) |
| FDR adjusted p | — | only set after /ic-aggregate |

## Permutation Test

| Field | Value |
|-------|-------|
| real_mean_ic | 0.0219 |
| null_mean | -0.0001 |
| null_std | 0.0032 |
| percentile | 1.0000 |
| p_value_empirical | 0.0066 |
| p_value_empirical_floor | 0.0066 |
| conclusion | significant_positive |
| n_permutations | 300 |

## Regime Decomposition

| Regime | mean_ic | n |
|--------|---------|---|
| trending_up | -0.0009 | 23 |
| trending_down | 0.0523 | 14 |
| ranging | 0.0248 | 34 |

## Bucket Decomposition (yearly / half-year)

| Bucket | mean_ic | ic_ir | t_stat | p_value | n |
|--------|---------|-------|--------|---------|---|
| 2020 | 0.0214 | 0.2922 | 1.0120 | 0.3332 | 12 |
| 2021 | 0.0314 | 0.2988 | 1.0350 | 0.3228 | 12 |
| 2022 | 0.0009 | 0.0115 | 0.0400 | 0.9689 | 12 |
| 2023 | 0.0391 | 0.6289 | 2.1790 | 0.0520 | 12 |
| 2024-H1 | 0.0440 | 0.5419 | 1.3270 | 0.2418 | 6 |
| 2024-H2 | 0.0140 | 0.2510 | 0.6150 | 0.5656 | 6 |
| 2025-H1 | 0.0146 | 0.1704 | 0.4170 | 0.6937 | 6 |
| 2025-H2 | 0.0006 | 0.0231 | 0.0520 | 0.9612 | 5 |

## Reproducibility Note

Reproducer 重跑與 `reports/factor_ic/pead_eps_ic.json` 對齊：
- mean_ic / bootstrap_ci_95 / DSR / n_periods 全 within Pro tolerance (≤0.001 drift)
- effective_n 統一漂 +3-4（industry label cache 增量更新所致）
- 詳見 `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §3
