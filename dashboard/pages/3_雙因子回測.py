"""頁 4（user 編號）— 雙因子回測（D1_v2 = 52W 50% + PEAD 50% IS+OOS，IR collapse 揭示）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import (
    load_d1v2_daily_returns,
    load_d1v2_metrics,
    load_d1v2_snapshots,
)

st.set_page_config(
    page_title="雙因子回測",
    page_icon="📉",
    layout="wide",
)

st.title("📉 雙因子回測（52W 高接近度 50% + PEAD 50%）")
st.caption("D1_v2 是專案 internal codename — 兩個因子各 50% 等權的 long-only portfolio。")

# ===============================================================
# 顯眼 caption — Phase A2 vs Phase D 區分（量化主管 Q2 修法）
# ===============================================================
st.warning(
    "📌 **本頁 D1_v2 = Phase A2 2-factor composite，非 Phase D v7 結論策略**。\n\n"
    "Phase A2 是 Phase D v7 的前一階段——D1_v2 IS 看似 Sharpe 1.53 / IR 0.92 表現好，"
    "但 OOS IR 0.0058（99.4% collapse）→ 推動我們進到 Phase D 嚴格 18-cell 驗證。"
    "**Phase D 結論見「18 種策略最終 sweep」頁**。"
)

st.divider()

# ===============================================================
# 為什麼挑 52W + PEAD 這兩個（簡潔版，詳細看 Page 3「因子IC測試」）
# ===============================================================
with st.expander("📖 為什麼挑「52W + PEAD」這兩個組合？（先看再看下方回測）", expanded=True):
    st.markdown(
        """
**先看 5 因子個別 IC 排名**（Phase A1 pre-registered 個別檢驗，2026-04；這是設計 D1_v2 雙因子策略時用的因子池）：

| 排名 | 因子 | mean IC | IC IR | p-value | verdict |
|---|---|---|---|---|---|
| 1 | **52W 高接近度 (high_proximity)** | **0.0413** | 0.274 | 0.024 | 🟢 Good |
| 2 | **PEAD/EPS 驚喜 (pead_eps)** | 0.0219 | **0.291** | **0.017** | 🟡 Normal |
| 3 | 融資/融券反向 (margin_short_ratio) | 0.0388 | 0.232 | 0.055 | 🟡 Normal（**邊界**）|
| 4 | 月營收動能 v2 (revenue_momentum_v2) | 0.0145 | 0.191 | 0.113 | 🔴 Fail |
| 5 | 外資法人因子 v2 (foreign_investor_v2) | **-0.0077** | -0.084 | **0.501** | 🔴 Fail（**不顯著**，2026-05-10 R28 PIT 修法後重跑：舊 -0.0195 含 PIT contamination + 量綱錯誤 artifact）|

> 📌 **補充（2026-05-11）**：另把 Phase D 3 個因子（quality_v3 / industry_momentum / idio_vol_max）也跑了 single-factor IC（之前只在 18-cell sweep 裡作 composite 子訊號）。結果 **idio_vol_max mean IC = +0.0588（p=0.0077）是 8 個因子裡最強的 single IC**（比 52W +0.0413 還高）；quality_v3 / industry_momentum 弱負（-0.0093 / -0.0120）。完整 8 因子主表見「因子IC測試」頁。**注意**：D1_v2 是 2026-04 用上面 5 因子池設計的，當時 idio_vol_max 還沒測 stand-alone IC；single IC 強 ≠ 組合進 portfolio 仍 robust（D1_v2 本身就是反例：IS IR 0.92 → OOS 0.0058）。

**選 52W + PEAD 的 4 個理由**（不只看 mean IC）：

