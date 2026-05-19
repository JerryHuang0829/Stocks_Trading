"""P3 — 策略驗證 + 工程亮點（4 tabs → 3 tabs，Tab A+B 合併為「策略結果」）。

頁面結構：
1. 頁首 3 hero metric（IR collapse / 0 過關 / 雙重否定）
2. Tab A — 策略結果（合併原雙因子 IR collapse + 18-cell sweep）
3. Tab B — Bootstrap CI 雙重否定（原 Tab C）
4. Tab C — 工程亮點（原 Tab D，含 silent bug Sharpe 對比表 + code diff 單欄）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import (  # noqa: E402
    STRATEGY_FACTORS,
    STRATEGY_LOGIC,
    STRATEGY_NAMES,
    gate_pass_count,
    is_gate_passed,
    load_a11_l6_ci_comparison_md,
    load_bootstrap_ci_lowers,
    load_cell_summary,
    load_d1v2_daily_returns,
    load_d1v2_metrics,
    load_d1v2_snapshots,
    strategy_label,
)

st.set_page_config(
    page_title="策略驗證 + 工程亮點",
    page_icon="🎯",
    layout="wide",
)

st.title("🎯 策略驗證 + 工程亮點")

# ===============================================================
# 3 個 tab（原 4 個 → 3 個，Tab A+B 合併）
# ===============================================================
tab_strategy, tab_ci, tab_eng = st.tabs([
    "📊 策略結果（雙因子 + 18-cell sweep）",
    "📐 Bootstrap CI 雙重否定",
    "🛠️ 工程亮點",
])

# ===============================================================
# Tab A — 策略結果（合併原 Tab A + Tab B）
# ===============================================================
with tab_strategy:

    # ─────────────────────────────────────────
    # 階段二：雙因子策略 IR collapse
    # ─────────────────────────────────────────
    st.markdown("### 階段二：雙因子策略（52W 50% + PEAD 50%）— IR collapse 揭示")

    st.warning(
        """
📌 **為什麼先測雙因子，再做 18 種策略 sweep？**

**起因**：5 個學術因子單獨 IC 驗證後（見「因子驗證」頁），**只有 52W 高接近度 + PEAD/EPS 2 個過嚴格門檻**。
自然就先把這 2 個強因子等權組起來測——這是**雙因子策略**。

**轉折**：雙因子 IS 60 月看似 Sharpe 1.53 / IR 0.92 強到不真實，但 **OOS IR 0.0058 = 99.4% collapse**。
揭示「**2 個 IS 強因子綁定測試容易 over-fit lucky run**」——沒做「加入第三個 variable factor 是否帶來增益」的 ablation。

**升級到 3 因子組合**：推動進入階段三——共用 52W + PEAD 當基底，
**加 1 個變動的第三因子**（融資 / 品質 / 產業動量 / 特質波動）+ 3 種持股數 = **18 種組合**。
看哪個 3-factor variant 能帶來真實 OOS robustness。

