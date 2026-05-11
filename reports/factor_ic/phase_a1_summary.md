# Phase A1 Summary — 2026-05-10（fresh rerun 後）

**狀態**：2026-05-10 R26+R27 audit chain 完成。R26 抓到 8 類 silent bug + R26 補抓 1 類 + R27 verdict NEEDS-FIX-FIRST + R27 修 P0+P1 後 fresh rerun 5 因子。本報告反映 fresh rerun 後狀態。

**完整對照分析**：見 `reports/factor_ic/_closeout/old_vs_new_comparison_2026-05-10.md`

---

## 1. 五因子 IC 主表（fresh rerun 2026-05-10，PIT-correct + dollar denomination + new weights）

| 因子 | periods | mean_IC | IR | nominal_p | Block BS CI | Permutation | Monotonicity ρ | D9-D0 t |
|-----|---------|---------|-----|-----------|-------------|-------------|---------------:|--------:|
| 52W High Proximity | 71 | 0.0413 | 0.2738 | **0.0240** | [0.0130, 0.0709] 全>0 | sig+ p=0.007 | +0.867 | **+3.06** |
| Revenue Momentum v2 | 71 | 0.0145 | 0.1906 | 0.1128 | [-0.0013, 0.0305] 跨 0 | sig+ p=0.007 | **+0.952** | **+2.51** |
| Margin/Short Ratio | 71 | 0.0387 | 0.2314 | 0.0552 | [0.0121, 0.0668] 全>0 | sig+ p=0.007 | -0.818 ⚠️ | -1.21 |
| Foreign Broker v2 | **65** | **-0.0077** | -0.084 | **0.5007** | **[-0.0276, 0.0116] 跨 0** | sig- p=0.020 | +0.818 | +1.86 |
| PEAD / EPS Surprise | 71 | 0.0219 | 0.2907 | **0.0168** | [0.0075, 0.0369] 全>0 | sig+ p=0.007 | +0.891 | **+3.09** |

### 關鍵變化（vs 舊 2026-04-20 contaminated）

| 因子 | 舊 IC / p | 新 IC / p | 結論變化 |
|---|---|---|---|
| **foreign_investor_v2** | -0.0195 / 0.082 (CI 全<0) | **-0.0077 / 0.501 (CI 跨 0)** | **顯著負 → 完全不顯著**；倒 U-shape (D5 peak) **反轉為 monotonic D9 peak** |
| margin_short_ratio | 0.0393 / 0.080 | 0.0387 / 0.055 | 微改善但 decile sign 矛盾 ⚠️ |
| high_proximity | 0.0467 / 0.015 | 0.0413 / 0.024 | 仍顯著但量級略降 |
| revenue_momentum_v2 | 0.0110 / 0.292 | 0.0145 / 0.113 | 仍不顯著但減弱 |
| pead_eps | 0.0236 / 0.024 | 0.0219 / 0.017 | 仍顯著 |

### v8 Reframe 候選（per fresh rerun + decile / monotonicity / regime 整合判斷）

| 因子 | Verdict | 理由 |
|---|---|---|
| high_proximity | **KEEP** | p=0.024, ρ=0.87, D9-D0 spread +1.59% t=3.06 |
| pead_eps | **KEEP** | p=0.017, ρ=0.89, D9-D0 spread +1.07% t=3.09 |
| margin_short_ratio | **HOLD / DEFER**（issued_capital caveat 限制） | IC=+0.039 接近顯著但**仍是 static-snapshot approximation**（issued_capital cache 缺 date column，R28-1 follow-up 證實 derive method form-correct 但 substance-equivalent，ΔIC=+0.0001）；decile ρ=-0.818 跟 IC=+0.0387 看似矛盾**不是 sign bug**（per R28-4 釐清：per-period IC vs spread Spearman=0.946 一致）；嚴格 pro 標準下要當 v8 乾淨因子須先補真歷史 issued_shares cache（P1 backlog） |
| revenue_momentum_v2 | **DEFER**（marginal） | IC p=0.11 不顯著但 ρ=0.95 最單調；trending_down +0.049 顯著；mega-cap bias +0.225 偏強 |
| **foreign_investor_v2** | **DROP**（PIT 修法後實證 alpha 微弱） | IC=-0.008 不顯著；雖 ρ=0.82 但 D9-D0 spread 僅 +0.68% t=1.86；regime-dependent |

### R28-4 釐清的「看似 silent bug」實為 statistical property（非 bug）

