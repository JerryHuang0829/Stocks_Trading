# 策略研究

最後更新：**2026-05-07**（Phase D v7 18-cell run 完成 → CONFIRM-NO-GO / Outcome-2 Partial / 0/18 cells 過 6 hard gates；採 A-then-B：先 v7 closeout + architecture hardening，再決定 v8）

---

## 🟥 2026-05-07 — Phase D v7 結論：CONFIRM-NO-GO（Outcome-2 Partial）

### 18-cell sweep 實證結果

| 統計 | 值 |
|---|---|
| 跑時間 | 2026-05-06 20:18:46 ~ 23:48:45（3.5 hr） |
| 完成 cells | 18/18 |
| 過 6/6 (Outcome-1) | **0** |
| `outcome_classification` | **Outcome-2 Partial** |
| `sole_survivor` | **null** |

最接近的 cells：
- **D-C\|12** 4/6（L5 A1 gate 與 L6 fail）
- **D-E\|12** 4/6（L3 TE upper bound 與 L6 fail）
- **D-E\|16** 4/6（L5 A1 gate 與 L6 fail）

L5 是 active_corr + TE + beta-adjusted alpha t 的 aggregate gate，不是單看 correlation。因此沒有任何 5/6 cell。18/18 cells 全 fail L6，代表「IS metric 看起來不錯，但 stationary block bootstrap 80% CI 沒辦法把下界推上 0」 → 統計上無法確信策略真贏 0050。

### 5 個 P0 closure（V0.22-V0.26）

從 Codex Round 1 pre-run audit NO-GO 開始，逐個修法到 V0.26：

| # | P0 | 修法 |
|---|---|---|
| 1 | FinMind transient error 未分類 | V0.22 `FinMindTransientError` class |
| 2 | universe build look-ahead | V0.23 `_is_above_min_price_at()` PIT-safe |
| 3 | 0050 dividend 未強制 | V0.24 `require_dividend_adjust=True` |
| 4 | `_build_financial_history` join overlap | V0.25 `rsuffix="_bs"` |
| 5 | TSMC NetIncome 2020+ NaN（FinMind schema change）| V0.26 `NetIncome.fillna(IncomeAfterTaxes)` |

V0.26 是最重要的 silent bug — Phase A1 期沒踩到（2019 schema 還對），Phase D 拉到 2026 才暴露 quality_v3 period 卡 2019-12-31。

### 雙重驗證：Codex R25-final + Claude 獨立 read JSON

User 在收 Codex CONFIRM-NO-GO 後指示「請你實際驗證 不要依賴codex」。Claude 直接 read cell_summary.json + 跑 `scripts/_verify_qv3_period.py`、`scripts/_verify_tsmc_schema.py` 獨立確認：

- evaluator JSON 0 mismatch
- L6 bootstrap CI re-compute 數字 match stored
- V0.26 period 推到 2026-03-31 confirm
- No new P0

→ 結論：**CONFIRM-NO-GO 不是 Codex 過度謹慎，是真實沒過。**

### Outcome-2 系統性失敗根因（不是 v7 evaluator bug）

1. **60 個月樣本對 stationary block bootstrap 偏短**（block_len=3 → effective n ≈ 20）
2. **n_trials=18 DSR 診斷**：18 trials 會壓縮單一訊號信心；但 binding NO-GO 來自 L1-L6 hard gates，不是只因 DSR
3. **2020-2024 極端市場**：covid + 科技股巨漲 + 升息 + AI，因子過度集中
4. **台股 1900 檔流動性受限**
5. **monthly freq 60 obs 對 80% CI 訊雜比不足**

→ v7 不是「程式 bug」，是**現實上 60 個月 + 嚴格 retail-realistic gate 下 18 個假設都不夠強**。

### Phase D v7 對研究本身的學習

**正面收穫**：
- Pro methodology infra 全套建好（DSR / FDR / stationary block bootstrap / PIT mutation tests / forward-leak guard）
- 5 個 P0 silent bug 修法（V0.22-V0.26）成為永久 evaluator asset
- 18-cell 結果可當未來 v8 / v9 baseline 對照組
- Codex R25 round 1-3 + R25-final 4 輪 audit 累積 ~50 個攻擊角度全答完
- Claude 獨立驗證 SOP 練到位（不依賴 Codex 也能交付）

**負面教訓**：
- Phase A1 5 因子 + Phase D 6 candidates × 3 top_n 全 NO-GO → **台股 monthly freq long-only 60-month sample 找穩定 edge 的 prior 應再降低**
- 給 Codex 的 prompt 表格手算錯 4 cell（silent bug pattern #2 + #3：未實測就寫文件 + 修法無 grep sweep）
- 未來方向若仍走 stock factor，應 prioritize：
  - 樣本延伸（2015-2024 = 10 年；2008-2024 = 17 年含金融海嘯）
  - 改 weekly / daily frequency（增 obs 數）
  - 改 multi-asset（不只台股）
  - 改 alternative gate spec（如 L6 改 advisory rather than hard）