**為什麼 D-A 預先 disqualify**：D-A 候選（純 52W + PEAD 50/50）= 階段二的雙因子策略本人。
OOS 已 collapse 99.4%，**沒理由再測一次**，直接從 18 個 cell 中排除。
"""
    )

    metrics_is = load_d1v2_metrics("is")
    metrics_oos = load_d1v2_metrics("oos")
    daily_is = load_d1v2_daily_returns("is")
    daily_oos = load_d1v2_daily_returns("oos")
    snapshots_is = load_d1v2_snapshots("is")

    if not all([metrics_is, metrics_oos, daily_is, daily_oos, snapshots_is]):
        st.error("讀不到雙因子策略 IS / OOS backtest data。")
    else:
        # 累積報酬時序
        st.markdown("##### 📈 累積報酬時序（IS 2020-2024 + OOS 2025）")

        def _build_cum_df(daily: dict) -> pd.DataFrame:
            portfolio = daily.get("portfolio", {})
            benchmark = daily.get("benchmark", {})
            rows = [{"date": d, "portfolio": r} for d, r in sorted(portfolio.items())]
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            bench_df = pd.DataFrame(
                [{"date": d, "benchmark": r} for d, r in sorted(benchmark.items())]
            )
            bench_df["date"] = pd.to_datetime(bench_df["date"])
            df = df.merge(bench_df, on="date", how="left")
            df["benchmark"] = df["benchmark"].fillna(0)
            df["portfolio_cum"] = (1 + df["portfolio"]).cumprod() - 1
            df["benchmark_cum"] = (1 + df["benchmark"]).cumprod() - 1
            return df

        df_is = _build_cum_df(daily_is)
        df_oos = _build_cum_df(daily_oos)

        df_oos_cont = df_oos.copy()
        last_is_p = df_is["portfolio_cum"].iloc[-1] if len(df_is) else 0
        last_is_b = df_is["benchmark_cum"].iloc[-1] if len(df_is) else 0
        df_oos_cont["portfolio_cum"] = (1 + df_oos_cont["portfolio"]).cumprod() * (1 + last_is_p) - 1
        df_oos_cont["benchmark_cum"] = (1 + df_oos_cont["benchmark"]).cumprod() * (1 + last_is_b) - 1

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_is["date"], y=df_is["portfolio_cum"],
            mode="lines", line=dict(color="#3498db", width=2),
            name="雙因子策略 (IS)",
            hovertemplate="%{x|%Y-%m-%d}<br>累積報酬：%{y:.2%}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=df_is["date"], y=df_is["benchmark_cum"],
            mode="lines", line=dict(color="#95a5a6", width=2, dash="dot"),
            name="0050 (IS)",
            hovertemplate="%{x|%Y-%m-%d}<br>累積報酬：%{y:.2%}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=df_oos_cont["date"], y=df_oos_cont["portfolio_cum"],
            mode="lines", line=dict(color="#e74c3c", width=2),
            name="雙因子策略 (OOS 2025)",
            hovertemplate="%{x|%Y-%m-%d}<br>累積報酬：%{y:.2%}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=df_oos_cont["date"], y=df_oos_cont["benchmark_cum"],
            mode="lines", line=dict(color="#7f8c8d", width=2, dash="dot"),
            name="0050 (OOS 2025)",
            hovertemplate="%{x|%Y-%m-%d}<br>累積報酬：%{y:.2%}<extra></extra>",
        ))
        if len(df_oos) > 0:
            divider = str(df_oos["date"].iloc[0])[:10]
            fig.add_shape(type="line", x0=divider, x1=divider, y0=0, y1=1, yref="paper",
                          line=dict(color="orange", width=2, dash="dash"))
            fig.add_annotation(x=divider, y=1, yref="paper", text="IS / OOS 分界",
                               showarrow=False, bgcolor="rgba(243,156,18,0.7)", font=dict(color="white"))
        fig.update_layout(
            height=400,
            title="累積報酬：雙因子策略 vs 0050（含配息）",
            xaxis_title="日期", yaxis_title="累積報酬", yaxis_tickformat=".0%",
            hovermode="x unified", margin=dict(t=50, b=20),
        )
        st.plotly_chart(fig, width="stretch")

        st.warning(
            """
📖 **怎麼看這張圖（很關鍵 — 不要被絕對值騙了）**

雙因子策略（藍 / 紅線）看起來一直**高於** 0050（灰虛線），會誤以為「策略一直贏」。
**但 alpha 要看「斜率」（period growth），不是「絕對位置」**：

| 期間 | 雙因子 growth | 0050 growth | Alpha |
|---|---|---|---|
| **IS（2020-2024，5 年）** | +400% | +120% | 策略 5 年**大贏 280pp** ✅ |
| **OOS（2025，1 年）** | **+33.6%**（從 5.3x → 7.1x）| **+33.5%**（從 2.4x → 3.2x）| 策略 OOS **持平大盤（+0.1pp，落入 noise）** ⚠️ |

