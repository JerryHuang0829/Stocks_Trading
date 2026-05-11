# 5 因子 IC 舊 vs 新 對照分析 — 2026-05-10

**Plan reference**: `C:/Users/chongweihuang/.claude/plans/codex-pro-codex-precious-reef.md`
**修法 audit chain**: Codex R26 (8 patterns) + Claude R26 (B9 補抓 _load_issued_capital) + Codex R27 (4 P0/P1 verdict NEEDS-FIX) + Claude R27 (P0-2 / 新 P0 / P1-1 / P1-3 修)
**Fresh rerun timestamps**:
  - foreign_investor_v2: 2026-05-10 04:22:38
  - margin_short_ratio: 2026-05-10 05:04:29
  - high_proximity: 2026-05-10 05:09:33
  - revenue_momentum_v2: 2026-05-10 05:25:30
  - pead_eps: 2026-05-10 05:35:29

**前提**：
- 舊（contaminated）= 2026-04-20 跑的版本（foreign_investor_v2 用 latest mv 違反 PIT；其他 4 因子用舊 universe filter / 較短期間）
- 新（PIT-correct + dollar 制 + new weights + last20 stale guard + covered-weight rescale）= 2026-05-10 fresh rerun

---

## 1. 5 因子 IC 主表（舊 vs 新）

| # | 因子 | 舊 mean_IC | **新 mean_IC** | Δ | 舊 p | **新 p** | 顯著性變化 |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | foreign_investor_v2 | -0.0195 | **-0.0077** | +0.0118 | 0.0816 | **0.5007** | **顯著負 → 完全不顯著** |
| 2 | margin_short_ratio | +0.0393 | +0.0387 | -0.0006 | 0.0797 | **0.0552** | 接近顯著（CI 全正） |
| 3 | high_proximity | +0.0467 | +0.0413 | -0.0054 | 0.0152 | 0.0240 | 仍顯著 (p<0.05) |
| 4 | revenue_momentum_v2 | +0.0110 | +0.0145 | +0.0035 | 0.2923 | 0.1128 | 仍不顯著但減弱 |
| 5 | pead_eps | +0.0236 | +0.0219 | -0.0017 | 0.0237 | **0.0168** | 仍顯著 (p<0.05) |

| # | 因子 | 舊 IC IR | **新 IC IR** | 舊 n_periods | **新 n_periods** | 樣本變化 |
|---|---|---:|---:|---:|---:|---|
| 1 | foreign_investor_v2 | -0.2097 | **-0.084** | 71 | **65** | -6 (P1-C stale guard 嚴 4-5 期 skip + 1 期 0 forward returns) |
| 2 | margin_short_ratio | +0.2322 | +0.2314 | 59 | **71** | +12 (延伸至 2025-11) |
| 3 | high_proximity | +0.3256 | +0.2738 | 59 | **71** | +12 |
| 4 | revenue_momentum_v2 | +0.1384 | +0.1906 | 59 | **71** | +12 |
| 5 | pead_eps | +0.3025 | +0.2907 | 59 | **71** | +12 |

| # | 因子 | 舊 Bootstrap CI 95% (block) | **新 Bootstrap CI 95% (block)** | CI 跨 0? |
|---|---|---|---|---|
| 1 | foreign_investor_v2 | [-0.0383, -0.0017] 全<0 | **[-0.0276, +0.0116]** | **YES（不再顯著）** |
| 2 | margin_short_ratio | [0.0098, 0.0715] | **[0.0121, 0.0668]** 全>0 | NO（仍顯著正） |
| 3 | high_proximity | [0.0176, 0.0763] | [0.0130, 0.0709] 全>0 | NO |
| 4 | revenue_momentum_v2 | [-0.0068, 0.0282] | [-0.0013, 0.0305] | YES |
| 5 | pead_eps | [0.0065, 0.0410] | [0.0075, 0.0369] 全>0 | NO |

---

## 2. 新增診斷指標（Codex R26 推薦）

### 2.1 Decile 分桶平均報酬（across periods, 月報酬）

| Decile | foreign_v2 | margin_S | high_prox | rev_v2 | pead_eps |
|---|---:|---:|---:|---:|---:|
| D0 | 0.94% | **1.57%** | 0.87% | 0.84% | 0.44% |
| D1 | 0.70% | 1.04% | 0.65% | 0.94% | 0.64% |
| D2 | 1.00% | 1.15% | 0.95% | 0.94% | 0.90% |
| D3 | 1.40% | 1.14% | 0.67% | 0.98% | 0.95% |
| D4 | 1.12% | 1.09% | 0.86% | 1.17% | 1.29% |
| D5 | 1.37% | 0.86% | 1.10% | 1.20% | 1.39% |
| D6 | 1.29% | 1.02% | 1.12% | 1.09% | 1.58% |
| D7 | 1.21% | 0.70% | 1.50% | 1.51% | 1.58% |
| D8 | 1.44% | 0.80% | 1.75% | 1.62% | 1.55% |
| D9 | **1.62%** | 0.88% | **2.46%** | 1.58% | 1.51% |

