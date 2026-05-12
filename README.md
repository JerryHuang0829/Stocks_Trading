# 台股量化長倉投組系統

[![tests](https://github.com/JerryHuang0829/Stocks_Trading/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/JerryHuang0829/Stocks_Trading/actions/workflows/tests.yml)
&nbsp;![python](https://img.shields.io/badge/python-3.12-blue)
&nbsp;![license](https://img.shields.io/badge/license-MIT-lightgrey)

> 零售規模（NT$ 1,000,000 baseline）的台股系統化選股研究專案，採用機構等級驗證方法論。
> 最終以**誠實的 NO-GO 結論結案**——這是這個 repo 最重要的價值。

---

## 一分鐘看懂（先讀這段）

- **這是什麼**：用機構等級的統計流程，嚴格檢驗一個問題——「在台股、月頻、long-only、零售 NT$ 100 萬規模下，組合幾個學術因子（價格動量 / 獲利驚喜 / 品質 / 產業動量 等）能不能**穩定贏過 0050**？」
- **結論**：6 個候選因子組合 × 3 種持股數 = 18 種策略，每種都跑 6 道**事前鎖定**的 hard gate → **0/18 全部沒過** → 誠實記 NO-GO，pivot 回被動 100% 0050 定期定額。過程中還抓出 2 個讓過去 4 年回測「全是幻覺」的 silent bug（timezone + universe pre-filter）。
- **為什麼值得看**：嚴格驗證下的負面結果 + 一條「不降標、不 cherry-pick、敢揭穿自己舊結果」的研究紀律——比鬆散方法論下的漂亮 PASS 更有科學價值。**想看白話互動版** → `streamlit run dashboard/專案背景.py`（6 頁圖表展示）。

> **只有 5 分鐘？** 讀完上面這段 → 跑 `streamlit run dashboard/專案背景.py` 看主頁 → 翻 [src/analysis/ic_analysis.py](src/analysis/ic_analysis.py)（Pro 統計核心）。看到不懂的代號（D1_v2 / V0.x / R0x …）→ 拉到下面「[名詞速查](#名詞速查看到代號不迷路)」。完整研究路徑 → [docs/research-findings.md](docs/research-findings.md)。

---

## 結論（誠實揭露）

最終的多因子 long-only 驗證 sprint（內部代號 **Phase D v7**）：

- **18 種策略** = 6 個候選因子組合 × 3 種持股數 {8, 12, 16}
- 每種跑 **6 道事前鎖定的 hard gate**：IR / 月超額 α / Tracking Error / Max Drawdown 偏離 / A1 主動性 aggregate gate / 80% stationary block bootstrap CI 下界
- **0 / 18 通過全部 6 道** → 結案 `Outcome-2 Partial`，pivot 回**被動 100% 0050 定期定額**

結論用兩條獨立軌道驗過：external audit 給 **CONFIRM-NO-GO**；不依賴它、直接 read evaluator JSON 自我驗算也是 **CONFIRM-NO-GO**（0 mismatch、bootstrap CI re-compute 對得上 stored value）。

> **為什麼這個 NO-GO 是價值**：嚴格驗證下的負面結果，比鬆散方法論下的正面結果更具科學紀律——量化研究 80% 的工作就是驗證假設失敗。這個 repo 完整紀錄了一條從「找 alpha」到「承認沒 alpha」、不降標不 cherry-pick 的研究路徑。

<details>
<summary>更細的結案細節（修法版本 / audit 輪次）— 一般讀者可略過</summary>

- external audit anchor `aba7459` + 18-cell run → Round 25-final verdict CONFIRM-NO-GO；self-audit 獨立確認：evaluator JSON 0 mismatch / L6 bootstrap CI lowers re-compute match stored / DSR=0 by design（n=60、k=18 trials、90% trial penalty）/ `_build_financial_history` period 從 2019-12-31 推到 2026-03-31（V0.26 修法後）/ no new P0
- 5 個 P0 silent bug 修法（Phase D v7 內部子版本 V0.22–V0.26）：FinMind transient error 分類 / universe build PIT-safe filter / 0050 dividend 強制 / `_build_financial_history` join overlap / TSMC NetIncome 2020+ NaN（FinMind schema change）→ `NetIncome.fillna(IncomeAfterTaxes)`
- 完整迭代鏈（P0–P7 / Phase A1–D / V0.x / R0x）見 [docs/CHANGELOG.md](docs/CHANGELOG.md)

</details>

---

## 方法論亮點

### Point-in-Time（PIT）紀律
- `_DataSlicer`（[src/backtest/engine.py](src/backtest/engine.py)）強制 no-look-ahead，所有資料截斷至 `as_of` 日期
- **8 個 PIT mutation tests** 守 forward-leak 回歸（[tests/_pit_mutation/](tests/_pit_mutation/)：4 條 `_DataSlicer` cutoff + 4 條 feature-module cutoff（pead_eps / margin_short / market_value panel），閉合 architecture audit §B.2 與 J 報告 §P6.1 列的 follow-up gap）
- Universe 建構（V0.23 修法）改 per-rebalance-date PIT-safe filter，杜絕用 future close 過濾

### Pro 統計驗證
- **Spearman rank IC** + **Stationary block bootstrap**（Politis-Romano 1994，block_len=3，n=10000，seed=42）
- **Deflated Sharpe Ratio**（Bailey-Lopez de Prado 2014），含 empirical skew-kurt + n_trials 校正
- **FDR Benjamini-Hochberg** multiple-testing 校正（Phase A1 5 因子 pre-registered → m=5；Phase D 3 因子 2026-05-11 補測 single IC，不在 m=5 內）
- **Per-seed permutation** + **effective_n** cross-sectional cluster 校正

實作：[src/analysis/ic_analysis.py](src/analysis/ic_analysis.py)（~940 LOC，~50 個對應測試）

### Hypothesis Pre-registration
- 6 candidates × 3 top_n = 18 cells **事前鎖定**於 [reports/phase_d/H_d_v6_preregistration.md](reports/phase_d/H_d_v6_preregistration.md)
- **13 條 pre-commit constraints** + **3 條 code-level enforcement assertions**
- D-A 預先 disqualify（per Phase A2 D6 OOS IR 0.0058 / 99.4% collapse），不在 sweep 範圍

### Multi-round 獨立審計
- **25+ 輪獨立交叉驗證**（兩條獨立 audit 軌道對賭：一條跑 self-audit，一條跑對抗式攻擊測試），累積 **50+ 攻擊角度**全部正面回應
- 每輪 verdict 紀錄於 [reports/](reports/)
- Self-Audit SOP 6 步：mutation test / 數字驗算 / grep 終態 / cross-interference / 對抗式自攻擊 / full pytest regression

---

## 架構

**資料流**：FinMind API + TWSE/TPEX 爬蟲 → pickle cache → `_DataSlicer`（PIT 截斷至 `as_of`）→ 因子計算（`features/`）→ 橫截面 percentile 排名 + 加權（`tw_stock.py`）→ 選股（top_n + 同產業上限 + turnover 門檻）→ `BacktestEngine` 逐月 replay → metrics（Sharpe / α / IR / MDD）→ `reports/` JSON → dashboard 視覺化。

```
src/
├── portfolio/tw_stock.py      核心選股（~1900 LOC，目前單檔最大，已列為 v8 前拆檔 candidate）— _analyze_symbol → _rank_analyses → _select_positions
├── analysis/ic_analysis.py    Pro methodology 核心（~940 LOC）— DSR / FDR / Bootstrap / Permutation
├── backtest/
│   ├── engine.py              BacktestEngine + _DataSlicer（PIT-safe）
│   ├── metrics.py             Sharpe / MDD / Alpha + stock split + dividend total return
│   └── universe.py            HistoricalUniverse（含下市股，防 survivorship bias）
├── data/
│   ├── finmind.py             FinMind API + pickle cache（V0.22 transient classification）
│   └── twse_scraper.py        TWSE/TPEX 爬蟲 + dividend / monthly revenue
├── strategy/
│   ├── regime.py              大盤多空判斷（ADX + SMA → risk_on/caution/risk_off）
│   └── indicators.py          技術指標（pandas-ta 0.4.71b0 wrapper，相容 numpy 2.x）
├── features/                  9 個因子模組（8 個跑 single-factor IC；low_vol_v2 = B0-Lite spike，未進策略）
│   ├── high_proximity.py        52W High Proximity（George-Hwang 2004）
│   ├── revenue_momentum_v2.py   月營收 YoY + 3M accel + 24M percentile
│   ├── margin_short_ratio.py    融資/融券反向
│   ├── foreign_investor_v2.py     外資法人因子 v2（4 sub-signal composite；R28 後 consistency deprecated）
│   ├── pead_eps.py              PEAD / EPS Surprise（Bernard-Thomas 1989）
│   ├── low_vol_v2.py            低波動因子（B0-Lite spike，未進策略）
│   ├── quality_v3.py            QMJ profitability sub-component（D-E 用）
│   ├── industry_momentum.py     6m industry momentum（Moskowitz-Grinblatt 1999；D-F 用）
│   └── idio_vol_max.py          IdioVol 0.5 + MAX 0.5（Bali-Cakici-Whitelaw 2011；D-G 用）
├── utils/                     共用 utility（thresholds / paths / constants / config / retry）
└── storage/database.py        SQLite signals.db（live mode 預備未啟動，主要研究流程不用）

scripts/                       執行 CLI
├── d_cell_sweep_v7_real.py       Phase D v7 18-cell sweep runner
├── walk_forward_d_v7.py          L6 80% bootstrap CI（Politis-Romano）
├── d_cell_aggregate_v7.py        18-cell aggregate + Outcome 1/2/4 classification
├── sole_survivor_v7.py           tie-break + D-A guard + tag emit
├── run_factor_ic.py              Phase A1 5 因子 IC CLI（full-universe Spearman IC + DSR / FDR / bootstrap / permutation）
├── run_phase_d_factor_ic.py      Phase D 3 因子 IC CLI（quality_v3 / industry_momentum / idio_vol_max；2026-05-11 補測）
├── _enrich_factor_ic_diagnostics.py  IC JSON 補診斷（decile / monotonicity / peak / price-score-corr / pit_violation）
├── run_backtest.py               回測 CLI（preflight + multi-token fallback）
├── walk_forward.py               Rolling OOS 滾動驗證
├── cache_rebuild.py              Cache 全新重建
└── cache_fill_new_factors.py     Phase D 新因子 cache fill（含 --seed-issued-capital）

tests/                         694 tests（含 8 PIT mutation tests + 14 因子修法 mutation tests + 8-factor IC schema parity + foreign/revenue yaml-sync tests）
reports/                       研究 evidence
config/                        settings.yaml + factor_thresholds.yaml
docs/                          研究文件
```

---

## 名詞速查（看到代號不迷路）

研究紀錄用內部代號。看到代號 = 它連到 `reports/` 或 `docs/CHANGELOG.md` 裡某個 evidence 檔。

| 代號 | 是什麼 |
|---|---|
| **Phase A1** | 5 個學術因子各自單獨檢驗的階段（2026-04）|
| **Phase A2 / A3** | A2 = 2 因子組合（D1_v2）IS+OOS；A3 = sector-neutral / regime-aware 強化（全 fail）|
| **Phase D v6 / v7** | 多因子 long-only 最終驗證階段；v7 = 把 v6 hard gate 調成 retail-realistic 後跑的 18-cell sweep |
| **D1_v2** | Phase A2 的雙因子組合 = 52W 高接近度 50% + PEAD 50%（IS IR 0.92 → OOS 0.0058 collapse 那個）|
| **D-A … D-G** | Phase D 的 6 個候選因子組合（D-A 已預先 disqualify，不在 sweep）|
| **D-C\|12** 等 | `<候選>\|<持股數>` cell 命名（D-C 因子組合 + 持股 12 檔）|
| **L1 … L6** | Phase D v7 的 6 道 hard gate（IR / 月α / TE / ΔMaxDD / A1 aggregate / 80% bootstrap CI 下界）|
| **IS / OOS** | In-Sample（2020-2024，60 個月，調策略用）/ Out-of-Sample（2025，純驗證）|
| **IC / IC IR / DSR / TE / IR** | Spearman rank IC / IC 的夏普 / Deflated Sharpe Ratio / Tracking Error / Information Ratio |
| **V0.x / R0x / P0–P7** | 修法子版本（v7 內部）/ audit 輪次 / 2024 原始研究階段 |
| **Outcome-1/2/4** | sweep 結果分類：1 = ≥1 cell 過 6/6 / 2 = 只有 4-5/6（partial）/ 4 = 0 cell 過 |

---

## 技術棧

| 類別 | 工具 |
|---|---|
| Language | Python 3.12 |
| 核心套件 | pandas / NumPy / scipy / pandas-ta 0.4.71b0 / pytest |
| 資料源 | FinMind API + TWSE/TPEX 爬蟲 |
| 儲存 | pickle cache（OHLCV / 因子 IC 等）+ JSON reports（18-cell sweep / metrics 等）|
| Dashboard | Streamlit + Plotly |
| 環境 | conda env `quant`（主開發） / Docker（reproducible 全測試 + external audit env）|
| OS | Windows 11 + WSL（主開發） / Linux container（Docker）|

---

## 快速啟動

### 1. 環境

```bash
# 用 conda
conda create -n quant python=3.12 -y
conda activate quant
pip install -r requirements.txt

# 或用 Docker
docker compose build
```

### 2. 環境變數

複製 `.env.example` 成 `.env`：
```
FINMIND_TOKEN=<your_finmind_token>
DATA_CACHE_DIR=<absolute_path_to_data_cache>
TELEGRAM_BOT_TOKEN=<optional>
TELEGRAM_CHAT_ID=<optional>
```

### 3. 跑測試

```bash
conda run -n quant python -m pytest tests/ -q
# 期望: 694 passed
```

### 4. 跑互動式研究展示 Dashboard ⭐

```bash
streamlit run dashboard/專案背景.py
# 預設 http://localhost:8501（本機跑，不在 docker 流程）
```

6 頁互動視覺化（含主頁）：

- **主頁（專案背景）** — 專案 elevator pitch + 規格 + 技術棧 + 研究路徑時間軸
- **頁 1 因子介紹** — 8 個因子的學術依據 / 計算方式 / 資料源 / PIT 防護 + single-IC verdict
- **頁 2 因子 IC 測試** — 8 因子單獨 IC 主表（Phase A1 5 + Phase D 3，2026-05-11 補測；FDR m=5 只跑 Phase A1）+ 5×5 Spearman correlation（Phase A1 only）+ 進階分析
- **頁 3 雙因子回測** — D1_v2（52W 50% + PEAD 50%）IS+OOS 累積報酬 + 12 metrics 對照（IR 0.92 → 0.0058 collapse）
- **頁 4 18 種策略最終 sweep** — Phase D v7 6×3 cells heatmap + L1-L6 詳表 + 月超額時序
- **頁 5 為什麼相信這個 NO-GO** — 雙重否定 triangulation：揭穿 Overfit + Bootstrap CI 數學

依賴：streamlit + plotly + pandas-ta（已在 requirements.txt）。

### 5. 跑 Phase D v7 18-cell sweep

```bash
# 需要 FinMind cache 完成（quarterly_financial_full + balance_sheet）
python scripts/d_cell_sweep_v7_real.py --output reports/phase_d/cell_sweep_<date>/

# 跑 walk-forward L6 80% bootstrap CI
python scripts/walk_forward_d_v7.py \
  --input-monthly-returns reports/phase_d/cell_sweep_<date>/cell_monthly_active_returns.json \
  --output reports/phase_d/cell_sweep_<date>/cell_bootstrap_ci_lowers.json

# Aggregate + sole survivor
python scripts/d_cell_aggregate_v7.py --input reports/phase_d/cell_sweep_<date>/
python scripts/sole_survivor_v7.py --cell-summary reports/phase_d/cell_sweep_<date>/cell_summary.json
```

### 6. 跑因子 IC（8 個因子）

```bash
# Phase A1 5 因子
python scripts/run_factor_ic.py --factor high_proximity
python scripts/run_factor_ic.py --factor pead_eps --start 2020-01-01 --end 2025-12-31

# Phase D 3 因子（2026-05-11 補測；用 CellSweepContext 資料源 + 自動補 enrichment 診斷）
python scripts/run_phase_d_factor_ic.py --factor idio_vol_max
python scripts/run_phase_d_factor_ic.py --factor quality_v3
python scripts/run_phase_d_factor_ic.py --factor industry_momentum
```

---

## 重要研究文件

| 文件 | 內容 |
|---|---|
| [docs/research-findings.md](docs/research-findings.md) | 完整因子研究結論 + Phase 路徑 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 完整迭代紀錄（P0-P7 / Phase A1-A3 / Phase D v6-v7）|
| [reports/phase_d/H_d_v6_preregistration.md](reports/phase_d/H_d_v6_preregistration.md) | Phase D 假設 pre-registration（6 candidates / 6 hard gates / 13 pre-commit）|
| [reports/phase_d/v7_outcome2_summary.md](reports/phase_d/v7_outcome2_summary.md) | **Phase D v7 正式結案**（Outcome-2 / 0 cell 過 6/6 / no paper trade）|
| [reports/phase_d/cell_sweep_v7_2026_05_06/cell_summary.json](reports/phase_d/cell_sweep_v7_2026_05_06/cell_summary.json) | **18-cell canonical 結果**（CONFIRM-NO-GO 證據）|
| [reports/phase_d/R24_resolution.md](reports/phase_d/R24_resolution.md) | 5 P0 + 7 設計修法逐條 |
| [reports/sprint_pro_validation/J_multi_perspective_audit.md](reports/sprint_pro_validation/J_multi_perspective_audit.md) | 7 角色 + external audit 21 attack 全答 |
| [reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md](reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md) | Pro Validation Sprint canonical evidence |
| [reports/factor_ic/phase_a1_summary.md](reports/factor_ic/phase_a1_summary.md) | Phase A1 5 因子綜合結論 |
| [reports/diagnosis/2026-04-16_edge_diagnosis.md](reports/diagnosis/2026-04-16_edge_diagnosis.md) | 揭穿過去三因子 alpha 為 overfit（timezone bug + universe pre-filter bug）|

---

## 18 種策略最終結果一覽（Phase D v7 18-cell sweep）

| Cell | L1 IR | L2 α | L3 TE | L4 ΔDD | L5 A1 | L6 CI | 過幾關 |
|---|---|---|---|---|---|---|---|
| D-B \| 8 | F | F | P | P | F | F | 2/6 |
| D-B \| 12 | F | F | P | F | F | F | 1/6 |
| D-B \| 16 | F | F | P | F | F | F | 1/6 |
| D-C \| 8 | F | F | F | P | F | F | 1/6 |
| **D-C \| 12** | **P** | **P** | **P** | **P** | **F** | **F** | **4/6** |
| D-C \| 16 | F | F | P | P | F | F | 2/6 |
| D-D \| all | F | F | P | F | F | F | 1/6 |
| D-E \| 8 | P | P | F | P | F | F | 3/6 |
| **D-E \| 12** | **P** | **P** | **F** | **P** | **P** | **F** | **4/6** |
| **D-E \| 16** | **P** | **P** | **P** | **P** | **F** | **F** | **4/6** |
| D-F \| 8 | P | P | F | P | F | F | 3/6 |
| D-F \| 12 | P | P | F | P | F | F | 3/6 |
| D-F \| 16 | F | F | F | P | F | F | 1/6 |
| D-G \| all | F | F | P | F | P | F | 2/6 |

最佳 cells **D-C\|12**、**D-E\|12**、**D-E\|16** 都只有 4/6；沒有任何 cell 達到 5/6 或 6/6。L5 是 active_corr + TE + beta-adjusted alpha t 的 aggregate gate，不是單看 correlation。

---

## Outcome-2 失敗根因

1. **樣本太短**：60 個月（2020-2024）對嚴格 stationary block bootstrap 偏短（block_len=3 → effective n ≈ 20）
2. **n_trials=18 DSR 診斷**：18 個假設一起測，單一通過 90% 機率會被壓縮；但 v7 的 binding NO-GO 來自 L1-L6 hard gates，不是只因 DSR
3. **2020-2024 極端市場**：covid 急跌 + 科技股巨漲 + 升息 + AI 浪潮，因子過度集中
4. **台股 1900 檔流動性受限**（不像美股有萬檔可分散）
5. **monthly freq 60 obs 對 80% CI 訊雜比不足**

→ 不是 evaluator bug，是「現實上 60 個月 + 嚴格 retail-realistic gate 下 18 個假設都不夠強」。

---

## 學到的事 + 可攜資產

Phase A1 5 因子 + Phase D 18 cells 全 NO-GO 之後，校正出來的結論是：**「台股月頻 long-only、60 個月樣本下找穩定 factor edge 的 base rate 很低」**——不是哪個 evaluator 寫太嚴，是這個 setup 本身的統計力天花板就在那（60 monthly obs × block_len=3 → effective n ≈ 20）。

所以這個專案真正**可攜的資產不是「跑出 alpha」**（沒有），而是：

1. **一整套機構等級的驗證基建**——Spearman IC + stationary block bootstrap（Politis-Romano）+ Deflated Sharpe Ratio（Bailey-López de Prado）+ FDR-BH 多重檢定校正 + PIT mutation tests + hypothesis pre-registration + survivorship-bias-free universe + guard-not-silent-fallback；任何後續策略都能直接複用。
2. **一條紀律性的 NO-GO**——不降標（4/6 ≠ 6/6）、不 cherry-pick（18 cells 事前鎖定）、不靠 external audit（self-audit 獨立 read evaluator JSON 雙重確認 CONFIRM-NO-GO）、敢揭穿自己過去的 overfit。

---

## 結論之後：如果要讓它變 GO，下一步會怎麼做（v8 hypothesis，不是承諾）

v8 不會是「微調 v7 的權重把它調到過關」（那是 p-hacking、是 silent_bug），而是**從一開始就改 setup**。下表是「哪個方向 / 為什麼可能讓 NO-GO 變 GO / 誠實的 caveat」——重點是「方向」不是「保證」，任何一條都可能再次 NO-GO，那才是誠實的 prior：

| 方向 | 機制（為什麼可能讓 NO-GO 變 GO）| 誠實 caveat |
|---|---|---|
| **樣本延伸 2008-2024（≈17 年，含金融海嘯）** | 60 obs × block_len=3 → effective n ≈ 20 是統計力天花板；17 年 ≈ 200+ obs，L6 80% CI 下界才有機會推上 0 | 2008-2014 的 FinMind cache 覆蓋率 / schema 一致性要先驗；台股早期流動性更差 |
| **改 weekly / daily 頻率** | 月頻 12 obs/年太稀疏；weekly ≈ 52、daily ≈ 250 → 同 5 年就有夠多獨立資訊；也讓 PEAD / 法人流這種 short-decay 訊號發揮空間 | turnover 暴增 → 成本模型要重估；訊號 decay 要重對齊 horizon |
| **改 multi-asset（不只台股 1900 檔）** | long-only 在「大權值股獨舞年」（如 2024）結構性追不上 0050；加美股 / ETF / 商品 → universe 廣、分散度高、不被單一 beta 綁死 | 跨市場 PIT / 時區 / 交易日曆對齊是新工程量；資料源多一條依賴 |
| **改 long/short market-neutral** | 真正能穩定贏 0050 的路徑通常在這——把 beta 中性化掉、純賺 factor spread | 台股放空成本 / 借券限制 / 平盤下不得放空；工程上等於重寫 `_select_positions` |
| **L6 由 hard gate 改 advisory + ensemble-of-gates 總判** | 承認 60-month / retail scale 下「80% CI 下界 > 0」本來就難達；改成「報告 CI 但不一票否決」 | 灰色地帶——只有在**新 pre-registration 事前鎖定**新 gate spec、不是事後解套，才不算 silent_bug |
| **option vol risk premium（另一條線）** | factor long-only 不是唯一路；姊妹 repo [`Options_Trading`](../Options_Trading) 已試 TXO Iron Condor（5yr OOS Sharpe −2.1 ~ −2.9 證偽）→ 下一步可試 vol RP harvesting 而非 directional | Iron Condor 那條已證偽；vol RP 要有 TAIFEX surface 資料 + 嚴格 tail risk 管理 |

→ v8 真開跑前會先寫一份新 pre-registration（樣本窗 / universe / 引擎路徑 / 成本 / gates / max trials 事前鎖定），跟 v7 一樣**不允許事後 retune**。詳見 [docs/research-findings.md](docs/research-findings.md) 的「下一步（A-then-B）」段。

---

## License

MIT

## 作者

JerryHuang0829