### 累積階段結論（更新）

| Phase | 結論 |
|---|---|
| P1-P7 (2024) | 三因子 tw_3m_stable 過去 alpha 揭穿為 overfit (timezone + universe pre-filter bug) |
| Phase A1 (2026-04) | 5 新因子 + Pro methodology 建立；2 通過中道（52W High / PEAD）|
| Phase A2 D1_v2 | 2-factor composite OOS 2025 α 不顯著 |
| Phase A3.1 | sector_neutral / regime_aware 全 fail |
| Pivot Options (2026-04-23 ~ 05-02) | TXO Iron Condor 5yr OOS Sharpe -2.1~-2.9 → Phase 1 alpha 證偽 |
| Pivot back (2026-05-02) | 重啟 Quantitative Phase D |
| Phase D v6 → v7 (2026-05-04) | Plan v4/v5 NO-GO → v6 baseline → v7 closeout |
| **Phase D v7 (2026-05-07)** | **18-cell run 完成 / 0 過 6/6 / Outcome-2 Partial / CONFIRM-NO-GO** |

### 下一步（A-then-B）

| Step | 路徑 | 狀態 |
|---|---|---|
| A | 結案 `v7_outcome2_summary.md` + 0050 DCA practical baseline | 進行中 |
| B0 | architecture hardening：文件一致性、conda 測試、BacktestEngine import reliability | 進行中 |
| B | v8 reframe：樣本延伸 / core-satellite / formal engine / preregistered trials | 待 B0 完成後決定 |

**紀律**：CONFIRM-NO-GO 下不允許 active top-N paper trade kickoff（4/6 ≠ 6/6，降標 hard gates = silent_bug）。

---

---

## 🟢 2026-05-04 — Phase D v7 hypothesis lock + 6 candidates + Sprint manifest verification

### H_d_v6 (= H_d_v7) 假設陳述（pre-registered，事前鎖定）

> 在台股 2019-2024 historical validation set + TWSE/TPEX top-80 close×volume universe + long-only top_n ∈ {8, 12, 16} + monthly frequency + 6 candidate factor sets (D-B/C/D/E/F/G; D-A 已預先 disqualify per D6 OOS 退化) 條件下，至少 1 個 (factor_set × top_n) cell 同時滿足全部 6 條 hard reject criteria L1-L6（v7 retail-realistic 降標版），且 6 個月 paper trade live PnL (L7) > 0050 cost-adjusted DCA bootstrap CI 不跨零。

詳 `reports/phase_d/H_d_v6_preregistration.md`。

### 6 hard gates v7 vs v5/v6

| Gate | v5 | v7 (= v6 降標) |
|---|---|---|
| L1 IR vs 0050 | ≥ 0.30 | ≥ 0.20 |
| L2 monthly net α (cost 0.67% × turnover) | ≥ 0.010 | ≥ 0.005 |
| L3 TE | [0.10, 0.30] | 同 |
| L4 Max DD diff | ≤ +0.05 | 同 |
| L5 A1 gate | active_corr ≤ 0.50 + TE ≥ 0.10 + β-adj α t > 1.5 | 同（Phase 2 Session 5 binding implement）|
| L6 Bootstrap CI | 95% 下界 > 0 | **80% 下界 > 0** |
| L7 paper | 6m paper > 0050 + CI 不跨零 | 同 |

L6 降標 numerical justification：D-A IS 60 monthly active returns 95% CI [-0.04%, 3.41%] 下界 < 0 → 即使最強 IS 候選都 fail v5；80% CI 在同樣資料下界 +0.66% pass。70% 以下無統計力。

### 6 candidate factor sets

| ID | 組成 | Weighting |
|---|---|---|
| ~~D-A~~ | 52W + PEAD 50/50 | **預先 disqualify** per D6 |
| D-B | 52W + PEAD + Margin = 39/41/20 | IR-weighted (純 IR 34.4/36.5/29.1 + Margin cap 20%) |
| D-C | D-B + low_vol_v2 0.20 rescaled | IR-weighted |
| D-D | 4 factors equal-weighted | Equal |
| D-E | quality_v3 (TTM ROE × gross_margin × Δassets reverse) | Single, **QMJ profitability sub-component**（不含 growth/safety/payout）|
| D-F | industry_momentum **6m per Moskowitz-Grinblatt 1999** | Single |
| D-G | idio_vol_max (IdioVol 0.5 + MAX 0.5) | + 監控 cross-corr with low_vol_v2 |

### D-A 預先 disqualification 證據（D6 觸發）

| Source (10 bps cost post-`0d31572`) | IS 2020-2024 | OOS 2025 | Threshold | Status |
|---|---|---|---|---|
| `reports/sprint_pro_validation/B_repro/d1v2_*` | TE 0.23673, IR 0.9238 | TE 0.223253, IR 0.0058 | — | 99.4% IR collapse |
| Computed monthly α (OOS) | — | ~0.011% | ≥ 0.5% | **FAIL** D6 |