### 2.2 Monotonicity Spearman ρ（decile_idx vs avg_ret）

| 因子 | ρ | 解讀 |
|---|---:|---|
| **foreign_investor_v2** | **+0.818** | **單調正向**（從舊 +0.103 反轉，Codex R26 倒 U-shape 是 PIT+量綱 artifact） |
| margin_short_ratio | **-0.818** | 強反向（D0 最高、D9 最低）— **與 IC=+0.0387 矛盾**，需 audit |
| high_proximity | +0.867 | 強單調，top decile 2.46%/月 vs bottom 0.87% |
| revenue_momentum_v2 | +0.952 | 最強單調 |
| pead_eps | +0.891 | 強單調但 D6-D9 plateau |

### 2.3 Peak-in-middle t-stats

| 因子 | D5-D0 t | D5-D9 t | D9-D0 t | 結論 |
|---|---:|---:|---:|---|
| foreign_investor_v2 | 2.13 | -0.75 | **1.86** | D9 reverses to top |
| margin_short_ratio | -1.83 | -0.10 | -1.21 | D0 highest |
| high_proximity | 0.68 | -4.46 | **3.06** | strong long-only |
| revenue_momentum_v2 | 1.72 | -2.05 | **2.51** | strong long-only |
| pead_eps | **4.22** | -0.38 | **3.09** | strong long-only with mid plateau |

### 2.4 Price-Score Correlation 71期 mean ± std（mega-cap / scale bias 偵測）

| 因子 | mean | std | min | max | 解讀 |
|---|---:|---:|---:|---:|---|
| foreign_investor_v2 | +0.079 | 0.057 | -0.07 | +0.23 | mega-cap bias 仍在（dollar 制沒消除） |
| margin_short_ratio | -0.041 | 0.054 | — | — | 微反向（融資高股偏低價，正常） |
| high_proximity | +0.111 | 0.084 | — | — | mega-cap |
| **revenue_momentum_v2** | **+0.225** | 0.035 | — | — | **強 mega-cap bias** |
| pead_eps | +0.127 | 0.049 | — | — | mega-cap |

---

## 3. Regime-Conditional IC（Bull / Ranging / Bear, 新版）

| 因子 | trending_up | ranging | trending_down |
|---|---:|---:|---:|
| foreign_investor_v2 | -0.0268 | -0.0121 | **+0.0348** |
| margin_short_ratio | **+0.0820** | +0.0358 | -0.0251 |
| high_proximity | **+0.0732** | +0.0565 | -0.0478 |
| revenue_momentum_v2 | +0.0002 | +0.0098 | **+0.0491** |
| pead_eps | -0.0009 | +0.0248 | **+0.0523** |

**Regime pattern 摘要**：
- **trending_up（牛市）**：margin_short / high_proximity 強；其他 4 因子弱或反向
- **trending_down（熊市）**：foreign_broker / revenue_momentum / pead_eps 強；high_proximity / margin_short 反向
- **ranging**：表現平淡（~0）

---

## 4. Permutation Significance（per-factor null distribution）

| 因子 | Permutation 結果 | p_emp |
|---|---|---:|
| foreign_investor_v2 | significant_negative | 0.0199 |
| margin_short_ratio | significant_positive | 0.0066 |
| high_proximity | significant_positive | 0.0066 |
| revenue_momentum_v2 | significant_positive | 0.0066 |
| pead_eps | significant_positive | 0.0066 |

**注意**：foreign_investor_v2 permutation 仍 negative 但 single-period IC = -0.0077 量級小且 95% CI 跨 0 → permutation 顯著但無 economic significance。

---

## 5. 因子相關性矩陣（fresh，2026-05-10）

|            | 52W_High | PEAD_EPS | Margin_S | Rev_v2  | Foreign_v2 |
|------------|---------:|---------:|---------:|--------:|-----------:|
| 52W_High   |  +1.0000 |  +0.2126 |  +0.1599 | +0.1606 |    +0.0875 |
| PEAD_EPS   |  +0.2126 |  +1.0000 |  -0.0720 | +0.3841 |    +0.1000 |
| Margin_S   |  +0.1599 |  -0.0720 |  +1.0000 | -0.0618 |    -0.1634 |
| Rev_v2     |  +0.1606 |  +0.3841 |  -0.0618 | +1.0000 |    +0.0710 |
| Foreign_v2 |  +0.0875 |  +0.1000 |  -0.1634 | +0.0710 |    +1.0000 |

**vs 舊版 (archived pre-rerun)**：
- 大部分 cells 變動 < 0.01（穩定）
- foreign_v2 跟其他因子相關性微降（vs old 修法影響最大）
  - 52W × Foreign_v2: 0.0982 → 0.0875 (Δ -0.011)
  - PEAD × Foreign_v2: 0.1080 → 0.1000 (Δ -0.008)
  - Margin_S × Foreign_v2: -0.1634（最強反向 pair）

