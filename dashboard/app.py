"""台股量化投組 Dashboard — 主頁面。"""

import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import load_latest_paper_trade, load_walk_forward_summary, load_backtest_metrics

st.set_page_config(
    page_title="台股量化投組",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 台股量化投組 Dashboard")

# --- 一眼看懂的摘要 ---
st.header("目前狀態")

# 載入最新數據
pt = load_latest_paper_trade()
wf = load_walk_forward_summary()

col1, col2, col3 = st.columns(3)

with col1:
    if pt:
        signal = pt.get("market_signal", "unknown")
        emoji = {"risk_on": "🟢", "caution": "🟡", "risk_off": "🔴"}.get(signal, "⚪")
        label = {"risk_on": "積極買入", "caution": "謹慎觀望", "risk_off": "保守防禦"}.get(signal, signal)
        st.metric("市場狀態", f"{emoji} {label}")
        st.caption({
            "risk_on": "大盤趨勢向上，策略全力投入（96%資金買股票）",
            "caution": "大盤方向不明，策略只用 70% 資金，其餘留現金",
            "risk_off": "大盤趨勢向下，策略只用 35% 資金，保留大量現金避險",
        }.get(signal, ""))
    else:
        st.metric("市場狀態", "尚未記錄")

with col2:
    if pt:
        st.metric("本月建議持股", f"{pt.get('selected_count', 0)} 檔")
        top_stocks = [p.get("name", p.get("symbol", "")) for p in pt.get("positions", [])[:3]]
        st.caption(f"前三名：{'、'.join(top_stocks)}")
    else:
        st.metric("本月建議持股", "—")

with col3:
    if wf:
        agg = wf.get("aggregate", {})
        mean_sharpe = agg.get("mean_sharpe", 0)
        win_rate = agg.get("win_rate", 0)
        st.metric("歷史勝率", f"{win_rate:.0%}")
        st.caption(f"過去 11 個半年中，{win_rate:.0%} 的時間是賺錢的")
    else:
        st.metric("歷史勝率", "—")

st.divider()

# --- 策略說明（白話版）---
st.header("這個策略在做什麼？")

st.markdown("""
**簡單說：每個月自動從台股中挑出最強的 8 支股票來投資。**

#### 挑股票的方法（三個評分標準）

| 標準 | 佔比 | 白話說明 |
|------|------|---------|
| 📈 價格動能 | 55% | 過去一年漲最多的股票，通常還會繼續漲 |
| 📊 趨勢品質 | 20% | 股價走勢是否穩定向上（不是亂跳的） |
| 💰 營收成長 | 25% | 公司每月營收是否持續成長 |

#### 風險控制

| 狀態 | 投入資金 | 什麼時候 |
|------|---------|---------|
| 🟢 積極買入 | 96% | 大盤在漲，放心投 |
| 🟡 謹慎觀望 | 70% | 大盤不確定，留 30% 現金 |
| 🔴 保守防禦 | 35% | 大盤在跌，只投一點點，主要持有現金 |

#### 每月操作

```
每月 12 號 → 程式自動分析 → 告訴你該買什麼、買多少
→ 你去券商 APP 買入 → 下個月 12 號再看要不要換
```
""")

st.divider()

# --- 頁面導覽 ---
st.header("Dashboard 頁面說明")

st.markdown("""
| 頁面 | 看什麼 | 什麼時候看 |
|------|--------|-----------|
| 📋 **持股建議** | 這個月該買哪幾支、各買多少 | 每月 12 號 |
| 📈 **績效走勢** | 策略過去賺了多少、跟 0050 比如何 | 想了解策略實力時 |
| 🔄 **歷史驗證** | 策略在多個不同時期的表現 | 想確認策略是否真的有效 |
| 📅 **Paper Trading** | 模擬投資的每月紀錄 | 每月看 |
| 💰 **實盤追蹤** | 你實際投入的錢賺了多少 | 每月看 |
""")
