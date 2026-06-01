"""P2 — 因子驗證（合併原 1_因子介紹.py + 2_因子IC測試.py）。

頁面結構：
1. 頁首 — 8 因子 verdict 主表
2. Tab A — 學術背景 + 計算公式 + PIT 防護（動態 select factor）
3. Tab B — IC 統計細部（select factor → by_regime + by_bucket + 月度 IC）
4. Tab C — 8×8 相關性 heatmap
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import (  # noqa: E402
    ALL_FACTORS,
    FACTOR_DISPLAY_NAMES,
    FIVE_FACTORS,
    PHASE_D_FACTORS,
    load_all_eight_factor_ics,
    load_factor_correlation,
    load_factor_ic,
)

# 2026-05-10 P1-F: dashboard 動態重算 5 因子 BH FDR
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from src.analysis.ic_analysis import fdr_correct  # noqa: E402

st.set_page_config(
    page_title="因子驗證",
    page_icon="📈",
    layout="wide",
)


# ===============================================================
# 因子靜態背景（學術 / 計算 / PIT / 用在哪些策略）
# ===============================================================
FACTOR_BACKGROUNDS: dict[str, dict[str, str]] = {
    "high_proximity": {
        "intuition": "股價接近 52 週新高的股票，未來會繼續強嗎？",
        "academic": "George & Hwang (2004) *The 52-Week High and Momentum Investing*。"
                    "投資人對 52 週新高有 **anchoring 心理**——跨過 52W 高代表市場吸收完好消息但"
                    "反應不足，後續會繼續向上漂移。",
        "data": "**OHLCV 日線收盤價**（過去 252 個交易日 ≈ 1 年）。來源：FinMind `taiwan_stock_daily` 或 TWSE `STOCK_DAY`。",
        "formula": "proximity = close_today / max(close[-252:-1]) - 1\n\nrolling max 排除今日（shift=1 防 look-ahead）。",
        "pit": "rolling max 嚴格用 today-1 之前的 close，今日 close 不入 window；新上市股最少 126 天歷史。",
        "used_in": "D-B / D-C / D-D / D-E / D-F / D-G **全部 6 個策略**（共用主動量因子）",
    },
    "pead_eps": {
        "intuition": "EPS 公告超出市場預期的股票，會繼續強嗎？",
        "academic": "Bernard & Thomas (1989) *Post-Earnings Announcement Drift*。"
                    "經典 anomaly：好/壞 earnings 公告後股價會「漂移」幾週甚至幾月，不是 efficient 反應完。",
        "data": "**季報 EPS**（每季公告，過去至少 12 季）。來源：FinMind `taiwan_stock_financial_statement`（type=EPS）。",
        "formula": "eps_z = (eps_latest - mean(prior 8Q EPS)) / std(prior 8Q EPS)\n\n"
                   "台股無 FactSet consensus，用歷史 base-rate 取代分析師預期。",
        "pit": "**per-quarter lag**：Q4 = 90 天（年報法定 3/31）/ Q1-3 = 45 天（季報法定下季結束 +45 天）。最少 12 季歷史。",
        "used_in": "D-B / D-C / D-D / D-E / D-F / D-G **全部 6 個策略**（共用基本面因子）",
    },
    "revenue_momentum_v2": {
        "intuition": "月營收成長強的股票，未來股價會繼續強嗎？",
        "academic": "Fundamental momentum 慣例。台股每月 10 日前公告月營收，**比季報快 1-2 個月**，是 retail 可用的領先指標。",
        "data": "**月營收**（每月 10 日前公告，過去至少 15 個月）。來源：FinMind `taiwan_stock_month_revenue` 或 TWSE/TPEX OpenData。",
        "formula": "4 個子訊號加權平均：\n"
                   "- **YoY 同比** (0.50)：latest / same_month_last_year - 1（嚴格年月配對）\n"
                   "- **3M / 3M 加速度** (0.20)：last_3m_avg / prev_3m_avg - 1\n"
                   "- **24M 百分位** (0.15)：最近 3M 平均在過去 24 個月 rolling 3M 中的 percentile rank\n"
                   "- **Seasonal z-score** (0.15)：最近 3 個月各自跟過去 24 個月**同月份**的 z-score 平均\n\n"
                   "**v2 改進**：YoY 嚴格 year-month match，禁止 ±45 天容忍。",
        "pit": "cutoff = `as_of - 45 天`（次月 10 日法定公告 + 5 天 publication buffer）。",
        "used_in": "**個別 IC 測試**。最終 6 個策略候選未納入（IC ≈ 0.0145 p=0.11 不顯著 + PEAD 已涵蓋基本面意涵）",
    },
    "margin_short_ratio": {
        "intuition": "融資餘額高 + 融資快速增加 = retail 在追高 → 未來反而可能跌。**反向因子**。",
        "academic": "散戶情緒 contrarian indicator。台股融資 / 融券交易特別反映 retail 參與度，是台股獨有的 sentiment signal。",
        "data": "- **融資 / 融券每日餘額**：FinMind `taiwan_stock_margin_purchase_short_sale`\n"
                "- **已發行股數**（normalize）：TWSE OpenAPI `t187ap03_L`",
        "formula": "margin_ratio = （融資 - 融券）× 1000 股 / 已發行股數\n"
                   "margin_change_20d = 融資_today / 融資_T-20 - 1\n\n"
                   "score = **-0.5** × z(margin_ratio) **-0.5** × z(margin_change_20d)\n\n"
                   "**負號**：兩個子訊號越高 = retail 越熱 → score 越低 = 我們越不想買。",
        "pit": "cutoff = `as_of - 2 天`（TWSE 盤後 T+1 公告 + 1 天 buffer）；最少 40 個交易日歷史。",
        "used_in": "D-B (20%) / D-D (30%)",
    },
    "foreign_investor_v2": {
        "intuition": "外資（連同投信）連續且持續地買進的股票，會繼續強嗎？單純「今天外資淨買」太雜訊，**改用 4 個子訊號**：連續性 + 規模 + 排名穩定 + 內外法人一致。",
        "academic": "Institutional flow signal。",
        "data": "- **三大法人買賣超**：FinMind `taiwan_stock_institutional_investors`\n"
                "- **個股市值**（normalize）：TWSE 已發行股數 × 收盤價",
        "formula": "4 個子訊號 cross-sectional z-score + 加權（2026-05-10 後）：\n"
                   "- **foreign_cum_ratio** (0.50)：過去 20 日外資累積**金額** / market_value\n"
                   "- **persistence** (0.25)：過去 20 日中外資**正淨額**的天數比例\n"
                   "- **rank_stability** (0.25)：過去 60 日中該股排名前 20% 的天數比例\n"
                   "- **consistency** (0.0, deprecated)：原 0.20，因 78% 0-sparsity 移除權重",
        "pit": "cutoff = `as_of - 2 天`；最少 60 個交易日歷史。",
        "used_in": "**個別 IC 測試**（最終 6 個策略候選未納入；fresh rerun 後 IC = -0.0077 p=0.50 不顯著）",
    },
    "quality_v3": {
        "intuition": "在價格動量強的股票裡，再挑「**ROE 高 / 毛利好 / 總資產不過度膨脹**」的，更穩？",
        "academic": "AQR **Quality Minus Junk (QMJ)** 的 profitability sub-component（Asness et al.）。"
                    "**不是完整 QMJ**——QMJ 還包括 growth / safety / payout，這裡只取 profitability + investment。",
        "data": "- **季度損益表**（Revenue / GrossProfit / NetIncome）：FinMind `taiwan_stock_financial_statement`\n"
                "- **季度資產負債表**（Equity / TotalAssets）：FinMind `taiwan_stock_balance_sheet`",
        "formula": "quality_v3 = 0.4 × z(ROE_TTM) + 0.4 × z(gross_margin_TTM) + 0.2 × z(Δassets_YoY)\n\n"
                   "**Δassets 反向**：總資產過度膨脹 = 低品質。\n\n"
                   "TTM 滾動：過去 4 季 trailing-12-month。",
        "pit": "per-quarter, max(income_lag, balance_lag)：IS Q4=90d / Q1-3=45d；BS 60d。**取 max** = 兩個都 PIT-valid 才採用該季。",
        "used_in": "D-E (20%)",
    },
    "industry_momentum": {
        "intuition": "個股動量強之外，所屬**產業整體** 6 個月也強的股票，是不是更穩？",
        "academic": "Moskowitz & Grinblatt (1999) *Do Industries Explain Momentum?*。"
                    "研究發現美股動量的相當大部分來自「產業層面」而非單股 idiosyncratic。",
        "data": "- **OHLCV 日線**（過去 6 個月 ≈ 132 個交易日）\n"
                "- **產業分類 label**：FinMind `taiwan_stock_info.industry_category`",
        "formula": "1. 每支股票算過去 6 個月（132d）總報酬\n"
                   "2. 按產業分組，算每產業的「平均過去 6m 報酬」（每股等權）\n"
                   "3. 每支股票的 score = 自己所屬產業的「平均過去 6m 報酬」\n"
                   "4. cross-sectional z-score（clip ±3σ）\n\n"
                   "**鎖 6 個月不允許改 12m**（避免 post-hoc tuning）。",
        "pit": "- 6m 報酬窗口 strict-before today（shift=1）\n"
               "- 產業 label：理想用 `as_of - 30 天` 歷史快照，目前用 current snapshot（caveat 標 D-F 風險）",
        "used_in": "D-F (20%)",
    },
    "idio_vol_max": {
        "intuition": "避開「特質波動高」+「過去 1 個月有大漲」的股票（容易是樂透型 retail 追逐標的，事後表現差）。",
        "academic": "- **Idiosyncratic Volatility puzzle**：高特質波動股票事後報酬偏低\n"
                    "- **MAX lottery effect** (Bali, Cakici & Whitelaw 2011)：過去 1 個月最高的幾天日報酬代表「樂透屬性」",
        "data": "- 個股 OHLCV（過去 60 個交易日）\n"
                "- 0050 OHLCV（作市場 benchmark 算特質波動殘差）",
        "formula": "1. residual_std (60d) = stock_std × √(1 - corr(stock, market)²)\n"
                   "2. MAX lottery (22d) = mean of top-5 daily returns in last 22 days\n\n"
                   "idio_vol_max = 0.5 × z(-residual_std) + 0.5 × z(-MAX)\n\n"
                   "**負號**：兩個都是 anti-feature。",
        "pit": "兩個 lookback 都 strict-before today（shift=1）。",
        "used_in": "D-G (20%)",
    },
}


# ===============================================================
# 內部 helper：算單因子 verdict
# ===============================================================
def _verdict(ic: dict | None) -> str:
    """IC dict → verdict 字串（與舊頁邏輯一致）。"""
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


# ===============================================================
# Title
# ===============================================================
st.title("📈 因子驗證")
st.caption(
    "8 個學術因子各自跑 single-factor IC，用 Spearman rank IC + Stationary Block Bootstrap + "
    "Deflated Sharpe Ratio + FDR Benjamini-Hochberg 多重檢定校正。"
    "資料窗口 **2020-01 到 2025-11 共 71 個月**——因子 IC 用全窗口、不切 IS/OOS；"
    "別頁提到的「60 月」是策略回測的 IS（2020-2024），兩者不同。"
)

st.divider()

# ===============================================================
# 因子漏斗 8 → 6
# ===============================================================
st.subheader("📊 因子漏斗:8 → 6")
st.caption("「因子」在不同階段有不同數量 —— 先看這個漏斗，下方主表才不會誤解。")

fn_c1, fn_c2 = st.columns(2)
with fn_c1:
    st.metric(
        label="① 進下方 IC 主表",
        value="8",
        delta="階段一 5 + 階段三 3",
        delta_color="off",
    )
with fn_c2:
    st.metric(
        label="② 進最終策略候選",
        value="6",
        delta="排除:月營收 弱IC、外資 負IR",
        delta_color="off",
    )

st.caption(
    "**①→②**:8 個因子排除 2 個 —— 月營收 v2（IC 不顯著 p≈0.11）、外資 v2"
    "（IR 為負，long-only 無法萃取 alpha）→ **最後 6 個**進策略驗證頁的 6 個候選組合。"
)

st.divider()

# ===============================================================
# 8 因子 verdict 主表
# ===============================================================
st.subheader("📋 8 因子 IC 主表（漏斗第 ① 層）")

ics = load_all_eight_factor_ics()
if not ics:
    st.error("讀不到 reports/factor_ic/ 內因子 IC JSON。")
    st.stop()

# 動態算 FDR（v2.0 的 5 因子 m=5 pre-registered）
_nominal_pvals = [
    ics.get(f, {}).get("overall", {}).get("p_value") if ics.get(f) else None
    for f in FIVE_FACTORS
]
_fdr_adjusted = fdr_correct(_nominal_pvals)
_fdr_by_factor = dict(zip(FIVE_FACTORS, _fdr_adjusted))

# Contamination warnings
for f in ALL_FACTORS:
    _ic = ics.get(f)
    if _ic and _ic.get("pit_violation", {}).get("violated"):
        st.warning(
            f"⚠️ **{FACTOR_DISPLAY_NAMES.get(f, f)} contaminated**："
            f"{_ic['pit_violation'].get('reason', 'PIT violation')}（fresh rerun pending）"
        )

# 主表 rows
table_rows = []
for factor in ALL_FACTORS:
    ic = ics.get(factor)
    if ic is None:
        continue
    overall = ic.get("overall", {})
    ci = overall.get("bootstrap_ci_95", [None, None])
    is_phase_d = factor in PHASE_D_FACTORS
    fdr = _fdr_by_factor.get(factor)
    dsr = ic.get("deflated_sharpe_ratio")

    mean_ic = overall.get("mean_ic", 0)
    p_val = overall.get("p_value", 1)
    if mean_ic > 0.04 and p_val < 0.05 and (dsr is not None and dsr > 0.5):
        verdict = "🟢 Good"
    elif mean_ic > 0.02 and p_val < 0.10:
        verdict = "🟡 Normal"
    else:
        verdict = "🔴 Fail"

    table_rows.append({
        "因子": f"{FACTOR_DISPLAY_NAMES.get(factor, factor)} ({factor})",
        "分組": "階段三" if is_phase_d else "階段一",
        "mean IC": f"{mean_ic:.4f}",
        "IC IR": f"{overall.get('ic_ir', 0):.3f}",
        "p-value": f"{p_val:.4f}",
        "FDR-adj p": "N/A (非 m=5 pre-reg)" if is_phase_d else (f"{fdr:.4f}" if fdr is not None else "N/A"),
        "DSR": f"{dsr:.4f}" if dsr is not None else "N/A",
        "Bootstrap CI 95%": f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci and ci[0] is not None else "N/A",
        "verdict": verdict,
    })

st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)

with st.expander("📊 怎麼讀 DSR、為什麼 FDR 部分顯示 N/A？（點開看白話說明）", expanded=False):
    st.markdown(
        """
