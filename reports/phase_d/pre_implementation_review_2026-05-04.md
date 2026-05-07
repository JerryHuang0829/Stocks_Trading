# Pre-Implementation Review — Phase D v7 接手執行

**Review date**: 2026-05-04
**Reviewer**: Claude Opus 4.7（接手 session）
**Anchor**: HEAD `e7faa9a` / `phase-d-v7-baseline = d55d4ea` / `phase-d-v6-baseline = 54b952a`
**Mode**: 方案 (C) — In-house Skill chain (multi-perspective + self-audit + forensic-sweep) 替代 Codex pre-audit
**Codex round**: R24 已收 GO-WITH-CAVEATS verdict; R25-mid pending @ Phase 2 S4 完
**Verdict**: **GO-WITH-CAVEATS** for Step 4 開工 V1.1

---

## Summary

Plan v7 hypothesis lock + 13 pre-commit + 3 code-level assertions 整體**設計合理**且 R24 5 P0 + 7 設計修法已對應到 v6 → v7 closeout（Phase 0 V0.1-V0.12），但 in-house Skill chain 跑出 **27 件 patch（4 P0 / 12 P1 / 6 P2 / 2 P3）**——其中 P0 全為 spec lock 細節 / silent default / pipeline 未明，**不 trigger Plan v7.1 reframe**，可走 GO-WITH-CAVEATS 進 Phase 1。

4 P0 必須在進入 **Phase 2 S1 之前**補完 H_d_v6 V0.13 spec lock 或 code-level fix（建議與 Phase 1 V1.1 numerical justification 同 commit 落地，不另開 Session）。

---

## Skill Chain Evidence

| Skill | 狀態 | 結果 |
|-------|------|------|
| `multi-perspective` | ✅ 已跑 | 7+1 角色（量化主管 / 策略研究員 / CRO / 面試官 / 資料工程師 / 台灣 Retail / Codex adversary）共 22 attack；產 22 patch |
| `self-audit` | ✅ 已跑 | 19 hard checks 4 大類；7 ✅ / 5 ⚠️ / 7 ❌；FAIL 8 件全 cross-validate multi-perspective P0/P1（無新 sibling）|
| `forensic-sweep` | ✅ 已跑 | 8 sweep / 27 hits；confirm 5 件 + refute 1 件 P0（bootstrap method）+ 🆕 新發現 3 件 P0/P1 sibling |
| `audit_doc_drift.py` | ✅ exit 0 | drift 0 / 4 warnings / R24 / PASS |

---

## 5 Corrective Items 手驗結果

| # | Corrective Item | Files | 狀態 |
|---|----------------|-------|------|
| 1 | Cost dual-model engine | engine.py / tw_stock.py / constants.py = 0.0047 ✅；`composite_backtest.py:47 = 57.0` ❌ 仍 drift | **Partial**（engine path 已修，composite path Phase 2 S1 owns per H_d_v6:104）|
| 2 | quality_v3 (D-E QMJ profitability sub) | 檔案不存在；只有 `quality_v2.py` 自我宣告 lookahead | **Spec only**（Phase 2 S2 實作；v2 deprecation 策略未鎖 → P1）|
| 3 | industry_momentum 6m (D-F per MG1999) | 檔案不存在 | **Spec only**（Phase 2 S3 實作；industry_category 非 PIT label → P0/P1）|
| 4 | idio_vol_max 0.5/0.5 (D-G) | 檔案不存在 | **Spec only**（Phase 2 S3 實作）|
| 5 | composite_d_v7 generic + 6 yaml | 檔案不存在 | **Spec only**（Phase 2 S4 實作）|

**Conclusion**：5 件 = 1 partial + 4 spec only，符合 Plan v7 V0.8-V0.12 closeout 是 spec lock 不是 implementation 的 expected state ✓。

---

## Patch List（合併 22 + 8 + 3 = 27 件）

### P0（4 件，Phase 2 S1 之前必補）

