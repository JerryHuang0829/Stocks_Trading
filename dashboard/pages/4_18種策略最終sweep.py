"""頁 5（user 編號）— Phase D v7 18 種策略 sweep 結果。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import (
    STRATEGY_FACTORS,
    STRATEGY_LOGIC,
    STRATEGY_NAMES,
    gate_pass_count,
    get_monthly_active_return_dates,
    load_cell_summary,
    load_monthly_active_returns,
    strategy_label,
)

st.set_page_config(
    page_title="Phase D v7 18 種策略掃描",
    page_icon="🎯",
    layout="wide",
)

st.title("🎯 18 種策略 × 持股數 全表現掃描")
st.caption(
    "6 個候選因子組合 × 3 種持股數（8/12/16 檔）= 18 種策略，"
    "每種都跑 6 道 hard gate（L1-L6）。"
    "Canonical 結果：reports/phase_d/cell_sweep_v7_2026_05_06/cell_summary.json"
)

# ===============================================================
# 6 candidates 策略對照表（讓讀者一進來就懂 D-X 是什麼）
# ===============================================================
st.subheader("📖 6 個策略候選")

strategy_table = pd.DataFrame(
    [
        {
            "代號": cid,
            "策略名": STRATEGY_NAMES[cid],
            "因子組成": STRATEGY_FACTORS[cid],
            "策略邏輯": STRATEGY_LOGIC[cid],
        }
        for cid in ["D-B", "D-C", "D-D", "D-E", "D-F", "D-G"]
    ]
)
st.dataframe(strategy_table, width="stretch", hide_index=True)

st.caption(
    "📌 三個共用因子：**52W 高接近度（價格動量）** + **PEAD/EPS 驚喜（基本面）** "
    "+ **融資反向 / 品質 / 產業動量 / 特質波動**（後 4 個是 v6/v7 的測試 variant）。"
    "D-A（52W+PEAD 50/50）已預先 disqualify（D6 OOS IR 從 0.92 collapse 到 0.006）。"
)

# ===============================================================
# Load data
# ===============================================================
summary = load_cell_summary()
monthly_returns = load_monthly_active_returns()

if summary is None or monthly_returns is None:
    st.error("讀不到 Phase D 18-cell 結果。請確認 reports/phase_d/cell_sweep_v7_2026_05_06/ 存在。")
    st.stop()

cells = summary.get("cells", [])
if not cells:
    st.error("cell_summary.json 內 cells 列表為空")
    st.stop()

# ===============================================================
# 6 × 3 Heatmap — 軸 label 中文化
# ===============================================================
st.subheader("📊 18 種策略過關 heatmap（6 策略 × 3 持股數）")

# 建 heatmap matrix: rows = candidates (D-B/C/D/E/F/G), cols = top_n (8/12/16)
candidates_order = ["D-B", "D-C", "D-D", "D-E", "D-F", "D-G"]
top_ns = [8, 12, 16]

matrix = [[0 for _ in top_ns] for _ in candidates_order]
hover_texts = [["" for _ in top_ns] for _ in candidates_order]

for c in cells:
    cid = c.get("candidate_id", "")
    tn = c.get("top_n", 0)
    if cid in candidates_order and tn in top_ns:
        i = candidates_order.index(cid)
        j = top_ns.index(tn)
        gates = c.get("gates", {})
        passes = gate_pass_count(gates)
        matrix[i][j] = passes
        ci_low = c.get("bootstrap_ci_lower", 0)
        all_pass = c.get("all_l1_l6_passed", False)
        metrics = c.get("metrics", {})
        hover_texts[i][j] = (
            f"<b>{STRATEGY_NAMES.get(cid, cid)} | {tn} 檔</b><br>"
            f"代號：{cid}|{tn}<br>"
            f"因子組成：{STRATEGY_FACTORS.get(cid, '?')}<br>"
            f"邏輯：{STRATEGY_LOGIC.get(cid, '?')}<br>"
            f"<br>"
            f"過 {passes}/6 hard gates<br>"
            f"IR：{metrics.get('ir', 0):.3f}（hard gate L1：≥ 0.20）<br>"
            f"月 α：{metrics.get('mean_alpha_monthly', 0):.4f}（hard gate L2：≥ 0.005）<br>"
            f"L6 CI lower：{ci_low:.4f}（hard gate L6：> 0）<br>"
            f"全部 6 hard gate 通過：{'是' if all_pass else '否'}"
        )

# Plotly Heatmap with FIXED colorscale [0, 6]
fig_heat = go.Figure(
    data=go.Heatmap(
        z=matrix,
        x=[f"持股 {tn} 檔" for tn in top_ns],   # ← X 軸中文化
        y=[STRATEGY_NAMES[cid] for cid in candidates_order],   # ← Y 軸用策略名而非代號
        colorscale=[
            [0.0, "#c0392b"],   # 0  — 紅 deep
            [0.33, "#e67e22"],  # 2  — 紅
            [0.5, "#f39c12"],   # 3  — 橙
            [0.67, "#f1c40f"],  # 4  — 黃
            [0.83, "#2ecc71"],  # 5  — 綠淺
            [1.0, "#27ae60"],   # 6  — 綠深 PASS
        ],
        zmin=0,
        zmax=6,
        text=matrix,
        texttemplate="%{text}/6",
        textfont={"size": 16, "color": "white"},
        hovertext=hover_texts,
        hovertemplate="%{hovertext}<extra></extra>",
        colorbar=dict(title="過幾關 (0-6)", tickvals=[0, 1, 2, 3, 4, 5, 6]),
    )
)
fig_heat.update_layout(
    height=480,
    xaxis_title="持股數",
    yaxis_title="策略候選",
    margin=dict(t=20, b=20, l=180, r=20),   # 加大左 margin 給中文 label
)
st.plotly_chart(fig_heat, width="stretch")

st.warning(
    "⚠️ **過 4 關 ≠ 接近 alpha**。L6 是 80% Stationary Block Bootstrap CI lower，"
    "lower bound ≤ 0 即統計上**無顯著 edge**——再多 IS metric 漂亮都救不回。"
    "本 sweep 18 cells 最高過 4/6（D-C\\|12 / D-E\\|12 / D-E\\|16），全 18 cells L6 都 fail（CI lower 全 ≤ 0），故 0 cell 過 6/6。"
)

st.divider()

# ===============================================================
# L1-L6 詳表
# ===============================================================
st.subheader("📋 18-cell L1-L6 gates + metrics 詳表")

table_rows = []
gate_labels = {
    "L1_ir_ge_0_20": "L1 IR≥0.20",
    "L2_mean_alpha_ge_0_005": "L2 月α≥0.5%",
    "L3_te_in_range": "L3 TE∈[0.10,0.30]",
    "L4_max_dd_diff_le_0_05": "L4 ΔDD≤+5%",
    "L5_a1_active_corr_le_0_50": "L5 A1 corr≤0.5",
    "L6_bootstrap_ci_lower_gt_0": "L6 CI>0",
}

for c in cells:
    candidate_id = c.get("candidate_id", "?")
    tn = c.get("top_n", "?")
    label = strategy_label(candidate_id, tn) if candidate_id != "?" else f"{candidate_id}|{tn}"
    gates = c.get("gates", {})
    metrics = c.get("metrics", {})
    row = {"策略 | 持股數": label, "代號": f"{candidate_id}|{tn}"}
    for gk, gl in gate_labels.items():
        row[gl] = "✅" if gates.get(gk, False) else "❌"
    row["過幾關"] = f"{gate_pass_count(gates)}/6"
    row["IR"] = f"{metrics.get('ir', 0):.3f}"
    row["月α"] = f"{metrics.get('mean_alpha_monthly', 0):.4f}"
    row["TE"] = f"{metrics.get('te', 0):.3f}"
    row["ΔMaxDD"] = f"{metrics.get('max_dd_diff_vs_0050', 0):+.3f}"
    row["DSR"] = f"{c.get('dsr', 0):.3f}"
    row["L6 CI lower"] = f"{c.get('bootstrap_ci_lower', 0):+.4f}"
    table_rows.append(row)

df_cells = pd.DataFrame(table_rows)
df_cells_sorted = df_cells.sort_values("過幾關", ascending=False).reset_index(drop=True)
st.dataframe(df_cells_sorted, width="stretch", hide_index=True)

st.caption(
    "**單位**：IR / 月α / TE / ΔMaxDD / DSR / L6 CI lower 都是 decimal（非 %）。"
    "月α=0.005 即每月超額 0.5%。"
)

st.divider()

# ===============================================================
# L4 max_dd_diff 獨立條狀圖（CRO Q2 修法）
# ===============================================================
st.subheader("⚠️ L4 風險指標 — 各 cell 最大回撤偏離度")

st.caption(
    "L4 hard gate：max_dd_diff_vs_0050 ≤ +0.05（即最大回撤不能比 0050 差超過 5%）。"
    "對 retail 100 萬 NTD baseline，這是最重要的風險指標——alpha 再高，"
    "若回撤遠大於 0050 也不可接受。"
)

dd_data = sorted(
    [(strategy_label(c.get("candidate_id", "?"), c.get("top_n", "?")),
      c.get("metrics", {}).get("max_dd_diff_vs_0050", 0))
     for c in cells],
    key=lambda x: x[1],
    reverse=True,
)
fig_dd = go.Figure(
    go.Bar(
        x=[d[1] for d in dd_data],
        y=[d[0] for d in dd_data],
        orientation="h",
        marker=dict(
            color=["#c0392b" if d[1] > 0.05 else "#27ae60" for d in dd_data],
        ),
        text=[f"{d[1]:+.3f}" for d in dd_data],
        textposition="outside",
    )
)
fig_dd.add_vline(
    x=0.05,
    line_dash="dash",
    line_color="red",
    annotation_text="L4 threshold +0.05",
    annotation_position="top",
)
fig_dd.update_layout(
    height=500,
    xaxis_title="ΔMaxDD vs 0050（越大越差）",
    yaxis_title="策略 | 持股數",
    margin=dict(t=20, b=20, l=240, r=80),
)
st.plotly_chart(fig_dd, width="stretch")

st.divider()

# ===============================================================
# 互動：選 cell 看月超額報酬時序
# ===============================================================
st.subheader("📈 選 cell 看月超額報酬時序")

st.info(
    "📌 **跟「雙因子回測」頁差在哪**：\n\n"
    "- **本頁這 section**：18 種策略**任選一個**，看 IS 5 年（2020-2024）的**月超額報酬**（已扣 0050）。**只有 IS、沒有 OOS**。\n"
    "- **「雙因子回測」頁**：**固定一個策略 D1_v2**（52W 50% + PEAD 50%），看 **IS+OOS 完整對照**＋12 個 metrics 詳解。\n\n"
    "**為什麼本頁沒 OOS**：Phase D v7 紀律是「IS 過 6/6 hard gates 才能開 OOS paper trade」。"
    "0/18 cells 過 → 沒人有資格動 OOS 資料 → 避免事後 cherry-pick 解套。"
)

cell_id_options = [f"{c.get('candidate_id')}|{c.get('top_n')}" for c in cells]
# 顯示用 label，背後值仍用代號（保持 monthly_returns dict key 對應）
display_options = [
    strategy_label(c.get("candidate_id", "?"), c.get("top_n", "?"))
    for c in cells
]
default_idx = cell_id_options.index("D-C|12") if "D-C|12" in cell_id_options else 0

selected_display = st.selectbox(
    "選一個策略 × 持股數：",
    options=display_options,
    index=default_idx,
    help="預設「動量+獲利 | 12 檔」（D-C|12，過 4/6，是最接近 alpha 的策略之一；L5 A1 子條件 beta-adj t < 1.5 + L6 CI fail）",
)
# 反查：display label → 原代號 ID（用於 monthly_returns dict）
selected = cell_id_options[display_options.index(selected_display)]

dates = get_monthly_active_return_dates()
returns = monthly_returns.get(selected, [])

if not dates or not returns or len(dates) != len(returns):
    st.warning(
        f"無法對齊日期：dates={len(dates) if dates else 0}, returns={len(returns)}"
    )
else:
    df_ts = pd.DataFrame({"date": pd.to_datetime(dates), "monthly_active_return": returns})

    fig_ts = go.Figure()
    fig_ts.add_trace(
        go.Bar(
            x=df_ts["date"],
            y=df_ts["monthly_active_return"],
            marker=dict(
                color=[
                    "#27ae60" if r >= 0 else "#c0392b" for r in df_ts["monthly_active_return"]
                ]
            ),
            name=selected_display,
            hovertemplate="%{x|%Y-%m}<br>月超額： %{y:.2%}<extra></extra>",
        )
    )
    fig_ts.add_hline(y=0, line_color="gray", line_width=1)
    fig_ts.update_layout(
        height=350,
        title=f"{selected_display}（{selected}）月超額報酬 vs 0050，{df_ts['date'].dt.year.min()}-{df_ts['date'].dt.year.max()} 共 {len(df_ts)} 個月",
        xaxis_title="月份",
        yaxis_title="月超額報酬",
        yaxis_tickformat=".1%",
        margin=dict(t=50, b=20),
    )
    st.plotly_chart(fig_ts, width="stretch")

    # 累積報酬
    cum = (1 + pd.Series(returns)).cumprod() - 1
    df_cum = pd.DataFrame({"date": pd.to_datetime(dates), "cum_active_return": cum.values})

    fig_cum = go.Figure()
    fig_cum.add_trace(
        go.Scatter(
            x=df_cum["date"],
            y=df_cum["cum_active_return"],
            mode="lines",
            line=dict(color="#3498db", width=2),
            fill="tozeroy",
            fillcolor="rgba(52, 152, 219, 0.2)",
            name=selected,
            hovertemplate="%{x|%Y-%m}<br>累積超額： %{y:.2%}<extra></extra>",
        )
    )
    fig_cum.add_hline(y=0, line_color="gray", line_width=1)
    fig_cum.update_layout(
        height=350,
        title=f"{selected} 累積超額報酬",
        xaxis_title="月份",
        yaxis_title="累積超額報酬",
        yaxis_tickformat=".0%",
        margin=dict(t=50, b=20),
    )
    st.plotly_chart(fig_cum, width="stretch")

    # 該 cell 的 metrics summary
    selected_cell = next((c for c in cells if f"{c.get('candidate_id')}|{c.get('top_n')}" == selected), None)
    if selected_cell:
        gates = selected_cell.get("gates", {})
        metrics = selected_cell.get("metrics", {})
        passes = gate_pass_count(gates)
        st.info(
            f"**{selected}**：過 {passes}/6 gates | "
            f"IR={metrics.get('ir', 0):.3f} | "
            f"月α={metrics.get('mean_alpha_monthly', 0):.4f} | "
            f"TE={metrics.get('te', 0):.3f} | "
            f"L6 CI lower={selected_cell.get('bootstrap_ci_lower', 0):+.4f}"
        )