**OOS 紅線跟灰虛線斜率變一致** = 沒有持續拉開差距 = **alpha 已 collapse**。
紅線位置高只是 IS 期間積累的歷史優勢、不是 OOS 持續創造的 alpha。
**這就是 IR 從 0.9238 → 0.0058（collapse 99.4%）的視覺呈現**。
"""
        )

        # 12 指標表
        st.markdown("##### 📊 12 個指標 IS vs OOS 對照")
        st.caption(
            "📌 **表中紅色高亮的 3 行（Alpha / IR / Calmar）是 over-fitting 證據**——"
            "純 alpha 在 OOS 完全消失。其他 metric 如 Sharpe / Total Return 還在，"
            "是因為 OOS 2025 大盤自己漲 37%，**被 beta × 大盤蓋過、不能當策略沒崩的依據**。"
        )

        def _fmt(v, fmt=".3f"):
            if v is None:
                return "N/A"
            try:
                return format(v, fmt)
            except (ValueError, TypeError):
                return str(v)

        def _fmt_pct(v):
            if v is None:
                return "N/A"
            try:
                return f"{v * 100:.2f}%"
            except (ValueError, TypeError):
                return str(v)

        rows = [
            ("Total Return（總報酬）", _fmt_pct(metrics_is.get("total_return")), _fmt_pct(metrics_oos.get("total_return"))),
            ("Annualized Return（年化報酬）", _fmt_pct(metrics_is.get("annualized_return")), _fmt_pct(metrics_oos.get("annualized_return"))),
            ("Annualized Volatility（年化波動率）", _fmt_pct(metrics_is.get("annualized_volatility")), _fmt_pct(metrics_oos.get("annualized_volatility"))),
            ("Sharpe Ratio（夏普值）", _fmt(metrics_is.get("sharpe_ratio")), _fmt(metrics_oos.get("sharpe_ratio"))),
            ("Annualized Alpha（年化超額報酬 α）", _fmt_pct(metrics_is.get("annualized_alpha")), _fmt_pct(metrics_oos.get("annualized_alpha"))),
            ("Beta（系統性風險 β）", _fmt(metrics_is.get("beta")), _fmt(metrics_oos.get("beta"))),
            ("Tracking Error（追蹤誤差）", _fmt_pct(metrics_is.get("tracking_error")), _fmt_pct(metrics_oos.get("tracking_error"))),
            ("Information Ratio（資訊比率 = α 的夏普值）", _fmt(metrics_is.get("information_ratio")), _fmt(metrics_oos.get("information_ratio"))),
            ("Max Drawdown（最大回撤）", _fmt_pct(metrics_is.get("max_drawdown")), _fmt_pct(metrics_oos.get("max_drawdown"))),
            ("Calmar Ratio（年化報酬 / 最大回撤）", _fmt(metrics_is.get("calmar_ratio")), _fmt(metrics_oos.get("calmar_ratio"))),
            ("Total Turnover one-way（總週轉率 - 單邊）", _fmt(metrics_is.get("total_one_way_turnover"), ".2f"), _fmt(metrics_oos.get("total_one_way_turnover"), ".2f")),
            ("N Rebalances（再平衡次數）", str(metrics_is.get("n_rebalances", "N/A")), str(metrics_oos.get("n_rebalances", "N/A"))),
        ]
        df_cmp = pd.DataFrame(rows, columns=["Metric", "IS (2020-2024)", "OOS (2025)"])

        def _hl(row):
            # 標 Alpha / IR / Calmar 三行為 over-fit 證據（紅色高亮）
            metric_str = str(row["Metric"])
            if any(k in metric_str for k in ["Annualized Alpha", "Information Ratio", "Calmar"]):
                return ["background-color: #f8d7da; color: #721c24"] * len(row)
            return [""] * len(row)

        st.dataframe(df_cmp.style.apply(_hl, axis=1), width="stretch", hide_index=True)

        ir_is = metrics_is.get("information_ratio", 0)
        ir_oos = metrics_oos.get("information_ratio", 0)
        st.error(
            f"🚨 **IR collapse 99.4%**：IS IR = {ir_is:.4f} → OOS IR = {ir_oos:.4f}。"
            f"OOS 那 +37% 年化報酬不是策略賺的，是大盤自己漲的——策略從「有 alpha」退化為「高 beta 抱 0050 + 雜訊」。"
        )

    st.divider()

    # ─────────────────────────────────────────
    # 階段三：18 種策略嚴格 sweep
    # ─────────────────────────────────────────
    st.markdown("### 階段三：18 種策略嚴格 sweep — 0 / 18 過 6 / 6 關")
    st.caption(
        "因雙因子策略 OOS collapse，升級為 **6 候選因子組合 × 3 種持股數 = 18 種策略**全跑 + 6 道驗收關卡（第 1-6 關）。"
    )

    # 6 candidates 對照表
    st.markdown("##### 📖 6 個策略候選")
    strategy_table = pd.DataFrame([
        {
            "代號": cid,
            "策略名": STRATEGY_NAMES[cid],
            "因子組成": STRATEGY_FACTORS[cid],
            "策略邏輯": STRATEGY_LOGIC[cid],
        }
        for cid in ["D-B", "D-C", "D-D", "D-E", "D-F", "D-G"]
    ])
    st.dataframe(strategy_table, width="stretch", hide_index=True)

    summary = load_cell_summary()
    if summary is None:
        st.error("讀不到 cell_summary.json")
    else:
        cells = summary.get("cells", [])

        # 6 關詳表（折疊，置於 heatmap 上方）
        with st.expander("📋 6 關完整詳表（先看門檻定義 + 各 cell 過幾關）", expanded=False):
            gate_labels = {
                "L1_ir_ge_0_20": "第 1 關 IR≥0.20",
                "L2_mean_alpha_ge_0_005": "第 2 關 月α≥0.5%",
                "L3_te_in_range": "第 3 關 追蹤誤差∈[10%,30%]",
                "L4_max_dd_diff_le_0_05": "第 4 關 ΔMaxDD≤+5%",
                "L5_a1_active_corr_le_0_50": "第 5 關 與舊版相關性≤0.5",
                "L6_bootstrap_ci_lower_gt_0": "第 6 關 統計顯著（CI下界>0）",
            }
            detail_rows = []
            for c in cells:
                candidate_id = c.get("candidate_id", "?")
                tn = c.get("top_n", "?")
                label = strategy_label(candidate_id, tn) if candidate_id != "?" else f"{candidate_id}|{tn}"
                gates = c.get("gates", {})
                metrics = c.get("metrics", {})
                row = {"策略 | 持股數": label, "代號": f"{candidate_id}|{tn}"}
                for gk, gl in gate_labels.items():
                    row[gl] = "✅" if is_gate_passed(gates, gk) else "❌"
                row["過幾關"] = f"{gate_pass_count(gates)}/6"
                row["IR"] = f"{metrics.get('ir', 0):.3f}"
                row["第 6 關 CI 下界"] = f"{c.get('bootstrap_ci_lower', 0):+.4f}"
                detail_rows.append(row)
            df_cells = pd.DataFrame(detail_rows).sort_values("過幾關", ascending=False).reset_index(drop=True)
            st.dataframe(df_cells, width="stretch", hide_index=True)

        # 6×3 heatmap
        st.markdown("##### 📊 18 策略過關 heatmap（hover 看每 cell 完整 metrics）")
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
                    f"過 {passes}/6 關<br>"
                    f"IR：{metrics.get('ir', 0):.3f}<br>"
                    f"月 α：{metrics.get('mean_alpha_monthly', 0):.4f}<br>"
                    f"第 6 關 CI lower：{ci_low:.4f}<br>"
                    f"全 6 關通過：{'是' if all_pass else '否'}"
                )

        fig_heat = go.Figure(
            data=go.Heatmap(
                z=matrix,
                x=[f"持股 {tn} 檔" for tn in top_ns],
                y=[STRATEGY_NAMES[cid] for cid in candidates_order],
                colorscale=[
                    [0.0, "#c0392b"], [0.33, "#e67e22"], [0.5, "#f39c12"],
                    [0.67, "#f1c40f"], [0.83, "#2ecc71"], [1.0, "#27ae60"],
                ],
                zmin=0, zmax=6,
                text=matrix,
                texttemplate="%{text}/6",
                textfont={"size": 16, "color": "white"},
                hovertext=hover_texts,
                hovertemplate="%{hovertext}<extra></extra>",
                colorbar=dict(title="過幾關 (0-6)", tickvals=list(range(7))),
            )
        )
        fig_heat.update_layout(
            height=440,
            xaxis_title="持股數", yaxis_title="策略候選",
            margin=dict(t=20, b=20, l=180, r=20),
        )
        st.plotly_chart(fig_heat, width="stretch")

        st.error(
            "🚨 **18 / 18 cells 全部過不了第 6 關**（80% bootstrap CI 下界全 ≤ 0）。"
            "最高過 4 / 6 關（D-C\\|12 / D-E\\|12 / D-E\\|16）— **但仍卡在第 6 關 → 紀律性 NO-GO，不允許進 paper trade**。"
            "**→ 完整 NO-GO 結論 + 下一輪 Roadmap 見「結論」頁**。"
        )

# ===============================================================
# Tab B — Bootstrap CI 雙重否定（原 Tab C）
# ===============================================================
with tab_ci:
    st.markdown("### Bootstrap CI 雙重否定 — 為什麼結論可信")

    st.markdown(
        """
