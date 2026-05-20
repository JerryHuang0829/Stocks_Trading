"""台股量化長倉投組系統 — 研究展示 Dashboard 主頁。

跑：streamlit run dashboard/專案背景.py（本機跑，非 docker 流程）
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import render_hero_kpi_strip  # noqa: E402

st.set_page_config(
    page_title="台股量化研究展示",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ===============================================================
# Hero — 5 秒講完是什麼專案
# ===============================================================
st.title("📊 台股量化長倉投組系統")
st.markdown(
    "##### 用學術因子 + 機構等級驗證流程，誠實檢驗「**台股月頻 long-only 是否有可實盤 alpha**」的研究專案。"
)

st.divider()

# ===============================================================
# 專案背景（搬到最上面）
# ===============================================================
st.subheader("📚 專案背景")

st.markdown(
    """
**這是什麼**：一個個人量化研究專案，用機構等級的統計方法論，嚴格檢驗「在台灣股市，
拿幾個學術界已知的因子（價格動量 / EPS / 品質 / 產業動量 等），組合成一個
月頻再平衡的 long-only portfolio，能不能穩定贏過 0050 大盤？」這個問題。

**為什麼做**：很多量化教科書 / 論文宣稱因子能贏大盤；但這些研究多半在美股、
大樣本、機構規模下做的。**對 NT$ 100 萬等級的台股零售投資人**，這些因子是不是
仍然有效？需要實證。

**做法**：拆成多個階段循序檢驗 — 先單獨檢驗每個因子的學術顯著性，再組合成
2 因子策略測 IS+OOS，最後用 6 個候選因子組合 × 3 種持股數 = 18 種策略 sweep
找 sole survivor。每階段都跑 hard gate（事前鎖定的及格門檻），過不了就誠實
記錄 NO-GO，不降標。
"""
)

st.markdown("##### 專案規格")
col1, col2 = st.columns(2)
with col1:
    st.markdown(
        """
- **規模**：零售 NT$ 1,000,000 baseline
- **策略型態**：long-only 月頻再平衡
- **基準**：台灣 50 ETF（0050）含股息調整
- **樣本**：IS 評估 2020-2024 = 60 個月（資料窗口含 2019 因子 lookback）
"""
    )
with col2:
    st.markdown(
        """
- **持股數**：8 / 12 / 16 檔
- **資料源**：FinMind API + TWSE / TPEX 爬蟲
- **回測引擎**：自製 PIT-safe BacktestEngine
- **驗證樣本外**：2025 OOS + 6m paper（已禁用）
"""
    )

st.divider()

# ===============================================================
# Hero KPI strip — 8 個關鍵數字 30 秒看完研究體量
# ===============================================================
st.subheader("📊 研究體量速覽")
render_hero_kpi_strip(st)

st.divider()

# ===============================================================
# Pipeline 六層
# ===============================================================
st.subheader("⚙️ 策略 Pipeline 六層")
st.caption(
    "**台股月頻多因子 long-only**，每月 12 號盤後鎖價執行。"
    "整套 pipeline 從資料到下單分六層，每一層職責獨立。"
)

# ---- Layer 1: 資料源 ----
with st.container(border=True):
    col_l, col_r = st.columns([1, 5])
    with col_l:
        st.markdown("### 1️⃣")
        st.markdown("**資料源**")
        st.caption("自建 + 防 survivorship")
    with col_r:
        st.markdown(
            """
FinMind API + TWSE / TPEX 爬蟲**自建**台股資料層。資料抓進來不是直接能用 ——
台股有 3 個容易踩雷的點（dashboard 真正難的不是回測迴圈、是這些）：

| 處理 | 我的做法 |
|---|---|
| **Stock split 前復權** | `adjust_splits()` 偵測單日跌幅 > 40%（如 1:4 分割跌 75%）或漲幅 > 100%（合股），自動產生 split factor 前復權整段價格 |
| **配息調整（Total Return）** | `adjust_dividends()` 抓 TWSE `TWT49U` 端點除權息資料，scale-invariant 公式 `factor = 1 - cash_div / close_before` 不受 split-adj 影響；benchmark (0050) 跟 portfolio 個股都套 |
| **下市股（防 survivorship bias）** | `HistoricalUniverse` 保留下市股，回測選股池包含**該日點仍在市的股**——不是「現在還在的股票」回測 |
"""
        )

# ---- Layer 2: PIT 截斷 ----
with st.container(border=True):
    col_l, col_r = st.columns([1, 5])
    with col_l:
        st.markdown("### 2️⃣")
        st.markdown("**PIT 截斷**")
        st.caption("防 look-ahead")
    with col_r:
        st.markdown(
            """
