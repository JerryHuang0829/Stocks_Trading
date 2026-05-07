# Phase B0-Lite → P5 Pivot Decision

**決策日期**：2026-05-03
**Decided by**：user 拍板選 A（嚴格 hypothesis 解讀）
**Anchor commit**：`27e5fe6` (tag `phase-b0-baseline`)
**Override reference**：H_lite_preregistration.md hypothesis 完整陳述（line 28）

---

## TL;DR

B0-Lite spike 跑完 low_vol_v2 single-factor 在 2019-2024 historical validation set 結果：
- 表面 `mean rank IC = 0.0584` 看起來強，但有 **4 個 systemic warnings**
- 嚴格按 H_lite hypothesis 完整陳述（「IC > 0.02 **且** DSR Ψ ≥ 0.95」AND condition）→ **DSR Ψ = 0.0 → reject H_lite**
- 跟 Phase A1-A3 同一個故事重演（IC 看起來有但 systemic 結構性 fail）
- **Pivot P5 = 80% 0050 + 20% factor tilt**，不寫 full B0（省 50-60 hr 工程）

---

## Spike 結果證據（全保留 H_lite hypothesis lock 不可改）

| Metric | Value | 評估 |
|---|---|---|
| Mean rank IC | **0.0584** | 表面強訊號 |
| t-stat | 2.015 | p=0.048 顯著 |
| Permutation p-value | 0.0066 | 顯著 |
| Bootstrap CI 95 (block) | [0.0158, 0.099] | 不跨零 |
| **DSR Ψ (n_trials=12)** | **0.0** | ❌ **fail H_lite hypothesis line 28** |
| **0050 top-50 holdings overlap** | **78.0%** | ❌ low_vol top picks 6.24/8 在 0050 重壓 |
| **trending_down regime IC** | **-0.030** | ❌ 熊市反向 |
| **2023 yearly IC** | **-0.016** | ❌ 唯一 fail 年（risk-on 反彈年逆向）|
| L4 coverage mean | 96.4% | ✅ pass |
| L5 monthly turnover | 37.5% | ❌ fail (>30%) |

**Script 自動 verdict**：Lite-O4（按 reject criteria 主表 L2/L4/L5）
**Strict hypothesis verdict**：Lite-O2（按 hypothesis 完整陳述含 DSR ≥ 0.95）

→ **採 strict** = Lite-O2 = pivot P5

---

## R21 Codex Audit Update (2026-05-03)

R21 audit 對本決策抓 5 件 P1/P2，已修：

| # | Codex Finding | 修法 |
|---|---|---|
| **P1** | audit_doc_drift.py 沒實作 hypothesis-drift detector（H_lite line 6 引用不存在的功能）| F1 加 `_check_hypothesis_drift` + bump LATEST_AUDIT_ROUND R19→R21 + stale_nums 加 440 passed |
| **P2** | spike_results.json `decision.outcome=Lite-O4` 跟本檔 `Lite-O2` 衝突 | F2 spike script 加 dual outcome：`script_outcome=Lite-O4` / `strict_outcome=Lite-O2` 兩 field 並存；`outcome` alias 改 strict_outcome |
| **P2** | trending_down + 2023 yearly IC 統計力弱 (n=10, t=-0.32, p=0.75 / n=12, t=-0.33, p=0.75) | F3 evidence 強度重排（見下方）|
| **P2** | dual-env parity 沒完成（H_lite line 101 寫紀律但 spike report 沒 acknowledge）| F4 加「Dual-env parity status」段（見下方）|
| **P3** | `low_vol_v2.py` divide-by-zero on close=0 (4 stocks/12 rows in cache) + spike script private-import smell | 留 P5 main plan 開跑前處理（F5/F6）|

---

## Dual-env Parity Status (R21 F4)

| 環境 | spike 跑過? | mean_ic | 結果 |
|---|---|---|---|
| conda quant (Python 3.12.13 + pandas 3.0.2) | ✅ | 0.0584 | 本決策 baseline |
| Docker portfolio-bot (Python 3.12 + pandas 2.x) | ✅（Codex R21 audit 跑驗）| 0.0584 | 跟 conda 一致 ✓ |

**Diff**: Docker mean_ic - conda mean_ic = **0.000** ≤ 0.005 threshold per H_lite line 101 → **parity verified**（Codex external sanity check）。

**注意**：本機 user 拒絕跑 Docker（「怎麼不是 conda」），但 Codex audit 已獨立 reproduce → parity 真實 verified by external party，不是 silent gap。

---

## 4 個 Systemic Warnings 詳解（R21 F3 evidence 強度排序）

### 🔴 Strong evidence（直接 pivot 證據）

#### Warning 1: DSR Ψ = 0.0（institutional-grade fail）

#### Warning 2: 0050 holdings overlap 78%

### 🟡 Supporting context（不是主 pivot 證據）

#### Warning 3: trending_down regime IC = -0.030 — **Statistical noise (n=10, t=-0.32, p=0.75)**