**v8 composite candidate 篩選 implication**：
- foreign_investor_v2 跟 margin_short_ratio 相關 -0.16 → 可考慮做 hedge / pair
- Rev_v2 vs PEAD +0.38 重疊度高 → 不適合一起進 composite
- 52W vs Margin_S +0.16 + Rev_v2 +0.16 → low-corr 可組合

---

## 6. v8 Reframe 建議（per factor）

| 因子 | 新 verdict | 理由 |
|---|---|---|
| **foreign_investor_v2** | **DROP** | IC=-0.008 不顯著 / Permutation negative / Bootstrap CI 跨 0；雖 monotonicity ρ=0.82 但 D9-D0 spread 僅 +0.68% t=1.86；PIT 修法後實證 alpha 微弱 |
| **margin_short_ratio** | **HOLD / DEFER**（issued_capital caveat 限制；reconciled per R28-4 + R29） | IC=+0.039 接近顯著但仍是 **static-snapshot approximation**（R28-1 follow-up 證實 derive method form-correct 但 substance-equivalent，ΔIC=+0.0001）；decile ρ=-0.818 跟 IC=+0.0387 看似矛盾**不是 sign bug**（per R28-4：per-period Spearman=0.946 一致；docstring 已修）；要當 v8 乾淨因子須先補真歷史 issued_shares cache（P1 backlog 4-8 hr） |
| **high_proximity** | **KEEP**（v8 long-only candidate） | IC=+0.041 p=0.024 顯著；ρ=0.87；D9-D0 spread +1.59% t=3.06；trending_up regime +0.073 |
| **revenue_momentum_v2** | **DEFER**（marginal） | IC=+0.014 p=0.11 不顯著但 ρ=0.95 最單調；mega-cap bias +0.22 偏強；trending_down +0.049 顯著 → 可做 regime-conditional |
| **pead_eps** | **KEEP**（v8 long-only candidate） | IC=+0.022 p=0.017 顯著；ρ=0.89；D9-D0 spread +1.07% t=3.09；trending_down +0.052 |

---

## 7. 修法影響量化總結

| 修法 | 主要影響因子 | IC 影響量級 | 結論變化 |
|---|---|---:|---|
| P0-A PIT-asof market_value | foreign_investor_v2 | IC -0.0195 → -0.0077 (Δ +0.012) | 顯著負 → 不顯著 |
| P0-B 量綱 dollar 制 | foreign_investor_v2 | 倒 U → monotonic（ρ 0.10 → 0.82） | 推翻「反向因子」敘述 |
| P1-A PIT-asof issued_capital | margin_short_ratio | IC 微變（issued_shares 變動少） | 結論未實質改變 |
| P1-C last20 stale guard 35d | foreign_investor_v2 | n 71 → 65（5-6 期 skip） | 樣本損失但更嚴謹 |
| P1-D consistency weight 0.20→0 | foreign_investor_v2 | IC 弱化（移除 noisy sub-signal） | 訊雜比改善 |
| P1-E covered-weight rescale | foreign_investor_v2 | symbol drop (covered<50%) | 嚴格度提升 |
| Universe 範圍延伸至 2025-11 | 4 因子 | n 59 → 71 (+12) | 多 1 年 OOS data |

---

## 8. ⚠️ 修法降級標註（Codex R28 audit 發現）

### 8.1 issued_capital 不是真 PIT — 是 static-snapshot approximation

**事實**：`data/cache/issued_capital/_global.pkl` 原本只有 `[stock_id, issued_shares]` 欄位，**沒有 `date` column**。

#### 嘗試 R28-1 follow-up：跑 `seed_issued_capital`（user 拍板 C「跑 A 量化偏差」）

**步驟**：
1. ✅ 跑 `scripts/cache_fill_new_factors.py --seed-issued-capital`
2. ✅ Cache 從 1962 rows × 2 cols → **157374 rows × 3 cols**（加 date column 涵蓋 2019-01-31 ~ 2026-04-30）
3. ✅ 重跑 margin_short_ratio fresh rerun（用新 panel）

**實證量化結果**：

| 指標 | R27 (fallback Timestamp.min) | **R28 (new panel)** | Δ |
|---|---:|---:|---:|
| n_periods | 71 | 71 | 0 |
| **mean_ic** | **+0.0387** | **+0.0388** | **+0.0001** |
| ic_ir | 0.2314 | 0.2319 | +0.0005 |
| p_value | 0.0552 | 0.0547 | -0.0005 |
| Bootstrap CI block | [0.0121, 0.0668] | [0.0122, 0.0668] | 微差 |
| Permutation | sig+ p=0.0066 | sig+ p=0.0066 | 一致 |
| Regime trend_up | +0.0820 | +0.0821 | +0.0001 |
| Regime trend_down | -0.0251 | -0.0249 | +0.0002 |

#### 結論：seed_issued_capital 是 form-correct 但 substance-equivalent

**Root cause**：`seed_issued_capital` 邏輯是 `issued_shares = market_value / close`，但 `market_value` cache 本身是用 `latest_shares × historical_close` 算（per `src/data/finmind.py:1033 _compute_market_value_from_twse`）。所以 derive shares = `latest_shares × historical_close / historical_close` = **latest_shares 對所有 date 不變**。

