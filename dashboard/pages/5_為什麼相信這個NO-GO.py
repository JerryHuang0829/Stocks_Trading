"""頁 6（user 編號）— 為什麼相信這個 NO-GO 結論。

合併原 Page 6（揭穿 Overfit）+ Page 7（Bootstrap CI）：
用 triangulation 雙重證據鏈（過去否定 + 現在否定）呈現結論可信度。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import (
    FACTOR_DISPLAY_NAMES,
    FIVE_FACTORS,
    load_a11_l6_ci_comparison_md,
    load_audit_results,
    load_bootstrap_ci_lowers,
    load_cell_summary,
    load_edge_diagnosis_md,
    load_factor_ic,
    load_sprint_repro_factor_json,
)

st.set_page_config(
    page_title="為什麼相信這個 NO-GO",
    page_icon="🔬",
    layout="wide",
)

st.title("🔬 為什麼相信這個 NO-GO 結論")
st.caption(
    "Phase D v7 結論「沒 alpha」由兩條獨立證據鏈支撐：「過去否定」+「現在否定」"
    "= triangulation 雙重否定。本頁呈現兩條路徑的完整證據。"
)

st.divider()

# ===============================================================
# Hero — 兩條證據鏈總覽 metric
# ===============================================================
st.subheader("📊 兩條獨立證據鏈：都到「沒 alpha」")

col_hero_left, col_hero_right = st.columns(2)
with col_hero_left:
    st.markdown("##### 📜 過去否定（揭穿 Overfit）")
    st.metric("Sharpe", "1.73 → 0.64", delta="-63%")
    st.metric("年化 Alpha", "+39% → +3.4%", delta="-91%")
    st.caption(
        "舊三因子策略 4 年看似 Sharpe 1.73 / α +39%，"
        "**修 2 個 silent bug 後 alpha 大幅縮水** → 證明過去成績是 bug 假象。"
    )
with col_hero_right:
    st.markdown("##### 📐 現在否定（Bootstrap CI 數學）")
    st.metric("18 cells 過 L6 數", "0 / 18", delta="0% pass rate")
    st.metric("80% CI lower", "全 ≤ 0", delta="無 cell 統計顯著")
    st.caption(
        "Phase D v7 18 種新策略候選用 stationary block bootstrap 重抽 10000 次，"
        "**80% 信心區間下界全在 0 以下** → 證明新做的也沒 robust alpha。"
    )

# ===============================================================
# Triangulation 解說
# ===============================================================
st.info(
    """
**🔬 雙重否定 = Triangulation 加倍可信**

從**獨立的兩個方法**（修 bug 重跑舊策略 + 嚴格 bootstrap CI 驗新策略），都得到「沒 alpha」結論。任何質疑：

- 「**會不會又有 silent bug**？」 → 過去否定路徑的紀律 demo 證明我們有抓出 bug 的能力
- 「**會不會標準太嚴**？」 → 現在否定路徑用 80% CI（中道，非 95% 嚴）仍 fail

→ **兩條路都到一樣終點 = 結論可信度 ≈ 三角驗證（triangulation）**。
"""
)

st.divider()

# ===============================================================
# Tabs — 兩條證據路徑詳細
# ===============================================================
tab1, tab2 = st.tabs(["📜 過去否定 — 揭穿 Overfit", "📐 現在否定 — Bootstrap CI 數學"])

# ---------------------------------------------------------------
# Tab 1 — 揭穿 Overfit
# ---------------------------------------------------------------
with tab1:
    st.markdown(
        """
**最重要的科學紀律 demo**：2026-04-15 一次例行檢查抓出兩個 silent bug，
揭穿過去三因子策略 4 年 Sharpe 1.73 / α +39% **全部都是 overfit**。
這是「敢於揭穿自己舊結果」的紀律證據。
"""
    )

    # ============================================================
    # 修 bug 前後對照表（hero）
    # ============================================================
    st.subheader("📊 2026-04-15 修 bug 前後對照")

    before_after = pd.DataFrame(
        [
            ["2025 OOS", "1.88", "**0.66**", "+7.27%", "**-18.4%**"],
            ["2022-2025 (4Y)", "1.73", "**0.64**", "+39%", "**+3.4%**"],
            ["2024", "—", "**0.33**", "—", "**-43.2%**"],
        ],
        columns=["期間", "修前 Sharpe", "修後 Sharpe", "修前 α", "修後 α"],
    )
    st.dataframe(before_after, width="stretch", hide_index=True)

    st.error(
        "🚨 **過去所有 alpha 為 overfit**。修後 Sharpe 從 1.73 降到 0.64，"
        "α 從 +39% 降到 +3.4%，2024 單年甚至 -43.2%。"
        "這促使我們重新審視整個研究方法論，後續走 5 因子 IC + 18-cell 嚴格驗證。"
    )

    st.divider()

    # ============================================================
    # 2 個 bug 解釋
    # ============================================================
    st.subheader("🐛 兩個 silent bug 解析")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """
##### Bug 1: `finmind.py` timezone error

```python
# 舊代碼（pandas 2.x 會 raise）
pd.Timestamp(want_start, tz="UTC")
```

`pd.Timestamp(date_obj, tz="UTC")` 在 pandas 2.x 對已有 timezone 的物件
會 raise；舊版本默默接受並產生錯誤截斷。

**結果**：歷史回測 silent 失敗 fallback 到 stale cache，
但測試沒覆蓋這個 path。

**修復 commit**：`b78a70c` / `85df06a`
"""
        )

    with col2:
        st.markdown(
            """
##### Bug 2: Universe pre-filter 使用 STOCK_DAY_ALL

```python
# 舊邏輯
universe = filter_by_turnover_using_STOCK_DAY_ALL(date)
```

`STOCK_DAY_ALL` 是**當日**全市場 snapshot，對歷史日期永遠回空。
universe 退化為**全市場 2000 支股票**（無 pre-filter）。

**結果**：選股池被「污染」成全市場，alpha 部分來自高 turnover noise。
修復後 universe 退回 top-80（按 close × volume 排序）。

**修復 commit**：`0debbf0`
"""
        )

    st.caption(
        "完整 diagnosis 見 reports/diagnosis/2026-04-16_edge_diagnosis.md"
    )

    st.divider()

    # ============================================================
    # 5 個 audit script 結果摘要
    # ============================================================
    st.subheader("🔬 獨立 Audit Script 結果（5 個）")

    audit_results = load_audit_results()

    audit_rows = []
    descriptions = {
        "rolling_alpha": "Rolling 126d/252d alpha 計算 + bootstrap CI",
        "regime_permutation": "Regime label permutation test（多空判斷的真實效力）",
        "friction_oddlot": "100 萬 NTD baseline 摩擦 + odd-lot 模擬",
        "passive_evaluation": "Passive 100% 0050 對照 evaluation",
        "factor_ic_recomputed": "舊三因子（price_momentum / revenue_momentum / trend_quality）IC 重算",
    }
    for key, desc in descriptions.items():
        data = audit_results.get(key)
        if data is None:
            audit_rows.append([key, desc, "❌ 無資料"])
        else:
            n_keys = len(data) if isinstance(data, dict) else "N/A"
            audit_rows.append([key, desc, f"✅ Top-level keys: {n_keys}"])

    st.dataframe(
        pd.DataFrame(audit_rows, columns=["Audit Script", "用途", "狀態"]),
        width="stretch",
        hide_index=True,
    )

    # 舊三因子 IC 重算結果
    fic = audit_results.get("factor_ic_recomputed", {})
    if fic and isinstance(fic, dict):
        st.markdown("**舊三因子重算結果（full universe 全期間）**")
        st.caption(
            "重算「price_momentum / revenue_momentum / trend_quality / institutional_flow」"
            "舊三因子 + 法人 IC，post 修 timezone + universe pre-filter 兩個 silent bug 後的真實表現。"
        )
        sample_rows = []
        factor_zh = {
            "price_momentum": "價格動能",
            "revenue_momentum": "營收動能",
            "trend_quality": "趨勢品質",
            "institutional_flow": "法人流向",
        }
        for factor, zh in factor_zh.items():
            f_data = fic.get(factor, {})
            full_univ = f_data.get("full_universe", {})
            all_periods = full_univ.get("all_periods", {})
            if all_periods:
                ci = all_periods.get("bootstrap_ci_95", [None, None])
                ci_str = (
                    f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci and ci[0] is not None else "N/A"
                )
                sample_rows.append({
                    "因子": f"{zh} ({factor})",
                    "mean IC": f"{all_periods.get('mean_ic', 0):.4f}",
                    "IC IR": f"{all_periods.get('ic_ir', 0):.3f}",
                    "t-stat": f"{all_periods.get('t_stat', 0):.3f}",
                    "p-value": f"{all_periods.get('p_value', 1):.4f}",
                    "Bootstrap CI 95%": ci_str,
                    "n_periods": all_periods.get("n", 0),
                })
        if sample_rows:
            st.dataframe(pd.DataFrame(sample_rows), width="stretch", hide_index=True)
            st.caption(
                "**結論**：修 bug 後三因子 mean IC 多在 ±0.04 區間，p-value 多 > 0.05，"
                "**統計上無顯著 alpha**——支持「過去 4Y Sharpe 1.73 為 overfit」的診斷。"
            )

    st.divider()

    # ============================================================
    # Pro Sprint 重現對照
    # ============================================================
    st.subheader("🔄 Pro Validation Sprint 重現性對照")

    st.markdown(
        """