**① DSR 怎麼讀？（不是「全 0」）**

DSR（Deflated Sharpe Ratio）把「我試了好幾個因子、最後挑最好看那個」的運氣先扣掉（n_trials），再問:這個因子的 IC IR 在這麼多期裡、扣掉運氣後，信心度 Ψ 還剩多少。Ψ 越接近 1 越強。

本專案 8 個因子實際 DSR **不是全 0**（各因子已按其 pre-reg 家族做 n_trials 多檢定校正）:

- **idio_vol_max 0.95、high_proximity 0.81、margin_short_ratio 0.79、pead_eps 0.77**（4 個 DSR>0.5）+ revenue_momentum_v2 0.49（門檻邊緣）— 都不是雜訊。
- foreign_investor_v2 / quality_v3 / industry_momentum 3 個 ≈ 0 — 因為它們的 IC 是**負的**（方向反了），不是雜訊也不是強訊號。

**所以單因子層面，價格 / 盈餘動能這一族是有 edge 的。** 真正的問題不在「因子是不是雜訊」，而在下一頁:把這些因子組成 **long-only top-N 投組、扣成本、對 0050 比、且只有 11 個月嚴格 OOS** 時，組合層的 active edge 證不出統計顯著（見「策略驗證」「結論」頁）。

**常見誤解澄清:** DSR 不是拿 IC IR 去比一個「1.3~1.5 門檻」。它是把 observed IC IR 在 n_obs（71 期）+ n_trials（5）下 deflate 算信心度;IC IR 0.37、p=0.003 的 idio_vol_max，deflate 後 Ψ 仍有 0.946（逼近但未達 0.95 機構顯著線）。

