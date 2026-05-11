"""頁 1 — 因子介紹（學術背景 + 計算方式 + 資料源 + PIT 防護）。

本專案使用 8 個量化因子。本頁解釋每個因子的：
1. 直覺（為什麼可能有效）
2. 學術依據（哪篇 paper）
3. 需要的資料源（FinMind dataset / TWSE OpenAPI / 等）
4. 計算方式
5. PIT 防護（防 look-ahead bias）
6. 用在哪些策略
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import load_factor_ic  # noqa: E402

st.set_page_config(
    page_title="因子介紹",
    page_icon="🧩",
    layout="wide",
)


def _verdict_for(factor_name: str) -> str:
    """讀因子 IC JSON 算 verdict（與頁 2 邏輯一致）。N/A 若無 IC。"""
    ic = load_factor_ic(factor_name)
    if not ic:
        return "—"
    o = ic.get("overall", {})
    mean_ic = o.get("mean_ic", 0)
    p_val = o.get("p_value", 1)
    dsr = ic.get("deflated_sharpe_ratio")
    if mean_ic > 0.04 and p_val < 0.05 and (dsr is not None and dsr > 0.5):
        return f"🟢 Good (IC={mean_ic:+.4f})"
    if mean_ic > 0.02 and p_val < 0.10:
        return f"🟡 Normal (IC={mean_ic:+.4f})"
    return f"🔴 Fail (IC={mean_ic:+.4f})"

st.title("🧩 因子介紹")
st.caption(
    "本專案使用 **8 個量化因子**。本頁解釋每個因子的學術依據、計算方式、"
    "需要的資料源與 PIT（point-in-time）防護。"
    "下一頁（**因子IC測試**）會跑實證看哪些 signal 真的顯著。"
)

st.divider()

# ===============================================================
# 總覽表 — 一張表掃完 8 個因子
# ===============================================================
st.subheader("📋 8 個因子總覽")

overview_table = pd.DataFrame(
    [
        {
            "因子": "52W 高接近度 (high_proximity)",
            "用在哪些策略": "全部 6 個策略（共用主動量）",
            "需要的資料": "OHLCV 日線（close）",
            "學術依據": "George & Hwang (2004)",
            "single IC verdict": _verdict_for("high_proximity"),
        },
        {
            "因子": "PEAD / EPS 驚喜 (pead_eps)",
            "用在哪些策略": "全部 6 個策略（共用基本面）",
            "需要的資料": "季報 EPS",
            "學術依據": "Bernard & Thomas (1989)",
            "single IC verdict": _verdict_for("pead_eps"),
        },
        {
            "因子": "月營收動能 v2 (revenue_momentum_v2)",
            "用在哪些策略": "僅 IC 個別測試",
            "需要的資料": "月營收公告",
            "學術依據": "Fundamental momentum 慣例",
            "single IC verdict": _verdict_for("revenue_momentum_v2"),
        },
        {
            "因子": "融資 / 融券反向 (margin_short_ratio)",
            "用在哪些策略": "D-B(20%) / D-D(30%)",
            "需要的資料": "融資融券餘額 + 已發行股數",
            "學術依據": "Retail 情緒 contrarian",
            "single IC verdict": _verdict_for("margin_short_ratio"),
        },
        {
            "因子": "外資法人因子 v2 (foreign_investor_v2)",
            "用在哪些策略": "僅 IC 個別測試",
            "需要的資料": "三大法人買賣超 + 市值",
            "學術依據": "Institutional flow",
            "single IC verdict": _verdict_for("foreign_investor_v2"),
        },
        {
            "因子": "品質 (quality_v3)",
            "用在哪些策略": "D-E(20%)",
            "需要的資料": "季報損益表 + 資產負債表",
            "學術依據": "AQR QMJ profitability sub",
            "single IC verdict": _verdict_for("quality_v3"),
        },
        {
            "因子": "產業動量 (industry_momentum)",
            "用在哪些策略": "D-F(20%)",
            "需要的資料": "OHLCV + 產業分類",
            "學術依據": "Moskowitz & Grinblatt (1999)",
            "single IC verdict": _verdict_for("industry_momentum"),
        },
        {
            "因子": "特質波動 + MAX 樂透 (idio_vol_max)",
            "用在哪些策略": "D-G(20%)",
            "需要的資料": "OHLCV + 0050 （作市場 benchmark)",
            "學術依據": "Bali, Cakici & Whitelaw (2011)",
            "single IC verdict": _verdict_for("idio_vol_max"),
        },
    ]
)
st.dataframe(overview_table, use_container_width=True, hide_index=True)

st.caption(
    "📌 **說明**：6 個策略候選 = D-B/C/D/E/F/G。D-A（52W+PEAD 50/50）已預先 disqualify。"
    "「僅 IC 個別測試」表示因子有跑單獨 IC，但因 IC 不夠顯著或跟其他因子重疊，最終沒納入 6 個策略候選。"
)

st.divider()

# ===============================================================
# 8 個因子詳細
# ===============================================================
st.subheader("🔍 8 個因子詳細介紹")
st.caption("點開任一個 expander 看單一因子完整內容。")


# ---- 1. 52W 高接近度 ----
with st.expander("🟢 1. 52W 高接近度（high_proximity）", expanded=True):
    st.markdown(
        """
