# 台股量化長倉投組系統

> 零售規模（NT$ 1,000,000 baseline）的台股系統化選股研究專案，採用機構等級驗證方法論。
> 最終以**誠實的 NO-GO 結論結案**——這是這個 repo 最重要的價值。

---

## 結論（誠實揭露）

**Phase D v7** multi-factor long-only validation sprint：
- 18 個假設 cells（6 候選因子集 × 3 個持股數 {8, 12, 16}）
- 6 條 hard reject criteria（IR / monthly α / TE / Max DD / A1 active gate / 80% bootstrap CI）
- **0 / 18 cells 通過全部 6 條** → `Outcome-2 Partial`

獨立驗證鏈：
- R25-final audit verdict：**CONFIRM-NO-GO**
- 直接 read evaluator JSON 自我驗證：**CONFIRM-NO-GO**（0 mismatch / DSR=0 by design / V0.26 NetIncome→IncomeAfterTaxes period 推到 2026-Q1）

→ 結案，pivot 回**被動 100% 0050 DCA**（per 歷史決策路徑）。

> **為什麼這個 NO-GO 是價值**：嚴格驗證下的負面結果，比鬆散方法論下的正面結果更具科學紀律。
> 量化研究 80% 的工作是驗證假設失敗——這個 repo 完整紀錄了一條從「找 alpha」到「承認沒 alpha」的研究路徑。

---

## 方法論亮點

### Point-in-Time（PIT）紀律
- `_DataSlicer`（[src/backtest/engine.py](src/backtest/engine.py)）強制 no-look-ahead，所有資料截斷至 `as_of` 日期
- **4 個 PIT mutation tests** 守 forward-leak 回歸（[tests/_pit_mutation/test_pit_forward_leak.py](tests/_pit_mutation/test_pit_forward_leak.py)）
- Universe 建構（V0.23 修法）改 per-rebalance-date PIT-safe filter，杜絕用 future close 過濾

### Pro 統計驗證
- **Spearman rank IC** + **Stationary block bootstrap**（Politis-Romano 1994，block_len=3，n=10000，seed=42）
- **Deflated Sharpe Ratio**（Bailey-Lopez de Prado 2014），含 empirical skew-kurt + n_trials 校正
- **FDR Benjamini-Hochberg** multiple-testing 校正（Phase A1 5 因子 pre-registered → m=5；Phase D 3 因子 2026-05-11 補測 single IC，不在 m=5 內）
- **Per-seed permutation** + **effective_n** cross-sectional cluster 校正

實作：[src/analysis/ic_analysis.py](src/analysis/ic_analysis.py)（867 LOC，~50 個對應測試）

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

```
src/
├── portfolio/tw_stock.py      核心選股（1300 LOC）— _analyze_symbol → _rank_analyses → _select_positions
├── analysis/ic_analysis.py    Pro methodology 核心（867 LOC）— DSR / FDR / Bootstrap / Permutation
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
├── features/                  9 個因子
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
├── run_factor_ic.py              Phase A1 5 因子 IC CLI（/factor-ic skill 底層）
├── run_phase_d_factor_ic.py      Phase D 3 因子 IC CLI（quality_v3 / industry_momentum / idio_vol_max；2026-05-11 補測）
├── _enrich_factor_ic_diagnostics.py  IC JSON 補診斷（decile / monotonicity / peak / price-score-corr / pit_violation）
├── run_backtest.py               回測 CLI（preflight + multi-token fallback）
├── walk_forward.py               Rolling OOS 滾動驗證
├── cache_rebuild.py              Cache 全新重建
└── cache_fill_new_factors.py     Phase D 新因子 cache fill（含 --seed-issued-capital）

tests/                         690 tests（含 4 PIT mutation tests + 14 R26-R28 mutation tests + 8-factor IC schema parity + foreign/revenue yaml-sync tests）
reports/                       研究 evidence
config/                        settings.yaml + factor_thresholds.yaml
docs/                          研究文件
```

---

## 技術棧

| 類別 | 工具 |
|---|---|
| Language | Python 3.12 |
| 核心套件 | pandas / NumPy / scipy / pandas-ta 0.4.71b0 / pytest |
| 資料源 | FinMind API + TWSE/TPEX 爬蟲 |
| 儲存 | pickle cache（OHLCV / 因子 IC 等）+ JSON reports（18-cell sweep / metrics 等）|
| Dashboard | Streamlit + Plotly |
| 環境 | conda env `quant` / Docker |
| OS | Windows 11 + WSL（主開發） / Linux container（CI）|

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
# 期望: 690 passed
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
- **頁 3 雙因子回測** — D1_v2（52W 50% + PEAD 50%）IS+OOS 累積報酬 + 12 metrics 對照（IR 0.92 → 0.006 collapse）
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

## Phase D v7 18-cell 結果一覽

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

## License

MIT

## 作者

JerryHuang0829