意思：seed 後 cache 結構正確（有 date column），UserWarning 消失，但每個 stock_id 的 issued_shares 對所有 date 都是同一值 = latest snapshot。

**對 IC 數值的真實影響**：~0.0001 量級，可視為 numerical rounding noise。

#### 真正 PIT 修法路徑（P1 backlog，本輪未做）

要拿真正歷史 issued_shares，需要：
1. 寫新 TWSE OpenAPI scraper（如 `t187ap03_L` 或類似 monthly issued capital snapshot endpoint）
2. 抓 5+ 年歷史 snapshots
3. 取代當前的 derive method
4. 重跑 margin_short_ratio fresh rerun

**Effort estimate**: 4-8 hr（需研究 TWSE endpoint + 寫 scraper + cache fill + 重跑）

**v8 reframe 對 margin_short_ratio 的 implication**：
- 當前 IC = +0.0388 應視為 **static-snapshot approximation**，不是 fully PIT-correct
- 但實質效果跟 fully PIT 差距可能很小（因台股 issued_shares 變動少）
- 嚴格 pro 標準下 v8 reframe **要使用 margin_short_ratio 必須先補 historical issued_shares cache**
- 否則接受永久 caveat：「margin_short IC 是 static-snapshot approximation」

### 8.2 已知未修 P1/P2 backlog

1. **margin_short_ratio IC vs decile sign 解讀（不是 sign bug）**（Codex R28-4 釐清）— IC=+0.0387 跟 decile ρ=-0.818 看似矛盾，但 Codex R28 重算 per-period IC vs per-period D9-D0 spread 的 Spearman = 0.946（period-level 一致）。差異來自跨期平均後 IC mean 跟 spread mean 數學上不必相等。**修法**：(a) `src/features/margin_short_ratio.py:7` docstring 「higher factor score = lower expected return」是反的，應為「higher score = lower margin ratio = higher expected return（reverse-coded）」；(b) 對照分析應補 statistical 解讀。

2. **`src/portfolio/tw_stock.py:1117`** 仍用 `_bulk_fetch_latest_market_value`（非 PIT-asof）。Codex R28 P1-3 已加 close_by_symbol 但 mv 仍 latest。Live 模式 (as_of=today) 不受影響；Backtest 模式需切 PIT mv panel。

3. ✅ **`src/portfolio/tw_stock.py:1039`** `_load_issued_capital_dict` ~~portfolio 層 dormant function 也用 latest~~ → **R28-2 已修**：改用 `_load_issued_capital_panel` + `_issued_capital_asof` helper（IC pipeline 同套），signature 加 `as_of` keyword default None；caller line 1131 傳 `as_of=as_of_ts`。**Caveat**：實際 fallback 仍是 static snapshot（同 8.1 issued_capital cache 缺 date column 限制），但程式邏輯與 IC pipeline 對齊。

4. **`ic_analysis.py` JSON dump cp950 byte 污染** — fresh rerun 寫的 JSON 含 cp950 em dash bytes (0xa1 0x58)。本 closeout cycle 已逐個 patch 為 utf-8，但 ic_analysis.py 內部 dump 邏輯需顯式 `encoding="utf-8"`。

5. **mega-cap bias 仍存在（金額制沒消除）** — foreign_investor_v2 +0.079, revenue_momentum_v2 +0.225。size-neutralize 或 industry-neutralize 預處理可進一步測試。

---

## 9. ⚠️ R30 額外架構 caveat（Codex R30 抓的）

### 9.1 Permutation null vs time-series IC verdict aggregation

`reports/factor_ic/foreign_investor_v2_ic.json` 同時記錄：
- **time-series IC**: mean=-0.0077, p=0.5007, Bootstrap CI 跨 0 → **不顯著**
- **permutation null**: `significant_negative` p_emp=0.0199 → 看似顯著

**Codex R30 finding 1 解釋**：`ic_analysis.py:501-513 permutation_baseline` 是 per-period shuffle factor scores keep returns。null std 很小（只在 cross-section 重排），對極小 mean IC 容易判定「顯著」。但**正式因子 verdict 應以 time-series IC + block bootstrap CI 為主**（樣本期 71 期的真實統計不確定性）。

**讀者請以 time-series IC 為 primary verdict**。Permutation 是 secondary check（驗證 cross-section ranking 對 return 有非平凡關係），不能單獨判定因子顯著。

foreign_investor_v2 真實結論：**mean IC=-0.0077 p=0.50 等同噪音；permutation significant_negative 是 verdict aggregation artifact**，不是「微弱負向因子」。

### 9.2 market_value cache 不是 fully PIT（即使 PIT-asof lookup）

`src/data/finmind.py:1032 _compute_market_value_from_twse`：market_value cache 用 **latest_shares × historical_close** 公式。意思：
- 每個 (stock_id, date) row 的 close 是真歷史值 ✅
- 每個 row 的 shares 是 cache build 時點的 latest snapshot ❌

