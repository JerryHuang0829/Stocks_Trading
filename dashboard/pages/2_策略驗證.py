"""P3 — 策略驗證 + 工程亮點（4 tabs → 3 tabs，Tab A+B 合併為「策略結果」）。

頁面結構：
1. Tab A — 策略結果（雙因子 IR collapse + 18-cell sweep）
2. Tab B — Bootstrap CI 雙重否定
3. Tab C — 工程亮點（PIT engine）
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

        with st.expander("📖 怎麼看這張圖", expanded=False):
            st.markdown(
                """
雙因子策略（藍 / 紅線）看起來一直**高於** 0050（灰虛線），會誤以為「策略一直贏」。
**但 alpha 要看「斜率」（period growth），不是「絕對位置」**：

| 期間 | 雙因子 growth | 0050 growth | Alpha |
|---|---|---|---|
| **IS（2020-2024，5 年）** | +431% | +137% | 策略 5 年**大贏 294pp** ✅ |
| **OOS（2025，1 年）** | **+33.6%**（從 5.3x → 7.1x）| **+33.5%**（從 2.4x → 3.2x）| 策略 OOS **持平大盤（+0.1pp，落入 noise）** ⚠️ |

**OOS 紅線跟灰虛線斜率變一致** = 沒有持續拉開差距 = **alpha 已 collapse**。
紅線位置高只是 IS 期間積累的歷史優勢、不是 OOS 持續創造的 alpha。
**這就是 IR 從 0.9238 → 0.0058（collapse 99.4%）的視覺呈現**。
"""
            )

        # 12 指標表
        st.markdown("##### 📊 12 個指標 IS vs OOS 對照")
        st.caption(
            "📌 **紅色高亮的 2 行（Alpha / IR）是 over-fitting 證據**——純 alpha 在 OOS 完全消失。"
            "**判讀門檻**：alpha 沒有「> X%」這種固定標準，看 **IR**（alpha 的夏普值）"
            "—— ≥ 0.5 有料、≥ 1 強，本策略 IS 0.92 → OOS 0.006 等於歸零。"
            "**Sharpe** 雖然 ≥ 1 算可以，但它含 beta：OOS 的 1.41 是大盤漲（0050 +33.5%）撐的，"
            "不是 alpha，不能當策略沒崩的依據。"
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
            # 標 Alpha / IR 兩行為 over-fit 證據（紅色高亮）
            metric_str = str(row["Metric"])
            if any(k in metric_str for k in ["Annualized Alpha", "Information Ratio"]):
                return ["background-color: #f8d7da; color: #721c24"] * len(row)
            return [""] * len(row)

        st.dataframe(df_cmp.style.apply(_hl, axis=1), width="stretch", hide_index=True)

        ir_is = metrics_is.get("information_ratio", 0)
        ir_oos = metrics_oos.get("information_ratio", 0)
        st.error(
            f"🚨 **IR collapse 99.4%**：IS IR = {ir_is:.4f} → OOS IR = {ir_oos:.4f}。"
            f"OOS 那 +33.6% 報酬不是策略賺的，是大盤自己漲的——策略從「有 alpha」退化為「高 beta 抱 0050 + 雜訊」。"
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

    st.caption(
        "📌 這 6 個候選用到 6 個因子（52W / PEAD / 融資反向 / 品質 / 產業動量 / 特質波動）"
        "—— 篩自「因子驗證」頁的 8 個（排除月營收 v2 弱 IC、外資 v2 負 IR）。"
        "完整 9 → 8 → 6 因子漏斗見因子驗證頁頂部。"
    )

    summary = load_cell_summary()
    if summary is None:
        st.error("讀不到 cell_summary.json")
    else:
        cells = summary.get("cells", [])

        # 6 關完整詳表（預設展開）
        with st.expander("📋 6 關完整詳表（先看門檻定義 + 各 cell 過幾關）", expanded=True):
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

**為什麼是 3 和 42**：block_len=3 對應月報酬的短期自相關（約一季）；seed=42 只是固定種子讓結果可重現，**值本身不重要**。重點 —— 這兩個連同 alpha / n 都是 L6 規格**事前鎖定**，看到結果不能回頭調（調 block_len 湊 CI = p-hacking）。
"""
            )

            st.markdown("##### ⚖️ 80% vs 95% CI — 為何採 80% 非 95%")
            st.markdown(
                """
| CI 設定 | alpha | Lower Bound（雙因子策略 IS 60 月超額） | Verdict |
|---|---|---|---|
| 95% CI（早期標準）| 0.05 | -0.13% | ❌ Fail |
| **80% CI**（retail-realistic）| 0.20 | **+0.37%** | ✅ Pass |

> 📄 數字來源：`reports/phase_d/A11_l6_ci_comparison.md`（V1.3 canonical，block_len=3 / n=10000 / seed=42 / 10 bps slippage）。

**結論**：80% 是經過 empirical 驗證的「中道標準」——
- 95% 對 60 month sample 過嚴格，連雙因子策略 IS 都過不了
- 80% 對雙因子策略 IS 通過，但對階段三 18 cells **依然全部過不了**
- **證明本 sweep 的 NO-GO 不是「標準太嚴」造成，是真的沒 edge**
"""
            )

# ===============================================================
# Tab C — 工程亮點（PIT engine）
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
            "通用回測框架處理？": "❌ 不會",
        },
        {
            "資料類型": "📈 月營收",
            "事件日": "月底",
            "法定公告 lag": "次月 10 日 + 5 天 buffer = **45 天**",
            "PIT 重點": "month-end → 公告日 cutoff 嚴格",
            "通用回測框架處理？": "❌ 不會",
        },
        {
            "資料類型": "💰 融資 / 融券餘額",
            "事件日": "交易日",
            "法定公告 lag": "TWSE 盤後 T+1 + 1 天 buffer = **2 天**",
            "PIT 重點": "日資料但仍需 T+1 cutoff",
            "通用回測框架處理？": "⚠️ 部分",
        },
        {
            "資料類型": "🏦 三大法人買賣超",
            "事件日": "交易日",
            "法定公告 lag": "TWSE 17:00 + T+1 buffer = **2 天**",
            "PIT 重點": "盤後資料，T+1 才可用",
            "通用回測框架處理？": "⚠️ 部分",
        },
        {
            "資料類型": "🏢 資產負債表",
            "事件日": "季結算日",
            "法定公告 lag": "**60 天**（公告慣例晚 IS 數天到 2 週）",
            "PIT 重點": "與 EPS 取 max(income_lag, balance_lag)",
            "通用回測框架處理？": "❌ 不會",
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