#### P0-#1 — 3 新因子 PIT lag spec lock 缺失
**問題**：H_d_v6:51-58 D-E/F/G 設計但**3 新因子 PIT lag 未鎖死**：
- `quality_v3`：quarterly EPS lag (60d Q4 / 45d Q1-3) + balance sheet (Δassets) lag 未明
- `industry_momentum`：industry label PIT snapshot strategy 未明
- `idio_vol_max`：residual std lookback + MAX lottery composite calc 未明

**修法**：H_d_v6 V0.13 補 §"3 新因子 PIT lag spec"。引用 `src/utils/constants.py:QUARTERLY_EPS_LAG_DAYS_Q4=90 / _OTHER=45 / REVENUE_LAG_DAYS=45` 等既有常數。

**Owner**：Phase 1 V1.1 同 commit 落地。

#### P0-#2 — Phase 2 S6 fresh-rerun 估時與 quota math 矛盾
**問題**：v6_validation_manifest §10 寫 fresh-rerun 「1-2 hr」，但 quota math：3 token × 600/hr = 1,800/hr，11 panels × 80 stock × 71 month ≈ 62,480 record min → ~35 hr 純 API 抓。

**修法**：H_d_v6 V0.13 補 §"S6 fresh-rerun 範圍與時程"明訂：
- 限定範圍：OHLCV + dividends + monthly_revenue + quarterly_eps + margin_short + institutional_v2 = 6 panel（非全 11）
- 容差：±1% on numerical IC drift；categorical drift（industry label）需單獨驗
- 預期時程：6-12 hr（依 quota allocation）

**Owner**：Phase 1 V1.1 同 commit 落地（pre-S6 spec lock）。

#### P0-#3 — d_cell_sweep_v7 adjust pipeline 未鎖
**問題**：multi-perspective 資料工程師 Q2 抓到 — d_cell_sweep_v7.py 是否經 `metrics.py::adjust_splits + adjust_dividends`？若直接讀 cache OHLCV 跳過 metrics 層 → split / dividend 漏校正 → IR 整個 invalid。

**修法**：H_d_v6 V0.13 補 §"Cell sweep adjust pipeline"明訂 cell sweep 必經 `BacktestEngine` 而非 raw cache read，sharing engine.py adjust pipeline。

**Owner**：Phase 1 V1.1 同 commit 落地（Phase 2 S4 implementation 前 spec lock）。

#### P0-#4 — DEFAULT_DSR_N_TRIALS=5 silent default 風險
**問題**：forensic-sweep 新發現 — `src/analysis/ic_analysis.py:35 DEFAULT_DSR_N_TRIALS = 5`（Phase A1 single-factor 設定）；Plan v7 cell sweep 期望 `n_trials=18`。若 `d_cell_aggregate_v7.py` 沒 explicit 傳 `n_trials=18` → silently default 5 → DSR 寬鬆 (n_trials 越小 DSR 越易 PASS) → cell sweep silent over-claim。

**修法**：兩選一：
- (a) H_d_v6:142 Assertion 3 加強：`deflated_sharpe_ratio()` call 必 explicit `n_trials=18` keyword arg + 加 mutation test 驗 default fallback raise
- (b) `ic_analysis.py:35` 改 `DEFAULT_DSR_N_TRIALS = None` 並在 `deflated_sharpe_ratio()` raise on None → 強制 caller explicit pass

**Owner**：建議走 (a)（侵入性低）；Phase 1 V1.1 同 commit 落地。

### P1（12 件，Phase 1 first session 補 / 進 Phase 2 S1 前）

#### P1-#5 — composite_backtest.py 真實 57bps drift
- `scripts/composite_backtest.py:47/262/297` 仍 57bps；engine path 0.47% × ~1.42 倍 = 67bps drift
- 修法：Phase 2 S1 修法已 spec at H_d_v6:104，但 Assertion 1 measured path **4 path 中 2 path 漏 guard**（composite_backtest + d_cell_sweep_v7 ✓；d_cell_aggregate_v7 + bootstrap_active_returns_v6 漏）
- Owner：Phase 2 S1 + V0.13 spec 補 4 path Assertion 1

#### P1-#6 — IR 單位 monthly vs annualized + sole_survivor tie-break
- H_d_v6:23-36 / 74 IR 單位未明
- 修法：H_d_v6 V0.13 §6 hard gates 加註「L1 IR = annualized monthly active IR (× √12)；sole_survivor IR = annualized」

