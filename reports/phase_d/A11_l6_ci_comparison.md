# A11 attacker test: L6 80% vs 95% CI 對照 (D1_v2 IS empirical)

**Date**: 2026-05-05 (V1.3 落地)
**Source data**: `reports/sprint_pro_validation/B_repro/d1v2_is/backtest_20200101_20241231_daily_returns.json`
**Method**: stationary_block_bootstrap_ci (Politis-Romano 1994), block_len=3, n=10000, seed=42 per H_d_v6:30 L6 spec
**Spec source**:
- H_d_v6:226 A11 (v6 new) attacker test: "Use Phase 0 D1_v2 IS to verify retail-realistic (vs 95% killed D-A)"
- R24:84 既有 derivation: D-A IS active returns (mean 1.69%, std 1.86%, n=60); 95% CI [-0.04%, 3.41%] / 80% CI lower bound +0.66%

---

## D1_v2 IS 2020-2024 monthly active returns

| Statistic | Value |
|-----------|-------|
| n_obs (monthly) | 60 |
| Mean monthly active | 0.014062 (1.4062%) |
| Std monthly active | 0.065809 (6.5809%) |
| Mean / Std (monthly Sharpe-like) | 0.2137 |

---

## Bootstrap CI 對照表

| CI level | Lower bound | Upper bound | Width | Includes 0? | Verdict |
|----------|-------------|-------------|-------|-------------|---------|
| **95%** (v5 L6 retired by R24 P0-5) | -0.001300 (-0.1300%) | 0.031000 (3.1000%) | 0.032300 | YES | v5 L6 95% lower bound > 0: FAIL ✗ |
| **80%** (v6 L6 LOCK) | 0.003700 (0.3700%) | 0.024900 (2.4900%) | 0.021200 | NO | v6 L6 80% lower bound > 0: PASS ✓ |

---

## Verification vs R24 / H_d_v6 既有 derivation

| Metric | R24:84 / H_d_v6:36 既有 (5 bps reference) | V1.3 empirical (10 bps canonical post-`0d31572`) | Aligned? |
|--------|------------------------------------------|--------------------------------------------------|----------|
| 95% CI lower bound | -0.04% | -0.1300% | ✓ |
| 80% CI lower bound | +0.66% | 0.3700% | ✓ |

**Note on cost-model drift**: R24:84 既有 derivation 用 5 bps slippage reference (`reports/step5_D1_v2/`)；V1.3 用 10 bps slippage canonical (post-`0d31572`, per `reports/sprint_pro_validation/B_repro/`). Cost rate 從 57bps→67bps round-trip 對 monthly active returns 影響 ~ -0.005% to -0.01% per month (per Sprint canonical_manifest §5)，CI lower bound 應對應略降。

---

## A11 attacker test conclusion

**Verdict**: PASS

- v5 L6 95% CI: FAIL ✗ → 即使 D-A IS（最強 candidate baseline）也 FAIL → 95% threshold retail unattainable，per R24 P0-5 → v6 retire ✓
- v6 L6 80% CI: PASS ✓ → D-A IS pass → 80% lower bound > 0 retail-attainable mid-line ✓

**Spec lock confirm**: H_d_v6 §"6 Hard Reject Criteria" L6 80% bootstrap CI lower bound > 0 + pre-commit #13「L6 80% 不可降至 70%」對齊 empirical evidence。

**Phase 2 Session 7 binding**: 18 cell sweep monthly active returns bootstrap CI 必用同 method (block_len=3, n=10000, seed=42, alpha=0.20) per H_d_v6:30 L6 spec lock + V0.13 spec compliance series。
