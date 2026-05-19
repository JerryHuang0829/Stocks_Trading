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
# Pipeline 4 層 + 獨立驗證設計
# ===============================================================
st.subheader("⚙️ 策略 Pipeline 五層")
st.caption(
    "**台股月頻多因子 long-only**，每月 12 號盤後鎖價執行。"
    "整套 pipeline 從資料到下單分五層，每一層職責獨立。"
)

# ---- Layer 1: Data ----
with st.container(border=True):
    data_l, data_r = st.columns([1, 5])
    with data_l:
        st.markdown("### 1️⃣")
        st.markdown("**Data 層**")
        st.caption("PIT-safe")
    with data_r:
        st.markdown(
            """
FinMind API + TWSE / TPEX 爬蟲。自做 `_DataSlicer` 按 **per-row event lag** 截斷防 look-ahead bias：

| 資料類型 | 事件日 → 可用日 lag |
|---|---|
| EPS（季報） | Q4 = 90 天 / Q1-Q3 = 45 天 |
| 月營收 | 45 天（次月 10 日 + buffer）|
| 融資 / 融券 | 2 天（T+1 + buffer）|
| 三大法人 | 2 天（盤後 T+1）|
| 資產負債表 | 60 天（與 EPS 取 max）|

→ 同一份 DataFrame 不同列要套不同 lag，**這層 Backtrader / Finlab 不會幫你做**。

---

**🔧 台股資料層另外 3 個容易踩雷的點**（dashboard 真正難的不是回測迴圈、是這些）：

| 處理 | 我的做法 |
|---|---|
| **Stock split 前復權** | `adjust_splits()` 偵測單日跌幅 > 40%（如 1:4 分割跌 75%）或漲幅 > 100%（合股），自動產生 split factor 前復權整段價格 |
| **配息調整（Total Return）** | `adjust_dividends()` 抓 TWSE `TWT49U` 端點除權息資料，scale-invariant 公式 `factor = 1 - cash_div / close_before` 不受 split-adj 影響；benchmark (0050) 跟 portfolio 個股都套 |
| **下市股 universe（防 survivorship bias）** | `HistoricalUniverse` 保留下市股，回測選股池包含**該日點仍在市的股**——不是「現在還在的股票」回測 |
"""
        )

# ---- Layer 2: Universe ----
with st.container(border=True):
    uni_l, uni_r = st.columns([1, 5])
    with uni_l:
        st.markdown("### 2️⃣")
        st.markdown("**Universe 層**")
        st.caption("流動性篩選")
    with uni_r:
        st.markdown(
            """
**1900 多檔台股** → 用 **close × volume 過去 20 個交易日均值**（約 1 個月）做兩步篩選：

| Step | 動作 | 結果 |
|---|---|---|
| 粗篩 | 批次計算所有股票（效能優化）| 1900 → **top 400** |
| 精篩 | 逐支精算同一公式 | 400 → **top 80** universe |

**兩步公式相同**，分兩步是效能考量——先批次粗篩快速縮範圍、再逐支精算確定 universe。

📌 **PIT 安全**：嚴格使用 as_of **之前**的 OHLCV，回測模式從 cache 切片，
live 模式從 TWSE / TPEX API 拿，不會用到 as_of 當天或之後的資訊。
"""
        )

# ---- Layer 3: Regime ----
with st.container(border=True):
    reg_l, reg_r = st.columns([1, 5])
    with reg_l:
        st.markdown("### 3️⃣")
        st.markdown("**Regime 層**")
        st.caption("大盤 regime detection")
    with reg_r:
        st.markdown(
            """
用 **0050 的 ADX** 衡量趨勢強度 + **SMA 排列**判斷方向，將月度市況分三種狀態決定**總曝險**：

| Regime | 判斷條件 | 總曝險 |
|---|---|---|
| 🟢 risk_on | ADX > 25 + 多頭排列 | **96%** |
| 🟡 caution | ADX 20-25 或盤整 | **70%** |
| 🔴 risk_off | ADX > 25 + 空頭排列 | **35%** |

📌 **為什麼用 ADX 不用 RSI**：ADX 衡量「**趨勢強度**」不是「方向」，方向交給 SMA 排列。
RSI 在強趨勢會鈍化在 70 以上很久，照絕對值做 regime 判斷會一路誤判。

⚠️ **Regime 拿來調曝險，不拿來換因子** — 實測過 regime-aware 換因子反而傷 alpha
（動量因子在空頭被削弱反而扣分）。
"""
        )

# ---- Layer 4: 因子層 ----
with st.container(border=True):
    fac_l, fac_r = st.columns([1, 5])
    with fac_l:
        st.markdown("### 4️⃣")
        st.markdown("**因子層**")
        st.caption("Cross-sectional ranking")
    with fac_r:
        st.markdown(
            """
對 80 檔算 **8 個學術因子**分數：52W 高接近度、PEAD / EPS、月營收動能 v2、融資 / 融券反向、
外資法人因子 v2、品質 (quality_v3)、產業動量、特質波動 + MAX 樂透。

每個因子做 **cross-sectional percentile rank**——同一天比較 80 檔的相對排名，
避免因子絕對值在不同時期 scale 不同造成偏誤。
"""
        )

# ---- Layer 5: 選股 + 風控 ----
with st.container(border=True):
    sel_l, sel_r = st.columns([1, 5])
    with sel_l:
        st.markdown("### 5️⃣")
        st.markdown("**選股 + 風控層**")
        st.caption("組合 + 約束")
    with sel_r:
        st.markdown(
            """
- **加權**：rank 加權成 portfolio_score → 取 **top_n** 檔
- **產業約束**：同產業上限 3 檔（避免單一產業過度集中）
- **換倉摩擦控制**：新股 score 超過被換股 **6 分以上**才換倉（降低 turnover）
- **應用 Regime 曝險**：portfolio 總部位 × Layer 3 算出的 96 / 70 / 35%
- **成本**：turnover_cost 0.47% + slippage 10bp + 證交稅 0.3%
- **再平衡時點**：每月 **12 號盤後**
"""
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
- **Pro 統計方法論**：`src/analysis/ic_analysis.py` (~940 LOC)
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
# 4 頁 anchor jump
# ===============================================================
st.subheader("📑 接下來看什麼")

nav_col1, nav_col2, nav_col3 = st.columns(3)
with nav_col1:
    st.info(
        "**📈 因子驗證**\n\n"
        "8 個學術因子單獨 IC 檢驗，含 DSR / FDR / Bootstrap 完整 methodology。"
    )
with nav_col2:
    st.info(
        "**🎯 策略驗證 + 工程亮點**\n\n"
        "雙因子 IR collapse / 18-cell sweep / 雙重否定證據鏈 / "
        "per-row PIT lag 為何自做 engine。"
    )
with nav_col3:
    st.info(
        "**🏁 結論 + Roadmap**\n\n"
        "本輪三件做對的事、NO-GO 三個失敗點、本輪學到的事、"
        "Path to GO Roadmap 5 條改造路線。"
    )
