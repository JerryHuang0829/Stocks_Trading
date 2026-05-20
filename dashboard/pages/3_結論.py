"""P4 — 結論 + 下一輪 Roadmap。

頁面結構：
1. 本輪結論：NO-GO 三個失敗點
2. 本輪學到的事
3. Path to GO Roadmap（5 items）
4. 結尾紀律
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="結論 + Roadmap",
    page_icon="🏁",
    layout="wide",
)

st.title("🏁 結論 + 下一輪 Roadmap")

# ===============================================================
# 本輪結論：NO-GO
# ===============================================================
st.subheader("🔴 本輪結論：NO-GO — 三個關鍵失敗點")

fail_col1, fail_col2, fail_col3 = st.columns(3)

with fail_col1:
    st.error(
        """
##### 第 6 關 Bootstrap CI 全 fail

**18 / 18 cells 第 6 關 80% Bootstrap CI 下界 ≤ 0**

→ 沒有任何 cell 的超額報酬統計顯著
"""
    )

with fail_col2:
    st.error(
        """
##### 雙因子策略 IR collapse 99.4%

**IS IR = 0.92 → OOS IR = 0.0058**

→ 典型 in-sample over-fit，OOS 立即 collapse
"""
    )

with fail_col3:
    st.error(
        """
##### 8 因子訊號本身就弱

**最強 IC IR 僅 0.33、8 因子 DSR 全 = 0**

→ 原料就弱，組合再怎麼搭也擠不出 robust alpha
"""
    )

st.divider()

# ===============================================================
# 本輪學到的事
# ===============================================================
st.subheader("📚 本輪學到的事 — 結構問題不是運氣不好")

st.markdown(
    """
- **📊 long-only 月頻 vs 0050 有結構劣勢**——像 2024 大權值股獨舞的年份，因子怎麼選都追不上，這不是「因子不夠強」，是結構問題（→ Roadmap #3 L / S）
- **🧮 60 個月對嚴格統計檢定遠遠不夠**——Block bootstrap 把時序相關性扣掉後 effective n 只剩 20，普通 t-test 假定獨立會嚴重高估顯著性（→ Roadmap #1 樣本擴充）
- **🔬 IC IR ≠ portfolio Sharpe**——因子層的 signal-to-noise（IC IR）再高，也不代表組合成策略後就有 alpha。中間隔著加權、產業約束、交易成本——factor-level 指標好看 ≠ portfolio-level 有 edge
"""
)

st.divider()

# ===============================================================
# Path to GO Roadmap（呼應書審題 5 + JD「AI 模型應用」）
# ===============================================================
st.subheader("🚀 接下來的優化方向")

st.markdown(
    """
基於本輪 18 cells 失敗根因（第 6 關全 fail / 60 月 effective n 不足 / long-only 結構劣勢），
下一輪規劃 **5 條改造路線**。