**直覺**：股價接近 52 週新高的股票，未來會繼續強嗎？

**學術依據**：George & Hwang (2004) "The 52-Week High and Momentum Investing"。
投資人對 52 週新高有 **anchoring 心理**——跨過 52W 高代表市場吸收完好消息但
反應不足，後續會繼續向上漂移。

**需要的資料**：
- **OHLCV 日線收盤價**（過去 252 個交易日 ≈ 1 年）
- 來源：FinMind `taiwan_stock_daily` 或 TWSE `STOCK_DAY` 公開資料

**計算方式**：
```
proximity = close_today / max(close[-252:-1]) - 1
```
其中 max 取「過去 252 個交易日」收盤價最高點，且 **排除今日**（shift=1 防 look-ahead）。

**範圍**：通常落在 [-1, 0]。-0.05 = 距 52W 高還差 5%；0 = 剛剛追平 52W 高。

**PIT 防護**：rolling max 嚴格用 today-1 之前的 close，今日 close 不入 window；
新上市股票最少要有 126 天歷史才採用。

**用在哪些策略**：D-B / D-C / D-D / D-E / D-F / D-G **全部 6 個策略**（共用主動量因子）。
"""
    )


# ---- 2. PEAD/EPS 驚喜 ----
with st.expander("🟢 2. PEAD / EPS 驚喜（pead_eps）", expanded=False):
    st.markdown(
        """
**直覺**：EPS 公告超出市場預期的股票，會繼續強嗎？

**學術依據**：Bernard & Thomas (1989) "Post-Earnings Announcement Drift"。
經典 anomaly：好/壞 earnings 公告後股價會「漂移」幾週甚至幾月，不是 efficient 反應完。

**需要的資料**：
- **季報 EPS**（每季公告一次，過去至少 12 季）
- 來源：FinMind `taiwan_stock_financial_statement`（type=EPS）

**計算方式**：
```
surprise_z = (eps_latest - mean(prior 8Q EPS)) / std(prior 8Q EPS)
```
台股無 FactSet consensus，**用歷史 base-rate 取代分析師預期**——拿最近 8 季
EPS 的 mean ± std 當「市場應該看到的」，今期 EPS 偏離多少 sigma 就是 surprise。

**PIT 防護**（per-quarter lag，防早期年初混入未公告 Q4）：
- Q4（年報）：90 天 lag（台股年報法定 3/31 截止）
- Q1-Q3（季報）：45 天 lag（季報法定下季結束 +45 天）
- 最少 12 季歷史才採用（base rate 才穩定）

**用在哪些策略**：D-B / D-C / D-D / D-E / D-F / D-G **全部 6 個策略**（共用基本面因子）。
"""
    )


# ---- 3. 月營收動能 ----
with st.expander("🟢 3. 月營收動能 v2（revenue_momentum_v2）", expanded=False):
    st.markdown(
        """
**直覺**：月營收成長強的股票，未來股價會繼續強嗎？