自做 `_DataSlicer` 把所有資料**按 per-row event lag 截斷到 `as_of` 日期**，
嚴格防 look-ahead bias —— 這是本專案最核心的方法論防線：

| 資料類型 | 事件日 → 可用日 lag |
|---|---|
| EPS（季報） | Q4 = 90 天 / Q1-Q3 = 45 天 |
| 月營收 | 45 天（次月 10 日 + buffer）|
| 融資 / 融券 | 2 天（T+1 + buffer）|
| 三大法人 | 2 天（盤後 T+1）|
| 資產負債表 | 60 天（與 EPS 取 max）|

→ 台股 PIT 只認**事件日**不認公開日，EPS 的 lag 還隨每季改變 ——
**同一份 DataFrame 不同列要套不同 lag，這層 Backtrader / Finlab 不會幫你做**。
"""
        )

# ---- Layer 3: Universe ----
with st.container(border=True):
    col_l, col_r = st.columns([1, 5])
    with col_l:
        st.markdown("### 3️⃣")
        st.markdown("**Universe**")
        st.caption("流動性篩選")
    with col_r:
        st.markdown(
            """
**1900 多檔台股** → 用 **close × volume 過去 20 個交易日均值**（約 1 個月）做兩步篩選：

| Step | 動作 | 結果 |
|---|---|---|
| 粗篩 | 批次計算所有股票（效能優化）| 1900 → **top 400** |
| 精篩 | 逐支精算同一公式 | 400 → **top 80** universe |

**兩步公式相同**，分兩步是效能考量——先批次粗篩快速縮範圍、再逐支精算確定 universe。

📌 嚴格使用 as_of **之前**的 OHLCV（沿用 Layer 2 PIT 紀律），回測從 cache 切片、
live 從 TWSE / TPEX API 拿，不會用到 as_of 當天或之後的資訊。
"""
        )

# ---- Layer 4: 因子分數 ----
with st.container(border=True):
    col_l, col_r = st.columns([1, 5])
    with col_l:
        st.markdown("### 4️⃣")
        st.markdown("**因子分數**")
        st.caption("score → rank")
    with col_r:
        st.markdown(
            """
對 80 檔算 **8 個學術因子**分數：52W 高接近度、PEAD / EPS、月營收動能 v2、融資 / 融券反向、
外資法人因子 v2、品質 (quality_v3)、產業動量、特質波動 + MAX 樂透。

每個因子做 **cross-sectional percentile rank**——同一天比較 80 檔的相對排名，
避免因子絕對值在不同時期 scale 不同造成偏誤。

⭐ **這層算的是「因子 score」，不是「IC」**：IC（資訊係數）是因子分數 vs **未來報酬**的
相關性 —— 選股當下還沒有未來報酬，**算不出 IC**。IC 是事後拿歷史資料驗證因子有沒有效時
才算的指標（見「因子驗證」頁）；pipeline 這層只算 score + 排名。
"""
        )

# ---- Layer 5: 選股 + 風控 ----
with st.container(border=True):
    col_l, col_r = st.columns([1, 5])
    with col_l:
        st.markdown("### 5️⃣")
        st.markdown("**選股 + 風控**")
        st.caption("組合 + 約束")
    with col_r:
        st.markdown(
            """
- **加權**：rank 加權成 portfolio_score → 取 **top_n** 檔（live 策略 top_n=8）
- **產業約束**：同產業上限 3 檔（避免單一產業過度集中）
- **換倉摩擦控制**：新股 score 超過被換股 **6 分以上**才換倉（降低 turnover）
- **成本**：turnover_cost 0.47% + slippage 10bp + 證交稅 0.3%
- **再平衡時點**：每月 **12 號盤後**
"""
        )

# ---- Layer 6: Regime 曝險 ----
with st.container(border=True):
    col_l, col_r = st.columns([1, 5])
    with col_l:
        st.markdown("### 6️⃣")
        st.markdown("**Regime 曝險**")
        st.caption("大盤 → 總曝險")
    with col_r:
        st.markdown(
            """
最後一步：用 **0050 的 ADX** 衡量趨勢強度 + **SMA 排列**判斷方向，把月度市況分三種狀態，
**決定上一層選好的 portfolio 要用多少總曝險**：

| Regime | 判斷條件 | 總曝險 |
|---|---|---|
| 🟢 risk_on | ADX > 25 + 多頭排列 | **96%** |
| 🟡 caution | ADX 20-25 或盤整 | **70%** |
| 🔴 risk_off | ADX > 25 + 空頭排列 | **35%** |

→ 最終部位 = Layer 5 選好的股票 × 這裡算出的 96 / 70 / 35%。