對 foreign_investor_v2 cum_ratio (= cum_dollar / market_value) 影響：
- 分子 `cum_dollar = sum(net_shares × historical_close)` PIT-correct
- 分母 `market_value(asof) = latest_shares × close_at_asof` 用 historical close 但 latest shares
- 比例 ≈ PIT 但不 fully PIT

**台股 shares 變動少**（除權息/減增資）→ latest ≈ historical → ratio 接近 fully PIT，但嚴格 pro 標準仍非真 PIT。

**完整修法**（P1 backlog 同 issued_capital）：寫新 TWSE OpenAPI scraper 抓真歷史 shares snapshots 重 build market_value cache。

### 9.3 thresholds.py default 跟 yaml/module 之前 silent drift（R30-3 已修）

R26-R28 修法只改 `config/factor_thresholds.yaml` + `src/features/foreign_investor_v2.py::SUBSIGNAL_WEIGHTS`，**漏改 `src/utils/thresholds.py:76` default fallback**（仍是舊權重 0.40/0.20/0.20/0.20）。Codex R30-3 抓到。已修為 0.50/0.25/0.25/0.0 對齊。

### 9.4 default profile institutional_flow=0.10 silent legacy（R30-6 已修）

`src/portfolio/tw_stock.py:74-84` `tw_3m_stable` profile + line 145-156 `tw_6m_defensive` profile 之前 default 仍含 `institutional_flow: 0.10`（legacy 因子，IC=-0.053 已 fail）。切換 profile 可能 silent 帶回。已改為 0.00 對齊當前 active settings.yaml。

6. **`reports/factor_ic/_audit/fresh_rerun_foreign_investor_v2_2026-05-10.log`** 缺**（Codex R28-3）— foreign_investor_v2 fresh rerun 是直接 background task 跑（沒走 wrapper script），原始 log 留在 task output 但沒拷到 `_audit/` dir。已從 background task output 補回。

7. **Codex R28-5 metadata provenance**: 4 個非 foreign_broker 因子 JSON 的 `pit_violation.fixes_applied` 之前被 enrich script 寫成 foreign_broker 專用 fixes（P0-B / P1-C / P1-D / P1-E 對它們不適用）。已修 `_enrich_factor_ic_diagnostics.py` 加 per-factor differentiation + re-patch 5 因子 JSON。

---

## 9.5 Codex R31 audit 後續修法（2026-05-11）+ Phase D 3 因子 single IC

### 9.5.1 R31 Codex 6 finding 處理

| # | R31 Finding | 修法 | 嚴重度 |
|---|---|---|---|
| **R31-1** | Phase D 3 因子 IC JSON 缺 enrichment diagnostics（`run_phase_d_factor_ic.py` 只寫 `result.to_dict()`，沒補 decile / monotonicity / peak / price_score_corr / pit_violation；test 名稱說 schema parity 但只檢查 overall/by_regime/by_bucket） | **真 P1 已修**：(a) `_enrich_factor_ic_diagnostics.py` 加 Phase D 分支 + run 在 3 個 JSON 上 (b) `run_phase_d_factor_ic.py` 結尾自動 call enrich（`--no-enrich` 可跳） (c) `test_load_factor_ic_phase_d_3factors` 強化為驗 8 個 enrichment 欄位 + top-level key parity vs high_proximity | **P1 真 bug** |
| **R31-2** | `config/settings_D1.yaml:95` + `settings_D1_v2.yaml:95` + `settings_D2.yaml:94` + `settings_D3.yaml:94` 仍留 `foreign_broker_v2: 0.0`（值 0.0 不是即時交易錯，但若日後用 D profile 調權重會被新程式忽略） | **真 P2 已修**：4 檔全改 `foreign_investor_v2: 0.0` + R31 comment | **P2 真 bug** |
| **R31-3** | `dashboard/pages/2_因子IC測試.py` 主表已擴 8 因子，但後面 ref_table（line 347-349）+ caption（line 354-360）仍寫 quality_v3 / industry_momentum / idio_vol_max「不單獨測 IC」+ "本頁主表只列 5 個 Phase A1 因子" — **同頁自相矛盾**，誤導讀者 | **真 P1 已修**：ref_table 9 行改為 8 走 single IC（標 2026-05-11 補測 + per-factor universe + 同時嵌入 D-X composite）+ 1 走 spike；caption 改為「Phase D 3 因子的 single-factor IC（2026-05-11 補測）」並保留「single IC ≠ portfolio robust」+ FDR m=5 邊界 caveat | **P1 真 bug** |
| **R31-4** | `src/features/foreign_investor_v2.py` 硬編 `SUBSIGNAL_WEIGHTS` / `LAST20_MAX_CALENDAR_SPAN_DAYS=35` / `top_pct=0.20` default；整個檔案只有 rank_stability min universe 讀 `get_threshold()` → `config/factor_thresholds.yaml :: factor_specific.foreign_investor_v2` 的 weights/last20/top_pct 其實沒被讀（違反 CLAUDE.md「禁止 hardcode yaml 值到 src/features/」） | **真 P2 架構債已修**：加 `_subsignal_weights()` / `_last20_max_calendar_span_days()` / `_rank_stability_top_pct()` 3 個 helper（同 `_rank_stability_min_universe()` pattern，yaml 為 live source + 模組常數為 fallback）；`_subsignal_weights()` 額外驗 key 集合 + active weight sum≈1.0（malformed yaml fall back to constant）；replace 3 個 usage site；加 2 個 test（`test_subsignal_weights_yaml_in_sync_with_constant` + `test_last20_span_and_top_pct_yaml_resolved`） | **P2 架構債** |
| R31-5 | `src/data/finmind.py:1039/1096` market_value = latest_shares × historical_close 不是 fully PIT | 已知 caveat（section 9.2 已 documented），無需新修 | (acknowledged) |
| R31-6 | foreign_investor_v2 IC=-0.0077 / p=0.5007 / 65 期正 33 負 32 → 不穩定，「沒 alpha」非「應反向用」；Codex 手算 2024 三期 IC 0.0784/-0.0108/-0.0121 也支持 | 確認 Claude 結論一致（DROP，不是 invert） | (confirms conclusion) |