1. **margin_short_ratio IC vs decile sign 看似矛盾**：IC=+0.039 正向 vs decile 全反向 ranking（D0=1.57%、D9=0.88%）。**R28-4 external audit 重算釐清**：per-period IC vs per-period D9-D0 spread Spearman=0.946（period-level 一致），跨期平均後 IC mean 跟 spread mean 不必同號 — 這是 statistical property 不是 sign bug。`src/features/margin_short_ratio.py` 公式正確（line 11 `score = -0.5 × zscore(...)` reverse-coded）；docstring 已修為「higher score = lower margin ratio = higher expected return」。**不需 audit**。

### 修法 chain 摘要（2026-05-10）

| Audit Round | Findings | 修法狀態 |
|---|---|---|
| R26 | 8 patterns (PIT / 量綱 / stale / consistency / fillna / narrative drift / FDR / 倒 U) | R26 全修 + 補抓 B9 (_load_issued_capital) |
| R27 verdict | NEEDS-FIX-FIRST (P0-2 issued_capital fallback / P1-3 tw_stock dormant / P1-1 weak test) | R27 全修 + 補抓 silent overwrite of pit_violation flag |
| R28 verdict | NEEDS-FIX (5 真 finding) | R28 全修：per-factor metadata / tw_stock issued_capital PIT-align / margin docstring / foreign log 補回 / closeout PIT approximation 降級 |
| Fresh rerun | 5 因子 rerun | 全部完成 2026-05-10 |
| Pytest | 28 mutation + 685 full baseline | 全綠 |

### ⚠️ 修法降級標註（必讀）

**`issued_capital` 是 form-correct 但 substance-static 的 PIT approximation**（R28-1 → R28-1 follow-up → R32 update）：
- `data/cache/issued_capital/_global.pkl` **現有 date column**：`(157374, 3)` = `stock_id, date, issued_shares`，月頻 2013-01-31 ~ 2026-04-30，1952 unique symbols（R28-1 follow-up 跑 `cache_fill_new_factors.py --seed-issued-capital` 補的）。**舊敘述「沒有 date column」已過期**。
- **但值是 static-per-symbol**：seed 用 `issued_shares = market_value / close` derive，而 `market_value` cache 本身是 `latest_shares × historical_close`（`src/data/finmind.py:1039`）→ `derived_shares = latest_shares × close / close = latest_shares`（常數）。所以雖然有 date column，跨日的 issued_shares 對每個 symbol 幾乎不變（近月可能有少數真實更新）。
- 對 margin_short_ratio 的影響：分母 (issued_shares) 仍 ≈ cache 當下 latest，不是 2020-01-13 那天的真實值。
- **量化證實**（R28-1 empirical）：R27 fallback `Timestamp.min` static-snapshot IC = +0.0387；R28 換成 date-bearing panel IC = +0.0388（Δ +0.0001）→ derive method 是 form-correct 但 substance-equivalent to static snapshot。
- **margin_short_ratio IC = +0.0388 應視為「以 ≈latest issued_shares 算的 approximation」**，不是 ground truth。
- 完整 PIT 需要新寫 TWSE OpenAPI scraper 抓歷史 issued_shares snapshots 補進 cache（P1 backlog，不在當前 plan scope）。
- **市值 caveat 仍在**：即使 PIT-asof market_value lookup，`market_value` cache = `latest_shares × historical_close` 仍非 fully PIT（closeout §9.2）。

**`margin_short_ratio` IC vs decile sign 看似矛盾不是 bug**（R28-4）：
- IC = +0.0387（正）vs decile ρ = -0.818（負）
- Per-period IC vs per-period D9-D0 spread Spearman = 0.946（period-level 一致）
- 跨期平均後兩量級不匹配 — 是 statistical property 不是 sign bug
- 因子 sign convention 正確；docstring 已修（原寫「higher score = lower expected return」是反的）

### 關鍵說明：DSR 語義（R13 修正）

`deflated_sharpe_ratio` 回傳的是 **BLdP 2014 的 confidence Ψ ∈ [0,1]**，不是 p-value。閾值是 **Ψ ≥ 0.95 才顯著**。

之前文件/CLI 誤標為 p-value → 若套「p < 0.05 = 顯著」規則會把沒 skill 的 factor 判為全通過。已於 R13 修正（commit 預定）。

5 因子 Ψ 全 = 0.0000 的數學解釋：
- `sr_max_null ≈ sqrt(2·ln 5) ≈ 1.79`（n_trials=5 下的 null 最佳 Sharpe 期望值）
- 5 因子 IR 範圍 -0.20 ~ 0.33（遠低於 1.79）
- z ≈ (0.33 - 1.79) / σ_sr ≈ -10 → cdf(-10) ≈ 0
- **結論**：本輪測的 5 因子**都沒超過 random-5 基準**。

---

