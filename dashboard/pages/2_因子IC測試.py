"""頁 3（user 編號）/ pages/2_因子IC測試.py — 5 個 Phase A1 因子 single-factor IC 實證。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import (
    FACTOR_DISPLAY_NAMES,
    FIVE_FACTORS,
    load_all_factor_ics,
    load_factor_correlation,
)

st.set_page_config(
    page_title="因子 IC 測試",
    page_icon="📈",
    layout="wide",
)

st.title("📈 因子 IC 測試")
st.caption(
    "用 Spearman rank IC + Stationary Block Bootstrap + Deflated Sharpe Ratio + "
    "FDR Benjamini-Hochberg 多重檢定校正，**個別檢驗**每個因子的學術顯著性。"
)

st.divider()

# ===============================================================
# 評估指標說明 — 看主表前先懂指標
# ===============================================================
with st.expander("📖 5 個評估指標說明（先看再對照下方表）", expanded=True):
    st.markdown(
        """
| 指標 | 中文 | 意思 |
|---|---|---|
| **mean IC** | 平均資訊係數 | 每個月「**因子排名**」與「**下個月報酬排名**」的 Spearman 相關係數，再對所有月份取平均。> 0 = 排名強 → 報酬高；越大越好。經驗法則：≥ 0.04 算 strong，0.02-0.04 算中道，< 0.02 弱。|
| **IC IR** | 資訊比率 | mean IC / std(IC）。等於 IC 的「夏普比率」——signal-to-noise。越大越穩。≥ 0.5 強，0.3-0.5 中道。|
| **t-stat / p-value** | t 檢定 | 檢定 mean IC ≠ 0 的統計顯著性。p < 0.05 = 95% 信心因子真的有 signal。|
| **FDR-adj p** | 多重檢定校正 p | 同時測 5 個因子，**Benjamini-Hochberg 校正**避免「testing fishing」假陽性。比 p-value 嚴格。|
| **DSR (Deflated Sharpe Ratio)** | 折減夏普比率 | Bailey & Lopez de Prado (2014）。把「同時測多個 trial 的選擇偏誤」+「IC 分佈非常態」校正後的信心度。**Ψ ≥ 0.95 = 強信心；≈ 0.5 = 不分上下；≤ 0.05 = 連 null 都贏不了**（注意：DSR 是 confidence 不是 p-value，方向相反！）|
| **Bootstrap CI 95%** | 重抽樣 95% 信心區間 | Politis-Romano stationary block bootstrap（block_len=3）保留時序自相關，估 mean IC 的 95% CI。下界 > 0 = robust。|
| **effective n** | 有效樣本數 | 產業 cluster 校正後的有效樣本數（不是 raw months）。會比實際月數小，避免高度相關 cluster 過度膨脹顯著性。|

---

**verdict 規則**：
- 🟢 **Good**（過嚴格門檻）：mean IC > 0.04 + p < 0.05 + DSR > 0.5
- 🟡 **Normal**（過中道門檻）：mean IC > 0.02 + p < 0.10
- 🔴 **Fail**（以上都不滿足）
"""
    )

st.divider()

# ===============================================================
# 5 因子 IC 主表
# ===============================================================
st.subheader("📋 5 因子 IC 主表")

ics = load_all_factor_ics()
if not ics:
    st.error("讀不到 reports/factor_ic/ 內 5 因子 IC JSON")
    st.stop()

table_rows = []
for factor in FIVE_FACTORS:
    ic = ics.get(factor)
    if ic is None:
        continue
    overall = ic.get("overall", {})
    ci = overall.get("bootstrap_ci_95", [None, None])
    fdr = ic.get("fdr_adjusted_p", None)
    dsr = ic.get("deflated_sharpe_ratio", None)
    eff_n = ic.get("effective_n", None)

    # Verdict logic
    mean_ic = overall.get("mean_ic", 0)
    p_val = overall.get("p_value", 1)
    if mean_ic > 0.04 and p_val < 0.05 and (dsr is not None and dsr > 0.5):
        verdict = "🟢 Good"
    elif mean_ic > 0.02 and p_val < 0.10:
        verdict = "🟡 Normal"
    else:
        verdict = "🔴 Fail"

    table_rows.append({
        "因子": FACTOR_DISPLAY_NAMES.get(factor, factor),
        "mean IC": f"{mean_ic:.4f}",
        "IC IR": f"{overall.get('ic_ir', 0):.3f}",
        "t-stat": f"{overall.get('t_stat', 0):.3f}",
        "p-value": f"{p_val:.4f}",
        "FDR-adj p": f"{fdr:.4f}" if fdr is not None else "N/A",
        "DSR": f"{dsr:.4f}" if dsr is not None else "N/A",
        "Bootstrap CI 95%": f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci and ci[0] is not None else "N/A",
        "effective n": f"{eff_n:.0f}" if eff_n else "N/A",
        "n_periods": overall.get("n", 0),
        "verdict": verdict,
    })

df_ic = pd.DataFrame(table_rows)
st.dataframe(df_ic, use_container_width=True, hide_index=True)

st.warning(
    "⚠️ **IC IR ≠ 策略 Sharpe**。高 IC IR 因子也可能無 strategy edge——"
    "「雙因子回測」頁可看到 D1_v2 IS IR 0.92 → OOS IR 0.0058 collapse 99.4%。"
    "IC IR 是 factor signal-to-noise，portfolio Sharpe 還要過 selection / weighting / cost。"
)

st.divider()

# ===============================================================
# 5×5 Spearman Correlation Heatmap
# ===============================================================
st.subheader("🔗 5 因子 Spearman 相關性 heatmap")

st.caption(
    "因子之間相關性高 → 加在一起組合會 redundant（沒互補）。"
    "理想：找相關性低（|ρ| < 0.3）的因子組合，互補才有 diversification benefit。"
)

corr_data = load_factor_correlation()
if corr_data is None:
    st.warning("讀不到 factor_correlation_matrix.json")
else:
    factors_list = corr_data.get("factors", [])
    matrix_dict = corr_data.get("matrix", {})

    # 建 5×5 matrix
    n = len(factors_list)
    corr_matrix = [[matrix_dict.get(f1, {}).get(f2, 0) for f2 in factors_list] for f1 in factors_list]
    display_names = [FACTOR_DISPLAY_NAMES.get(f, f) for f in factors_list]

    fig_corr = go.Figure(
        data=go.Heatmap(
            z=corr_matrix,
            x=display_names,
            y=display_names,
            colorscale="RdBu_r",
            zmid=0,
            zmin=-1,
            zmax=1,
            text=corr_matrix,
            texttemplate="%{text:.3f}",
            textfont={"size": 12},
            hovertemplate="%{y}<br>vs<br>%{x}<br>ρ = %{z:.3f}<extra></extra>",
            colorbar=dict(title="Spearman ρ"),
        )
    )
    fig_corr.update_layout(
        height=500,
        margin=dict(t=40, b=40, l=120, r=80),
    )
    st.plotly_chart(fig_corr, use_container_width=True)

    n_periods = corr_data.get("period_counts", {})
    n_periods_avg = next(iter(n_periods.values()), 71) if isinstance(n_periods, dict) else 71
    st.caption(
        f"**Spearman rank correlation**, n_periods≈{n_periods_avg} monthly。"
        "RdBu_r colorscale 中央對齊 0；對角線 1.0 是因子自相關。"
    )

st.divider()

# ===============================================================
# 進階分析 — 折疊起來，外人不必看
# ===============================================================
st.subheader("🔬 進階分析（按因子細看）")

with st.expander("📊 選因子看 by_regime / by_bucket / 月度 IC 時序", expanded=False):
    st.caption(
        "**by_regime**：按市場狀態（risk_on / caution / risk_off）拆 IC，看因子在哪種市場有效。"
        "**by_bucket**：按時間切片（年度）拆 IC，看因子表現是否穩定。"
        "**月度 IC**：每月 IC 的時序圖，看是否有 collapse / regime shift。"
    )

    selected_factor = st.selectbox(
        "選一個因子：",
        options=FIVE_FACTORS,
        format_func=lambda f: f"{FACTOR_DISPLAY_NAMES.get(f, f)} ({f})",
        index=0,
    )

    ic = ics.get(selected_factor, {})

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**By Regime（按市場狀態拆）**")
        by_regime = ic.get("by_regime", {})
        if by_regime:
            regime_rows = []
            for regime, m in by_regime.items():
                regime_rows.append({
                    "Regime": regime,
                    "mean IC": f"{m.get('mean_ic', 0):.4f}",
                    "IC IR": f"{m.get('ic_ir', 0):.3f}",
                    "t-stat": f"{m.get('t_stat', 0):.3f}",
                    "p-value": f"{m.get('p_value', 1):.4f}",
                    "n": m.get("n", 0),
                })
            st.dataframe(pd.DataFrame(regime_rows), use_container_width=True, hide_index=True)
        else:
            st.info("無 by_regime 資料")

    with col_b:
        st.markdown("**By Bucket（按時間切片拆）**")
        by_bucket = ic.get("by_bucket", {})
        if by_bucket:
            bucket_rows = []
            for bucket, m in by_bucket.items():
                bucket_rows.append({
                    "Bucket": bucket,
                    "mean IC": f"{m.get('mean_ic', 0):.4f}",
                    "IC IR": f"{m.get('ic_ir', 0):.3f}",
                    "t-stat": f"{m.get('t_stat', 0):.3f}",
                    "p-value": f"{m.get('p_value', 1):.4f}",
                    "n": m.get("n", 0),
                })
            st.dataframe(pd.DataFrame(bucket_rows), use_container_width=True, hide_index=True)
        else:
            st.info("無 by_bucket 資料")

    st.markdown("---")
    st.markdown("**月度 IC 時間序列**")

    period_ics = ic.get("period_ics", [])
    df_period = pd.DataFrame()
    if isinstance(period_ics, list) and period_ics:
        df_raw = pd.DataFrame(period_ics)
        if "rebalance_date" in df_raw.columns and "rank_ic" in df_raw.columns:
            df_period = df_raw[["rebalance_date", "rank_ic"]].copy()
            df_period.columns = ["period", "monthly_ic"]
            df_period["period"] = pd.to_datetime(df_period["period"])
            df_period = df_period.sort_values("period").reset_index(drop=True)

    if not df_period.empty:
        fig_period = go.Figure()
        fig_period.add_trace(
            go.Bar(
                x=df_period["period"],
                y=df_period["monthly_ic"],
                marker=dict(
                    color=[
                        "#27ae60" if ic_val >= 0 else "#c0392b" for ic_val in df_period["monthly_ic"]
                    ]
                ),
                hovertemplate="%{x|%Y-%m}<br>IC: %{y:.4f}<extra></extra>",
            )
        )
        fig_period.add_hline(y=0, line_color="gray")

        mean_ic_val = ic.get("overall", {}).get("mean_ic", 0)
        fig_period.add_hline(
            y=mean_ic_val,
            line_dash="dash",
            line_color="blue",
            annotation_text=f"mean IC = {mean_ic_val:.4f}",
        )

        fig_period.update_layout(
            height=350,
            title=f"{FACTOR_DISPLAY_NAMES.get(selected_factor, selected_factor)} — 月度 Spearman IC",
            xaxis_title="月份",
            yaxis_title="月 IC",
            margin=dict(t=50, b=20),
        )
        st.plotly_chart(fig_period, use_container_width=True)
    else:
        st.info("無 period_ics 資料或 schema 異常")

st.divider()

# ===============================================================
# 9 個因子完整對照（精簡版，移到最後當 reference）
# ===============================================================
st.subheader("📌 9 個因子完整評估方式對照（reference）")

st.caption(
    "本頁主表只列 5 個 Phase A1 因子。下方 reference 列出 repo 內全部 9 個 active 因子的評估管道，"
    "讓你看到「哪些走 single-factor IC、哪些走 spike pipeline、哪些直接走 portfolio sweep」。"
)

ref_table = pd.DataFrame(
    [
        {"因子": "52W 高接近度（high_proximity）", "評估方式": "Single-factor IC pipeline（DSR + FDR + Bootstrap）", "結果在哪看": "本頁主表 + reports/factor_ic/"},
        {"因子": "PEAD / EPS 驚喜（pead_eps）", "評估方式": "Single-factor IC pipeline（DSR + FDR + Bootstrap）", "結果在哪看": "本頁主表 + reports/factor_ic/"},
        {"因子": "月營收動能 v2（revenue_momentum_v2）", "評估方式": "Single-factor IC pipeline（DSR + FDR + Bootstrap）", "結果在哪看": "本頁主表 + reports/factor_ic/"},
        {"因子": "融資 / 融券反向（margin_short_ratio）", "評估方式": "Single-factor IC pipeline（DSR + FDR + Bootstrap）", "結果在哪看": "本頁主表 + reports/factor_ic/"},
        {"因子": "外資 4 子訊號（foreign_broker_v2）", "評估方式": "Single-factor IC pipeline（DSR + FDR + Bootstrap）", "結果在哪看": "本頁主表 + reports/factor_ic/"},
        {"因子": "低波動（low_vol_v2）", "評估方式": "Spike pipeline（IC + DSR + turnover）", "結果在哪看": "reports/phase_b0_lite/spike_results.json"},
        {"因子": "QMJ profitability（quality_v3）", "評估方式": "Portfolio sweep（嵌入 D-E 三因子 composite，不單獨測 IC）", "結果在哪看": "「18 種策略最終 sweep」頁"},
        {"因子": "產業動能（industry_momentum）", "評估方式": "Portfolio sweep（嵌入 D-F 三因子 composite，不單獨測 IC）", "結果在哪看": "「18 種策略最終 sweep」頁"},
        {"因子": "特異波動（idio_vol_max）", "評估方式": "Portfolio sweep（嵌入 D-G 三因子 composite，不單獨測 IC）", "結果在哪看": "「18 種策略最終 sweep」頁"},
    ]
)
st.dataframe(ref_table, use_container_width=True, hide_index=True)

st.caption(
    "**為什麼 quality_v3 / industry_momentum / idio_vol_max 沒跑 single-factor IC**："
    "這 3 個因子設計上是「為 52W + PEAD anchor 加互補訊號」，**不打算單獨用**——"
    "個別 IC 即使顯著，也不代表組合進 portfolio 後仍 robust（D1_v2 案例已示範 IS IR 0.92 → OOS 0.0058 collapse）。"
    "因此跳過 single-factor IC，直接走 portfolio-level cell sweep（即「18 種策略最終 sweep」頁），"
    "更貼近實戰。"
    "\n\n"
    "**為什麼 low_vol_v2 走 spike 而不是 production**："
    "spike = 1-2 天快速驗假設（IC + DSR + turnover 基本）；production = Pro methodology 完整 layer。"
    "low_vol_v2 spike IC=0.0584 ✅ 但 DSR Ψ=0 ❌ + turnover 37.5%（過 H_lite gate）→ 直接 reject 不晉升 production。"
)