### 9.5.2 Phase D 3 因子 single IC 結果（2026-05-11 補測，per-factor 自然 universe）

| 因子 | n_periods | mean IC | p_value | IC IR | bootstrap CI 95% | DSR | permutation | verdict |
|---|---:|---:|---:|---:|---|---:|---|---|
| **idio_vol_max** | 71 | **+0.0588** | **0.0077** | **+0.326** | [0.0283, 0.0923] | 0 | significant_positive (p_emp 0.0066) | 🟡 **Normal**（8 因子最強 single IC） |
| quality_v3 | 71 | -0.0093 | 0.3815 | -0.104 | [-0.0323, 0.0124] | 0 | significant_negative (p_emp 0.0066) | 🔴 Fail |
| industry_momentum | 71 | -0.0120 | 0.4002 | -0.101 | [-0.0344, 0.0111] | 0 | significant_negative (p_emp 0.0066) | 🔴 Fail |

**8 因子 single IC 排名（mean IC 由強到弱）**：idio_vol_max +0.0588 > high_proximity +0.0413 > margin_short_ratio +0.0388 > pead_eps +0.0219 > revenue_momentum_v2 +0.0145 > foreign_investor_v2 -0.0077 > quality_v3 -0.0093 > industry_momentum -0.0120。

**重要 caveat**：
- Phase D 3 因子用 per-factor 自然 universe（**沒做 Phase A1 5 panel 的 intersection**），跟 Phase A1 5 因子 universe 不完全可比
- DSR=0 across all 3：IC IR < BLdP expected max SR (≈1.65 for n_trials=5) → 同 foreign_investor_v2 的 DSR=0 情況
- single IC 顯著 ≠ portfolio robust：v7 cell sweep（D-G 給 idio_vol_max 20%）仍 CONFIRM-NO-GO；idio 升權測試走 Plan v8（pre-reg DRAFT，等 Codex audit + lock）

### 9.5.3 idio_vol_max / margin_short_ratio：rank IC 正 vs decile arithmetic mean 反向

延續 section 2.2 對 margin_short_ratio「IC=+0.0387 vs mono_rho=-0.818 矛盾」的觀察 —— idio_vol_max 也是同款：

| 因子 | mean IC（rank Spearman） | decile_avg D0 | decile_avg D9 | mono_rho | 解讀 |
|---|---:|---:|---:|---:|---|
| idio_vol_max | +0.0588 | 2.22%/月 | 0.43%/月 | -0.952 | rank-IC 正、arithmetic-mean 反向 |
| margin_short_ratio | +0.0388 | 1.57%/月 | 0.88%/月 | -0.818 | 同款 |

**結論（非 bug，是 positive-skew lottery 結構）**：兩個都是「anti-froth / anti-lottery」型因子（margin_short 高分=低融資=不過熱；idio_vol_max 高分=低特質波動+低 MAX=不像樂透）。它們的低分 decile（高融資froth / 高 idio 樂透股）有 **fat right tail** —— 在多數期被 rank 在後面（→ Spearman rank IC 正），但偶爾爆漲幾期把 arithmetic mean 拉高（→ decile_avg 反向 + mono_rho 負）。這是 Bali-Cakici-Whitelaw 2011 MAX 效應的已知特徵（樂透股：算術平均高，但多數時候輸）。**rank IC 是 production 用的訊號（factor module 已含反向設計），decile_avg/mono_rho 是揭露 skew 結構的診斷指標** —— 兩者方向不一致是預期的，不是 sign bug。R28 標的「margin_short_ratio 需 audit」可結論為此。

### 9.5.4 已知未修 sibling（留下一輪）→ R32 已修

- ~~`src/features/revenue_momentum_v2.py:39 SUBSIGNAL_WEIGHTS` 跟 yaml `factor_specific.revenue_momentum_v2.weights` 同款 R31-4 架構債~~ → **R32 已修**（見 §9.6）。