#### P1-#7 — D-E mislabel（QMJ profitability vs FF investment Δassets）
- H_d_v6:56 `quality_v3 (PIT TTM ROE × gross_margin × Δassets)` — Δassets 是 FF investment factor 非 QMJ profitability
- 修法：兩選一：(a) 移除 Δassets 純化 ROE × GM；(b) relabel 為「QMJ profitability + FF investment combined」

#### P1-#8 — industry_category 非 PIT label
- `universe.py:341 / ic_analysis.py:406` industry_labels 來自 stock_info cache snapshot 非 PIT
- 修法：Phase 2 S3 industry_momentum 實作必 lock PIT industry snapshot strategy（cache `industry_category_at_<asof_date>` 或 fix snapshot 並 explicit caveat）

#### P1-#9 — quality_v2 deprecation strategy
- `src/features/quality_v2.py` 自我宣告 lookahead bias（line 18/93/162）
- 修法：Phase 2 S2 quality_v3 完全 replace v2，或 v2 → archive/deprecated dir

#### P1-#10 — Cross-freq alignment 文件補 + Phase 2 S1 explicit re-verify
- 5-factor pipeline 已隱含 alignment（月初 t 點 PIT lag）；v7 加 quality_v3 quarterly + industry_momentum monthly + idio_vol_max daily 後須 re-verify
- 修法：H_d_v6 V0.13 §A1 補 cross-freq alignment 規則；Phase 2 S1「跨頻 infra」spec 對齊

#### P1-#11 — 18 cells 跨 top_n risk profile 不可比
- `max_position_weight=0.12` × top_n=8/12/16 → cap 96%/100%/100%（top_n=12 起 cap force less concentrated）
- 修法：sole_survivor tie-break 加 risk-adjusted layer 或 normalize position size to common exposure cap

#### P1-#12 — Walk-forward sub-period DSR n_trials 處理
- Phase 2 S7 walk-forward 若跑 multiple periods，per-period n_trials = 18 還是 18 × n_period？
- 修法：H_d_v6:142 Assertion 3 spec 補

#### P1-#13 — Cache fresh-rerun categorical drift 容差規則
- ±1% 對 numerical OK，industry label drift 是 categorical
- 修法：與 P0-#2 同 commit 補

#### P1-#14 — hold_buffer per cell sweep 一致性
- `hold_buffer = ceil(top_n × 0.3)` → 3, 4, 5 for top_n 8/12/16
- 修法：H_d_v6 V0.13 §A8 spec 補 cell sweep 用對應 hold_buffer

#### P1-#15 — d_cell_sweep_v7.py single-command run interface
- Phase 2 S4 `python scripts/d_cell_sweep_v7.py --config config/d_v7_D-E.yaml --top_n 12` 介面
- Owner：Phase 2 S4 implementation

#### P1-#16 — Pre-flight 3 件 gate（cache coverage / lookback prereq / smoke 1-fold）
- 既有 `_preflight_check` 是 token-level，缺 cache coverage / lookback prereq / smoke
- 修法：Phase 2 S5 Cell Sweep CLI 加 3 件 pre-flight gate

#### P1-#17 — 證交稅 + 手續費 6 折假設 explicit
- `constants.py:23` cost = 0.0047（含手續費 6 折 × 2 + 證交稅 0.3% sell only）；retail default 無折扣應 0.585%
- 修法：H_d_v6:42 cost formula 補 explicit「assume 手續費 6 折，retail 默認無折扣 → 0.585%」+ R5 risk register 加 caveat

### P2（6 件，R25-mid 前補）

- P2-#18 mutation test algorithmic 強度規範（H_d_v6:151 弱 mutation 強化）
- P2-#19 D-A guard 改 enum 防 typo（assertion 2 string typo risk）
- P2-#20 Single-name event tail 防禦（max_same_supply_chain or single-name MaxDD limit）
- P2-#21 Outcome bucket L7 fail case 補（H_d_v6:200-208）
- P2-#22 3 新因子 silent skip 統計 + log 紀律
- P2-#23 Bootstrap method 文件對齊（從 P0 降 — code 已 spec stationary block，文件 H_d_v6:30 引用既有 spec）

