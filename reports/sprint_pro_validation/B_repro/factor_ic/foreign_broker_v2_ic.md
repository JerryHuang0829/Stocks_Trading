# foreign_broker_v2 IC Report — Pro Sprint 2026-05-04 Reproducer

**Source JSON**: `reports/sprint_pro_validation/B_repro/factor_ic/foreign_broker_v2_ic.json`
**Reproducer commit**: `0d31572` (post 2 P0 fixes)
**Cache**: FinMind cache @ 2026-04-21
**Range**: 2020-01-01 → 2025-12-31, monthly rebalance day 12, intersection universe 1696 symbols

---

## Overall Metrics

| Metric | Value | Notes |
|--------|-------|-------|
| n_periods | 71 | warmup-adjusted (full range 72 → effective 71) |
| n_symbols_avg | 1610.0300 | cross-sectional cohort size |
| **mean_ic** | **-0.0195** | Spearman rank IC, monthly average |
| std_ic | 0.0930 | period-level std |
| **ic_ir** | **-0.2098** | mean / std |
| t_stat | -1.7680 | df = 70 |
| p_value | 0.0814 | two-sided |
| bootstrap CI 95% (block) | [-0.0383, -0.0017] | block_len=3, n_iter=10000, seed=42 |
| bootstrap CI 95% (iid) | [-0.0406, 0.0017] | comparison only |

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
| real_mean_ic | -0.0195 |
| null_mean | 0.0000 |
| null_std | 0.0029 |
| percentile | 0.0000 |
| p_value_empirical | 0.0066 |
| p_value_empirical_floor | 0.0066 |
| conclusion | significant_negative |
| n_permutations | 300 |

## Regime Decomposition

| Regime | mean_ic | n |
|--------|---------|---|
| trending_up | -0.0347 | 23 |
| trending_down | 0.0131 | 14 |
| ranging | -0.0227 | 34 |

## Bucket Decomposition (yearly / half-year)

| Bucket | mean_ic | ic_ir | t_stat | p_value | n |
|--------|---------|-------|--------|---------|---|
| 2020 | 0.0055 | 0.0596 | 0.2070 | 0.8401 | 12 |
| 2021 | -0.0219 | -0.2106 | -0.7300 | 0.4809 | 12 |
| 2022 | -0.0569 | -0.5126 | -1.7760 | 0.1034 | 12 |
| 2023 | -0.0124 | -0.1430 | -0.4950 | 0.6301 | 12 |
| 2024-H1 | 0.0174 | 0.4406 | 1.0790 | 0.3297 | 6 |
| 2024-H2 | -0.0774 | -0.9955 | -2.4390 | 0.0588 | 6 |
| 2025-H1 | -0.0078 | -0.0646 | -0.1580 | 0.8805 | 6 |
| 2025-H2 | 0.0101 | 0.2480 | 0.5540 | 0.6088 | 5 |

## Reproducibility Note

Reproducer 重跑與 `reports/factor_ic/foreign_broker_v2_ic.json` 對齊：
- mean_ic / bootstrap_ci_95 / DSR / n_periods 全 within Pro tolerance (≤0.001 drift)
- effective_n 統一漂 +3-4（industry label cache 增量更新所致）
- 詳見 `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §3
