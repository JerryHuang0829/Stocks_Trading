# H_v8_idio_led — Plan v8 Idio-Led Variant Pre-Registration DRAFT

**Status**: 🟡 **DRAFT — awaiting latest 獨立 audit pass (currently R33) + user sign-off lock**
**Date drafted**: 2026-05-11
**Predecessor**: H_d_v6 (v7 cell sweep, locked 2026-05-04)
**Trigger**: 2026-05-11 Phase D 3 因子 single IC 補測 → idio_vol_max +0.0588 = 8 因子最強 single IC

---

## ⚠️ Status & Lock Requirement

本 doc 是 **DRAFT**。lock 必須滿足兩條件：
1. **獨立 audit pass on the Plan-v8 pre-reg block**（R32 已 PASS C1-C7：post-hoc bias / sample reuse / L1-L6 不降標 / n_trials=24 / 8×3=24 cells / pre-rerun checklist / sign-off / 同 v7 不可改；R33 確認 R32-fix 後 baseline + round reference 不 stale）
2. **User 明確簽 lock signature**（user 看完 外部 audit 後拍板）

**未 lock 前**：不可跑 24-cell v8 sweep，不可寫 v8 cell yaml configs，不可 freeze candidate sets。

---

## 1. Hypothesis Statement

### 1.1 H_v8_idio_led

> 假設：將 `idio_vol_max` 升權至 30-50% primary（取代 v7 D-G 給的 20% secondary 配置），並重新組合 8 個 candidate factor sets，在沿用 v7 sample 2019-2024 IS + 2025 OOS 跑 24-cell sweep，能否找到 ≥1 個 cell **同時通過 6 hard gates L1-L6**（IR ≥ 0.20 / α ≥ 0.005 monthly / TE ∈ [0.10, 0.30] / Max DD diff ≤ +0.05 / active_corr ≤ 0.50 / 80% bootstrap CI lower > 0）。

### 1.2 Null hypothesis

- **H_0**：no cell 通過 6 hard gates（同 v7 Outcome-2 Partial CONFIRM-NO-GO）
- **H_1**：≥1 cell 通過 6 hard gates → 進 6-month paper trade evaluation gate

### 1.3 Falsification criterion（不可改）

- 全 24 cells 任一 L1-L6 fail = H_v8_idio_led falsified → NO-GO 結案
- 若任一 cell 5/6 但 L6 卡 0（同 v7 D-C\|12 / D-E\|16）= NO-GO（不準破例放行 paper）
- 若 ≥1 cell 6/6 → tie-breaker IR > α > active_corr → sole_survivor → paper gate

---

## 2. Motivation & Bias Disclosure（嚴格 pro 標準）

### 2.1 Source of design

idio_vol_max 升權設計**直接源於 2026-05-11 Phase D 3 因子 single IC 補測 finding**：

| 因子 | 2026-05-11 mean IC | 2026-05-11 p_value | 2026-05-11 IC IR | v7 weight in D-G |
|---|---:|---:|---:|---:|
| idio_vol_max | **+0.0588** | **0.0077** | **+0.326** | 20% |
| (對照 high_proximity) | +0.0413 | 0.0240 | +0.274 | 40% (D-G 主動量) |
| (對照 pead_eps) | +0.0219 | 0.0168 | +0.291 | 40% (D-G 主基本面) |

**重點**：idio_vol_max 的 single IC 比 v7 D-G 給 40% 權重的 high_proximity / pead_eps 都強。但 v7 受限於 H_d_v6 pre-reg lock 不能事中調整。

### 2.2 Post-hoc bias (P1 公開揭露)

**⚠️ 本設計是 post-hoc data-driven adjustment，不是 v6 pre-reg 時就知道的 design**。

風險：若 v8 sweep 真有 cell 過 6 gates，部分績效**可能歸因於本設計就是 "選了單因子最強的把它升權"**，而**不是純粹獨立 OOS evidence**。

緩解措施：
- 沿用 v7 sample 2019-2024 IS + 2025 OOS — 2025 OOS 已被 v7 "看過" 一次（partial data leakage）
- 6 hard gates **照抄 v7 不可降標**（避免降標 silent_bug）
- DSR n_trials = 24（比 v7 的 18 更嚴，補償 candidate inflation）
- 結論報告必須明示「v8 結果若 GO，不是 fresh OOS evidence，2025 樣本已被 v7 cell sweep + v8 cell sweep 雙重使用」

### 2.3 Sample reuse caveat (P1 公開揭露)

**⚠️ 沿用 v7 sample 2019-2024 IS + 2025 OOS**（不是 fresh OOS）。

選用此路徑的理由（per user 2026-05-11 拍板）：
- 速度：避免延 cache 到 2026-Q1 多 2-3 hr
- 一致性：與 v7 直接對照，可看 idio-led 設計是否 dominate 原 6 candidates