只是「風險提示」不是 pivot 證據。t-stat -0.32 / p-value 0.75 = **完全不顯著**；-0.030 vs +0.030 在統計上不可區分。

#### Warning 4: 2023 yearly IC = -0.016 — **Statistical noise (n=12, t=-0.33, p=0.75)**

同 Warning 3 — t-stat -0.33 / p-value 0.75 = **完全不顯著**。「2023 fail year」可能 noise 不是 systemic。

→ **Pivot 主要靠 Warnings 1-2（strong evidence），3-4 為輔助 context**。

---

## 4 個 Systemic Warnings 詳細展開（保留原文供 reference）

### Warning 1：DSR Ψ = 0.0（institutional-grade 不可達）

H_lite line 28 把「DSR Ψ ≥ 0.95」寫進 hypothesis（institutional-grade 標準）。
但 IC IR = 0.256，n_trials=12 conservative 校正下 `E[max IR] ≈ 1.897` → z 大負 → Ψ ≈ 0。

對齊 user 既有 memory（`策略研究.md:104`）：
> 「DSR Ψ ≥ 0.95 是 hedge fund pro 門檻，**retail monthly TW stock 幾乎不可能達到**」

這個 spike 結果**不是新發現** — 是 user 既有 prior 的實證。

### Warning 2：0050 holdings overlap 78%

low_vol_v2 top-8 picks 跟 0050 top-50 月平均 overlap = 78%（6.24/8 重疊）。

→ 結構性 = 0050 重壓 + 22% 微調。獨立 alpha 預期接近 0（即使 IC 0.05+，portfolio 跟 0050 同向動）。

學術文獻（AQR 2014 BAB）長期觀察：low-vol 在 large-cap-tilted 市場（如台股）會收斂到 large-cap proxy。

### Warning 3：trending_down regime IC = -0.030

10 個月熊市 regime 下 IC 反向（low-vol picks 在熊市 underperform）。

理論：熊市 risk-off 期 high-vol stocks 大幅 sell-off，low-vol stocks 「相對」抗跌但仍跌；short-side rebalance 把 low-vol stocks 排前面 → 仍跌 → IC 變負。

實務：full B0 即使加 regime gate 也很難解（regime detection 滯後 + low-vol 真實熊市表現 noise 大）。

### Warning 4：2023 IC = -0.016

2019-2024 6 年中**唯一 fail 年**：2023 IC -0.016（其他年份 0.04-0.12）。

2023 是台股反彈年（0050 +27%）— **risk-on 強牛市 high-vol stocks 跑贏 low-vol** → 跟 Warning 3 同型不同年（trending_up + risk-on 同樣不利）。

→ 預期 future 牛市 / risk-on 年同樣 underperform。

---

## Pivot 邏輯

### 為什麼進 full B0 預期 fail

| 解的 | 不解的 |
|---|---|
| ✅ Quarterly rebal 解 L5 turnover 37.5% | ❌ 解不了 DSR=0（IR 不會因 quarterly 變高）|
| ✅ A1 gate 加 overlap < 70% threshold 警示 | ❌ 但實際 overlap 78% > 70% → A1 gate 必 fail |
| ✅ Composite quality_v2 + low_vol_v2 可能 IC 提升 | ❌ 但 quality_v2 在台股大型股 ROE 高 + 0050 holdings overlap 預期 80%+ → 加 quality 也是 0050 重壓 |
| | ❌ 解不了 trending_down -0.030 IC reversal |
| | ❌ 解不了 2023-style 牛市年 fail |

→ Full B0 工程 50-60 hr 預期跑出 same systemic fail，但更精緻包裝。**不值得**。

### 為什麼進 P5 alignment

P5 = 80% 0050 + 20% factor tilt 是「**承認 0050 是 main alpha source，自己只做 marginal tilt**」。

這跟 spike 證據完全吻合：
- low_vol_v2 在台股 = 78% 0050 重壓 → **不如直接買 0050**
- 剩 20% 用 factor tilt（low_vol top-8 OR 既有 5 因子 OR 它們組合）做 marginal alpha
- IR vs 100% 0050 期望 ≥ 0.3（institutional benchmark：AQR Smart Beta tilt IR 平均 0.4-0.6）
- 100 萬 NTD baseline 真實可實盤（不像 D1 long/short 100 萬太薄）

### 12 個月 horizon 不變

| Phase | 原 plan | Pivot 後 plan |
|---|---|---|
| Month 1 | B0 spike + start | ✅ 已完成 (B0-Lite + pivot decision) |
| Month 2-3 | Full B0 implementation | **改 P5 main plan + Smart Beta tilt 工程** |
| Month 4 | B2 walk-forward | P5 baseline backtest 5yr OOS |
| Month 5-6 | B3 paper trade start | P5 paper trade 啟動 |
| Month 7-12 | B4 paper 6 個月 | P5 paper 6 個月 + monthly reconcile |
| Month 13 | B5 GO/NO-GO 100 萬 NTD 實盤 | P5 GO/NO-GO 100 萬 NTD 實盤 |