> 📌 **本卡為 roadmap placeholder，實作將於下一輪另開 dashboard project**。
"""
)

roadmap_items = [
    {
        "title": "🗓️ 1. 樣本擴充：60 月 → 2008-2024（17 年）",
        "pain": "**60 月 effective n ≈ 20**（block bootstrap 扣掉時序相關後），第 6 關 Bootstrap CI 過寬、統計檢定力嚴重不足；2008-2019 完全沒覆蓋",
        "plan": "拉到 **2008-2024（17 年）**，涵蓋金融海嘯 / 量化寬鬆 / 升息週期 / 疫情多個 regime",
        "expect": "Block bootstrap CI 寬度縮 ~2x，第 6 關有機會通過；多個 regime 覆蓋也讓結果不再只反映 2020-2024 單一環境",
    },
    {
        "title": "📅 2. 頻率升級：monthly → weekly / daily",
        "pain": "月頻可用 observations 太少；同樣 5 年 daily 等於 ~1250 個獨立資訊 vs monthly 60 個",
        "plan": "改 **weekly 或 daily 再平衡**（trade-off：交易成本上升、turnover 約束更嚴）。\n\n"
                "**附帶機會**：日頻 obs 充足後可評估 **HMM regime detection** (Hamilton 1989 系列) "
                "作為 ADX+SMA 的 **robustness check**——但需考量 OOS 穩定性 + 解釋性 trade-off，"
                "**不取代 rule-based ADX，並行用**",
        "expect": "同樣 5 年樣本 effective n × 4-5，能跑更細的 walk-forward 而不擔心 over-fit；"
                  "HMM 並行可平滑曝險（state probability 取代 binary regime）",
    },
    {
        "title": "⚖️ 3. 架構升級：long-only → L / S market-neutral",
        "pain": "**long-only + monthly 結構劣勢 vs 0050**——2024 大權值股獨舞那種年份，因子怎麼選都追不上 0050",
        "plan": "做多相對強 / 放空相對弱、中性化 β。賺**橫截面 spread**，不依賴市場方向",
        "expect": "假設「相對排序資訊比絕對方向資訊穩」。L / S 至少解掉「market-driven 報酬蓋過 alpha」這個結構問題",
    },
    {
        "title": "🧠 4. 因子擴充（多元增量來源）",
        "pain": "目前 8 因子訊號普遍弱（只 3 個過 p<0.05、8 個 DSR 全 0）；主要資料源也吃光（OHLCV / EPS / 月營收 / 法人 / 融資）",
        "plan": "**兩條增量來源並行**：\n\n"
                "**(a) LLM feature engineering**（目標 2-3 個候選）—— 新聞 / 法說會逐字稿 / 產業情緒文本特徵工程。"
                "**LLM 仍走完整驗證**：PIT-safe（只能吃當天前已公開的文本、防 training data contamination）"
                "+ single-factor IC + DSR + FDR\n\n"
                "**(b) 其他資料來源**（目標 1-2 個候選）—— 選擇權 implied vol skew、上下游供應鏈 momentum、"
                "insider / analyst activity 等跨資料來源新因子",
        "expect": "候選因子池 8 → 12+；**新增來源多元化降低單一方向風險**——不把希望全壓 LLM 一條，"
                  "其他因子也不繞 DSR / FDR",
    },
    {
        "title": "🤖 5. ML 模型升級（呼應 AI 模型應用）",
        "pain": "8 因子目前是 linear weight + 等權，沒利用因子間非線性互動——"
                "例如低波動因子在多頭 / 震盪 / 空頭 regime 下 IC 方向會反轉，"
                "線性模型給固定權重表達不出這個",
        "plan": "**主力 XGBoost (gradient boosted trees)，保留 linear + shrinkage 當 baseline 對照**；"
                "Optuna / 貝氏優化調超參，搭配 **nested time-series walk-forward CV** 防 OOS 洩漏；"
                "用 SHAP 看每個因子的邊際貢獻保持可解釋性；OOS 仍只測一次。\n\n"
                "**LSTM / Transformer 等樣本拉到 weekly / daily 再評估**——"
                "monthly 60 obs 養不起 deep learning，這跟 Roadmap #2 樣本擴充是 prerequisite chain",
        "expect": "抓非線性互動 + regime-aware 差別化權重帶來增量 alpha；"
                  "overfit 風險**三層控制**：nested CV / pre-registered hard gates / OOS-once。"
                  "**評估標準：vs linear baseline 的 alpha lift——沒打贏 baseline 不上線**",
    },
]

for item in roadmap_items:
    with st.container(border=True):
        st.markdown(f"#### {item['title']}")
        col_p, col_l, col_e = st.columns([1, 1.2, 1])
        with col_p:
            st.markdown("**🔴 本輪痛點**")
            st.markdown(item["pain"])
        with col_l:
            st.markdown("**🟡 下輪做法**")
            st.markdown(item["plan"])
        with col_e:
            st.markdown("**🟢 預期改善**")
            st.markdown(item["expect"])

st.divider()

# ===============================================================
# 結尾紀律
# ===============================================================
st.success(
    """
##### 🎯 結尾：所有 roadmap items 仍受同一套驗證紀律約束

- **PIT-safe**（per-row event lag）
- **DSR / FDR** multi-test correction
- **Stationary Block Bootstrap** CI
- **Pre-registered hard gates**（事前鎖、跑完不准回去挑）
- **OOS 只測一次**

**LLM 不是繞 PIT 的捷徑，ML 模型不是繞 DSR 的捷徑。** 結果再漂亮也要過同一套門檻。

→ 這就是本專案「驗證紀律比 edge 重要」的延續，不是下一輪換新工具就丟掉紀律。
"""
)