承擔的代價（清楚揭露）：
- v8 結果**不是純 fresh OOS**，2025 樣本已用於 v7 cell sweep selection
- 即使 v8 有 cell 6/6 過，drift from v7 部分可歸因於 weight redesign（不是純 data discovery）
- 若 v8 同樣 Outcome-2 Partial → CONFIRM-NO-GO 結論強化（idio 升權沒救）
- 若 v8 Outcome-1（有 cell 通過）→ 必須在 closeout 明示「post-hoc + partial sample reuse caveat」並建議 fresh 2026-Q1 OOS gate

### 2.4 Why NOT extend cache to 2026-Q1（per user choice）

| 路徑 | 工時 | OOS rigor | User 拍板 |
|---|---|---|---|
| **沿用 v7 sample** | ~5-8 hr v8 sweep | partial reuse, weak | ✅ 選此 |
| 延 cache 2026-Q1 | +2-3 hr cache 補 | 真 fresh OOS | ❌ 不選 |
| 延 historical 2017-2018 | +6+ hr cache 補 | 最強 sample | ❌ 不選 |

---

## 3. 8 Candidate Factor Sets

### 3.1 Weight grid

| Candidate | idio_vol_max | high_proximity | pead_eps | margin_short_ratio | quality_v3 | Sum |
|---|---:|---:|---:|---:|---:|---:|
| **V-A** | 40% | 30% | 30% | — | — | 100% |
| **V-B** | 30% | 35% | 35% | — | — | 100% |
| **V-C** | 30% | 30% | 25% | 15% | — | 100% |
| **V-D** | 35% | 30% | 25% | — | 10% | 100% |
| **V-E** | 30% | 30% | 30% | 10% | — | 100% |
| **V-F** | 40% | — | 60% | — | — | 100% |
| **V-G** | 50% | 25% | 25% | — | — | 100% |
| **V-H** | 35% | 25% | 25% | 10% | 5% | 100% |

### 3.2 Design rationale (per candidate)

- **V-A**: idio 升至 40%（取代 v7 D-G 的 20%），保持 52W+PEAD 共 60% 主軸。「idio 領跑但動能基本面仍主」最 minimal design。
- **V-B**: idio 30% + 52W/PEAD 各 35%。「idio 補強但動能基本面仍主導」最保守 design。
- **V-C**: 加入 margin_short 15% 看融資反向是否補強 idio anti-feature 特性。
- **V-D**: 加入 quality 10% 看品質是否補強 low-vol 安全特性。
- **V-E**: 加入 margin_short 10% 但 v7 D-B/D-D 已測過 20%/30%，看更小權重是否更穩。
- **V-F**: 完全去掉 52W，純 idio + earnings（pead）。「動量去除測試」極端 design。
- **V-G**: idio 升至 50%（極限），52W+PEAD 各 25%。「idio 完全主導」最 aggressive design。
- **V-H**: 5-factor 完全分散（idio + 52W + PEAD + margin + quality）。「分散化測試」極端 design。

### 3.3 Hypothesis prediction（pre-commit）

預期最有可能過 gates 的順序（純 a priori 猜測，pre-commit 鎖死）：
1. V-A / V-D / V-H（idio 中等 35-40% + 多因子分散）
2. V-B / V-E（保守 idio 30% + 動量基本面主導）
3. V-C / V-G（極端 idio 30% w/margin OR 50% 重押）
4. V-F（無 52W 太冒險）

若 cell sweep 結果**最佳 cell 不在上方順序前 3**，必須在 closeout 揭露「prediction failure」並反思 hypothesis 設計缺陷。

---

## 4. Top_n Grid

```yaml
top_n_values: [8, 12, 16]
```

同 v7（不可改）。共 8 × 3 = **24 cells**。

---

## 5. Hard Gates L1-L6（不可降標）

| Gate | Threshold | Source |
|---|---|---|
| L1 | IR ≥ 0.20 | H_d_v6 §"6 hard gates" line 1 |
| L2 | net α ≥ 0.005 monthly | H_d_v6 §"6 hard gates" line 2 |
| L3 | TE ∈ [0.10, 0.30] | H_d_v6 §"6 hard gates" line 3 |
| L4 | Max DD diff ≤ +0.05 | H_d_v6 §"6 hard gates" line 4 |
| L5 | A1 active_corr ≤ 0.50 | H_d_v6 §"6 hard gates" line 5 |
| L6 | 80% bootstrap CI lower > 0 | H_d_v6 §"6 hard gates" line 6 |

**禁止任何 silent threshold adjust**。獨立 audit 會逐條 grep 確認。

---

## 6. Sample & Engine Constants（沿用 v7，不可改）

| Item | Value | Note |
|---|---|---|
| Sample IS | 2019-01-01 ~ 2024-12-31 (60 months) | 同 v7 |
| Sample OOS | 2025-01-01 ~ 2025-12-31 (12 months) | 同 v7（**partial sample reuse** vs v7） |
| Rebalance frequency | Monthly BME (business month end) | 同 v7 |
| Universe | TWSE/TPEX top-80 by close × 20d avg volume | 同 v7 |
| Benchmark | 0050 dividend-adjusted total return | 同 v7（V0.24 hard fail if dividends missing） |
| Round-trip cost | from settings.yaml (V0.13 Assertion 1) | 同 v7 |