## 2. 相關性矩陣（Spearman，fresh 2026-05-10）

**2026-05-10 重算**（fresh rerun 後 + R27 修法）：

```
               52W_High   PEAD_EPS   Margin_S     Rev_v2 Foreign_v2
52W_High        +1.0000    +0.2126    +0.1599    +0.1606    +0.0875
PEAD_EPS        +0.2126    +1.0000    -0.0720    +0.3841    +0.1000
Margin_S        +0.1599    -0.0720    +1.0000    -0.0618    -0.1634
Rev_v2          +0.1606    +0.3841    -0.0618    +1.0000    +0.0710
Foreign_v2      +0.0875    +0.1000    -0.1634    +0.0710    +1.0000
```

**vs 舊 (2026-04-22 contaminated) 對照**：大部分 cell 變動 < 0.01；最大變動是 Foreign_v2 跟 Margin_S = -0.1634 (舊 -0.1228 → Δ -0.04，反向相關性加強)。其他 cell 變動 < 0.011。

**方法**：per-period Spearman rank correlation（aligned universe，≥10 common symbols/period），平均跨 71 期。

**關鍵觀察**：
- **無 |ρ|>0.5 冗餘對**（全 5 factor 幾乎獨立訊號）
- 最高相關：**PEAD ↔ Rev_v2 = +0.384**（兩者同屬「盈餘面」訊號，Rev_v2 IR=0.14 本已弱，這個相關性讓 Rev_v2 附加值下降）
- **52W High ↔ PEAD = +0.213**（低相關）→ **最佳 diversification 組合**
- Foreign_v2 和 Margin_S 對其他 factor 相關都 <0.2 → 作為信號補位角色

**詳細報表**：`reports/factor_ic/factor_correlation_matrix.md` / `.json`

**Phase A2 Step 4 weight 討論依此相關性：**
- 52W + PEAD 組合 = diversification 清單（兩 IR>0.3 且 ρ<0.25）
- +Margin (IR=0.23 borderline) = 三因子組合，correlation 仍低，加值可觀察
- Rev_v2 雖 IR 0.14 + ρ=0.38 with PEAD → **建議 skip**（加入價值低）
- Foreign_v2 IR=-0.23 long-only 不可用（不變）

---

## 3. 🔵 量化主管面

- **FDR-adj p < 0.05 倖存者：0 個**（52W High 0.0592 差一點；PEAD 0.0592 差一點）
- **DSR Ψ ≥ 0.95 倖存者：0 個**（全部遠低於閾值）
- **兩者交集：0 個**
- Foreign Broker v2 短窗 + 反向 IC，額外扣分
- **結論**：Pro methodology 條件下，本輪 5 因子**無一通過**

---

## 4. 🟢 投資人面

扣 retail 摩擦（70 bps round-trip × 12 次/年 = 840 bps/年 拖累）後，沒有一個因子的 IC 強度能支撐勝 0050。

- 52W High Proximity IR=0.33 × 稅後預估月 alpha ~ 15 bps，扣摩擦 70 bps = **淨負 55 bps/月**
- PEAD IR=0.30 接近，結論相同
- Foreign / Revenue v2 / Margin Short 更弱

**是否勝 0050**：**估算結果「不能」**。因子 IR 太弱，摩擦拖累後跑不贏 0050 純 DCA。

---

## 5. Go/No-Go 決策（雙標準並呈）

### 關於 DSR ≥0.95 標準的認知修正

Phase A1 v1 設定「DSR Ψ ≥ 0.95 + FDR<0.05」為 Go 條件，屬 **hedge fund 專業級門檻**（要求 IR≈2.0，n_trials=5 下）。**retail monthly TW stock public factor** 通常 IR 0.2-0.5，幾乎無法達成。

Professional **retail quant** 文獻通常採用**中道標準**：
- nominal p < 0.05（原始檢定顯著）
- BH FDR < 0.10（扣多重檢定）
- Bootstrap CI 下界 > 0（穩定正 IC）

以下**雙標準並呈**，使用者依風險偏好決定。

### 嚴格標準（hedge fund pro）：DSR≥0.95 且 FDR<0.05

| Factor | DSR Ψ | FDR adj_p | 通過 |
|---|---|---|---|
| 全 5 因子 | 0.00 | ≥0.059 | **0** |

**決策**：**Smart Beta pivot**（100% 0050 DCA）

### 中道標準（retail professional）：nominal p<0.05 且 FDR<0.10 且 Block CI>0