**② 為什麼階段三 3 個因子的 FDR 顯示「N/A」**

FDR 在修正「同時測很多因子」造成的假警報。只測一個因子時，`p < 0.05` 代表「純靠運氣的機率只有 5%」;但同時測 5 個，就算 5 個全是噪音，光靠運氣大概也會有 1 個 p 掉進 0.05 以下。FDR 把這個虛胖扣掉。

但 FDR 要先界定「家族有哪幾個成員」才能算:

- 階段一 5 個因子:**看資料前就講好**要一起測的家族 → 算得出 FDR 值。
- 階段三 3 個因子:**後來才補測**的，不在那份 5 人名單裡 → 沒有對應的 FDR 值 → 標 **N/A**。

把 8 個事後硬湊成一個家族一起算，等於看完結果才回頭改當初的承諾（p-hacking）。**標 N/A 才是誠實的做法。**
"""
    )

st.divider()

# ===============================================================
# 評估指標說明（折疊 expander）
# ===============================================================
with st.expander("📖 評估指標說明（看上方主表前先看）", expanded=False):
    st.markdown(
        """
| 指標 | 直觀講 | 正式定義 |
|---|---|---|
| **mean IC** | **訊號強不強**——因子排名跟下月報酬排名同步嗎？ | 每個月「因子排名」vs「下個月報酬排名」Spearman 相關，再對所有月份取平均。≥ 0.04 strong，0.02-0.04 中道 |
| **IC IR** | **IC 的夏普值**——IC 訊號穩不穩定？ | mean(IC) / std(IC)。signal-to-noise，≥ 0.5 強 |
| **t-stat / p-value** | **mean IC ≠ 0 是運氣還是有真訊號？** | t 檢定 mean IC 顯著性。p < 0.05 = 95% 信心非運氣 |
| **FDR-adj p** | **同時測多個因子有人純粹運氣過 0.05 嗎？** | Benjamini-Hochberg 校正（階段一 5 因子 m=5 pre-reg）。避免 testing fishing 假陽性 |
| **DSR (Deflated Sharpe Ratio)** | **校正後的 Sharpe 信心度**——Sharpe 偏離常態 + 試了多個 trial 後，真的有 edge 嗎？ | Bailey & Lopez de Prado (2014)。Ψ ≥ 0.95 強信心；Ψ ≤ 0.05 連 null 都贏不了。**注意：DSR 是 confidence 不是 p-value，方向相反！** |
| **Bootstrap CI 95%** | **重抽 10000 次後，這個 IC 還會是正的嗎？** | Politis-Romano stationary block bootstrap (block_len=3) 保留時序自相關，估 mean IC 的 95% CI。下界 > 0 = robust |