---

## 7. DSR Configuration

| Item | Value | 對照 v7 |
|---|---|---|
| n_trials | **24** | v7 = 18 |
| 配 cell 數 | 8 × 3 = 24 | v7 = 6 × 3 = 18 |
| Caller must pass `n_trials=24` kwarg | 不可 silent default | (V0.13 Assertion 3 enforce) |

---

## 8. Pre-Rerun Checklist（lock 前必過）

| # | Item | Status | Verifier |
|---|---|---|---|
| 1 | 獨立 audit Plan-v8 block all PASS / SUFFICIENT（R32 PASS C1-C7；R33 final confirm） | ⏳ pending | external audit |
| 2 | User 簽 lock signature | ⏳ pending | User |
| 3 | pytest baseline passed 0 regression（685 → 690：Phase D IC schema parity tests + foreign/revenue yaml-sync tests；R33 實測 690 passed） | ✅ done | self-audit this session |
| 4 | Cache up to date through 2025-12-31 | ✅ done | (sample IS+OOS 截至 2025-12-31) |
| 5 | 8 candidate yaml configs 寫好 | ⏳ DRAFT | self-audit pending lock |
| 6 | `scripts/d_cell_sweep_v8.py` adapter from v7 | ⏳ DRAFT | self-audit pending lock |
| 7 | `scripts/d_cell_aggregate_v8.py` aggregator (n_trials=24) | ⏳ DRAFT | self-audit pending lock |
| 8 | `reports/phase_d_v8/v8_validation_manifest.md` | ⏳ DRAFT | self-audit pending lock |

---

## 9. Workflow After Lock

| Phase | 內容 | 工時 |
|---|---|---|
| Lock | 獨立 audit pass (Plan-v8 block) + user 簽 | (即時) |
| S1 | 寫 8 candidate yaml configs | 1 hr |
| S2 | adapter `d_cell_sweep_v8.py` + `d_cell_aggregate_v8.py` (n_trials=24) | 2 hr |
| S3 | 24-cell sweep run | 5-8 hr |
| S4 | merge + aggregate + outcome classification | 1 hr |
| S5 | closeout report + v7 對照 + bias disclosure | 3 hr |
| S6 | R32 audit (v8 final) | (1.5 hr) |
| S7 | If APPROVE → user 拍板 paper trade kickoff (Outcome-1) OR 結案 (Outcome-2/3) | (variable) |
| | **TOTAL post-lock** | **~13-15 hr** |

---

## 10. Outcome Classification（同 v7）

| Outcome | 條件 | Action |
|---|---|---|
| **Outcome-1 GO** | ≥1 cell 6/6 過 hard gates | sole_survivor → 6m paper trade（**含 post-hoc + sample reuse caveat**） |
| **Outcome-2 Partial** | 0 cell 過 6/6，但有 cell 5/6 卡 L6 | CONFIRM-NO-GO；不放行 paper |
| **Outcome-3 Total Fail** | 全 cells ≤ 4/6 | CONFIRM-NO-GO；結論 「idio 升權無效」+ 思考 retire factor research |

**禁止任何 sole_survivor 規則放寬**（per H1/H8 v7 紀律）。

---

## 11. Sign-Off (lock 後填)

| Role | Name | Date | Sign |
|---|---|---|---|
| Researcher | self-audit (this session) | 2026-05-11 | (DRAFT 只簽 draft submission) |
| Auditor | external audit (latest audit, currently R33) | TBD | TBD |
| Final lock | User | TBD | TBD |

---

## 12. Risk Register

| Risk | Mitigation |
|---|---|
| Post-hoc weight selection bias | §2.2 完整 disclosure + 獨立 audit (post-hoc bias block) |
| Partial sample reuse (2025 OOS 已被 v7 看過) | §2.3 完整 disclosure + closeout 必說明 |
| 24-cell sweep timeout | S3 切 batch 分段（每 batch ~6 cells）+ cache 中間結果 |
| Silent gate threshold adjust | §5 hard threshold list + external audit K.3 audit |
| DSR n_trials silent drift | §7 explicit kwarg + V0.13 Assertion 3 enforce |
| 8 candidate yaml configs typo / weight 不 sum 1.0 | S1 寫完後 mutation test 驗 weight sum + external audit 復檢 |

---

## 13. References

- Predecessor: `reports/phase_d/H_d_v6_preregistration.md`
- v7 cell sweep results: `reports/phase_d/cell_sweep_v7_2026_05_06/`
- v7 closeout: `reports/phase_d/v7_outcome2_summary.md`
- 2026-05-11 Phase D 3 因子 IC: `reports/factor_ic/{quality_v3,industry_momentum,idio_vol_max}_ic.json`
- audit prompt: `audit-prompt.md` (currently R33)

---

## DRAFT 結束 — 等 獨立 audit pass (Plan-v8 block) + user 簽 lock