**第 6 關**是階段三 6 條驗收門檻中最嚴格的——
**80% Stationary Block Bootstrap (Politis-Romano 1994) CI 下界 > 0**。

用 60 個月超額報酬重抽 10000 次（block_len=3, seed=42），
80% 信心區間下界要嚴格大於 0，才算統計上**真有 alpha**（非運氣）。
"""
    )

    ci_data = load_bootstrap_ci_lowers()
    if ci_data is None:
        st.error("讀不到 cell_bootstrap_ci_lowers.json")
    else:
        ci_lowers = ci_data.get("ci_lowers", {})
        if not ci_lowers:
            st.error("ci_lowers 為空")
        else:
            st.markdown("##### 📊 18 cells 第 6 關 80% Bootstrap CI 下界")
            sorted_cells = sorted(ci_lowers.items(), key=lambda x: x[1], reverse=True)

            fig = go.Figure(
                go.Bar(
                    x=[v for _, v in sorted_cells],
                    y=[k for k, _ in sorted_cells],
                    orientation="h",
                    marker=dict(
                        color=[
                            "#27ae60" if v > 0 else ("#f39c12" if v > -0.01 else "#c0392b")
                            for _, v in sorted_cells
                        ],
                    ),
                    text=[f"{v:+.4f}" for _, v in sorted_cells],
                    textposition="outside",
                    hovertemplate="<b>%{y}</b><br>第 6 關 CI 下界: %{x:.4f}<extra></extra>",
                )
            )
            fig.add_vline(x=0, line_dash="dash", line_color="red", line_width=2,
                          annotation_text="第 6 關門檻 (CI 下界 > 0)",
                          annotation_position="top right")
            fig.update_layout(
                height=560,
                xaxis_title="80% Bootstrap CI lower bound",
                yaxis_title="Cell",
                margin=dict(t=30, b=20, l=80, r=80),
            )
            st.plotly_chart(fig, width="stretch")

            st.error(
                "🚨 **18 / 18 cells 第 6 關 CI 下界 ≤ 0** → 0 cell 過第 6 關 → "
                "無 cell 統計上具顯著 alpha（即便 IS metric IR 高、月 α 高也救不回）。"
            )

            # Methodology
            st.markdown("##### 📚 Methodology — Stationary Block Bootstrap")
            alpha = ci_data.get("L6_alpha", "N/A")
            n_iter = ci_data.get("L6_bootstrap_n", "N/A")
            block_len = ci_data.get("L6_avg_block_len", "N/A")
            seed = ci_data.get("L6_seed", "N/A")

            mc1, mc2, mc3, mc4 = st.columns(4)
            with mc1:
                st.metric("alpha", f"{alpha}")
                st.caption("0.20 = 80% CI")
            with mc2:
                st.metric("n_iter", f"{n_iter:,}" if isinstance(n_iter, int) else str(n_iter))
                st.caption("10000 次重抽")
            with mc3:
                st.metric("block_len", f"{block_len}")
                st.caption("3 month blocks")
            with mc4:
                st.metric("seed", f"{seed}")
                st.caption("42 — reproducibility")

            st.markdown(
                """
