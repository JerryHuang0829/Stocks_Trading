"""P4 — 結論 + 下一輪 Roadmap。

頁面結構：
1. 本輪結論：NO-GO 三個失敗點
2. Path to GO Roadmap（5 items）
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
##### 因子訊號弱（輸入端）

**8 因子最強 IC IR 僅 0.33、DSR 全 = 0**

→ 訊號搆不到機構門檻，因子本身就弱
"""
    )

with fail_col2:
    st.error(
        """
##### long-only 月頻架構吃虧（架構端）

**不能放空、只吃得到因子排序的上半截**

→ 月頻 long-only 結構性追不上 0050
"""
    )

with fail_col3:
    st.error(
        """
##### 統計上驗不出顯著（驗證端）

**18 / 18 cells 第 6 關 Bootstrap CI 下界 ≤ 0**

→ 60 月 / effective n≈20，樣本太薄、證不出 edge
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
        "title": "🧠 4. 因子升級：教科書因子 → 自建較不擁擠訊號",
        "pain": "8 因子多數訊號弱（3 個過 p<0.05、DSR 全 0）。**更根本的問題：公開的學術因子"
                "本來就被市場套利** —— McLean & Pontiff (2016) 實證，因子在論文發表後報酬"
                "平均衰減約 58%。只抄教科書 / 開源因子，撿的是別人吃過的。",
        "plan": "從「抄公開因子」轉成「**自建較不擁擠的因子**」，兩條並行：\n\n"
                "**(a) proprietary feature engineering** —— 用 LLM 把公開但非結構化的資料"
                "（新聞 / 法說會逐字稿 / 產業情緒）做成文本因子。\n\n"
                "**(b) 學術 / 他人研究 —— 當起點，不照抄** —— 已發表的訊號（選擇權 implied "
                "vol skew、上下游供應鏈動量、insider / analyst activity 等）可以用，但原始 "
                "construction 就是被套利的擁擠版本；要把它當「假設起點」，改算法 / 換 universe "
                "/ 重新組合成較不擁擠的變體。\n\n"
                "**關鍵紀律：自建因子是 data-snooping 風險最高的一類** —— 驗證標準要"
                "**比現在更嚴**：先寫經濟假設、pre-register、PIT-safe（防文本 contamination），"
                "再跑 single-factor IC + DSR + FDR + OOS。",
        "expect": "因子池從「撿公開因子」升級為「自建、變形」，驗證加嚴後留下的因子"
                  "可信度更高。**評估標準不變 —— 過不了 DSR / FDR，自建的一樣淘汰；"
                  "自建不是繞驗證的捷徑。**",
    },
    {
        "title": "🤖 5. ML 模型升級（呼應 AI 模型應用）",
        "pain": "8 因子目前是 linear weight + 等權，沒利用因子間非線性互動",
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
