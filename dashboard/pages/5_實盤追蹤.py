"""你的實際投資紀錄。"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import json
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import PROJECT_ROOT, load_latest_close

REAL_TRADING_DIR = PROJECT_ROOT / "reports" / "real_trading"
PORTFOLIO_FILE = REAL_TRADING_DIR / "portfolio.json"
TRADES_FILE = REAL_TRADING_DIR / "trades.json"
PERFORMANCE_FILE = REAL_TRADING_DIR / "performance.json"

st.set_page_config(page_title="實盤追蹤", page_icon="💰", layout="wide")
st.title("💰 你的實際投資")


def _load(path):
    if not path.exists():
        return [] if "trades" in path.name or "performance" in path.name else {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


portfolio = _load(PORTFOLIO_FILE)
trades = _load(TRADES_FILE)
performance = _load(PERFORMANCE_FILE)

if not portfolio and not trades:
    st.info("尚未開始實盤投資。開始記錄方法：")
    st.markdown("""
    ```bash
    # 在券商 APP 用零股交易買入後，記錄交易
    python scripts/real_trade.py buy 2330 12 580.0

    # 查看持股
    python scripts/real_trade.py status
    ```
    """)
    st.stop()

# --- 目前持股（含現值損益）---
if portfolio:
    st.subheader("目前持股")

    today = date.today().isoformat()
    rows = []
    total_cost = 0.0
    total_value = 0.0
    any_price_missing = False

    for symbol, info in sorted(portfolio.items()):
        shares = info["shares"]
        avg_cost = info["avg_cost"]
        cost = info["total_cost"]
        total_cost += cost

        latest_price = load_latest_close(symbol)
        if latest_price:
            market_value = latest_price * shares
            pnl = market_value - cost
            pnl_pct = pnl / cost if cost > 0 else 0
            total_value += market_value
            rows.append({
                "股票": symbol,
                "股數": shares,
                "均價": f"{avg_cost:.1f}",
                "現價": f"{latest_price:.1f}",
                "成本": f"{cost:,.0f}",
                "現值": f"{market_value:,.0f}",
                "損益": f"{pnl:+,.0f}",
                "損益率": f"{pnl_pct:+.1%}",
                "_pnl": pnl,
            })
        else:
            any_price_missing = True
            total_value += cost  # 無法取得時用成本代替
            rows.append({
                "股票": symbol,
                "股數": shares,
                "均價": f"{avg_cost:.1f}",
                "現價": "—",
                "成本": f"{cost:,.0f}",
                "現值": "—",
                "損益": "—",
                "損益率": "—",
                "_pnl": 0,
            })

    if any_price_missing:
        st.caption("⚠️ 部分股票無法取得最新收盤價（可能今日未交易）")

    # 顯示 DataFrame（去掉 _pnl 欄）
    display_df = pd.DataFrame(rows).drop(columns=["_pnl"])
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # 總覽指標
    total_pnl = total_value - total_cost
    total_pnl_pct = total_pnl / total_cost if total_cost > 0 else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("總投入成本", f"{total_cost:,.0f} 元")
    col2.metric("目前市值", f"{total_value:,.0f} 元")
    col3.metric("總損益", f"{total_pnl:+,.0f} 元", delta=f"{total_pnl_pct:+.1%}")

    # 個股損益長條圖
    if any(r["損益"] != "—" for r in rows):
        fig = go.Figure(go.Bar(
            x=[r["股票"] for r in rows if r["損益"] != "—"],
            y=[r["_pnl"] for r in rows if r["損益"] != "—"],
            marker_color=["#2ecc71" if r["_pnl"] >= 0 else "#e74c3c" for r in rows if r["損益"] != "—"],
            hovertemplate="%{x}<br>損益：%{y:+,.0f} 元",
        ))
        fig.update_layout(
            yaxis_title="損益（元）",
            height=250, margin=dict(t=10, b=20),
        )
        fig.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.4)
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- 交易紀錄 ---
if trades:
    st.subheader("交易紀錄")
    for t in reversed(trades[-10:]):
        if t["action"] == "BUY":
            st.markdown(f"🟢 {t['date']} 買入 **{t['symbol']}** {t['shares']}股 @ {t['price']}元 = {t['total']:,.0f}元")
        else:
            emoji = "📈" if t.get("profit", 0) >= 0 else "📉"
            st.markdown(f"🔴 {t['date']} 賣出 **{t['symbol']}** {t['shares']}股 @ {t['price']}元 {emoji} {t.get('profit', 0):+,.0f}元")

    total_fees = sum(t.get("fee", 0) for t in trades)
    total_tax = sum(t.get("tax", 0) for t in trades)
    st.caption(f"累計手續費 {total_fees:,.0f} 元 + 證交稅 {total_tax:,.0f} 元")

st.divider()

# --- 月績效 ---
if performance:
    st.subheader("每月成績")
    for p in reversed(performance):
        emoji = "📈" if p.get("total_return", 0) >= 0 else "📉"
        st.markdown(f"{emoji} **{p['month_key']}** — 報酬 {p.get('total_return', 0):+.1%}，損益 {p.get('total_profit', 0):+,.0f} 元")