**學術依據**：營收動能是 fundamental momentum 的常見替代指標。台股每月 10 日前
公告月營收，**比季報快 1-2 個月**，是 retail 可用的領先指標。

**需要的資料**：
- **月營收**（每月 10 日前公告，過去至少 15 個月）
- 來源：FinMind `taiwan_stock_month_revenue` 或 TWSE/TPEX OpenData 月營收

**計算方式**：4 個子訊號加權平均（**v2 改進版**）：

| 子訊號 | 權重 | 計算 |
|---|---|---|
| **YoY 同比** | 0.50 | `latest_revenue / same_month_last_year - 1`（嚴格年月配對）|
| **3M/3M 加速度** | 0.20 | `last_3m_avg / prev_3m_avg - 1` |
| **24M 百分位** | 0.15 | 最近 3M 平均在過去 24 個月 rolling 3M 中的 percentile rank |
| **Seasonal z-score** | 0.15 | 最近 3 個月各自跟過去 24 個月**同月份**的 z-score 平均 |

**v2 vs v1 改進**：YoY 嚴格 year-month match，禁止 ±45 天容忍——
舊版會在某月資料缺漏時偷拿鄰近月當 base，造成 seasonal drift contamination。

**PIT 防護**：cutoff = `as_of - 45 天`（次月 10 日法定公告 + 5 天 publication buffer）。

**用在哪些策略**：個別 IC 測試。最終 6 個策略候選未納入（被 PEAD 取代為基本面因子，
跟營收動能 IC 重疊度高）。
"""
    )


# ---- 4. 融資/融券反向 ----
with st.expander("🟢 4. 融資 / 融券反向（margin_short_ratio）", expanded=False):
    st.markdown(
        """
**直覺**：融資餘額高 + 融資快速增加 = retail 在追高 → 未來反而可能跌。
這是**反向因子**（高分代表 retail 沒過熱 → bullish）。

**學術依據**：散戶情緒 contrarian indicator。台股融資/融券交易特別反映 retail
參與度，是台股獨有的 sentiment signal。

**需要的資料**：
- **融資 / 融券每日餘額**：FinMind `taiwan_stock_margin_purchase_short_sale`
- **已發行股數**（normalize 用）：TWSE OpenAPI `t187ap03_L`（公司基本資料）

**計算方式**（兩個子訊號 z-score 後**負加權**）：

```
margin_ratio       = （融資餘額 - 融券餘額） × 1000 股 / 已發行股數
margin_change_20d  = 融資餘額_今天 / 融資餘額_20天前 - 1

score = -0.5 × z(margin_ratio) - 0.5 × z(margin_change_20d)
```

**負號**：兩個子訊號越高 = retail 越熱 → score 越低 = 我們越不想買。

**PIT 防護**：cutoff = `as_of - 2 天`（TWSE 盤後結算 +T+1 公告，再加 1 天 buffer）；
最少 40 個交易日歷史。

**用在哪些策略**：D-B（20% 權重）、D-D（30% 權重）。
"""
    )


# ---- 5. 外資法人因子 v2 ----
with st.expander("🟢 5. 外資法人因子 v2（foreign_investor_v2）", expanded=False):
    st.markdown(
        """
**直覺**：外資（連同投信）連續且持續地買進的股票，會繼續強嗎？
單純看「今天外資淨買」太雜訊，**改用 4 個子訊號** 描述「連續性 + 規模 + 排名穩定 + 內外法人一致」。

**需要的資料**：
- **三大法人買賣超**（外資 / 投信 / 自營商，每日）：FinMind `taiwan_stock_institutional_investors`
- **個股市值**（normalize 用）：TWSE 已發行股數 × 收盤價

**計算方式**（4 個子訊號 cross-sectional z-score + 加權）：

| 子訊號 | 權重 (2026-05-10 後) | 計算 |
|---|---|---|
| **foreign_cum_ratio** | **0.50** (was 0.40) | 過去 20 日外資累積**金額**（net × close）/ market_value（P0-B 量綱修正） |
| **persistence** | **0.25** (was 0.20) | 過去 20 日中外資**正淨額**的天數比例 |
| **rank_stability** | **0.25** (was 0.20) | 過去 60 日中該股**排名前 20%（外資金額/mv）**的天數比例 |
| **consistency** | **0.0** (was 0.20, deprecated) | 過去 20 日中**外資+投信同方向**正淨額的天數比例（P1-D 78% 0-sparsity，移除權重） |