**為什麼用 Stationary Block Bootstrap 而非 IID Bootstrap？**

月超額報酬序列**有時序自相關**，普通 IID bootstrap 假設每觀測獨立，會嚴重低估 CI 寬度。
Politis-Romano 1994 的 stationary block bootstrap 以 block 為單位重抽（block_len 隨機長度），
保留 short-term dependence 同時 valid for inference。

**block_len=3 的意義**：60 個 monthly observations × block_len=3 → effective n ≈ 20，
這就是為什麼 80% CI lower 是嚴格門檻——你不能用 60 個觀測偽裝出 60 個獨立資訊。
"""
            )

            st.markdown("##### ⚖️ 80% vs 95% CI — 為何採 80% 非 95%")
            st.markdown(
                """
| CI 設定 | alpha | Lower Bound（雙因子策略 IS 60 月超額） | Verdict |
|---|---|---|---|
| 95% CI（早期標準）| 0.05 | -0.04% | ❌ Fail |
| **80% CI**（retail-realistic）| 0.20 | **+0.66%** | ✅ Pass |

**結論**：80% 是經過 empirical 驗證的「中道標準」——
- 95% 對 60 month sample 過嚴格，連雙因子策略 IS 都過不了
- 80% 對雙因子策略 IS 通過，但對階段三 18 cells **依然全部過不了**
- **證明本 sweep 的 NO-GO 不是「標準太嚴」造成，是真的沒 edge**
"""
            )

            st.success(
                """
