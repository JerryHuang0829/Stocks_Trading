# Phase B0-Lite Hypothesis Pre-Registration

**Lock 日期**：2026-05-03
**Anchor commit**：`27e5fe6` （tag `phase-b0-baseline`）
**性質**：**事前鎖定**（pre-registered）— B0-Lite spike 跑完後不可回頭改 reject criteria / sample period / factor 定義
**反例守則**：本檔由 `scripts/audit_doc_drift.py` 的 hypothesis-drift detector 監看；任何「修改 H_lite」「rebid H_lite」字面 → audit fail

---

## H_lite — 假設陳述

> **在台股 2019-2024 historical validation set（不是新鮮 OOS）+ TWSE/TPEX top-80 close×volume universe + 月頻 long-only top_n=8 框架下，low_vol_v2 single factor 的 mean rank IC > 0.02 且 DSR Ψ ≥ 0.95（n_trials=12）**

### Null

> low_vol_v2 alpha indistinguishable from 0；DSR < 0.95 在 n_trials=12 校正後

### 為什麼選 low_vol_v2 當 spike 金絲雀（不是 quality_v2）

1. **既有 cache 限制**：`data/cache/quality/` 完全不存在（`fetch_financial_quality` 從沒被呼叫過）；建 cache 需 ~1-2 hr FinMind API call ＋ 結果有 lookahead bias 不能信。**quality_v2 spike 推遲到 full B0 一次到位做 PIT-correct quality_history rewrite + cache build**
2. **學術 prior**：low_vol 是 quality+lowvol 雙因子裡**較弱的那個**；若連弱的都沒 edge → 強的 quality 也很可能沒救（金絲雀邏輯）
3. **Pro spike 紀律**：B0-Lite 目的是「最低成本判生死」，建 quality cache 違反 spike 精神

---

## Pre-registered Reject Criteria（4 條全鎖死）

### L1（quality_v2 IC）— **DEFERRED to full B0**
- **Reason**：`data/cache/quality/` 不存在；single-snapshot version 有 lookahead bias 不能當決策證據；full B0 必先實作 `fetch_financial_quality_history` PIT-correct 才能驗
- **Pre-commit**：B0-Lite 不評估 L1，**不准事後改成 「lite 階段先用 single-snapshot 看一眼」**

### L2 — low_vol_v2 single-factor IC
- Metric: mean rank IC across 71 monthly rebalance periods (2019-01 ~ 2024-12)
- DSR config: `n_trials=12, avg_block_len=3.0`（涵蓋整個研究家族 — 5 既有因子 + quality + lowvol + composite + sector_neutral 變體 + regime_aware 變體 + D1_v2 + D1_v3a/b）
- **Pass threshold**：mean rank IC > 0.02
- **Fail action**：**直接 pivot P5**（low_vol 沒 edge → 整個 quality+lowvol 路線結構性 fail）

### L4 — low_vol_v2 coverage
- Metric: 每 rebalance period top-80 universe 中能算 252d std 的 stock 比例 (=  ≥ 200 trading day OHLCV in window) 的時間平均
- **Pass threshold**：≥ 60%
- **Fail action**：infra 問題（OHLCV cache 不齊）→ 跑 `bash scripts/daily_update.sh` + `python scripts/cache_health.py` 補完再重跑

### L5 — low_vol_v2 monthly turnover
- Metric: top_n=8 single-factor backtest 的 mean monthly one-way turnover（new positions / total positions）
- **Pass threshold**：< 30% / 月
- **Fail action**：full B0 spec 改 quarterly rebalance（不全棄；low_vol 可能在季頻 turnover 友善）

---

## Observation Metrics（不是 reject criteria，僅紀錄）

| Metric | 用途 |
|---|---|
| **O1** low_vol_v2 portfolio vs 0050 monthly active return rolling correlation (n=5 windows) | 為 full B0 設計修正版 A1 gate threshold（active corr ≤ 0.30 / TE ≥ 3% / beta-adjusted α t > 1.5）提供 prior estimate |
| **O2** low_vol_v2 top-8 holdings vs 0050 50 檔月平均 overlap | 揭示 low_vol 在台股是不是 = 0050 重壓（中華電 / 統一 / 台塑 / 兆豐金）|
| **O3** low_vol_v2 IC by regime（trending_up / ranging / trending_down） | full B0 regime-aware weighting 設計 input |
| **O4** low_vol_v2 IC by year (2019/2020/.../2024) | 看 2024 大權值股獨舞年是否壓制 |

---

## Pre-commit 紀律（不可事後改）

1. **L1 deferred 不可解禁**：B0-Lite 跑完不可回頭跑 single-snapshot quality IC 然後合併報告
2. **L2 IC threshold 0.02 不可調**：跑出 IC=0.018 不能改 「0.018 ≈ 0.02 算 pass」 — 0.018 = fail
3. **DSR n_trials=12 不可降**：跑出 DSR=0.93 不能改 n_trials=10 找 0.95
4. **Sample period 2019-2024 不可改**：不可換 2017-2022 找 PASS
5. **Universe top-80 不可改**：不可換 top-50 / top-100 找 PASS
6. **Pivot fail 行動執行**：L2 fail → 真 pivot P5，不准「再試 low_vol 變體（60d / 126d window）」
7. **Stage gate fail 真 commit**：L4 fail → 真補 cache 不准「降標到 50%」；L5 fail → 真改 quarterly 不准「降標到 35%」

---

## B0-Lite Pass / Fail 條件總表

| Outcome | 條件 | Next step |
|---|---|---|
| **Lite-O1 pass** | L2 + L4 + L5 全 pass | 進 full B0（含 quality_history PIT rewrite + 兩因子 composite + 修正版 A1 gate）|
| **Lite-O2 fail（IC）** | L2 fail（IC ≤ 0.02）| **直接 pivot P5**（80% 0050 + 20% factor tilt）|
| **Lite-O3 fail（infra）** | L4 fail（coverage < 60%） | halt，補 OHLCV cache 後重跑 |
| **Lite-O4 borderline** | L5 fail 但 L2/L4 pass | full B0 改 quarterly rebal spec，仍進 |

---

## Reproducer

跑命令（事前 lock，B0-Lite 跑完用相同命令 verify）：

```bash
# Conda quant env
conda run -n quant python scripts/phase_b0_lite_spike.py \
    --start 2019-01-01 --end 2024-12-31 \
    --output-dir reports/phase_b0_lite/

# Docker portfolio-bot env (parity check)
MSYS_NO_PATHCONV=1 docker compose run --rm --entrypoint python portfolio-bot \
    scripts/phase_b0_lite_spike.py \
    --start 2019-01-01 --end 2024-12-31 \
    --output-dir reports/phase_b0_lite/_docker
```

雙環境 IC diff ≤ 0.005 為 pass；> 0.005 標 P1 進 full B0 須鎖 pandas 版本。

---

## Audit chain anchor

- **Baseline commit**：`27e5fe6` (tag `phase-b0-baseline`)
- **R19 audit chain**：commit msg 引用「R19 audit + 3 件 Pro 補強：pivot back from Options 後第一輪研究紀律」
- **R20 audit**（external audit 已給 v1 plan NO-GO）：findings 整合在本 H_lite 設計（DSR n_trials 從 7 升 12 / "OOS" 正名 「historical validation set」 / A1 gate 推遲到 full B0 重設計）
- **下一輪 audit**：B0-Lite spike 跑完後 user 可選擇送 R21 audit 或直接進 full B0