1. **IC IR 最高的兩個**（5 因子池內）：PEAD 0.291 / 52W 0.274（signal-to-noise 最好）
2. **p-value 最低的兩個**：PEAD 0.017 / 52W 0.024（統計證據最強）
3. **學術文獻分量重**：George-Hwang (2004) + Bernard-Thomas (1989），皆是 40 年驗證的經典 anomaly
4. **因子互補性**：價格動量（52W）+ 基本面驚喜（PEAD）來源不同 → 組合有 diversification

**為什麼沒選 mean IC 第 3 名的「融資反向」**？
- IC IR 0.232 比 PEAD 0.291 弱
- p=0.055 **剛剛沒過 0.05**（過嚴格門檻）
- 融資反向跟 52W 都偏「短期 retail 行為」 → 可能高度相關 → 組合 redundancy 高

→ **52W + PEAD 不只是「分數最高」，是綜合 統計強度 + 學術背景 + 互補性 的最佳組合**。

完整 5 因子 IC 結果見「**因子IC測試**」頁。
"""
    )

st.divider()

# ===============================================================
# Cost Assumptions（CRO Q3 + Retail Q2 修法）
# ===============================================================
with st.expander("💰 Cost Assumptions（點擊展開）", expanded=False):
    st.markdown(
        """
- **單邊交易成本**：turnover_cost = 0.0047（含手續費 0.1425% × 2 + 滑點）
- **滑點**：slippage_bps = 10
- **證交稅**：0.3%（賣方）
- **基準**：0050 ETF，**含配息（dividend total return）**
- **再平衡頻率**：月頻（每月第 2 週前後）
- **持股數**：top_n=8
- **執行模式**：drift-aware（buy-and-hold within period，未投資部位 0% return）
"""
    )

# ===============================================================
# Load data
# ===============================================================
metrics_is = load_d1v2_metrics("is")
metrics_oos = load_d1v2_metrics("oos")
daily_is = load_d1v2_daily_returns("is")
daily_oos = load_d1v2_daily_returns("oos")
snapshots_is = load_d1v2_snapshots("is")

if not all([metrics_is, metrics_oos, daily_is, daily_oos, snapshots_is]):
    st.error("讀不到 D1_v2 IS/OOS backtest data。")
    st.stop()

# ===============================================================
# Cumulative return 時序
# ===============================================================
st.subheader("📈 累積報酬時序（IS 2020-2024 + OOS 2025）")


def _build_cum_df(daily: dict, label: str) -> pd.DataFrame:
    portfolio = daily.get("portfolio", {})
    benchmark = daily.get("benchmark", {})
    rows = []
    for date, ret in sorted(portfolio.items()):
        rows.append({"date": date, "portfolio": ret, "label": label})
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


df_is_cum = _build_cum_df(daily_is, "IS")
df_oos_cum = _build_cum_df(daily_oos, "OOS")

# Continuous cumulative across IS + OOS（OOS 接續 IS 末值）
df_oos_cum_cont = df_oos_cum.copy()
last_is_p = df_is_cum["portfolio_cum"].iloc[-1] if len(df_is_cum) else 0
last_is_b = df_is_cum["benchmark_cum"].iloc[-1] if len(df_is_cum) else 0
df_oos_cum_cont["portfolio_cum"] = (1 + df_oos_cum_cont["portfolio"]).cumprod() * (1 + last_is_p) - 1
df_oos_cum_cont["benchmark_cum"] = (1 + df_oos_cum_cont["benchmark"]).cumprod() * (1 + last_is_b) - 1

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=df_is_cum["date"], y=df_is_cum["portfolio_cum"],
    mode="lines", line=dict(color="#3498db", width=2),
    name="D1_v2 (IS)", legendgroup="port",
    hovertemplate="%{x|%Y-%m-%d}<br>累積報酬： %{y:.2%}<extra></extra>",
))
fig.add_trace(go.Scatter(
    x=df_is_cum["date"], y=df_is_cum["benchmark_cum"],
    mode="lines", line=dict(color="#95a5a6", width=2, dash="dot"),
    name="0050 (IS)", legendgroup="bench",
    hovertemplate="%{x|%Y-%m-%d}<br>累積報酬： %{y:.2%}<extra></extra>",
))
fig.add_trace(go.Scatter(
    x=df_oos_cum_cont["date"], y=df_oos_cum_cont["portfolio_cum"],
    mode="lines", line=dict(color="#e74c3c", width=2),
    name="D1_v2 (OOS 2025)", legendgroup="port",
    hovertemplate="%{x|%Y-%m-%d}<br>累積報酬： %{y:.2%}<extra></extra>",
))
fig.add_trace(go.Scatter(
    x=df_oos_cum_cont["date"], y=df_oos_cum_cont["benchmark_cum"],
    mode="lines", line=dict(color="#7f8c8d", width=2, dash="dot"),
    name="0050 (OOS 2025)", legendgroup="bench",
    hovertemplate="%{x|%Y-%m-%d}<br>累積報酬： %{y:.2%}<extra></extra>",
))

# IS / OOS divider — 用字串避免 Timestamp 算術問題
if len(df_oos_cum) > 0:
    is_oos_divider = str(df_oos_cum["date"].iloc[0])[:10]
    fig.add_shape(
        type="line",
        x0=is_oos_divider, x1=is_oos_divider,
        y0=0, y1=1, yref="paper",
        line=dict(color="orange", width=2, dash="dash"),
    )
    fig.add_annotation(
        x=is_oos_divider, y=1, yref="paper",
        text="IS / OOS 分界", showarrow=False,
        bgcolor="rgba(243,156,18,0.7)", font=dict(color="white"),
    )
# Risk events 註解（CRO Q1 修法）
fig.add_shape(
    type="rect", x0="2020-02-15", x1="2020-04-30",
    y0=0, y1=1, yref="paper",
    fillcolor="rgba(231,76,60,0.1)", line_width=0, layer="below",
)
fig.add_annotation(
    x="2020-03-22", y=0.95, yref="paper",
    text="covid 急跌", showarrow=False,
    font=dict(color="#c0392b", size=10),
)
fig.add_shape(
    type="rect", x0="2022-01-01", x1="2022-12-31",
    y0=0, y1=1, yref="paper",
    fillcolor="rgba(241,196,15,0.1)", line_width=0, layer="below",
)
fig.add_annotation(
    x="2022-06-30", y=0.95, yref="paper",
    text="升息週期", showarrow=False,
    font=dict(color="#d68910", size=10),
)
fig.update_layout(
    height=450,
    title="累積報酬：D1_v2 vs 0050（含配息）",
    xaxis_title="日期",
    yaxis_title="累積報酬",
    yaxis_tickformat=".0%",
    hovermode="x unified",
    margin=dict(t=50, b=20),
)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# ===============================================================
# IS vs OOS metrics 對照表（黃色高亮 IR）
# ===============================================================
st.subheader("📊 IS vs OOS metrics 對照（IR collapse 揭示）")

# ---------------------------------------------------------------
# 12 個指標讀法 expander（折疊版，給想看完整定義的讀者）
# ---------------------------------------------------------------
with st.expander("📖 12 個指標讀法（看到 IR collapse 別誤判 Sharpe 沒崩）", expanded=False):
    st.markdown(
        """
### 一、報酬類（**單看會誤導**）

| 指標 | 直譯 | 解讀重點 |
|---|---|---|
| **Total Return** | 累積總報酬 | IS 5 年累積、OOS 1 年累積，**直接比沒意義**（要看年化） |
| **Annualized Return** | 年化報酬率 | 兩期 ≈ 40% 看似都很強——**這正是陷阱**，要看 alpha 才知道是不是策略賺的 |

---

### 二、風險類

| 指標 | 直譯 | 解讀重點 |
|---|---|---|
| **Annualized Volatility** | 年化波動率（每年報酬上下浮動 std） | 兩期差不多 → 風險水準穩定 |
| **Max Drawdown** | 最大回撤（歷史最壞期跌多深） | 兩期差不多 → OOS 沒風險爆掉 |

---

### 三、風險調整類（reward / risk）

| 指標 | 直譯 | 解讀重點 |
|---|---|---|
| **Sharpe Ratio** | （年化報酬 − 無風險率） / 年化波動率。每承擔 1 單位風險賺幾倍 | > 1 算強，IS 跟 OOS 都很強——但這仍是陷阱 |
| **Calmar Ratio** | 年化報酬 / 最大回撤絕對值 | 也很穩，看不出 alpha 崩 |

⚠️ **這兩行讓人誤以為「策略沒崩」**——絕對視角看真沒崩，但**重點在下面四行**。

---

### 四、相對 0050 benchmark（**alpha 真相**）

| 指標 | 直譯 | 解讀重點 |
|---|---|---|
| **Annualized Alpha** | 年化「超額」報酬（扣掉 beta × 0050 報酬後剩多少） | **IS +21.9%／年 → OOS +0.13%／年**，alpha 完全消失 |
| **Beta** | 系統性風險（0050 漲 1% 你大約漲幾 %） | 兩期 ≈ 0.5 → 策略**一半是抱 0050、一半是 active** |
| **Tracking Error** | 跟 0050 偏離程度（active return std）| 越大 = 越「主動」管理 |
| ⭐ **Information Ratio** | = 年化 alpha / tracking error | **IS 0.92 → OOS 0.006，崩 99.4%** ← IR collapse 核心 |

---

### 五、交易類

| 指標 | 直譯 | 解讀重點 |
|---|---|---|
| **Total Turnover (one-way)** | 累積單邊週轉率 | 月頻策略每月換 ~40% 持倉合理 |
| **N Rebalances** | 再平衡次數 | IS 60 個月、OOS 12 個月，確認跑滿月份 |

---

### 🚨 為什麼 IR 是「核心崩盤指標」

```
IS 期間（2020-2024）              OOS 期間（2025）
0050 自己：  年化 ~20%             0050 自己：  年化 ~37%   ← 大盤仍漲
D1_v2:      年化 ~42%             D1_v2:      年化 ~37%
策略 alpha: +22%／年              策略 alpha: +0.13%／年   ← alpha 沒了
IR = 0.92                          IR = 0.006              ← IR 崩
```

**結論**：絕對 Sharpe 跟年化報酬會被大盤 beta 蓋過 → **看 alpha + IR 才看得出策略真有沒有 selection 價值**。

OOS 那 +37% 的年化報酬「不是策略賺的，是大盤自己漲的」——
這就是 IR collapse 99.4% 的本質：策略**從「有 alpha」退化為「只是高 beta 抱 0050 + 雜訊」**。
"""
    )


def _fmt(value, fmt=".3f"):
    if value is None:
        return "N/A"
    try:
        return format(value, fmt)
    except (ValueError, TypeError):
        return str(value)


def _fmt_pct(value):
    if value is None:
        return "N/A"
    try:
        return f"{value * 100:.2f}%"
    except (ValueError, TypeError):
        return str(value)


comparison_rows = [
    ("Total Return", _fmt_pct(metrics_is.get("total_return")), _fmt_pct(metrics_oos.get("total_return"))),
    ("Annualized Return", _fmt_pct(metrics_is.get("annualized_return")), _fmt_pct(metrics_oos.get("annualized_return"))),
    ("Annualized Volatility", _fmt_pct(metrics_is.get("annualized_volatility")), _fmt_pct(metrics_oos.get("annualized_volatility"))),
    ("Sharpe Ratio", _fmt(metrics_is.get("sharpe_ratio")), _fmt(metrics_oos.get("sharpe_ratio"))),
    ("Annualized Alpha", _fmt_pct(metrics_is.get("annualized_alpha")), _fmt_pct(metrics_oos.get("annualized_alpha"))),
    ("Beta", _fmt(metrics_is.get("beta")), _fmt(metrics_oos.get("beta"))),
    ("Tracking Error", _fmt_pct(metrics_is.get("tracking_error")), _fmt_pct(metrics_oos.get("tracking_error"))),
    ("⭐ Information Ratio", _fmt(metrics_is.get("information_ratio")), _fmt(metrics_oos.get("information_ratio"))),
    ("Max Drawdown", _fmt_pct(metrics_is.get("max_drawdown")), _fmt_pct(metrics_oos.get("max_drawdown"))),
    ("Calmar Ratio", _fmt(metrics_is.get("calmar_ratio")), _fmt(metrics_oos.get("calmar_ratio"))),
    ("Total Turnover (one-way)", _fmt(metrics_is.get("total_one_way_turnover"), ".2f"), _fmt(metrics_oos.get("total_one_way_turnover"), ".2f")),
    ("N Rebalances", str(metrics_is.get("n_rebalances", "N/A")), str(metrics_oos.get("n_rebalances", "N/A"))),
]

df_cmp = pd.DataFrame(comparison_rows, columns=["Metric", "IS (2020-2024)", "OOS (2025)"])

# 高亮 IR 行
def _highlight_ir(row):
    if "Information Ratio" in str(row["Metric"]):
        return ["background-color: #fff3cd"] * len(row)
    return [""] * len(row)


styled = df_cmp.style.apply(_highlight_ir, axis=1)
st.dataframe(styled, use_container_width=True, hide_index=True)

ir_is = metrics_is.get("information_ratio", 0)
ir_oos = metrics_oos.get("information_ratio", 0)
collapse = (1 - abs(ir_oos / ir_is)) * 100 if ir_is != 0 else 0

st.error(
    f"🚨 **IR collapse 99.4%**：IS IR = {ir_is:.4f} → OOS IR = {ir_oos:.4f}\n\n"
    "這就是 **In-Sample 過擬合（overfit）的典型症狀**——IS 看起來有 alpha，"
    "但 OOS 立刻 collapse。也是為什麼後續走 Phase D v7 嚴格 18-cell 驗證 + L6 80% bootstrap CI。"
)

st.divider()

# ===============================================================
# 月再平衡 snapshots
# ===============================================================
st.subheader("📅 月再平衡 snapshots (IS 2020-2024)")
st.caption(f"共 {len(snapshots_is)} 個 monthly rebalance dates")

snap_rows = []
for snap in snapshots_is:
    snap_rows.append({
        "再平衡日": str(snap.get("rebalance_date", ""))[:10],
        "市場 regime": snap.get("market_signal", ""),
        "曝險": f"{snap.get('gross_exposure', 0):.1%}",
        "候選股數": snap.get("eligible_candidates", 0),
        "選股數": snap.get("selected_count", 0),
        "Universe": snap.get("universe_size", 0),
        "Turnover": f"{snap.get('one_way_turnover', 0):.2%}",
        "data_degraded": snap.get("data_degraded", False),
    })

df_snap = pd.DataFrame(snap_rows)
st.dataframe(df_snap, use_container_width=True, hide_index=True, height=400)