歷史 5 bps 對照：IS IR 0.9375, OOS IR 0.0373, monthly α ~0.069%（仍 fail D6）— D-A pre-disqualification robust to cost-model choice。

### Sprint manifest 驗證 v5 spec 內部一致

`reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §5 verified Plan v5 spec B1-B6+L5 7/7 changes 全 numerically traceable：
- B1 L3 TE [0.10, 0.30]：D1_v2 IS 0.23673 / OOS 0.223253 都 in range
- B2 IC source phase_a1_summary (n=59) → `reports/factor_ic/*_ic.json` (n=71) bit-exact reproducer
- B3 跨頻 monthly enforced (tw_stock.py:196-197)
- B4 cost 0.67% × turnover (engine.py:467-472 verified；composite_backtest.py:47 待 Phase 2 Session 1 修)
- B5 A5 attacker → D6 hard disqualifier verified
- B6 L2 0.010/月 alignment with L6 TE=12% 假設（v7 改 0.005 buffer floor，per 實測 D1_v2 IS TE=23.67% Sprint 假設過低反證）

R24 NO-GO 真因 = L6 95% over-strict + meta-issues（環境/cache/hypothesis-lock），**非 v5 spec 數值錯**。詳 `reports/phase_d/R24_resolution.md` §"Scope correction"。

### Code-level enforcement（Plan v7 V0.10）

H_d_v6 加 §"Code-level enforcement" 列 3 assertions 必入 Phase 2 Session 1 / 6 落地：
1. Cost dual-model check (`composite_backtest.py` cost == `engine.py` cost == 0.0067)
2. D-A pre-disqualification guard (`assert "D-A" not in CANDIDATE_FACTOR_SETS`)
3. DSR n_trials = 18 verify (matches cell count 6 candidates × 3 top_n)

### 4 種 Outcome 機率（v7 重估）

> ⚠️ **Historical pre-run prior（v6 寫，2026-05-04）**：以下 Outcome 機率分配是 v7 sweep 跑前的先驗估計，不是 v7 closeout 結論。實際 v7 落點：Outcome-2 Partial、最高 4/6（D-C\|12 / D-E\|12 / D-E\|16），無任何 5/6 cell；canonical 結論以 `reports/phase_d/v7_outcome2_summary.md` 與 `cell_summary.json` 為準。本表保留以資 prior vs posterior 對照。

| Outcome | 先驗機率 | 動作 |
|---|---|---|
| Outcome-1 Full Pass (≥1 cell L1-L7 全 pass) | 20-30% | 開 paper trade |
| Outcome-2 Partial (4-5/6 過) | 20-30% | caveat 報告，不 paper trade |
| Outcome-4 Full Fail (0 cell pass) | 40-60% | 認 retail long-only ceiling，pivot |

Outcome-3 (D-A control) v6 起移除（D-A 預先 disqualify）。

---

## 🟡 2026-05-03 — Phase B0-Lite low_vol_v2 spike 結論

**研究發現**：低波動率因子（IdioVol 殘差 std）single-factor IC 在 2019-2024 表面強但結構性 fail。

| 表面 | 數字 | 解讀 |
|---|---|---|
| mean rank IC | 0.0584 | 顯著正向 |
| t-stat | 2.015 (p=0.048) | 邊界顯著 |
| permutation p | 0.0066 | 非隨機 |
| bootstrap CI | [0.0158, 0.099] | 不跨零 |

**結構性 4 systemic warnings**：
1. **DSR Ψ = 0.0**（n_trials=12 conservative）：multi-trial 後預期最大 IR 已超過 observed IR，無 marginal value
2. **0050 holdings overlap 78%**：跟大型權值股共動，不是 alternative source
3. **trending_down regime IC -0.030**：在多頭收尾段（2024-06 ~）負相關
4. **2023 yearly IC -0.016**：fail year，不穩定

**結論**：H_lite hypothesis 含 DSR ≥ 0.95 AND condition → DSR=0 = strict fail → Lite-O2 → pivot。User reject 後續 P5「80%/20% 半放棄」 → pivot Phase D 多因子 long-only。

詳 `reports/phase_b0_lite/{spike_results,decision_pivot_p5}.md`。

---

## 🛑 2026-04-23 ~ 2026-05-02 — Pivot Options 5yr 證偽

外部 repo Options_Trading 跑 TXO Iron Condor + Vertical Phase 1：

| Scenario | Sharpe |
|---|---|
| Plain IC | -2.9 |
| IC + Vertical hedge | -2.5 |
| IC + Calendar hedge | -2.1 |
| Quick A calendar 改良 | -2.48 |

5yr OOS 全 Sharpe 負，alpha hypothesis 證偽 → 不啟動 Phase 2 paper trading → 2026-05-02 pivot back 本 repo 重啟 Pro Validation Sprint。

---

## 🛑 2026-04-23 — Phase A3.1 結案 + Pivot 決策

### Phase A3.1 執行結果（三架構強化 + 三 backtest）

**架構**（commits `2a50a8e` → `9265f2c` → `1c9d4bb` + 本次 A3.1.4 pool fix）：
- **A3.1.1 Sector-neutral ranking**（產業內排名，強制分散）
- **A3.1.2 Regime-aware weighting**（依 market regime 切 factor weight）
- **A3.1.3 Walk-forward step_months**（monthly-stride WF，備 48-slice）
- **A3.1.4 second-pass pool fix**（小組 valid < 2 pool 進 `_OTHER` 二次排名）

**Gate 條件**（user 指定 0-tolerance）：
- IS 2020-2024 α ≥ +15% AND 2024 單年 α > -5% AND OOS 2025 α ≥ 0
- 三條全過 → 跑 48-slice monthly WF 做 final；任一 fail → A3.1 整體報廢

**Backtest 結果**（D1_v2 baseline 作對照）：

| Config | 內容 | IS 2020-24 | 2024 單年 | OOS 2025 | 判決 |
|---|---|---|---|---|---|
| D1_v2（baseline）| 52W/PEAD 50/50 | Sharpe 1.54 / α +22.19% | α **-13.33%** | Sharpe 1.43 / α **+0.83%** | — |
| D1_v3a（regime-only）| + regime_score_weights | Sharpe 1.52 / α +22.45% | — | Sharpe 1.24 / α **-4.64%** | ❌ OOS 倒扣 5.47pp |
| D1_v3b（sector-only + A3.1.4 fix）| + sector_neutral_metrics | **IS CRASH** | Sharpe 1.35 / α **-14.78%** | Sharpe 1.29 / α **-3.83%** | ❌ 2024 -1.45pp / OOS -4.66pp |

→ **三 Gate 全 fail**。A3.1 架構整體報廢。

### 根因診斷（有實證支持）

透過 `scripts/a3_diagnose_concentration.py` + `scripts/a3_diagnose_monthly_returns.py`：

**診斷 1 — 產業分散度**（D1_v2 vs D1_v3b，2024 各月）：

| 指標 | D1_v2 | D1_v3b |
|---|---|---|
| 平均每月獨特產業 | 5.50/8 | **6.42/8** |
| 平均最大產業集中 | 2.58 檔 | **2.00 檔** |

→ **sector_neutral 實作正確**，確實分散產業。

**診斷 2 — 月均換股 overlap**：

- 12 個月平均 overlap **5.08/8** — 每月換 3 檔（非名義改動，真的換股）

**診斷 3 — 2024 月度輸贏**：

| 月 | D1_v2 vs 0050 | D1_v3b vs 0050 |
|---|---|---|
| **2024-06** | **-10.73pp** | -5.58pp |
| 2024-07 | -4.92pp | **-10.09pp** |
| 2024-10 | -7.42pp | -5.68pp |
| FY2024 | -8.74pp | -10.43pp |

→ 2024-06 0050 飆 +12.32%（PC AI 鏈拉回前最後一波），D1_v2 +1.59% 跟不上；D1_v3b 6 月改善但 7 月殺盤更慘。

### 結論（Phase A 研究最終判決）

1. **Sector_neutral 實作正確，但方向性錯誤**：分散確實發揮作用，但分散本身傷 alpha
2. **「產業集中是 factor 特性，不是 bug」**：52W High + PEAD 同為 momentum-family → 自然選大權值股 → 產業集中是 signal 來源
3. **2024 是 0050 權值股獨舞年**：任何削弱 momentum 的機制（sector / regime）都扣 alpha
4. **Long-only 月頻架構結構性劣勢**：面對「大權值股飆升年」（0050 被前幾大權值帶飛），long-only 策略很難跟上

### Pivot 決策

**實盤**：100% 0050 DCA，2.5 萬/月。不再 live active 選股策略。

**研究**：本 repo 進入**維護模式 + Smart Beta 學習檔案**。下一階段研究（中頻系統化期權）另開新 repo（月 3-4 後啟動）。

**原因**：
- A3.1 兩維度架構在 OOS 都傷 baseline alpha
- A3.2（5 新因子）中 Low vol 是唯一 uncorrelated，但 2024 beta 低追不上 0050 — 期望 ROI 負
- Continued research 期望值 < 沉沒成本
- 真正能贏 0050 的路徑是 **long/short market neutral** 或 **options vol trading**，不在 long-only factor 範疇

### A3 中有效的工程資產（保留）

- `_group_items_by_industry()`：產業分組 + `_OTHER` pool（~30 行）
- `_metric_ranks_sector_neutral()` + A3.1.4 second-pass pool（~40 行）
- `_resolve_regime_score_weights()`：regime-aware weight schedule（~20 行）
- `_rank_analyses(..., market_view=None)` 簽章：可往上傳入 regime
- `walk_forward.py` `step_months` 參數：支援 monthly-stride（可復用在未來任何策略）
- 24 個 Phase A3 新 tests（sector_neutral 13 / regime_aware 9 / WF stride 5）
- 診斷 scripts：`a3_diagnose_concentration.py` + `a3_diagnose_monthly_returns.py`

這些 code 保留當**架構練習成果**，即使 strategy 路線 pivot 也有 career 作品集價值。

---

## 🎯 2026-04-20 — Phase A1 holistic audit 結論 + Phase A2 啟動

### Phase A1 最終結論：**新 5 因子 IC 測完，嚴格標準 0 通過，中道標準 2 通過**

| Factor | IR | nominal p | FDR (m=5) | DSR Ψ | Block CI | 中道通過 |
|---|---|---|---|---|---|---|
| 52W High Proximity | **0.33** | 0.015 | 0.059 | 0.00 | [0.018, 0.076] | ✅ |
| PEAD EPS | **0.30** | 0.024 | 0.059 | 0.00 | [0.007, 0.041] | ✅ |
| Margin/Short | 0.23 | 0.080 | 0.133 | 0.00 | [0.010, 0.071] | ⚠️ borderline |
| Revenue v2 | 0.14 | 0.292 | 0.293 | 0.00 | [-0.007, 0.028] | ❌ |
| Foreign Broker v2 | **-0.23** | 0.089 | 0.111 | 0.00 | [-0.044, -0.001] 全<0 | ❌ long-only 不能用 |

**關鍵認知修正（R13 DSR 語義）**：DSR Ψ ≥ 0.95 是 hedge fund pro 門檻（需 IR ≈ 2.0），**retail monthly TW stock 幾乎不可能達到**（業界 public factor IR 通常 0.2-0.5）。

**Retail professional 標準 → 改用中道**：
- nominal p<0.05 + BH FDR<0.10 + Bootstrap CI>0
- **52W High + PEAD 2 factor 合格**

### 舊 3 因子 backtest 實證 overfit（Config A）

- 2020-2024 IS：alpha +11.17%（Sharpe 1.05）
- 2025 OOS：alpha **-16.49%**（Sharpe 0.80）
- **27% alpha swing 是 textbook overfit**

這也是為何 Phase A1 不能只看 IC 就上實盤——**必須 OOS 驗證**。新 composite 也沒 2025 OOS 驗過。

### Phase A2 Strategy C 路徑（2026-04-20 決議）

```
Step 2 engine 整合 5 新 factor → Step 3 Codex 審 → Step 4 討論 weight
→ Step 5 Backtest Config D1-D5 + walk-forward
→ Step 6 Go/No-Go：
   OOS alpha>0 + walk-forward stable → 小額實盤 10 萬
   OOS 正 但不穩 → Paper trade 6 個月
   OOS crash → Smart Beta pivot
```

所有 5 factor 都整合進 engine，透過 `settings.yaml` `score_weights` 自由配置。**不 grid search old+new**（p-hack）、**不 auto-weighting**（樣本不穩）。

### 實務認知

- 新 composite 即使整合後，**期望淨 alpha 僅 +1-3%/年 vs 0050**
- 2020-2024 是 0050 異常好的期間（CAGR 17%），composite 要超過很難
- Phase A2 結果可能仍是 Smart Beta pivot — 那是誠實結論不是失敗
- Pro methodology 完整流程是 quant engineer 面試級訓練，**職涯價值 > 找到賺錢 factor 本身**

### 意外發現：FinMind TPEX margin SS_Buy/Sell systematic swap bug

R11.1 v1/v2 累計修 **411,333 rows across 734 TPEX syms**（含 17 轉板股，如 1597 直得）。Root cause：FinMind 歷史 API 對 TPEX 端點順序「券賣→券買」沒 swap 回 FinMind canonical。R11 新 TWSE/TPEX fetcher 正確。Migration script idempotent 可重現。

2022-06-22 TWSE API 回傳值不穩定（同日不同查詢時不同結果），593 rows 保留 FinMind 原值，列 known anomaly。

---

## 🔥 2026-04-16 晚 — 獨立審計確認 + 研究方向轉 Pro 標準

### 三方審計結論（Claude 診斷 + Codex fact-check + 獨立 Claude 審計）

| 項目 | 審計結論 | 證據 |
|------|---------|------|
| PM 因子 IC truncated bias | **原 -0.0505 含選擇偏誤** | 完整 universe IC = **-0.0272**, p=0.40, CI [-0.085, +0.033] 跨零 → 統計雜訊 |
| Permutation test | **策略表現差於隨機** | Sharpe 0.697 在隨機選 8 檔分布僅第 34 百分位（中位 0.801） |
| Rolling alpha 2025-12 +8.43% | **β=0.53 數學幻象** | 精確 OLS 確認，非 edge 恢復 |
| 小資金不可行 | **可執行（盤中零股）** | 25k 資金 81% utilization，但 2025 淨 α -17~-19% 仍否決實盤 |
| 最優被動配置 | **60/40 0050/0056** | Sharpe 1.089 > 50/40/10 (1.074)，10% 現金拖累 2.5% CAGR |

**核心論斷**：
- ✅ 確認當前三因子架構**無 edge**
- ✅ 不是 regime shift 造成的暫時失效，是**研究方法不到位 + 架構過簡**
- ✅ Infrastructure 專業級（`_DataSlicer`、PIT、survivorship bias 防護、219 tests），可保留作研究基座

### 研究方向校準（用戶明確指示）

> 「研究不要因為我本金而侷限，以專業量化交易公司看齊」

**新基準線**：
- 假設 AUM：**10 億 NTD**（~$30M USD，TW quant sweet spot）
- 手續費：機構級 1-2 bps（非零售 6 bps）
- 必做：FDR 修正、Deflated Sharpe、capacity analysis、Kyle's lambda market impact
- 個人 2.5 萬月投 → 被動 60/40 0050/0056（獨立生活決策，與研究脫鉤）

### Baseline 集結果（2026-04-16 晚生成）

| Baseline | 期間 | Sharpe | Alpha | Return | MDD | Beta | Bench | n_Reb |
|----------|------|--------|-------|--------|-----|------|-------|-------|
| 6Y (2019-2024) | — | ❌ | ❌ | — | — | — | — | Cache 2019 覆蓋率 76%，**護欄擋下** |
| **5Y (2020-2024)** | 5 年 | **1.05** | **+11.17%** | +30.92% | -28.54% | 0.49 | +19.75% | 60 |
| 3Y (2022-2024) | 3 年 | 0.88 | +8.84% | +23.06% | -28.54% | 0.44 | +14.22% | 36 |
| **2Y (2023-2024)** | 2 年 | 1.33 | +6.35% | +42.84% | -28.54% | 0.44 | +36.49% | 24 |
| **4Y (2022-2025)** | 4 年 | 0.64 | +3.40% | +15.47% | -34.06% | 0.51 | +12.07% | 48 |
| **Holdout (2025-2026/4)** | 16 月 | 1.64 | +4.80% | +65.01% | -19.34% | 0.72 | +60.21% | 16 |
| 2025 單年 | 1 年 | 0.66 | **-18.44%** | +15.73% | -22.15% | 0.53 | +34.17% | 12 |

### 關鍵觀察

**Alpha 隨樣本期長度的波動**（按長度排序）：
- 5Y: +11.17%（最好）→ 涵蓋 2020-2021 大牛市 + 2022 熊 + 2023-2024 AI bull
- 3Y: +8.84%
- 2Y: +6.35%
- Holdout: +4.80%
- 4Y: +3.40%
- 2025: -18.44%（最差）→ 單年特定 regime

**Pattern**：長期 baseline 平均掉短期題材 luck，**5Y 才是最接近真實長期表現的數字**。

**Holdout (+4.80%) 的拆解**：
- 2025 年: 策略 +18.67%, 0050 +33.47% → Alpha **-14.80%**
- 2026 Q1 (Jan-Apr 15): 策略 **+51.45%**, 0050 +30.09% → Alpha **+21.36%**
- 2026 Q1 吃到 memory/DRAM 題材（南亞科 2408、群聯 8299、華邦電 2344 等半月漲 22-42%）

**架構驗證**（用戶擔心過 OOS 翻轉有 bug）：
- ✅ Benchmark（0050）2025-06-18 split 由 `metrics.py::adjust_splits()` 自動修正
- ✅ 2026-01 持股 cache 全部乾淨，漲幅是真實市場數字
- ✅ Daily returns × position × exposure 完美匹配（精準到小數後 2 位）
- ⚠️ 隱患：`data/cache/ohlcv/0050.pkl` raw 未調 split，downstream script 直接讀要小心

### 解讀：為什麼 OOS Alpha 正卻不翻案審計結論

1. **長期證據仍成立**：permutation test 策略 Sharpe 0.697 低於隨機選 8 檔中位 0.801（300 次模擬）
2. **Holdout 也只是題材吃得到**：2026 Q1 memory 大漲，類似 2022 航運、2023 AI 的**吃題材**模式
3. **2025 -18% + 2026 Q1 +21% = 回歸中性** → 非系統性 edge
4. **Deflated Sharpe 未修正**：Bootstrap/FDR 考量後顯著性仍不足

**專業結論**：數字真實但不代表技能。Alpha 來自 regime luck 而非穩定 factor edge。Pro 升級方向（A+C+D）不改變。

---

## 📊 2026-04-16 晚 — Phase A1 因子研究（月頻 × TW-specific 篩選）

### 篩選原則

1. **因子 horizon 必須配策略 horizon**（月頻 → 選 signal decay 1-6 月的因子）
2. **TW-specific edge 優先**（月營收、外資分點、融資融券 — 美股沒有）
3. **低因子相關性**（相關 > 0.7 只留一個，避免冗餘）
4. **IC > 0.05 + IC_IR > 0.5** 為單因子 gate
5. **Deflated Sharpe** 考慮多重比較

### 🥇 必做（Phase A1 第一批，5 個，**2026-04-16 晚重排依 retail 可用性**）

| 優先 | 因子 | 類別 | 月頻適配 | 預期 IC | 資料源（純官方 API） |
|------|------|------|---------|---------|---------------------|
| 1 | **Revenue Momentum v2** | TW-specific | ✅ 完美 | 0.05-0.10 | TWSE 月營收 OpenAPI（現有 scraper） |
| 2 | **52W High Proximity**（升級） | Academic (George-Hwang 2004) | ✅ 好 | 0.04-0.08 | OHLCV cache（現有） |
| 3 | **Margin/Short Ratio**（升級） | TW-specific | ✅ 好 | 0.03-0.07 | TWSE `twse_margin_short` OpenAPI（待寫 scraper） |
| 4 | **PEAD**（降級為 Revenue Surprise） | Academic | ✅ 完美（季報+月營收） | 0.05-0.08 | TWSE 月營收 + MOPS 季報 |
| 5 | **Foreign Broker v2**（降級為三大法人 aggregate） | TW-specific | ✅ 好 | 0.05-0.10 | TWSE T86 OpenAPI（待寫 scraper） |

**總時間**：4 週（Phase A1 4 週時程詳見 `Claude-Prompt.md`）
**關鍵變更（2026-04-16 晚）**：
- **純官方 API**：5 個因子零 FinMind 依賴（FinMind 只留 fallback）
- **PEAD / Foreign Broker 降級**：避開「EPS 預估值無免費 API」「分點細節需爬蟲」的坑
- **優先序重排**：Retail 可用性優先（52W High 升第 2，Foreign Broker 降第 5）

### 🥈 第二批（Phase A1 延伸或 A2）
- Quality (GP/A) × Momentum 交叉
- Industry-Relative Momentum
- Abnormal Turnover

### 🥉 第三批（defensive overlay 或 regime-conditional 用）
- Low Volatility（TW retail-driven 未必強）
- Dividend Yield（當 overlay，非選股主力）
- PEG（成長 × 估值）

### 🚫 放棄的（月頻不合）
- 1-week reversal（月底前早就過期）
- Pure Size（太慢 + 流動性限制）
- Accruals (Sloan 1996)（季度更新）
- Pure Value (B/P)（月頻 timing 不敏銳）

### 因子設計改良重點

**Foreign Broker Pressure v2**（當前 `institutional_flow` 失敗的真因）：
- ❌ 舊：5 日外資淨買超（雜訊太大）
- ✅ 新：20 日 rolling 累積 × persistence（連續買超天數）× rank stability

**Revenue Momentum v2**：
- 保留：YoY growth、3M/3M 加速度
- 新增：近 3 月營收 percentile vs 自身 24 月歷史、seasonal-adjusted revenue surprise

**PEAD 實作**：
- EPS surprise（FinMind 季報 + 分析師預估）
- 或代理：連續 N 季營收 YoY > 前 3 季均值作 surprise proxy

### 專業教訓

1. **避免 factor zoo**（Harvey 2016 指出 300+ 發表 factor，多半 data mining）
2. **單因子 IC 0.05 夠好，10 個低相關因子組合能達 Sharpe 1.5-2.0**
3. **TW-specific 是你的地理套利**（月營收、外資分點、融資融券）
4. **Regime-conditional activation** 才是 top firm 做法（bull/bear 用不同因子組）

---

## 🔧 2026-04-16 — Walk-forward 重跑（當前門檻）

Round 3 Codex follow-up 修復完成後，重跑 walk-forward：

| 指標 | 數值 | 解讀 |
|------|------|------|
| Mean Sharpe | **0.80** | 較 2022-2024 baseline（0.88）略降 |
| Median Sharpe | 0.61 | 半數 window 低於 0.61 |
| Std | 1.53 | 視窗間波動極大 |
| Bootstrap 95% CI | **[-0.07, 1.75]** | **跨 0 → 統計上不顯著** |
| 近 3 windows Alpha | W9/W10/W11 **全負** | **方向性警訊，非單點波動** |

**解讀**：
- 單窗 Sharpe 0.88 可重現 ≠ OOS 穩定
- 今晚獨立審計進一步確認這是**真實無 edge**（permutation < random），非暫時 regime shift

---

## 🚨 2026-04-15 重大發現 — 所有舊結論需重估

### 核心事實

兩個隱藏已久的 bug 導致過去一年所有「優秀」回測數字都是污染 universe 的產物：

**Bug 1**：`src/data/finmind.py` timezone 錯誤（`pd.Timestamp(tz="UTC")` on already-tz-aware）。pandas 2.x 會 raise，讓 benchmark 走錯路徑。

**Bug 2**：Pre-filter 用 TWSE `STOCK_DAY_ALL` API 對歷史日期永遠失敗（此 API 只支援當日快照）。導致 universe 從設計 400 → 80 → 退化為全市場 ~2000 → 80（隨機偶發篩選）。

**結果**：策略意外抓到中小型飆股（2022 航運、2023 AI 概念），alpha 來源是 **universe 污染而非選股能力**。

### 修前 vs 修後（關鍵數字，保留對照）

| 期間 | 修前 Sharpe | 修後 Sharpe | 修前 Alpha | 修後 Alpha |
|------|-------------|-------------|------------|------------|
| 2025 OOS | 1.88 | **0.66** | +7.27% | **-18.4%** |
| 2022-2025 (4Y) | 1.73 / 0.97 | **0.64** | +39% / +4.9% | +3.4% |
| 2024 單年 | — | **0.33** | — | **-43.2%** |
| Rolling OOS 平均 | 1.38 | 0.93 | — | +34.4% |
| Bootstrap 95% CI | [-0.13, 2.41] | [-0.05, 1.99] | — | 兩者皆跨 0 |

### 方法論教訓（最重要）

1. **任何「結果太好」的回測都要懷疑 bug**
2. **Pre-filter / universe 構建必須有單元測試**
3. **多台電腦同樣 repo 跑出不同結果 → 必有隱藏變異源**
4. **OOS 測試救不了 pre-filter 失效**（兩邊同樣污染）
5. **Truncated IC（只看 top-N candidates）會造成 selection bias** — 2026-04-16 審計新增教訓

> Bug 技術細節、修復 commit hash、完整修改檔案清單 → 詳見 `優化紀錄.md` Round 3 章節

---

## ⚠️ 以下為歷史紀錄（已失效，僅作對照）

**警告**：下方數字建立在 pre-filter 失效 universe，**已知為 overfit**。保留理由：reproducibility、對照新策略、記錄哪些設計曾試過。

### P1-P3 研究結論（污染 universe 上驗證，需重做）

| Phase | 結論 | 驗證狀態 |
|-------|------|---------|
| P1 | `max_same_industry` 2→3 改善 6M Alpha +23%、3Y Alpha +15% | ⚠️ 需在完整 universe 重驗 |
| P2 | `institutional_flow` 10→0% (rank IC -0.053) | ⚠️ 已由 Phase A 新版 Foreign Broker Pressure v2 取代 |
| P3 | 三因子組合最佳（vs vol-weighted ❌、quality ❌） | ⚠️ 已確認整個架構無 edge，結論作廢 |

### 污染 universe 下的「漂亮」績效（已知為幻象）

| 情境 | 修前 Sharpe | 修前 Alpha | 真相 |
|------|-------------|-----------|------|
| 3Y IS (2022-2024) | 1.85 | +48.43% | 修後 Sharpe 0.88, Alpha +8.84%（仍有部分真 alpha） |
| 2025 OOS | 1.81 | +8.16% | 修後 Sharpe 0.66, Alpha -18.4%（幾乎翻轉） |
| Walk-Forward 平均 | 1.38 | — | 修後 0.80，且 Bootstrap CI 跨零 |

### 方法論層面的正確部分（保留）

即使結論作廢，以下 **方法論** 仍有效：
- 「一次只改一項」（grid search 方法）
- 「區分選股改善 vs 加槓桿」（看 vol/beta 是否變化）
- 「caution exposure 調整屬典型 overfit 陷阱」— 概念正確，數據需重驗
- 「動能策略 alpha 來自追強勢股」— 定性方向正確

### Codex 對 P3 結論的補充（2026-03-31）

- ✅ 同意：波動率加權削弱動能 alpha 來源（邏輯成立）
- ✅ 同意：品質因子稀釋動能（artifact 支持）
- ⚠️ 但「三因子已是最佳」當時講太滿；更準確應說：「P3 測試的兩方向中三因子較佳」
- 2026-04-16 事後：**整個三因子架構已確認無 edge**，此 Codex 補充仍適用，但三因子本身就該汰換

---

## 研究態度 & Pro 標準

### 已內化的原則（從 P1-P7 + 2026-04-15/16 學到）

1. **統計顯著性優先於點估計**：任何 Sharpe/Alpha 都要附 Bootstrap CI，CI 跨零視為無 edge
2. **完整 universe 才算 IC**（top-N truncated = selection bias）
3. **Permutation test 是最強反駁工具**（比 < 隨機中位 = 策略無選股 skill）
4. **多重比較要修正**（測 N 個因子需 Deflated Sharpe 或 FDR）
5. ~~**Capacity analysis 必做**（1億/10億/100億 AUM 分層）~~ — 2026-04-16 晚 baseline 切換為 100 萬 retail，**capacity 分析不適用**
6. **Infrastructure 與 Signal 分開演化**（壞實驗不等於壞架構）

### Pro 標準下的新規範

- 任何新因子 → Pro IC 分析（full universe + FDR + regime-conditional + bootstrap + permutation）
- 任何新策略 → 3 層本金壓力測試 + market impact model
- 任何 recommendation → 同時給「策略層面」與「個人配置層面」但**分開討論**

> 詳細 Phase A1 4 週時程 + 5 因子實作規格 → **`Claude-Prompt.md`**
> Pro 研究標竿 + retail baseline 調整 → memory `feedback_pro_research_standard.md`
> 2026-04-16 晚更新：A5 Capacity 砍、A3 Long-Short 降優先、baseline 改 100 萬 NTD