---

## 9.6 Codex R32 audit 後續修法（2026-05-11）

### 9.6.1 R32 Codex 3 finding 處理

| # | R32 Finding | 修法 | 嚴重度 |
|---|---|---|---|
| **R32-1** | `src/features/revenue_momentum_v2.py:39 SUBSIGNAL_WEIGHTS` 硬編、yaml `factor_specific.revenue_momentum_v2.weights` 從沒被讀，且 key 不符（yaml `accel_3m3m`/`pct_24m` vs module `accel`/`percentile`）；`thresholds.py:11` docstring 還寫「revenue module reads weights」但其實沒讀 — 同 R31-4 foreign 同型 | **真 P2 架構債已修**：(a) yaml + `thresholds.py` `_DEFAULTS` key 改 `accel_3m3m→accel` / `pct_24m→percentile` 對齊 module (b) `revenue_momentum_v2.py` 加 `_subsignal_weights()` helper（yaml live source + 常數 fallback + 驗 key 集合 + sum≈1.0）(c) `_composite_score` 改用 `_subsignal_weights()` (d) `thresholds.py:8-16` docstring 改為**準確**列出誰真的讀 yaml（只 foreign + revenue）+ 標 high_prox/pead/margin 為 SPEC MIRROR (e) yaml 對應 3 section 加 `# SPEC MIRROR` 註解 (f) 加 test `test_subsignal_weights_yaml_in_sync_with_constant` | **P2 架構債** |
| **R32-2** | `reports/phase_d_v8/H_v8_idio_led_preregistration_draft.md:181` pre-rerun checklist 寫 「pytest baseline 687 passed」但實際 689（R31-fix 後） | **真 P3 已修**：改為「685 → 689 後含 Phase D IC schema parity + foreign/revenue yaml-sync tests；Codex R32 實測 689」 | **P3 stale doc** |
| **R32-3** | `reports/factor_ic/phase_a1_summary.md:56` 寫「issued_capital cache 沒有 date column」但 Codex 實讀 cache：`(157374, 3)` = `stock_id,date,issued_shares`，2013-01-31 ~ 2026-04-30（R28-1 follow-up 補的）| **真 P3 已修**：phase_a1_summary.md 改為「現有 date column 但 substance-static（derive 用 mv/close，mv=latest_shares×close → derived_shares=latest_shares 常數，Δ IC +0.0001 證實 form-correct 但 substance-equivalent）」+ 市值 caveat 仍在。**margin IC 不用重跑**（已是用 date-bearing cache 跑的）。closeout §8.1 本來就講了完整 history（was missing → seeded → 現有 date column）所以不用改 | **P3 stale doc** |

### 9.6.2 R32 完整 sweep — 其他 config-drift sibling

R32 修完 revenue 後，Claude 自己 sweep 全部 5 個 `factor_specific.*` section + module：

| Factor | yaml 有 section? | module 讀 yaml? | 狀態 |
|---|:---:|:---:|---|
| foreign_investor_v2 | ✓ | ✓（`_subsignal_weights()` etc.） | yaml-driven（R31-4 修） |
| revenue_momentum_v2 | ✓ | ✓（`_subsignal_weights()`） | yaml-driven（R32 修） |
| high_proximity | ✓ | ✗（硬編 rolling_max=252 / shift=1） | **SPEC MIRROR**（hypothesis-locked，George & Hwang 2004 canonical；yaml 加 `# SPEC MIRROR` 註解 + thresholds.py docstring 標明）|
| pead_eps | ✓ | ✗（硬編；lag 在 `src/utils/constants.py`） | **SPEC MIRROR**（lag_days_q4=90 是法定 deadline，hypothesis-locked；同上）|
| margin_short_ratio | ✓ | ✗（硬編 -0.5/-0.5/iloc[-21]） | **SPEC MIRROR**（reverse-factor 權重；yaml `use_trading_day_offset: true` 描述 module 用 row-offset 不是 calendar offset；同上）|

**結論**：foreign + revenue 已 yaml-driven；high_prox / pead / margin 的 yaml section 是 SPEC MIRROR（module 硬編、yaml mirror 供文件用，改要改兩邊）—— 已在 yaml + `thresholds.py` docstring 明確標示。這 3 個的參數是 hypothesis-locked 結構常數（52W window / 法定 lag / reverse-factor 權重），spec-mirror 可接受。Codex R33 可決定要不要也 wire 起來（或正式 document 為 spec-mirror）。

### 9.6.3 驗收結果

- **690 full pytest passed**（689 + 1 新 test `test_subsignal_weights_yaml_in_sync_with_constant`）；0 regression
- SOP 6 步（revenue logic change）：mutation test PASS（mutate yaml weights → module behavior 變；malformed sum → fallback；**mismatched keys accel_3m3m/pct_24m → fallback**，這正是 pre-R32 的 silent-noop 情境，現在被 caught）/ 3 numbers PASS / grep terminal PASS / cross-interference（無其他讀 accel_3m3m/pct_24m）/ self-attack done / full pytest 690

