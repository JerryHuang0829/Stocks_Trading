"""模擬投資追蹤。"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import load_paper_trading_history

st.set_page_config(page_title="模擬投資", page_icon="📅", layout="wide")
st.title("📅 模擬投資追蹤")
st.caption("策略每月建議的持股紀錄。等累積 6 個月以上才能評估策略是否有效。")

history = load_paper_trading_history()

if not history:
    st.info("尚無紀錄。每月 12 號執行一次 `scripts/paper_trade.py` 開始記錄。")
    st.stop()

# --- 摘要 ---
n_months = len(history)
st.metric("已記錄", f"{n_months} 個月", delta=f"從 {history[0].get('month_key', '')} 開始")

if n_months < 6:
    months_left = 6 - n_months
    st.info(f"⏳ 再累積 {months_left} 個月就可以初步評估了。目前先看策略每月選了什麼。")

st.divider()

# --- 累積績效圖（有 actual_return 才顯示）---
returns_data = [(r.get("month_key", ""), r.get("actual_return")) for r in history]
filled = [(m, r) for m, r in returns_data if r is not None]

if len(filled) >= 2:
    st.subheader("📈 模擬投資累積績效")
    months_list = [m for m, _ in filled]
    ret_list = [r for _, r in filled]

    cum_ret = 1.0
    cum_vals = []
    for r in ret_list:
        cum_ret *= (1 + r)
        cum_vals.append((cum_ret - 1) * 100)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=months_list, y=[r * 100 for r in ret_list],
        name="月報酬",
        marker_color=["#2ecc71" if r >= 0 else "#e74c3c" for r in ret_list],
        yaxis="y2",
        opacity=0.5,
        hovertemplate="%{x}<br>月報酬：%{y:.1f}%",
    ))
    fig.add_trace(go.Scatter(
        x=months_list, y=cum_vals,
        mode="lines+markers", name="累積報酬",
        line=dict(color="#3498db", width=2.5),
        hovertemplate="%{x}<br>累積：%{y:.1f}%",
    ))
    fig.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.4)
    fig.update_layout(
        yaxis=dict(title="累積報酬 (%)"),
        yaxis2=dict(title="月報酬 (%)", overlaying="y", side="right", showgrid=False),
        height=320, margin=dict(t=20, b=20),
        legend=dict(x=0.02, y=0.98),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.divider()

# --- 每月卡片 ---
for record in reversed(history):
    month = record.get("month_key", "?")
    signal = record.get("market_signal", "unknown")
    emoji = {"risk_on": "🟢", "caution": "🟡", "risk_off": "🔴"}.get(signal, "⚪")
    label = {"risk_on": "積極買入", "caution": "謹慎觀望", "risk_off": "保守防禦"}.get(signal, signal)
    exposure = record.get("gross_exposure", 0)

    with st.expander(f"{emoji} {month} — {label}（投入 {exposure:.0%}）", expanded=(record == history[-1])):
        positions = record.get("positions", [])
        if positions:
            for i, p in enumerate(positions, 1):
                st.markdown(f"{i}. **{p.get('symbol', '')} {p.get('name', '')}** — 權重 {p.get('weight', 0):.0%}　({p.get('industry', '')})")

        actual = record.get("actual_return")
        if actual is not None:
            color = "normal" if actual >= 0 else "inverse"
            st.metric("實際月報酬", f"{actual:+.1%}", delta_color=color)
        else:
            st.caption("⏳ 實際報酬尚未填入")