→ **12 個月 horizon + B5 -8% hard stop 不變**，只是策略類型從 「quality+lowvol composite」改 「Smart Beta tilt」。

---

## P5 Main Plan 大綱（待 user 拍板後寫 detailed plan）

### P5 hypothesis（待 pre-register）

> **80% 0050 + 20% factor tilt portfolio 在 2019-2024 historical validation set 的 IR vs 100% 0050 ≥ 0.3，net of cost（turnover + slippage）**

### P5 候選 tilt configs（待 sensitivity test）

| Config | Tilt 因子組合 | Tilt weight |
|---|---|---|
| P5-A | low_vol_v2 top-8（既 spike 結果用上）| 20% |
| P5-B | 既有 5 因子 composite top-8（high_proximity / pead_eps / margin_short / foreign_v2 / revenue_v2）| 20% |
| P5-C | low_vol_v2 + 既有 5 因子 6-factor composite | 20% |
| P5-D | high_proximity + pead_eps double-pick (Phase A2 D1_v2 sole survivor) | 20% |

### P5 reject criteria（pre-registered）

- IR vs 100% 0050 ≥ 0.3
- Mean monthly net alpha (cost-adjusted) ≥ 0.3% / 月
- Tracking error vs 0050 ∈ [3%, 8%]（不要太貼但也不要太離譜）
- Max drawdown 不超過 0050 max DD + 5%
- 6 個月 paper trade live PnL > 0050 cost-adjusted 才上實盤

### P5 工程量預估

| Item | Hours |
|---|---|
| `scripts/p5_smart_beta_tilt.py` (4 configs sensitivity backtest)| 6 |
| `tests/test_p5_smart_beta_tilt.py` (8 tests) | 4 |
| `reports/phase_p5/H_p5_preregistration.md` + lock | 2 |
| Update `scripts/smart_beta_tracker.py` 支援 tilt portfolio NAV tracking | 4 |
| Paper trade pipeline integration (`scripts/paper_trade.py --strategy p5_tilt`) | 6 |
| Codex R21 audit prompt + audit response | 4 |
| Buffer | 4 |
| **小計** | **~30 hr ≈ 4 work session** |

→ **P5 比 full B0 (50-60 hr) 省 20-30 hr**，且 outcome 預期更可達成。

---

## 對 user 的承諾（Pre-commit）

1. **不回頭跑 quality_v2 single-snapshot lookahead version 找漂亮數字** — H_lite L1 deferred 不解禁
2. **不調 H_lite reject criteria 主表加 DSR threshold 找 「Lite-O4 acceptable」改寫** — strict hypothesis win
3. **不調 low_vol_v2 window / min_history 找 IC > 0.06 漂亮數字** — pivot 已 committed
4. **不打 D1 long/short market neutral 主意** — Codex finding #5 已說 corr ≤ 0.5 + beta [0.6, 1.2] 不合理；100 萬 NTD baseline 也太薄
5. **P5 main plan 也守相同 P4 process 紀律** — 寫 H_p5 pre-registration + audit chain + Codex audit + audit_doc_drift gate

---

## Audit chain anchor

- **Baseline commit**：`27e5fe6` (tag `phase-b0-baseline`)
- **B0-Lite spike commit**：next commit (after this decision file lands)
- **R20 Codex audit**：v1 plan NO-GO + 6 findings → v2 plan B0-Lite spike
- **Spike outcome**：Lite-O2 (override script Lite-O4 per strict hypothesis)
- **Next milestone**：P5 main plan v1 + H_p5 pre-registration + Codex R21 audit (optional)

---

## Honest 補充

**Time estimate**：本 decision + 後續 docs 同步 + commit ≈ 30 min；P5 main plan v1 detailed implementation ≈ 30 hr ± 30%

**Failure modes**：
- P5 IR vs 0050 可能仍 < 0.3（tilt 跟 0050 高度共動，marginal alpha 也是噪音）
- 4 個 P5 candidate config sensitivity 跑完可能全 fail → 12 個月 horizon 後可能仍 0 strategy 上實盤
- 100 萬 NTD 真實成本 + 滑點 + 證交稅 + 期交稅 可能吃掉 marginal alpha → 過 cost 不容易
- 牛市年（如 2023）P5 仍可能 underperform 0050 raw return（接受 risk-adjusted 框架）

**Assumption boundary**：
- 假設 user 接受 「P5 主線後仍 fail = 退到 100% 0050 DCA」作為 12 個月 horizon 終點
- 假設 既有 5 因子 + low_vol_v2 都未來 12 個月內不會找到新 alpha source
- **不假設** P5 一定 pass — 機率分布跟 P2 接近（成功 25-35%）

---

**End of pivot decision.** Awaiting user 進 P5 main plan detailed design or pause for review.