🎯 **本路徑結論**：80% CI 已是 retail-realistic 中道（vs 95% 嚴格）+ block bootstrap 是時序資料的正確方法
+ 18 / 18 cells lower bound ≤ 0 → **任何降標都改變不了「無顯著 alpha」的事實**。
降標讓 D-C\\|12 / D-E\\|12 / D-E\\|16（4 / 6）進 paper trade = silent_bug pattern。
"""
            )

# ===============================================================
# Tab C — 工程亮點（原 Tab D，加 silent bug Sharpe 對比表）
# ===============================================================
with tab_eng:
    st.markdown("### 🛠️ 工程亮點 — 為何自做 PIT engine 不用 Backtrader / Finlab")

    # ===========================================
    # Per-row PIT lag 對照表
    # ===========================================
    st.markdown("#### 🎯 真正的難點：台股 PIT 不是「截到 as_of」這麼簡單")

    st.markdown(
        """
> 「台股 PIT 只認**事件日**、不認**公開日**——財報、月營收、融資券各有自己的法定公告 lag，
> 而且 **EPS 的 lag 還會隨每季改變**（Q4 要 90 天到隔年 3/31、Q1-Q3 各 45 天），
> 同一份 DataFrame 裡不同列要套不同 lag。」
"""
    )

    pit_lag_table = pd.DataFrame([
        {
            "資料類型": "📊 EPS（季度損益表）",
            "事件日": "季結算日",
            "法定公告 lag": "**Q4 = 90 天**（年報 3/31）/ Q1-Q3 = 45 天",
            "PIT 重點": "**per-row lag 不同**：同一支股票不同季要套不同 lag",
            "Backtrader / Finlab 處理？": "❌ 不會",
        },
        {
            "資料類型": "📈 月營收",
            "事件日": "月底",
            "法定公告 lag": "次月 10 日 + 5 天 buffer = **45 天**",
            "PIT 重點": "month-end → 公告日 cutoff 嚴格",
            "Backtrader / Finlab 處理？": "❌ 不會",
        },
        {
            "資料類型": "💰 融資 / 融券餘額",
            "事件日": "交易日",
            "法定公告 lag": "TWSE 盤後 T+1 + 1 天 buffer = **2 天**",
            "PIT 重點": "日資料但仍需 T+1 cutoff",
            "Backtrader / Finlab 處理？": "⚠️ 部分",
        },
        {
            "資料類型": "🏦 三大法人買賣超",
            "事件日": "交易日",
            "法定公告 lag": "TWSE 17:00 + T+1 buffer = **2 天**",
            "PIT 重點": "盤後資料，T+1 才可用",
            "Backtrader / Finlab 處理？": "⚠️ 部分",
        },
        {
            "資料類型": "🏢 資產負債表",
            "事件日": "季結算日",
            "法定公告 lag": "**60 天**（公告慣例晚 IS 數天到 2 週）",
            "PIT 重點": "與 EPS 取 max(income_lag, balance_lag)",
            "Backtrader / Finlab 處理？": "❌ 不會",
        },
    ])
    st.dataframe(pit_lag_table, width="stretch", hide_index=True)

    st.info(
        "💡 **這層框架不會幫你做**。加上這專案真正難的是台股資料層（FinMind、爬蟲、cache、"
        "下市股 universe 重建、split / 股息還原），不是回測迴圈。所以**自做、在資料截斷那層加 "
        "mutation test 守 forward-leak** 是有意識的選擇。"
    )

    st.markdown(
        """