## 9.7 Codex R33 audit 後續修法（2026-05-11）

R33 verdict O1=NEEDS-FIX（非 test fail，是 2 個小缺口）：

| # | R33 Finding | 修法 |
|---|---|---|
| **R33-B2** | `config/factor_thresholds.yaml :: factor_specific.revenue_momentum_v2` 是 **partial-live section**：`weights` 已 yaml-driven（R32 修），但同 section 的 `yoy_strict_month_matching` + `seasonal_window_months` 沒被 module 讀也沒標 SPEC MIRROR → 嚴格標準下仍是 future silent-drift 風險 | (a) `yoy_strict_month_matching` **刪除**：P1-新6 移除了 ±45 天容忍路徑 → module 永遠 strict matching，沒有 config knob，這 key 是 dead；yaml + `thresholds.py` `_DEFAULTS` 都刪 + 留 tombstone 註解 (b) `seasonal_window_months: 24` **標 SPEC MIRROR**：module 硬編 `DEFAULT_SEASONAL_LOOKBACK_MONTHS=24`；yaml 加 section-level「⚠️ PARTIAL-LIVE：只 weights yaml-driven，seasonal_window_months 是 SPEC MIRROR」+ inline `# SPEC MIRROR` 註解；`thresholds.py` `_DEFAULTS` comment 同步 |
| **R33-A2/C** | `reports/phase_d_v8/H_v8_idio_led_preregistration_draft.md` round reference 全 stale（寫 awaiting Codex R31 / R31 Block K / R32 實測 689）；R33 實測 690 | 全文 R31 reference 改為 round-agnostic（「latest Codex audit (currently R33)」）+ pre-rerun checklist baseline 改 690 + Codex R33 |

**驗收**：690 full pytest passed（無新 test；R33 修法皆 doc/yaml-comment + 刪 dead key，無 logic change）；0 regression。

**v8 pre-reg lock 狀態**：R33 後 baseline + round reference 不 stale；C1-C7 R32 已 PASS。lock 還缺：(1) Codex R34 final confirm（驗 R33 修法）(2) user 簽 lock signature (3) lock 後寫 v8 scripts/configs（by-design post-lock）。

---

## 9. Codex R26 vs R27 vs Claude 修法對照

| 修法 | Codex R26 提 | Claude R26 修 | Codex R27 抓問題 | Claude R27 修 |
|---|:---:|:---:|:---:|:---:|
| P0-A PIT mv | ✓ | ✓ | (passed) | n/a |
| P0-B 量綱 | ✓ | ✓ | (passed) | n/a |
| P0-C contaminated 旗標 | ✓ | ✓ | **silent overwrite by fresh rerun** | ✓ patch + JSON byte recovery |
| P1-A issued_capital PIT | n/a | ✓ (Codex 漏抓 B9) | **fallback today() bug** | ✓ Timestamp.min |
| P1-B stale narrative | ✓ | partial | foreign_v2 改但其他 4 因子 stale | ✓ (本對照報告 + 待 update phase_a1_summary) |
| P1-C last20 stale guard | ✓ | ✓ | (passed) | n/a |
| P1-D consistency drop | ✓ | ✓ | test 改寫驗證 PASS | n/a |
| P1-E covered-weight rescale | ✓ | ✓ | **test_p1e weak (no <50% case)** | ✓ mutation-proof rewrite |
| P1-F dashboard FDR | ✓ | ✓ | (passed) | n/a |
| tw_stock.py:1117 dormant | n/a | n/a | **production caller stale** | ✓ 加 close_by_symbol + P2 警告 |

---

## 10. Verdict

**Fresh rerun 5 因子 + Codex R27 修法後**：
- Foreign_broker_v2 結論完全反轉（負向因子 → 不顯著但 monotonic）
- margin_short_ratio 露出 IC vs decile sign 矛盾（新 silent bug）
- 其他 3 因子（high_proximity / pead_eps / revenue_momentum_v2）數字穩定但延伸到 2025

**v8 reframe 候選池**：
- KEEP: high_proximity, pead_eps（單獨顯著）
- HOLD: margin_short_ratio（待 sign 矛盾 audit）
- DEFER: revenue_momentum_v2（marginal + mega-cap bias）
- DROP: foreign_investor_v2（PIT 修後實證 alpha 微弱）

**Commit-readiness**：經 28 mutation tests + 685 full pytest baseline 全綠 + 5 因子 fresh rerun 完成 + 對照分析完整。建議 commit 進 git，等 Codex R28 驗證再進 v8 spec 階段。

下個 Codex audit (R28) 應驗證：
1. 對照分析的 monotonicity rho / peak t-stats 數字是否獨立可重現
2. margin_short_ratio IC vs decile 矛盾的 root cause
3. P0-2 fallback Timestamp.min 對未來新增 issued_capital cache（含 date column）的回歸測試
4. fresh rerun JSON 的 utf-8 一致性（檢查 byte 0xa1 殘留）
