"""策略在不同時期都有效嗎？"""

import streamlit as st
import plotly.express as px
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import load_walk_forward_summary

st.set_page_config(page_title="歷史驗證", page_icon="🔄", layout="wide")
st.title("🔄 策略在不同時期都有效嗎？")

wf = load_walk_forward_summary()
if wf is None:
    st.warning("尚無驗證結果。請先執行：")
    st.code("docker compose run --rm --entrypoint python portfolio-bot scripts/walk_forward.py")
    st.stop()

agg = wf.get("aggregate", {})
windows = [w for w in wf.get("windows", []) if "sharpe" in w and w["sharpe"] is not None]
n_windows = len(windows)

# 時間範圍（從 windows 動態讀取）
if windows:
    all_start = min(w["test_start"][:7] for w in windows)
    all_end = max(w["test_end"][:7] for w in windows)
    period_desc = f"{all_start} ～ {all_end}"
else:
    period_desc = "—"

st.caption(f"把 {period_desc} 切成 {n_windows} 個測試視窗，分別測試策略表現。就像考 {n_windows} 次試，看看是不是每次都及格。")

# --- 一句話結論 ---
win_rate = agg.get("win_rate", 0)
mean_sharpe = agg.get("mean_sharpe", 0)

if win_rate >= 0.7:
    st.success(f"✅ {n_windows} 次考試中 {win_rate:.0%} 及格 — 策略經得起考驗")
elif win_rate >= 0.5:
    st.warning(f"⚠️ {n_windows} 次考試中 {win_rate:.0%} 及格 — 策略有效但不穩定")
else:
    st.error(f"❌ {n_windows} 次考試中 {win_rate:.0%} 及格 — 策略可能有問題")

st.divider()

# --- 三個數字 ---
col1, col2, col3 = st.columns(3)
col1.metric("勝率", f"{win_rate:.0%}")
col1.caption("賺錢的視窗佔幾成")
col2.metric("平均表現", f"{mean_sharpe:.2f}")
col2.caption("Sharpe > 1 很好，> 0.5 及格")
worst_mdd = agg.get("worst_mdd")
col3.metric("最慘視窗 MDD", f"{worst_mdd:.0%}" if worst_mdd is not None else "—")
col3.caption("最差測試期的最大虧損")

st.divider()

# --- 一張圖：每個視窗是賺是虧 ---
st.subheader(f"{n_windows} 個測試視窗的成績單")

# date-based market context（依實際測試起始年月對應）
_PERIOD_LABELS: dict[str, str] = {
    "2020-01": "疫情衝擊",
    "2020-07": "疫後反彈",
    "2021-01": "航運飆漲",
    "2021-07": "高檔震盪",
    "2022-01": "升息崩跌",
    "2022-07": "熊市末段",
    "2023-01": "AI 爆發",
    "2023-07": "盤整消化",
    "2024-01": "台積電領漲",
    "2024-07": "權值股獨漲",
    "2025-01": "關稅衝擊",
    "2025-07": "下半年走勢",
}

chart_data = []
for w in windows:
    start_ym = w["test_start"][:7]
    end_ym = w["test_end"][:7]
    context = _PERIOD_LABELS.get(start_ym, "")
    period = f"{start_ym}~{end_ym}"
    label = f"{period}\n{context}" if context else period
    sharpe = w["sharpe"]
    chart_data.append({
        "時期": label,
        "表現": sharpe,
        "結果": "✅ 賺錢" if sharpe > 0 else "❌ 虧錢",
    })

df = pd.DataFrame(chart_data)
fig = px.bar(
    df, x="時期", y="表現",
    color="結果",
    color_discrete_map={"✅ 賺錢": "#2ecc71", "❌ 虧錢": "#e74c3c"},
)
fig.add_hline(y=0, line_dash="dash", line_color="gray")
fig.update_layout(
    yaxis_title="Sharpe（越高越好，0 以上 = 賺錢）",
    height=450,
    margin=dict(t=20, b=100),
    showlegend=True,
)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- 白話解讀 ---
st.subheader("什麼時候賺、什麼時候虧？")

st.markdown("""
**策略賺錢的環境 ✅**
- 市場有明確方向（不管漲還是跌，只要趨勢清楚）
- 例如：疫後反彈、AI 題材爆發、台積電領漲

**策略虧錢的環境 ❌**
- 市場突然反轉（昨天還在漲，今天突然崩）
- 大型股獨漲，中小型股沒跟上
- 例如：2022 上半年升息開始崩跌

**一句話總結：趨勢明確時很賺，轉折點會虧，但賺的時候賺得多、虧的時候虧得少。**
""")

# --- 明細（摺疊）---
with st.expander("📊 完整數據表（進階）"):
    rows = []
    for w in windows:
        rows.append({
            "視窗": f"W{w['window']}",
            "測試期間": f"{w['test_start'][:7]} → {w['test_end'][:7]}",
            "Sharpe": f"{w['sharpe']:+.2f}",
            "年化報酬": f"{w.get('annualized_return', 0):.1%}",
            "最大回撤": f"{w.get('max_drawdown', 0):.1%}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