**自做 PIT engine 的核心設計**：

```python
# src/backtest/engine.py 的 _DataSlicer
class _DataSlicer:
    def slice_for(self, as_of: pd.Timestamp, data_type: str) -> pd.DataFrame:
        if data_type == "eps":
            # per-quarter lag: Q4 = 90d, Q1-3 = 45d
            return self._eps_with_quarter_aware_lag(as_of)
        elif data_type == "monthly_revenue":
            return self.data[self.data["pub_date"] <= as_of - pd.Timedelta(days=45)]
        elif data_type == "margin":
            return self.data[self.data["date"] <= as_of - pd.Timedelta(days=2)]
        # ... 5 種資料每種獨立 cutoff 邏輯
```

**團隊有完善歷史資料層 + per-row PIT lag 處理好的情況下**，會評估使用 Backtrader 或 Finlab。
"""
    )

    st.divider()

    # ===========================================
    # 5 輪獨立 audit 紀律 timeline
    # ===========================================
    st.markdown("#### 🔍 5 輪獨立 audit 紀律")

    st.markdown(
        "**結果太好先懷疑 bug**——這是本輪 silent bug 經驗後的反思。"
        "每跑完一輪就跑 audit cycle，把所有可能的 silent bug pattern 重新檢視一遍。"
    )

    audit_data = [
        ("Round 1", "2026-05-08", "PIT contamination / 量綱錯誤", "8 類 silent bug"),
        ("Round 2", "2026-05-09", "PIT helper fallback / pit_violation overwrite", "4 P0/P1"),
        ("Round 3", "2026-05-10", "issued_capital fallback / margin_short sign", "5 finding"),
        ("Round 4", "2026-05-11", "4-path PIT helper sweep", "架構債清理"),
        ("Round 5", "2026-05-11", "thresholds.py default 權重 silent drift", "6 finding（2 真 silent bug 已修）"),
        ("Round 6", "2026-05-19", "Codex DSR audit 獨立驗證", "P0 REJECT (formula 正確) / 2 P1 CONFIRM"),
    ]

    fig_audit = go.Figure()
    for i, (label, date, what, finding) in enumerate(audit_data):
        fig_audit.add_trace(go.Scatter(
            x=[date], y=[0],
            mode="markers+text",
            marker=dict(size=24, color="#3498db", line=dict(color="white", width=2)),
            text=[label],
            textposition="top center",
            hovertemplate=f"<b>{label} ({date})</b><br>檢查項：{what}<br>發現：{finding}<extra></extra>",
            showlegend=False,
        ))
        fig_audit.add_annotation(
            x=date, y=-0.6,
            text=f"<b>{what}</b><br><span style='font-size:0.85em'>{finding}</span>",
            showarrow=False, font=dict(size=10), align="center",
        )
    fig_audit.add_trace(go.Scatter(
        x=[d[1] for d in audit_data], y=[0] * len(audit_data),
        mode="lines",
        line=dict(color="#bdc3c7", width=2, dash="dot"),
        showlegend=False, hoverinfo="skip",
    ))
    fig_audit.update_layout(
        height=240, margin=dict(t=20, b=20, l=20, r=20),
        xaxis=dict(type="date", showgrid=False, zeroline=False, showline=False, tickformat="%m-%d"),
        yaxis=dict(range=[-1.2, 0.8], showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_audit, width="stretch", config={"displayModeBar": False})

    st.caption(
        "📌 6 輪 audit cycle 共抓到 25+ 項 P0/P1 finding，**全部當下修完並補對應 mutation test**。"
        "Round 6（codex DSR audit）獨立驗證確認 DSR 公式正確、reject codex P0 misread；"
        "NO-GO 結論在 6 輪後仍然成立 → 結論 robust。"
    )

    st.divider()

    # ===========================================
    # Silent bug — Sharpe 對比表 + code diff 單欄
    # ===========================================
    st.markdown("#### 🐛 兩個 silent bug — 數字對比 + code diff")

    st.markdown(
        "**例行檢查抓到 2 個 silent bug**，揭穿過去 4 年 Sharpe 1.73 / α +39% 全部是 overfit。"
        "這是「結果太好先懷疑 bug」紀律的真實案例。"
    )

    # Sharpe 對比表（從原 P1 搬過來）
    sharpe_col1, sharpe_col2 = st.columns(2)

    with sharpe_col1:
        st.markdown("##### 🐛 修 bug 前（看似漂亮）")
        st.markdown(
            """