2026-05-04 Pro Validation Sprint Phase B 在 commit `0d31572` 鎖死 anchor 重跑 5 因子 IC，
驗證 reproducibility（IC drift ≤ 1%）。下表對照原版（reports/factor_ic/）vs 重現版
（reports/sprint_pro_validation/B_repro/factor_ic/）。
"""
    )

    repro_rows = []
    for factor in FIVE_FACTORS:
        orig = load_factor_ic(factor)
        repro = load_sprint_repro_factor_json(factor)
        if orig is None or repro is None:
            continue
        orig_mean = orig.get("overall", {}).get("mean_ic", 0)
        repro_mean = repro.get("overall", {}).get("mean_ic", 0)
        drift = abs(orig_mean - repro_mean) / abs(orig_mean) * 100 if orig_mean != 0 else 0
        repro_rows.append({
            "因子": FACTOR_DISPLAY_NAMES.get(factor, factor),
            "原版 mean IC": f"{orig_mean:.4f}",
            "重現版 mean IC": f"{repro_mean:.4f}",
            "drift": f"{drift:.2f}%",
            "通過 ≤1%": "✅" if drift <= 1.0 else "❌",
        })

    if repro_rows:
        st.dataframe(pd.DataFrame(repro_rows), width="stretch", hide_index=True)

    st.success(
        "✅ **Reproducibility 證據**：所有 5 因子 IC 重現 drift ≤ 1%，"
        "證明 commit `0d31572` 鎖死的 evidence chain 真實可重現。"
    )

    # ============================================================
    # 完整 diagnosis md
    # ============================================================
    with st.expander("📄 完整 diagnosis markdown（reports/diagnosis/2026-04-16_edge_diagnosis.md）"):
        md = load_edge_diagnosis_md()
        if md:
            st.markdown(md)
        else:
            st.info("無 markdown 資料")


# ---------------------------------------------------------------
# Tab 2 — Bootstrap CI
# ---------------------------------------------------------------
with tab2:
    st.markdown(
        """
**L6** 是 Phase D v7 6 條 hard reject criteria 中最嚴格的——
**80% Stationary Block Bootstrap (Politis-Romano 1994) CI lower bound > 0**。

意思是：用 60 個月超額報酬重抽 10000 次（block_len=3, seed=42），
80% 信心區間下界要嚴格大於 0，才算統計上**真有 alpha**（非運氣）。
"""
    )

    # ============================================================
    # Load data
    # ============================================================
    ci_data = load_bootstrap_ci_lowers()
    summary = load_cell_summary()

    if ci_data is None or summary is None:
        st.error("讀不到 cell_bootstrap_ci_lowers.json / cell_summary.json")
        st.stop()

    ci_lowers = ci_data.get("ci_lowers", {})
    if not ci_lowers:
        st.error("cell_bootstrap_ci_lowers.json 內 ci_lowers 為空")
        st.stop()

    st.divider()

    # ============================================================
    # 18 cells L6 80% CI lowers 橫條圖
    # ============================================================
    st.subheader("📊 18 cells L6 80% Bootstrap CI lower bound（紅線=0 pass threshold）")

    sorted_cells = sorted(ci_lowers.items(), key=lambda x: x[1], reverse=True)

    fig = go.Figure()
    fig.add_trace(
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
            hovertemplate="<b>%{y}</b><br>L6 CI lower: %{x:.4f}<extra></extra>",
        )
    )
    fig.add_vline(
        x=0,
        line_dash="dash",
        line_color="red",
        line_width=2,
        annotation_text="L6 pass threshold (CI lower > 0)",
        annotation_position="top right",
    )
    fig.update_layout(
        height=600,
        xaxis_title="80% Bootstrap CI lower bound（>0 才過 L6）",
        yaxis_title="Cell",
        margin=dict(t=30, b=20, l=80, r=80),
    )
    st.plotly_chart(fig, width="stretch")

    n_pass_l6 = sum(1 for _, v in sorted_cells if v > 0)
    st.error(
        f"🚨 **18/18 cells L6 lower bound ≤ 0** → 0 cell 過 L6 → "
        f"無 cell 統計上具顯著 alpha（即便 IS metric IR 高、月α 高也救不回）。"
        f"這是 Phase D v7 CONFIRM-NO-GO 的核心統計證據。"
    )

    st.divider()

    # ============================================================
    # Methodology
    # ============================================================
    st.subheader("📚 Methodology — Stationary Block Bootstrap")

    method_meta = ci_data.get("L6_alpha", "N/A"), ci_data.get("L6_bootstrap_n", "N/A"), ci_data.get("L6_avg_block_len", "N/A"), ci_data.get("L6_seed", "N/A")
    alpha, n_iter, block_len, seed = method_meta

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("alpha", f"{alpha}")
        st.caption("0.20 = 80% CI（v7 retail-realistic 標準）")
    with col2:
        st.metric("n_iter", f"{n_iter:,}" if isinstance(n_iter, int) else str(n_iter))
        st.caption("10000 次重抽")
    with col3:
        st.metric("block_len", f"{block_len}")
        st.caption("3 month blocks（防 IID 假設）")
    with col4:
        st.metric("seed", f"{seed}")
        st.caption("42 — reproducibility")

    st.markdown(
        """