📌 **為什麼用 ADX 不用 RSI**：ADX 衡量「**趨勢強度**」不是「方向」，方向交給 SMA 排列。
RSI 在強趨勢會鈍化在 70 以上很久，照絕對值做 regime 判斷會一路誤判。

⚠️ **Regime 拿來調曝險，不拿來換因子** — 實測過 regime-aware 換因子反而傷 alpha
（動量因子在空頭被削弱反而扣分）。
"""
        )

st.caption(
    "📍 以上是**每月執行的 pipeline**（資料 → 下單）。"
    "因子怎麼挑出來、單因子有沒有效 → 見「因子驗證」頁；"
    "策略怎麼回測驗證、18 種組合 sweep 結果 → 見「策略驗證」頁。"
)

st.divider()

# ===============================================================
# 技術棧（折疊起來避免主頁太長）
# ===============================================================
with st.expander("🛠️ 技術棧（點開展開）", expanded=False):
    st.markdown(
        """
- **Python 3.12** + pandas / NumPy / scipy / pandas-ta（0.4.71b0，相容 numpy 2.x）/ pytest
- **資料源**：FinMind API + TWSE / TPEX 爬蟲
- **儲存**：pickle cache（OHLCV / 因子 IC 等）+ JSON reports（18-cell sweep / metrics / 等）
- **回測引擎**：BacktestEngine + `_DataSlicer`（PIT-safe；backtest mode 下 cache miss raise）
- **Pro 統計方法論**：`src/analysis/ic_analysis.py` (~960 LOC)
  - Spearman IC + Stationary Block Bootstrap（Politis-Romano 1994）
  - Deflated Sharpe Ratio（Bailey-Lopez de Prado 2014）
  - FDR Benjamini-Hochberg multi-test correction
  - Per-iteration permutation null
- **Dashboard**：Streamlit + Plotly
- **環境**：conda env `quant` / Docker
"""
    )

st.divider()

# ===============================================================
# 程式碼專案資料的架構（Architecture）— Option A simple tree
# ===============================================================
st.subheader("🏗️ 程式碼專案資料的架構")
st.caption("Repo 主要 6 個 folder，各 LOC / file count + 一句話功能。")

st.code(
    """📦 Stock-Trading/  (~12k LOC source code + 696 pytest 全綠)
│
├── 📂 src/  (11,212 LOC, 42 modules)            核心邏輯
│   ├── portfolio/tw_stock.py        1,902 LOC   選股引擎 (_analyze_symbol → _rank → _select)
│   ├── data/                        2,840 LOC   FinMind + TWSE/TPEX 爬蟲 + pickle cache + PIT helpers
│   ├── features/                    2,366 LOC   9 個學術因子實作
│   ├── backtest/                    1,588 LOC   BacktestEngine + _DataSlicer (PIT-safe 截斷)
│   ├── analysis/ic_analysis.py        960 LOC   Pro stats (Spearman IC / DSR / FDR / Block Bootstrap)
│   ├── strategy/                      353 LOC   Regime (ADX+SMA → 3 狀態) + 技術指標
│   ├── utils/                         536 LOC   thresholds loader / paths / constants / retry
│   └── notify, storage, ...           584 LOC   通知 + SQLite (live mode 預備)
│
├── 📂 tests/  (66 files, 696 tests)             PIT mutation + factor IC + integration + golden
├── 📂 reports/  (41 JSON + 31 md)               研究 evidence 證據鏈
│   ├── factor_ic/                               8 因子 single-IC JSON + correlation matrix
│   ├── phase_d/cell_sweep_v7_2026_05_06/        18-cell sweep canonical (NO-GO 結果)
│   ├── sprint_pro_validation/B_repro/           D1_v2 雙因子 IS+OOS backtest
│   ├── phase_b0_lite/                           low_vol_v2 單因子 spike test 結果
│   └── diagnosis/                               揭穿過去 Sharpe 1.73 是 overfit
│
├── 📂 dashboard/  (5 files)                     本 Streamlit app (主頁 + 3 子頁 + utils.py)
├── 📂 scripts/  (30 files)                      CLI 工具 (cache build / backtest / IC pipeline)
└── 📂 config/  (13 yaml files)                  settings + factor_thresholds + Phase D 子設定
""",
    language=None,
)

st.info(
    "📌 **Dashboard 數字怎麼追根**：Dashboard 上任何圖表 / 表格的數字，"
    "都可以順著 `reports/` 對應子目錄找到原始 JSON，做 cross-check 或重跑驗證。"
    "**所有結論都對應 evidence**，沒有「相信我數字是這樣」的 hand-wave。"
)