### P3（2 件，Phase 2 S5+ backlog）

- P3-#24 GitHub Actions CI integration
- P3-#25 Top-80 末段流動性 sensitivity test

---

## Verdict 細節

### ✅ GO 部分
- Plan v7 hypothesis lock 設計合理（6 candidates × 3 top_n = 18 cells / 6 hard gates v7 retail-realistic）
- 13 pre-commit 紀律 + 3 code-level assertions architectural fix design ✓
- R24 5 P0 + 7 設計修法已 trace 至 V0.1-V0.12 closeout ✓
- audit_doc_drift drift 0 / R24 / PASS ✓
- Pytest 466 passed clean baseline ✓
- 5 corrective items 4 件 spec only + 1 件 partial fix 對齊 closeout intent ✓

### ⚠️ CAVEATS（4 P0 必補）
1. **3 新因子 PIT lag spec lock**（H_d_v6 V0.13 補）
2. **S6 fresh-rerun 估時與範圍**（quota math 重估）
3. **d_cell_sweep_v7 adjust pipeline 鎖**（必經 BacktestEngine）
4. **DEFAULT_DSR_N_TRIALS=5 silent default 修**（Assertion 3 強化或 ic_analysis.py:35 改 None）

### 🛑 NO-GO 條件未觸發
- 無 hypothesis lock 衝突
- 無 13 pre-commit 違反
- 無 D-A pre-disqualification 鬆動
- 無 Plan v7 設計層面致命漏洞

---

## 執行建議

### 立即（Phase 1 V1.1 同 commit）
- 落地 4 件 P0 spec lock + 1 件 code fix（DEFAULT_DSR_N_TRIALS）
- H_d_v6 從 V0.12 bump 至 V0.13 + audit_doc_drift drift 0 確認
- pytest 466 ≥ baseline + 新 mutation test (DEFAULT_DSR_N_TRIALS raise on missing)

### Phase 1 V1.2-V1.4
- 按 HANDOFF Section D 順序執行
- V1.4 Docker mutation test 跑前先 Docker pytest baseline 重驗

### Phase 2 S1
- 同 commit 落地 4 path Assertion 1 cost dual-model（補完 P1-#5 measured path）
- composite_backtest.py:47 修 57bps → settings.yaml read

### Phase 2 S2
- quality_v3 完全 replace quality_v2.py（v2 archive）
- D-E spec 二選一：(a) 移除 Δassets / (b) relabel

### Phase 2 S3
- industry_momentum 鎖 PIT industry snapshot strategy
- idio_vol_max 0.5/0.5 split + |ρ|>0.5 監控

### Phase 2 S4
- composite_d_v7 generic engine 必經 BacktestEngine（adjust pipeline）
- 6 yaml configs（D-A 不入）+ tests
- → R25-mid checkpoint trigger

### Phase 2 S5
- Cell Sweep CLI 加 3 件 pre-flight gate
- L5 active_corr 真實作

### Phase 2 S6
- Fresh-rerun 限定 6 panel（OHLCV / dividends / monthly_revenue / quarterly_eps / margin_short / institutional_v2）
- 18 cell sweep + Assertion 3 explicit n_trials=18

### Phase 2 S7-S8
- Walk-forward + bootstrap CI 80% + sole_survivor 鎖
- → R25-final checkpoint trigger

---

## Sign-off

**Verdict**: GO-WITH-CAVEATS for Step 4 V1.1 開工。
**Next action**: User 核可 → 進 Step 4 V1.1（H_d_v6 補 L1 0.20 / L2 0.005 numerical justification + 4 P0 spec lock 同 commit）。
**Codex round**: R25-mid pending @ Phase 2 S4 完；R25-final pending @ Phase 2 S8 完。
**Reviewer**: Claude Opus 4.7（接手 session）
**Plan v7 unchanged**: 13 pre-commit + 6 candidates + 6 hard gates + D-A pre-disqualification 全保。
