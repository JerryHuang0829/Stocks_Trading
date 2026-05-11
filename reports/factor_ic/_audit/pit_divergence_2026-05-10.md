# B3 Divergence Root Cause Audit — 2026-05-10

**Plan reference**: codex-pro-codex-precious-reef.md Phase 0

## Question

Claude R1 reported score Spearman 0.9675/0.9945 (latest mv vs as-of mv ranking); Codex R2 reported 0.9633/0.9914. Diff 0.003-0.004 across both dates.

## Method

Both rounds reportedly used a `cum_foreign / mv` simulator (no full composite). Test 4 universe-filter variants of the simulator to find which produces 0.9675 and which 0.9633.

- **V1**: each scenario keeps own native universe (drop sym if its own mv missing or ≤ 0)
- **V2**: pre-intersect mv universe (require BOTH latest+asof mv exist) before scoring
- **V3**: relaxed (no min_history filter, only `len(last20) >= 5`)
- **V4**: V2 + drop extreme mv ratio (latest/asof > 10x or < 0.1x)

## Reference Numbers (Round 1 / Round 2)

| Date | Claude R1 | Codex R2 | Diff |
|---|---:|---:|---:|
| 2020-01-13 | 0.9675 | 0.9633 | +0.0042 |
| 2025-11-12 | 0.9945 | 0.9914 | +0.0031 |

## Results

| Date | Variant | n_common | Spearman | Δ vs Claude R1 | Δ vs Codex R2 | Match |
|---|---|---:|---:|---:|---:|---|
| 2020-01-13 | V1: native universe (mv >0 per scenario) | 1481 | 0.967502 | +0.000002 | +0.004202 | Claude R1 |
| 2020-01-13 | V2: intersected mv universe | 1481 | 0.967502 | +0.000002 | +0.004202 | Claude R1 |
| 2020-01-13 | V3: no min_history (>=5 days only) | 1626 | 0.971913 | +0.004413 | +0.008613 | neither |
| 2020-01-13 | V4: intersected + ratio in [0.1x,10x] | 1437 | 0.975785 | +0.008285 | +0.012485 | neither |
| 2025-11-12 | V1: native universe (mv >0 per scenario) | 1894 | 0.994521 | +0.000021 | +0.003121 | Claude R1 |
| 2025-11-12 | V2: intersected mv universe | 1894 | 0.994521 | +0.000021 | +0.003121 | Claude R1 |
| 2025-11-12 | V3: no min_history (>=5 days only) | 1915 | 0.994414 | -0.000086 | +0.003014 | Claude R1 |
| 2025-11-12 | V4: intersected + ratio in [0.1x,10x] | 1894 | 0.994521 | +0.000021 | +0.003121 | Claude R1 |

## Top10 / Bot10 Jaccard (universe overlap diagnostic)

| Date | Variant | top10 J | bot10 J |
|---|---|---:|---:|
| 2020-01-13 | V1: native universe (mv >0 per scenario) | 0.566138 | 0.517949 |
| 2020-01-13 | V2: intersected mv universe | 0.566138 | 0.517949 |
| 2020-01-13 | V3: no min_history (>=5 days only) | 0.550239 | 0.557692 |
| 2020-01-13 | V4: intersected + ratio in [0.1x,10x] | 0.588889 | 0.529412 |
| 2025-11-12 | V1: native universe (mv >0 per scenario) | 0.843902 | 0.783019 |
| 2025-11-12 | V2: intersected mv universe | 0.843902 | 0.783019 |
| 2025-11-12 | V3: no min_history (>=5 days only) | 0.836538 | 0.776744 |
| 2025-11-12 | V4: intersected + ratio in [0.1x,10x] | 0.843902 | 0.783019 |

## Conclusion

**Claude R1 (0.9675 / 0.9945) reproducible**：V1/V2 simulator 全產 0.967502 / 0.994521，diff < 0.001。

**Codex R2 (0.9633 / 0.9914) 4 variant 全 miss**：本 audit 4 種 universe filter 變體中無任何一個產出 Codex R2 數字，diff 全 ≥ +0.003。Codex R2 用了我沒覆蓋的第三種 method（推測：可能加了部分 z-score / 不同 truncate / 不同 last20 邊界處理）。

**對 plan 的影響**：B3 divergence 是兩位 auditor simulator 寫法微差，不影響 P0 修法決策。Phase 1+ MODIFY-AND-RERUN 後 -0.0195 baseline obsolete，本 divergence 純為 Codex R3 reproducibility log。

## 對 long-only 策略的實質 implication（top10 Jaccard 揭示）

PIT 違規對 ranking 整體保留率高（Spearman ≥ 0.97），但對**實際選股清單**影響大：

| 期間 | Top 10% Jaccard | 換手率（不同標的占比）|
|---|---:|---:|
| 2020-01-13（早期）| 0.566 | **44%** 標的會換人 |
| 2025-11-12（近期）| 0.844 | 16% 標的會換人 |

意思是修 PIT 後，2020 期間 long-only top decile 將近一半標的會被替換。所以 ranking-level 0.97 高相關 ≠ 策略-level 影響輕微。Phase 3 fresh rerun 的新 IC 跟舊 -0.0195 預期會有顯著差異，特別在 2020-2022 早期樣本。
