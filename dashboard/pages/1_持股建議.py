"""這個月該買什麼？"""

import streamlit as st
import plotly.express as px
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import load_latest_paper_trade

st.set_page_config(page_title="持股建議", page_icon="📋", layout="wide")

data = load_latest_paper_trade()

if data is None:
    st.warning("尚無紀錄。請先執行 `scripts/paper_trade.py`。")
    st.stop()

# --- 標題：一句話告訴你現在的狀態 ---
signal = data.get("market_signal", "unknown")
signal_map = {
    "risk_on": ("🟢 積極買入期", "大盤在漲，可以放心投入 96% 資金"),
    "caution": ("🟡 謹慎觀望期", "大盤方向不明，只投 70%，留 30% 現金保護"),
    "risk_off": ("🔴 防禦期", "大盤在跌，只投 35%，大部分留現金"),
}
title, desc = signal_map.get(signal, ("⚪ 未知", ""))

st.title(f"📋 {data.get('date', '')} 持股建議")
st.subheader(title)
st.caption(desc)

st.divider()

# --- 持股表格（簡化版）---
positions = data.get("positions", [])
if positions:
    st.subheader(f"建議買入 {len(positions)} 檔")

    for i, p in enumerate(positions, 1):
        col1, col2, col3 = st.columns([2, 1, 1])
        col1.markdown(f"**{i}. {p.get('symbol', '')} {p.get('name', '')}**")
        col2.markdown(f"投入比例：**{p.get('weight', 0):.0%}**")
        col3.markdown(f"產業：{p.get('industry', '—')}")

    st.divider()

    # --- 一張圖：產業分佈 ---
    st.subheader("產業分佈")
    st.caption("看看你的錢分散在哪些產業")

    pos_df = pd.DataFrame(positions)
    if "industry" in pos_df.columns and "weight" in pos_df.columns:
        ind_w = pos_df.groupby("industry")["weight"].sum().reset_index()
        ind_w.columns = ["產業", "比例"]
        fig = px.pie(ind_w, values="比例", names="產業", hole=0.4,
                     color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_traces(textinfo="label+percent", textposition="outside")
        fig.update_layout(height=350, margin=dict(t=20, b=20), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- 如果只有 1 萬塊 ---
    st.subheader("💡 小資族怎麼買？")
    st.markdown(f"""
    如果你只有 **1 萬元**，不需要買全部 {len(positions)} 檔：

    | 建議 | 股票 | 預算 |
    |------|------|------|
    | 第 1 名 | {positions[0].get('symbol', '')} {positions[0].get('name', '')} | 5,000 元 |
    | 第 2 名 | {positions[1].get('symbol', '')} {positions[1].get('name', '')} | 5,000 元 |

    用**零股交易**（盤後 13:40-14:30）買入即可。
    """)