---

**📌 DSR 的 `n_trials` 是什麼（容易誤解，先講清楚）**

DSR 公式裡的 `n_trials` = **「事前鎖定的測試因子家族大小」**，**不是** dashboard 上的因子總數 8。
- 階段一 5 因子 → `n_trials=5`（pre-registered m=5 家族）
- 階段三 3 因子 → `n_trials=3`（後補的另一批，自己的家族）
- **不合併成 8**：兩批不同時間 pre-register，事後合併 = 改動 pre-registered 參數 = p-hacking

各因子 DSR 統一用 `n_trials=5` deflation。在此校正下，動能 / 盈餘族（idio_vol_max / high_proximity / margin_short_ratio / pead_eps）的 Ψ 達 0.77~0.95 — 單因子訊號扣掉多檢定運氣後依然站得住；revenue_momentum_v2 0.49 在門檻邊緣，IC 為負的 3 個因子 Ψ≈0。

這裡的 DSR 看的是「**單因子 IC 的信心度**」;策略能不能贏 0050 是「**投組層**」的事，受 11 個月 strict OOS 樣本限制（見策略驗證頁第 6 關 bootstrap CI）。**兩者不同層，不要混為一談。**

---

**verdict 規則**：
- 🟢 **Good**（嚴格門檻）：mean IC > 0.04 + p < 0.05 + DSR > 0.5
- 🟡 **Normal**（中道門檻）：mean IC > 0.02 + p < 0.10
- 🔴 **Fail**（以上都不滿足）
"""
    )

st.divider()

# ===============================================================
# Tabs — 因子細部分析
# ===============================================================
st.subheader("🔍 因子細部分析")

tab_a, tab_b, tab_c = st.tabs([
    "📚 學術背景 + 計算公式",
    "📊 IC 統計細部",
    "🔗 8×8 相關性 heatmap",
])

# ---- Tab A — 學術背景 + 計算公式 ----
with tab_a:
    st.caption("選一個因子查看完整學術 + 計算 + PIT 文件。")

    factor_a = st.selectbox(
        "選因子（學術背景）：",
        options=ALL_FACTORS,
        format_func=lambda f: f"{FACTOR_DISPLAY_NAMES.get(f, f)} ({_verdict(load_factor_ic(f))})",
        key="factor_a",
    )

    bg = FACTOR_BACKGROUNDS.get(factor_a)
    if not bg:
        st.info("此因子尚無 background 資料。")
    else:
        st.markdown(f"### {FACTOR_DISPLAY_NAMES.get(factor_a, factor_a)}")

        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown("**🎯 直覺**")
            st.info(bg["intuition"])
            st.markdown("**🎓 學術依據**")
            st.markdown(bg["academic"])
            st.markdown("**🛡️ PIT 防護**")
            st.markdown(bg["pit"])
        with col_r:
            st.markdown("**📊 需要的資料**")
            st.markdown(bg["data"])
            st.markdown("**🧮 計算方式**")
            st.code(bg["formula"], language="text")
            st.markdown("**📌 用在哪些策略**")
            st.markdown(bg["used_in"])

# ---- Tab B — IC 統計細部 ----
with tab_b:
    st.caption(
        "📏 下方表格數字的合格門檻 —— **mean IC**：≥ 0.04 算強、0.02-0.04 中道、< 0.02 偏弱；"
        "**IC IR**：≥ 0.5 算強（IC 的訊號雜訊比，越高代表 IC 越穩定）；"
        "**p-value**：< 0.05 才算顯著（有 95% 信心，不是運氣）。"
    )

    factor_b = st.selectbox(
        "選因子（IC 細部）：",
        options=ALL_FACTORS,
        format_func=lambda f: f"{FACTOR_DISPLAY_NAMES.get(f, f)}",
        key="factor_b",
    )

    ic = ics.get(factor_b, {})

    # by_regime / by_bucket
    col_x, col_y = st.columns(2)
    with col_x:
        st.markdown("**By Regime（按市場狀態拆）**")
        st.caption(
            "📌 **Regime 判斷條件**（用 0050 大盤）：\n\n"
            "- **trending_up**（上升趨勢）：ADX ≥ 25 + SMA 多頭排列（fast > slow）\n"
            "- **trending_down**（下降趨勢）：ADX ≥ 25 + SMA 空頭排列（fast < slow）\n"
            "- **ranging**（震盪盤整）：ADX < 20，或 ADX 20-25 灰區無 structure 確認\n\n"
            "看因子在哪種市場有效——理想是 trending_up / down 都正、ranging 弱 = 因子吃趨勢；"
            "若三種 regime IC 方向反轉 = 因子非 robust。"
        )
        by_regime = ic.get("by_regime", {})
        if by_regime:
            rg_rows = [
                {
                    "Regime": rg,
                    "mean IC": f"{m.get('mean_ic', 0):.4f}",
                    "IC IR": f"{m.get('ic_ir', 0):.3f}",
                    "p-value": f"{m.get('p_value', 1):.4f}",
                    "n": m.get("n", 0),
                }
                for rg, m in by_regime.items()
            ]
            st.dataframe(pd.DataFrame(rg_rows), width="stretch", hide_index=True)
        else:
            st.info("無 by_regime 資料")

    with col_y:
        st.markdown("**By Bucket（按時間切片拆）**")
        by_bucket = ic.get("by_bucket", {})
        if by_bucket:
            bk_rows = [
                {
                    "Bucket": bk,
                    "mean IC": f"{m.get('mean_ic', 0):.4f}",
                    "IC IR": f"{m.get('ic_ir', 0):.3f}",
                    "p-value": f"{m.get('p_value', 1):.4f}",
                    "n": m.get("n", 0),
                }
                for bk, m in by_bucket.items()
            ]
            st.dataframe(pd.DataFrame(bk_rows), width="stretch", hide_index=True)
        else:
            st.info("無 by_bucket 資料")

    # 月度 IC 時序
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
        fig_period = go.Figure(
            go.Bar(
                x=df_period["period"],
                y=df_period["monthly_ic"],
                marker=dict(
                    color=["#27ae60" if v >= 0 else "#c0392b" for v in df_period["monthly_ic"]]
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
            height=320,
            title=f"{FACTOR_DISPLAY_NAMES.get(factor_b, factor_b)} — 月度 Spearman IC",
            xaxis_title="月份",
            yaxis_title="月 IC",
            margin=dict(t=50, b=20),
        )
        st.plotly_chart(fig_period, width="stretch")
    else:
        st.info("無 period_ics 資料")

# ---- Tab C — 8×8 相關性 heatmap ----
with tab_c:
    st.caption(
        "因子之間相關性高 → 加在一起組合會 redundant（沒互補）。"
        "理想：找相關性低（|ρ| < 0.3）的因子組合，互補才有 diversification benefit。"
        "**範圍**：全部 8 因子（階段一 5 + 階段三 3）；相關性是描述性統計，"
        "不像 FDR 需要 pre-registered 家族，故 8 因子可一起檢視。"
    )

    corr_data = load_factor_correlation()
    if corr_data is None:
        st.warning("讀不到 factor_correlation_matrix.json")
    else:
        factors_list = corr_data.get("factors", [])
        matrix_dict = corr_data.get("matrix", {})

        corr_matrix = [
            [matrix_dict.get(f1, {}).get(f2, 0) for f2 in factors_list]
            for f1 in factors_list
        ]
        display_names = [FACTOR_DISPLAY_NAMES.get(f, f) for f in factors_list]

        fig_corr = go.Figure(
            go.Heatmap(
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
            height=480,
            margin=dict(t=20, b=20, l=120, r=80),
        )
        st.plotly_chart(fig_corr, width="stretch")

        n_periods = corr_data.get("period_counts", {})
        # period_counts 是 dict-of-dicts（outer key = factor, inner = {factor: count}）
        # 攤平所有 inner counts 取平均 → 顯示一個代表數字
        all_counts = [
            v for inner in n_periods.values()
            if isinstance(inner, dict)
            for v in inner.values()
            if isinstance(v, (int, float))
        ]
        n_avg = int(sum(all_counts) / len(all_counts)) if all_counts else 71
        st.caption(
            f"**Spearman rank correlation**, n_periods≈{n_avg} monthly。"
            "RdBu_r colorscale 中央對齊 0；對角線 1.0 是因子自相關。"
        )