**為什麼 v2**（vs v1 單一 net-flow snapshot）：v1 IC 實測 = -0.053（fail）；
v2 用持續性訊號取代 noise-prone 即時值。

**PIT 防護**：cutoff = `as_of - 2 天`（TWSE 盤後 17:00 公告，T+1 可用 + buffer）；
最少 60 個交易日歷史（rank_stability 需要）。

**用在哪些策略**：個別 IC 測試。最終 6 個策略候選未納入（v1 IC fail，
v2 是改進版但仍待後續實證；目前先觀察）。
"""
    )


# ---- 6. quality_v3 ----
with st.expander("🟡 6. 品質因子 quality_v3", expanded=False):
    st.markdown(
        """
**直覺**：在價格動量強的股票裡，再挑「**ROE 高、毛利好、總資產不過度膨脹**」的，
是不是更穩？

**學術依據**：AQR 的 **Quality Minus Junk (QMJ)** 因子（Asness et al.）的
**獲利能力子分量**（profitability sub-component）。注意這**不是完整 QMJ**——QMJ 還
包括 growth / safety / payout，這裡只取 profitability + investment。

**需要的資料**：
- **季度損益表**（Revenue / GrossProfit / NetIncome 或 IncomeAfterTaxes，每季）
  - FinMind `taiwan_stock_financial_statement`（不過濾 type，全表）
- **季度資產負債表**（Equity / TotalAssets，每季）
  - FinMind `taiwan_stock_balance_sheet`

**計算方式**（3 個子訊號 cross-sectional z-score + 加權）：

```
quality_v3 = 0.4 × z(ROE_TTM)
           + 0.4 × z(gross_margin_TTM)
           + 0.2 × z(Δassets_YoY)   ← 注意：總資產**反向**（過度膨脹 = 低品質）
```

**TTM 滾動**：過去 4 季營收 / 毛利 / 稅後淨利 / 平均股東權益的 trailing-12-month。