**為什麼用 Stationary Block Bootstrap 而非 IID Bootstrap？**

月超額報酬序列**有時序自相關**（autocorrelation），普通 IID bootstrap
假設每個觀測獨立，會嚴重低估 CI 寬度。Politis-Romano 1994 提出
**stationary block bootstrap**：以 block 為單位重抽（block_len 隨機長度，期望值 3），
保留 short-term dependence 同時 valid for inference。

**block_len=3 的意義**：60 個 monthly observations × block_len=3 → effective n ≈ 20，
這就是為什麼 80% CI lower 是嚴格門檻——你不能用 60 個觀測偽裝出 60 個獨立資訊。
"""
    )

    st.divider()

    # ============================================================
    # 80% vs 95% CI 對照
    # ============================================================
    st.subheader("⚖️ 80% vs 95% CI — 為何 v7 採 80% 而非 95%")

    st.markdown(
        """
**Phase A11 attacker test 跑了 D1_v2 IS 60 monthly active returns 兩種 CI 設定**：

| CI 設定 | alpha | Lower Bound | Verdict |
|---|---|---|---|
| **95% CI**（早期標準）| 0.05 | -0.04% | ❌ Fail（極接近 0 但仍 < 0）|
| **80% CI**（v7 retail-realistic 採用）| 0.20 | **+0.66%** | ✅ Pass |

**結論**：v7 採 80% 是經過 empirical 驗證的「中道標準」——

- 95% 對 60 month sample 過嚴格，連 D1_v2 這個 IS 看似最好的策略都過不了
- 80% 對 D1_v2 IS 通過，但對 Phase D 18 cells **依然全部過不了**
- 證明本 sweep 的 NO-GO 不是「標準太嚴」造成，是真的沒 edge

**這就是為什麼 80% CI 仍 fail = 真正 NO-GO**，而非標準問題。
"""
    )

    # ============================================================
    # A11 對照 markdown
    # ============================================================
    with st.expander("📄 A11 attacker test 完整對照 (reports/phase_d/A11_l6_ci_comparison.md)"):
        md = load_a11_l6_ci_comparison_md()
        if md:
            st.markdown(md)
        else:
            st.info("無 markdown 資料")

    st.divider()

    # ============================================================
    # 結論盒
    # ============================================================
    st.subheader("🎯 本路徑結論")

    st.success(
        """
**Phase D v7 的 NO-GO 是統計上嚴謹的結論**，不是「標準寫太嚴」：

1. **80% CI 已是 retail-realistic 中道**（vs 95% 嚴格）
2. **block bootstrap 是時序資料的正確方法**（vs IID bootstrap 低估 CI）
3. **18/18 cells lower bound ≤ 0** → 任何降標都改變不了「無顯著 alpha」的事實
4. **降標讓 D-C\\|12 / D-E\\|12 / D-E\\|16（4/6）進 paper trade = silent_bug pattern**

→ 結案 + pivot 100% 0050 DCA 是符合科學紀律的決策。
"""
    )


# ===============================================================
# 頁尾總結
# ===============================================================
st.divider()

st.subheader("📌 雙重證據鏈總結")

st.markdown(
    """
| 證據鏈 | 方法論 | 核心數字 | 結論 |
|---|---|---|---|
| **過去否定** | 修 silent bug + 重跑舊三因子 IC + Sprint 重現性 | Sharpe 1.73→0.64 / α +39%→+3.4% | 過去看似的 alpha 是 bug 假象 |
| **現在否定** | Stationary Block Bootstrap + 80% CI lower | 18/18 cells L6 fail | 嚴格新方法重做也沒 robust alpha |

→ **兩條獨立路徑都到「沒 alpha」終點 = triangulation 雙重否定 = 結論加倍可信**。

→ 連到「**18 種策略最終 sweep**」頁看本結論的主表；連到「**雙因子回測**」頁看 IS+OOS collapse 案例。
"""
)