| 期間 | Sharpe | 年化 α |
|---|---|---|
| 2022-2025 (4Y) | **1.73** | **+39%** |
| 2025 OOS | **1.88** | **+7.27%** |
| 2024 | — | — |
"""
        )
        st.caption("看似業界水準以上的成績，幾乎達到「準量化 fund」級別。")

    with sharpe_col2:
        st.markdown("##### ✅ 修 bug 後（誠實版）")
        st.markdown(
            """
| 期間 | Sharpe | 年化 α |
|---|---|---|
| 2022-2025 (4Y) | **0.64** | **+3.4%** |
| 2025 OOS | **0.66** | **-18.4%** |
| 2024 | **0.33** | **-43.2%** |
"""
        )
        st.caption("alpha 縮水 91%，2024 單年甚至大幅 underperform 大盤。")

    st.error(
        "🚨 **結論：先前 alpha 主要來自 look-ahead bias + universe contamination**。"
        "這次經驗讓我體會到**系統架構正確性比好看的 edge 更重要**——後續所有研究改用 mutation test 守 forward-leak、預先鎖定 hard gates、OOS 只測一次。"
    )

    # Code diff 單欄（合併 Bug 1 + Bug 2 為 sequential）
    st.markdown("##### 🐞 Bug 1：`finmind.py` pandas 2.x timezone error")
    st.markdown("**修前**（pandas 2.x 會 raise）：")
    st.code(
        """# src/data/finmind.py
want_start = pd.Timestamp(date_obj, tz="UTC")
# pandas 2.x: ValueError when date_obj already tz-aware""",
        language="python",
    )
    st.markdown("**修後**：")
    st.code(
        """# src/data/finmind.py
want_start = pd.Timestamp(date_obj)
if want_start.tz is None:
    want_start = want_start.tz_localize("UTC")
else:
    want_start = want_start.tz_convert("UTC")""",
        language="python",
    )
    st.caption(
        "**影響**：歷史回測 silent 失敗 fallback 到 stale cache，但 test 沒覆蓋這個 path → 抓到後補 4 個 PIT mutation test 守 forward-leak。"
    )

    st.markdown("---")

    st.markdown("##### 🐞 Bug 2：universe pre-filter 用 STOCK_DAY_ALL 對歷史日期回空")
    st.markdown("**修前**：")
    st.code(
        """# src/portfolio/tw_stock.py
turnover_df = fetch_twse_daily_all(date)
# STOCK_DAY_ALL 是「當日」全市場 snapshot
# 對歷史日期永遠回空 → universe 變全市場 2000 支""",
        language="python",
    )
    st.markdown("**修後**：")
    st.code(
        """# src/portfolio/tw_stock.py
turnover_df = self._build_turnover_from_ohlcv_cache(
    as_of_date=date, lookback_days=63
)
# 改用 OHLCV cache 算歷史 turnover
# universe 退回 top-80 close × volume""",
        language="python",
    )
    st.caption(
        "**影響**：universe 退化為全市場 2000 支 → alpha 部分來自高 turnover noise → 修後抓出舊 alpha 為 universe contamination artifact。"
    )

    st.info(
        "💡 **兩個 bug 都不是 try / except 漏接、不是 numerical 邊界，而是『程式跑了、沒報錯、結果錯了』的 silent bug**。"
        "這正是「驗證紀律比 edge 重要」最具體的體現——**靠 mutation test + 預先鎖定 hard gates 才抓得到**，"
        "不是靠 try / except 或 logger.error。"
    )