**PIT 防護**（per-quarter，max(income_lag, balance_lag））：
- Income statement：Q4 = 90 天，Q1-3 = 45 天
- Balance sheet：60 天（公告慣例晚 IS 數天到 2 週）
- 取 **max** = 兩個都 PIT-valid 才採用該季

**用在哪些策略**：D-E（20% 權重）。
"""
    )


# ---- 7. industry_momentum ----
with st.expander("🟡 7. 產業動量（industry_momentum）", expanded=False):
    st.markdown(
        """
**直覺**：個股動量強之外，所屬**產業整體** 6 個月也強的股票，是不是更穩？

**學術依據**：Moskowitz & Grinblatt (1999) "Do Industries Explain Momentum?"。
研究發現美股動量的相當大部分來自「產業層面」而非單股 idiosyncratic，
意味跟對產業比挑對個股還重要。

**需要的資料**：
- **OHLCV 日線收盤價**（過去 6 個月 ≈ 132 個交易日）
- **產業分類 label**（每支股票所屬產業）：FinMind `taiwan_stock_info` 的 `industry_category` 欄位

**計算方式**：
```
1. 每支股票算過去 6 個月（≈ 132 個交易日）總報酬
2. 按產業分組，算每產業的「平均過去 6m 報酬」（每股等權）
3. 每支股票的 score = 自己所屬產業的「平均過去 6m 報酬」
4. cross-sectional z-score（clip ±3σ）
```

**為何鎖 6 個月**：原 Moskowitz-Grinblatt 1999 用 6m formation，本專案 lock 不允許
改 12m 找 better fit（會變 post-hoc tuning）。

**PIT 防護**：
- 6m 報酬窗口 strict-before today（shift=1）
- 產業 label：理想要用 **as_of - 30 天的歷史快照**（避免歷史重分類），
  目前實作用 current snapshot（caveat 標 D-F 風險）

**用在哪些策略**：D-F（20% 權重）。
"""
    )


# ---- 8. idio_vol_max ----
with st.expander("🟡 8. 特質波動 + MAX 樂透（idio_vol_max）", expanded=False):
    st.markdown(
        """
**直覺**：避開「特質波動高」+「過去 1 個月有大漲」的股票（容易是樂透型 retail
追逐的標的，未來反而表現差）。

**學術依據**：
- **Idiosyncratic Volatility puzzle**：高特質波動股票事後報酬偏低
- **MAX lottery effect**（Bali, Cakici & Whitelaw 2011）：過去 1 個月最高的幾天日報酬，
  代表股票「樂透屬性」越重，retail 追高概率越大，事後報酬越差

**需要的資料**：
- **個股 OHLCV 日線收盤價**（過去 60 個交易日 ≈ 3 個月）
- **0050 OHLCV 日線收盤價**（作市場 benchmark 算特質波動殘差）

**計算方式**（兩個子訊號 0.5/0.5）：

```
1. residual_std (60 個交易日）
   = stock_std × √(1 - corr(stock, market)²)
   ※ 簡化版「OLS 殘差 std」，避開逐股 OLS fit 成本
2. MAX lottery (22 個交易日）
   = mean of top-5 daily returns in last 22 days

idio_vol_max = 0.5 × z(-residual_std) + 0.5 × z(-MAX)
```

**負號**：兩個都是 anti-feature——值越大 = 越像 retail 追高的樂透股 = 我們越不想買。

**PIT 防護**：兩個 lookback 都 strict-before today（shift=1）。

**用在哪些策略**：D-G（20% 權重）。
"""
    )


st.divider()

# ===============================================================
# 對照矩陣
# ===============================================================
st.subheader("📊 8 個因子 × 6 個策略候選 對照矩陣")

st.markdown(
    """
| 因子 | D-B | D-C | D-D | D-E | D-F | D-G | 角色 |
|---|---|---|---|---|---|---|---|
| **52W 高接近度** | 39% | 40% | 34% | 40% | 40% | 40% | 共用主動量 |
| **PEAD / EPS 驚喜** | 41% | 60% | 36% | 40% | 40% | 40% | 共用基本面 |
| **融資 / 融券反向** | 20% | — | 30% | — | — | — | D-B / D-D 變體 |
| **品質 quality_v3** | — | — | — | 20% | — | — | D-E 變體 |
| **產業動量** | — | — | — | — | 20% | — | D-F 變體 |
| **特質波動 + MAX** | — | — | — | — | — | 20% | D-G 變體 |
| **月營收動能** | — | — | — | — | — | — | 個別 IC 測試（未進策略）|
| **外資法人因子 v2** | — | — | — | — | — | — | 個別 IC 測試（未進策略）|

**為什麼月營收與外資沒進策略候選**：
- **月營收動能**：個別 IC ≈ +0.0145（p=0.113，未過 0.05）/ DSR=0 fail（與 PEAD 重疊度高，corr ≈ 0.38，PEAD 已涵蓋基本面意涵）
- **外資法人因子 v1** (legacy `institutional.py`)：IC = -0.053 fail（單一 net-flow snapshot，已棄用）
- **外資法人因子 v2** (`foreign_investor_v2`)：⚠️ 2026-05-10 Codex R26 audit 發現 PIT contamination（latest market value + 量綱錯誤）；2026-05-10 R28 fresh rerun 完成（PIT-asof market_value + dollar 制 cum_ratio + last20 stale guard + consistency=0 + covered-weight rescale）：**新 mean IC = -0.0077, p=0.5007, Bootstrap CI [-0.0276, +0.0116] 跨 0 不顯著**（vs 舊 -0.0195 p=0.082 CI 全<0），verdict **DROP**（PIT 修法後實證 alpha 微弱）。詳見 `reports/factor_ic/foreign_investor_v2_ic.json::pit_violation`（violated=false 已標 fresh rerun）+ `reports/factor_ic/_closeout/old_vs_new_comparison_2026-05-10.md`

→ 6 個策略候選把因子篩成「兩個共用 + 一個變體」結構，避免 high-dimensional overfit。
"""
)