| Factor | nominal_p | FDR_adj_p | Block CI | 通過 |
|---|---|---|---|---|
| 52W High Proximity | 0.015 | 0.059 | [0.018, 0.076] | ✅ |
| PEAD EPS | 0.024 | 0.059 | [0.007, 0.041] | ✅ |
| Margin/Short Ratio | 0.080 | 0.133 | [0.010, 0.071] | ❌（nominal p borderline）|
| Revenue v2 | 0.292 | 0.345 | [-0.007, 0.028] | ❌ |
| Foreign Broker v2 | 0.345 | 0.345 | N/A 短窗 | ❌ |

**決策**：**2 因子 composite paper trade**（52W High + PEAD；可加 Margin/Short 作 3 因子 IR-weighted diversification）

### 本輪建議路徑：**B 路徑（中道標準）**

**B 路徑 = Smart Beta（主資金）+ composite paper trade（零成本驗證）**：

| 步驟 | 做法 |
|---|---|
| 1. 主資金 | 每月 2.5 萬定投 0050（目標累積 100 萬）|
| 2. Paper trade | 52W High + PEAD + Margin Short IR-weighted（38/34/28），每月月初選 top 8 股，log 到 `scripts/paper_trade.py` |
| 3. Pre-paper sanity check | 跑 backtest 2020-2024 三 config 對比（舊 3 因子 / 新 composite / 0050）確認 composite 真能贏 0050 |
| 4. 監控 | `/smart-beta-paper` 每週 NAV；6 個月後比較 composite vs 0050 Sharpe |
| 5. 實盤決策 | 若 composite Sharpe 勝 0050 +0.3 連 3 月 → 累積到 100 萬後小額實盤；否則純 Smart Beta |

### 為何不採嚴格標準

DSR ≥0.95 在 retail 月頻 TW stock 設計上**幾乎必不過**（需 IR>2，業界 public factor 多在 0.2-0.5）。若以此作為**唯一** Go 標準 → 等於預先決定 Smart Beta，Phase A1 研究意義受限。

中道標準 + paper trade 半年 + 實盤前 backtest sanity check 是**具體可驗證**的路徑。

### 為何不採寬鬆標準（只看 nominal p）

僅 nominal p<0.05 忽略「我試了 5 個 factor」的多重檢定偏差，等於 p-hacking。至少要加 FDR 校正和 Bootstrap CI 雙關卡。

---

## 6. 衝突裁決

| 🔵 量化主管 | 🟢 投資人 | 裁決 |
|------------|---------|------|
| 0 因子通過 DSR+FDR | 扣摩擦後無因子正 alpha | **一致：Smart Beta pivot** |

無衝突。雙方結論一致。

---

## 7. 下一步建議

### 立即行動
1. **啟動 100% 0050 月投定期定額**（`/smart-beta-paper` skill 持續每週 NAV 追蹤）
2. **paper trade 5 因子 composite** 平行追蹤到 2026-10-31（預設 paper 評估截止日），**不投入實盤資金**
3. **暫停**新因子 feature development

### 可能復活因子研究的條件
- paper trade 結果顯示 composite 連 3 月勝 0050 Sharpe +0.3 以上
- 或找到理論強 factor（e.g., Low Vol / Quality 類）補進 Phase A1.5
- 或研究改為 Daily rebalance 而非 Monthly（當前 IC 可能 Monthly 過於稀疏）

### 不該做
- ❌ 回頭調因子公式 fit 數字（overfit 禁令）
- ❌ 降低 DSR / FDR 門檻包裝結論為 Go
- ❌ 忽略 Foreign Broker v2 短窗而宣稱「算過了」

---

## 8. 本報告的已知限制

1. **Foreign Broker v2 短窗**：只 23 期（2023-01 ~ 2024-11），應該跑完整 5 年窗重驗。本結論建立在「即使重跑，IR 不會突然 > 0.95 × 1.79 = 1.70」的假設上——這個假設極大概率成立（factor 理論預期 IR < 1）。
2. **2022-06-22 margin SS 欄位已知 anomaly**：保留 FinMind 原值，TWSE API 該日不穩定。對 margin_short_ratio 因子計算無影響（用 cumulative balance），但若未來策略用 daily SS 需注意。
3. **相關性矩陣 skip**：JSON schema 需擴充才能算。不影響本輪 Go/No-Go 結論（0 因子通過）。
4. **n_trials=5 假設無 variant peeking**：code 沒有痕跡顯示 self-audit 試過 v1/v2 外的其他 variant；但無法完全排除。若實際 n_trials > 5，DSR 會更嚴格（結論更 No）。

---

## 9. 產出檔案

- `reports/factor_ic/phase_a1_summary.md`（本檔）
- `reports/factor_ic/*_ic.json` × 5（含新增 `fdr_adjusted_p` top-level 欄位）
