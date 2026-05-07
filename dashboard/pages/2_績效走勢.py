"""策略過去賺了多少？"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import list_backtest_experiments, load_backtest_metrics, load_backtest_snapshots, load_daily_returns

st.set_page_config(page_title="績效走勢", page_icon="📈", layout="wide")
st.title("📈 策略過去賺了多少？")

# --- 回測選擇 ---
experiments = list_backtest_experiments()
if not experiments:
    st.warning("尚無回測結果。請先執行：")
    st.code("docker compose run --rm backtest --start 2022-01-01 --end 2025-12-31 --benchmark 0050")
    st.stop()

# 有 daily_returns 的優先排前面（圖表更完整）
sorted_exp = sorted(experiments, key=lambda x: (not x["has_daily"], x["start"]))

selected_idx = st.sidebar.selectbox(
    "選擇回測區間",
    range(len(sorted_exp)),
    format_func=lambda i: ("📊 " if sorted_exp[i]["has_daily"] else "") + sorted_exp[i]["label"],
    index=0,
)
selected = sorted_exp[selected_idx]

metrics = load_backtest_metrics(selected["subdir"], selected["start"], selected["end"])
daily = load_daily_returns(selected["subdir"], selected["start"], selected["end"])
snapshots = load_backtest_snapshots(selected["subdir"], selected["start"], selected["end"])

if not metrics:
    st.error("找不到資料。")
    st.stop()

# --- 頂部：三個最重要的數字 ---
col1, col2, col3 = st.columns(3)

ann_ret = metrics.get("annualized_return", 0)
bench_ret = metrics.get("benchmark_annualized_return", 0)
mdd = metrics.get("max_drawdown", 0)

with col1:
    st.metric("年化報酬", f"{ann_ret:.1%}")
    st.caption("策略平均每年幫你賺多少")

with col2:
    if "annualized_alpha" in metrics:
        alpha = metrics["annualized_alpha"]
        st.metric("超越 0050", f"{alpha:+.1%}")
        if alpha > 0:
            st.caption(f"策略每年比 0050 多賺 {alpha:.1%}")
        else:
            st.caption(f"策略每年輸 0050 {abs(alpha):.1%}")
    else:
        st.metric("0050 年化", f"{bench_ret:.1%}")

with col3:
    st.metric("最大回撤", f"{mdd:.1%}")
    if mdd < 0:
        remain = 100 + mdd * 100
        st.caption(f"最慘時期：100 萬會暫時變成 {remain:.0f} 萬")
    else:
        st.caption("無顯著回撤")

st.divider()

# --- 第一張圖：累積報酬曲線（最重要的圖）---
if daily and "portfolio" in daily:
    st.subheader("💰 你的錢會怎麼變化")
    st.caption("假設一開始投入 100 萬，藍線是策略，灰線是直接買 0050")

    port_rets = pd.Series(daily["portfolio"], dtype=float)
    port_rets.index = pd.to_datetime(port_rets.index)
    port_rets = port_rets.sort_index()
    port_cum = (1 + port_rets).cumprod()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=port_cum.index, y=port_cum * 100,
        mode="lines", name="策略",
        line=dict(color="#3498db", width=2.5),
        hovertemplate="%{x|%Y-%m-%d}<br>資產：%{y:.0f} 萬",
    ))

    if "benchmark" in daily:
        bench_rets = pd.Series(daily["benchmark"], dtype=float)
        bench_rets.index = pd.to_datetime(bench_rets.index)
        bench_rets = bench_rets.sort_index()
        start_date = port_rets.index.min()
        bench_rets = bench_rets[bench_rets.index >= start_date]
        if not bench_rets.empty:
            bench_cum = (1 + bench_rets).cumprod()
            fig.add_trace(go.Scatter(
                x=bench_cum.index, y=bench_cum * 100,
                mode="lines", name="0050",
                line=dict(color="#bdc3c7", width=1.5, dash="dash"),
                hovertemplate="%{x|%Y-%m-%d}<br>資產：%{y:.0f} 萬",
            ))

    fig.add_hline(y=100, line_dash="dot", line_color="gray", opacity=0.3)
    fig.update_layout(
        yaxis_title="資產（萬元，起始 100 萬）",
        height=450, margin=dict(t=20, b=20),
        legend=dict(x=0.02, y=0.98),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    # --- 第二張圖：回撤（最慘的時候虧多少）---
    st.subheader("📉 最慘的時期")
    st.caption("紅色區域越深，代表當時虧越多。看看策略多快恢復。")

    running_max = port_cum.cummax()
    drawdown = (port_cum - running_max) / running_max

    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=drawdown.index, y=drawdown * 100,
        fill="tozeroy", mode="lines",
        line=dict(color="#e74c3c", width=1),
        fillcolor="rgba(231, 76, 60, 0.3)",
        hovertemplate="%{x|%Y-%m-%d}<br>從高點下跌：%{y:.1f}%",
    ))
    fig_dd.update_layout(
        yaxis_title="從高點下跌 (%)",
        height=250, margin=dict(t=20, b=20),
        hovermode="x unified",
    )
    st.plotly_chart(fig_dd, use_container_width=True)

else:
    st.info("此回測尚無日頻資料（需包含 `daily_returns.json`）。")

st.divider()

# --- 更多數字（摺疊）---
with st.expander("📊 完整績效指標（進階）"):
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Sharpe", f"{metrics.get('sharpe_ratio', 0):.2f}")
    col_a.caption("風險調整後報酬，> 1 算好")
    col_b.metric("Sortino", f"{metrics.get('sortino_ratio', 0):.2f}" if "sortino_ratio" in metrics else "—")
    col_b.caption("只看下跌風險的 Sharpe")
    col_c.metric("Beta", f"{metrics.get('beta', 0):.2f}" if "beta" in metrics else "—")
    col_c.caption("跟大盤的連動性，0.5 = 大盤跌 10% 我跌 5%")
    col_d.metric("波動率", f"{metrics.get('annualized_volatility', 0):.1%}")
    col_d.caption("報酬的上下震盪幅度")

    st.markdown(f"""
    | 指標 | 數值 | 意思 |
    |------|------|------|
    | 交易次數 | {metrics.get('n_rebalances', 0)} 次 | 總共調整了幾次持股 |
    | 每次換手 | {metrics.get('avg_turnover_per_rebalance', 0):.0%} | 每次換掉幾成的持股 |
    | 交易成本 | {metrics.get('total_trade_cost', 0):.2%} | 累積的手續費 + 稅 |
    | 資料品質 | {'✅ 正常' if not metrics.get('data_degraded') else '⚠️ 有缺失'} | 回測數據是否完整 |
    """)

# --- 市場訊號變化 ---
if snapshots:
    with st.expander("🚦 市場訊號變化（進階）"):
        st.caption("每月策略判斷大盤是漲是跌，決定投多少錢")
        signal_data = []
        for s in snapshots:
            sig = s.get("market_signal", "")
            exposure = s.get("gross_exposure", 0)
            signal_data.append({
                "日期": s.get("rebalance_date", "")[:10],
                "訊號": {"risk_on": "🟢 積極", "caution": "🟡 觀望", "risk_off": "🔴 防禦"}.get(sig, sig),
                "投入比例": f"{exposure:.0%}",
                "持股數": s.get("selected_count", 0),
            })
        st.dataframe(pd.DataFrame(signal_data), use_container_width=True, hide_index=True)
