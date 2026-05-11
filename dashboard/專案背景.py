"""台股量化長倉投組系統 — 研究展示 Dashboard 主頁。

跑：streamlit run dashboard/專案背景.py（本機跑，非 docker 流程）
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
# 專案背景
# ===============================================================
st.subheader("🎯 專案背景")

st.markdown(
    """
**這是什麼**：一個個人量化研究專案，用機構等級的統計方法論，嚴格檢驗「在台灣股市，
拿幾個學術界已知的因子（價格動量 / 獲利驚喜 / 品質 / 產業動量 等），組合成一個
月頻再平衡的 long-only portfolio，能不能穩定贏過 0050 大盤？」這個問題。

**為什麼做**：很多量化教科書 / 論文宣稱因子能贏大盤；但這些研究多半在美股、
大樣本、機構規模下做的。**對 NT$ 100 萬等級的台股零售投資人**，這些因子是不是
仍然有效？需要實證。

**做法**：拆成多個階段循序檢驗 — 先單獨檢驗每個因子的學術顯著性，再組合成
2 因子策略測 IS+OOS，最後用 6 個候選因子組合 × 3 種持股數 = 18 種策略 sweep
找 sole survivor。每階段都跑 hard gate（事先鎖定的及格門檻），過不了就誠實
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
# 技術棧（移到專案背景下方，仍折疊起來避免主頁太長）
# ===============================================================
with st.expander("🛠️ 技術棧（點開展開）", expanded=False):
    st.markdown(
        """
- **Python 3.12** + pandas / NumPy / scipy / pandas-ta（0.4.71b0，相容 numpy 2.x）/ pytest
- **資料源**：FinMind API + TWSE / TPEX 爬蟲
- **儲存**：pickle cache（OHLCV / 因子 IC 等）+ JSON reports（18-cell sweep / metrics / 等）
- **回測引擎**：BacktestEngine + `_DataSlicer`（PIT-safe；backtest mode 下 cache miss raise）
- **Pro 統計方法論**：`src/analysis/ic_analysis.py` (867 LOC)
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
# 研究路徑時間軸（最下面）
# ===============================================================
st.subheader("🗓️ 研究路徑時間軸")

st.markdown(
    """
| 階段 | 一句話結果 |
|---|---|
| **起點：簡單三因子初版策略** | 過去 4 年 Sharpe 1.73 / α +39%（**後來揭穿是 overfit**）|
| **5 個因子各自單獨檢驗** | 用機構等級方法論測，5 個裡 2 個勉強過中道 |
| **挑兩個因子組合回測** | IS Sharpe 1.53 看起來很強；**但 2025 OOS 大幅 collapse** |
| **嘗試強制產業中性** | 反而傷 alpha → 結論：產業集中是動量因子特性 |
| **切換到期權方向試試** | TXO Iron Condor 5 年 OOS Sharpe -2.1 ~ -2.9 全負 |
| **18 種策略最終 sweep** | **0/18 過 6/6 hard gates → NO-GO** |
| **結案決定回 0050 DCA** | 不啟動 paper trade，pivot 100% 0050 定期定額 |
"""
)
