# Phase B0-Lite Spike Results — low_vol_v2

**執行日期**：2026-05-03 17:51
**Sample period**：2019-01-01 ~ 2024-12-31 (historical validation set)
**Universe**：top-80 by close × volume per rebalance
**Hypothesis lock**：reports/phase_b0_lite/H_lite_preregistration.md (commit 27e5fe6)

---

## Reject Criteria 評估

- **L1 (quality_v2 IC)** — DEFERRED to full B0 (cache 不存在 + lookahead bias)
- **L2 (low_vol_v2 IC > 0.02)**：✅ PASS — mean IC = 0.0584, DSR = 0.0
- **L4 (coverage ≥ 60%)**：✅ PASS — mean coverage = 96.4%
- **L5 (turnover < 30%/月)**：❌ FAIL — mean monthly one-way turnover = 37.5%
- **L_DSR (Ψ ≥ 0.95 per H_lite hypothesis line 28)**：❌ FAIL — DSR Ψ = 0.0

---

## Verdict (dual outcome per R21 P1/P2 fix)

- **Script outcome (按 reject criteria 主表 L2/L4/L5)**：**Lite-O4**
  - reason: low_vol_v2 monthly turnover = 37.5% ≥ 30%
  - next_step: 進 full B0 但 spec 改 quarterly rebal

- **Strict outcome (按 H_lite hypothesis 完整陳述含 DSR ≥ 0.95 AND condition)**：**Lite-O2** ← user 拍板採此
  - reason: H_lite hypothesis 完整陳述 fail: DSR Ψ = 0.0 < 0.95 (institutional-grade not reachable for retail monthly TW stock — 對齊 user memory 策略研究.md:104)
  - next_step: **pivot P5** (strict hypothesis lock 守住；不寫 full B0)

---

## Detailed IC Results

| Metric | Value |
|---|---|
| Periods | 62 |
| Symbols avg | 78.1 |
| Mean rank IC | 0.0584 |
| Std rank IC | 0.2283319595 |
| IC IR | 0.256 |
| t-stat | 2.015 |
| p-value | 0.0483 |
| Bootstrap CI 95 (block, len=3) | [0.0158, 0.099] |
| Bootstrap CI 95 (iid) | [-0.0006, 0.113] |
| Permutation p-value | 0.0066 |
| DSR Ψ (n_trials=12) | 0.0 |
| FDR adjusted p (overall) | None |

## IC by Regime (O3)

| Regime | Mean IC | n_periods |
|---|---|---|
| ranging | 0.0778 | 30 |
| trending_up | 0.0722 | 22 |
| trending_down | -0.0299 | 10 |

## IC by Bucket (O4 yearly)

| Bucket | Mean IC | n | FDR p |
|---|---|---|---|
| 2019 | 0.0977 | 3 | 0.5822 |
| 2020 | 0.0474 | 12 | 0.5822 |
| 2021 | 0.0768 | 12 | 0.5822 |
| 2022 | 0.089 | 12 | 0.5822 |
| 2023 | -0.0157 | 12 | 0.7476 |
| 2024-H1 | 0.1233 | 6 | 0.5822 |
| 2024-H2 | 0.044 | 5 | 0.5822 |

## Observation Metrics

### O1 — Active return rolling corr vs 0050 (5-month window)

最近 12 個月 rolling corr：

| Date | Rolling Corr |
|---|---|
| 2023-12-12 | 0.329 |
| 2024-01-12 | 0.885 |
| 2024-02-15 | 0.932 |
| 2024-03-12 | 0.920 |
| 2024-04-12 | 0.863 |
| 2024-05-13 | 0.596 |
| 2024-06-12 | 0.799 |
| 2024-07-12 | 0.942 |
| 2024-08-12 | 0.887 |
| 2024-09-12 | 0.897 |
| 2024-10-14 | 0.875 |
| 2024-11-12 | 0.840 |

### O2 — Top-8 holdings vs 0050 top-50 monthly overlap

Mean overlap (top-8 portfolio ∩ top-50 0050 proxy) / 8 = **78.0%**
→ ⚠️ overlap 偏高（low_vol top picks 跟 0050 重壓）— full B0 設計 A1 gate 須注意

### O3 / O4 — 見上方 by_regime / by_bucket

---

## Coverage by Period

低於 60% 的 period 數：2
最低 coverage：0.0625
最高 coverage：1.0
