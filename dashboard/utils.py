"""Dashboard 共用資料讀取函式 — 研究展示版（讀 reports/ 內研究 evidence）。"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

# ===============================================================
# Path constants（多 page 共用，避免散在各 page hard-code）
# ===============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS = PROJECT_ROOT / "reports"

PHASE_D_RESULTS = REPORTS / "phase_d" / "cell_sweep_v7_2026_05_06"
FACTOR_IC = REPORTS / "factor_ic"
SPRINT_REPRO = REPORTS / "sprint_pro_validation" / "B_repro"
DIAGNOSIS = REPORTS / "diagnosis"
PHASE_D = REPORTS / "phase_d"

FIVE_FACTORS = [
    "high_proximity",
    "pead_eps",
    "revenue_momentum_v2",
    "margin_short_ratio",
    "foreign_investor_v2",
]

# 2026-05-11 補測 Phase D 3 因子 single IC（之前只在 v7 cell sweep aggregate 中露面）
PHASE_D_FACTORS = [
    "quality_v3",
    "industry_momentum",
    "idio_vol_max",
]

ALL_FACTORS = FIVE_FACTORS + PHASE_D_FACTORS

FACTOR_DISPLAY_NAMES = {
    "high_proximity": "52W 高接近度",
    "pead_eps": "PEAD / EPS Surprise",
    "revenue_momentum_v2": "月營收動能 v2",
    "margin_short_ratio": "融資/融券反向",
    # 2026-05-11 rename: "外資 4 子訊號" → "外資法人因子 v2"
    # 「外資法人」對齊 FinMind row name "Foreign_Investor"（QFII / 境外機構投資者）
    # 標準台股中文翻譯；舊「4 子訊號」schema 因 R28 P1-D deprecate consistency
    # 後變誤導（實際 3 active sub-signal）。
    "foreign_investor_v2": "外資法人因子 v2",
    # Phase D 3 因子（2026-05-11 補跑 single IC）
    "quality_v3": "品質",
    "industry_momentum": "產業動量",
    "idio_vol_max": "特質波動+樂透",
}


# ===============================================================
# Phase D v7 6 個 candidates 的策略名稱 + 因子組合對照
# 多個 dashboard page 共用，避免 D-X 代號散落
# ===============================================================
STRATEGY_NAMES = {
    "D-B": "動量+獲利+融資（3 因子）",
    "D-C": "動量+獲利（2 因子）",
    "D-D": "動量+獲利+融資（高融資權重）",
    "D-E": "動量+獲利+品質",
    "D-F": "動量+獲利+產業動量",
    "D-G": "動量+獲利+特質波動",
}

STRATEGY_FACTORS = {
    "D-B": "52W 高 39% + 獲利驚喜 41% + 融資反向 20%",
    "D-C": "52W 高 40% + 獲利驚喜 60%",
    "D-D": "52W 高 34% + 獲利驚喜 36% + 融資反向 30%",
    "D-E": "52W 高 40% + 獲利驚喜 40% + 品質（ROE/毛利/Δ總資產）20%",
    "D-F": "52W 高 40% + 獲利驚喜 40% + 產業動量 20%",
    "D-G": "52W 高 40% + 獲利驚喜 40% + 特質波動+樂透 20%",
}

STRATEGY_LOGIC = {
    "D-B": "買逼近 52 週高且 EPS 驚喜大但融資不過熱的股票",
    "D-C": "純價格動量+基本面驚喜雙因子",
    "D-D": "同 D-B 但放大融資反向權重看是否更穩",
    "D-E": "動量股中挑 ROE/毛利強且總資產不過度膨脹的",
    "D-F": "動量股+EPS 驚喜，但要在當期強勢產業裡",
    "D-G": "動量股+EPS 驚喜，但避開特質波動高+樂透型",
}


def strategy_label(candidate_id: str, top_n: int | None = None) -> str:
    """產出 user-friendly 策略 label。

    candidate_id="D-E", top_n=12 → "動量+獲利+品質 | 12 檔"
    candidate_id="D-E", top_n=None → "動量+獲利+品質"
    """
    name = STRATEGY_NAMES.get(candidate_id, candidate_id)
    if top_n is None:
        return name
    return f"{name} | {top_n} 檔"


# ===============================================================
# Generic helper
# ===============================================================
def _load_json(path: Path) -> dict | list | None:
    """讀 JSON。檔不存在或無法解析回 None（dashboard 用 caller 處理）。"""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_text(path: Path) -> str | None:
    """讀純文字 (markdown)。"""
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# ===============================================================
# Phase D v7 18-cell sweep loaders（頁 1 + 頁 5 用）
# ===============================================================
@st.cache_data(ttl=600)
def load_cell_summary() -> dict | None:
    """Phase D v7 18-cell 結論 (outcome / sole_survivor / cells gates)。"""
    return _load_json(PHASE_D_RESULTS / "cell_summary.json")


@st.cache_data(ttl=600)
def load_cell_metrics() -> dict | None:
    """每個 cell 的 IR / α / TE / max_dd_diff / DSR。"""
    return _load_json(PHASE_D_RESULTS / "cell_metrics.json")


@st.cache_data(ttl=600)
def load_monthly_active_returns() -> dict | None:
    """每個 cell 的 59 月超額報酬序列 (60 rebalance 產 59 forward returns)。"""
    return _load_json(PHASE_D_RESULTS / "cell_monthly_active_returns.json")


@st.cache_data(ttl=600)
def load_bootstrap_ci_lowers() -> dict | None:
    """L6 80% block bootstrap CI lowers (頁 5)。"""
    return _load_json(PHASE_D_RESULTS / "cell_bootstrap_ci_lowers.json")


# ===============================================================
# Phase A1 5 因子 IC loaders（頁 2 用）
# ===============================================================
@st.cache_data(ttl=600)
def load_factor_ic(factor_name: str) -> dict | None:
    """單因子 IC raw（含 overall / by_regime / by_bucket）。"""
    return _load_json(FACTOR_IC / f"{factor_name}_ic.json")


@st.cache_data(ttl=600)
def load_all_factor_ics() -> dict[str, dict]:
    """5 因子 IC raw 總集（factor_name → ic dict）。"""
    out: dict[str, dict] = {}
    for f in FIVE_FACTORS:
        ic = load_factor_ic(f)
        if ic is not None:
            out[f] = ic
    return out


@st.cache_data(ttl=600)
def load_all_eight_factor_ics() -> dict[str, dict]:
    """8 因子 IC raw 總集（Phase A1 5 + Phase D 3）；2026-05-11 補測。"""
    out: dict[str, dict] = {}
    for f in ALL_FACTORS:
        ic = load_factor_ic(f)
        if ic is not None:
            out[f] = ic
    return out


@st.cache_data(ttl=600)
def load_factor_correlation() -> dict | None:
    """5×5 Spearman rank correlation matrix。"""
    return _load_json(FACTOR_IC / "factor_correlation_matrix.json")


@st.cache_data(ttl=600)
def load_phase_a1_summary_md() -> str | None:
    """Phase A1 綜合結論 markdown。"""
    return _load_text(FACTOR_IC / "phase_a1_summary.md")


# ===============================================================
# D1_v2 backtest loaders（頁 3 用）
# ===============================================================
def _d1v2_dir(period: str) -> Path:
    """period: 'is' | 'oos' → backtest dir。"""
    if period == "is":
        return SPRINT_REPRO / "d1v2_is"
    if period == "oos":
        return SPRINT_REPRO / "d1v2_oos"
    raise ValueError(f"period 必須是 'is' 或 'oos'，非 {period}")


def _d1v2_filename_prefix(period: str) -> str:
    """IS = 20200101_20241231 / OOS = 20250101_20251231。"""
    if period == "is":
        return "backtest_20200101_20241231"
    if period == "oos":
        return "backtest_20250101_20251231"
    raise ValueError(f"period 必須是 'is' 或 'oos'，非 {period}")


@st.cache_data(ttl=600)
def load_d1v2_metrics(period: str) -> dict | None:
    """D1_v2 IS/OOS metrics (Sharpe / IR / α / β / TE / Max DD / Total Return)。"""
    prefix = _d1v2_filename_prefix(period)
    return _load_json(_d1v2_dir(period) / f"{prefix}_metrics.json")


@st.cache_data(ttl=600)
def load_d1v2_daily_returns(period: str) -> dict | None:
    """D1_v2 IS/OOS daily returns (portfolio + benchmark dict)。"""
    prefix = _d1v2_filename_prefix(period)
    return _load_json(_d1v2_dir(period) / f"{prefix}_daily_returns.json")


@st.cache_data(ttl=600)
def load_d1v2_snapshots(period: str) -> list | None:
    """D1_v2 IS/OOS 月再平衡 snapshots (60 IS / OOS 視期間而定)。"""
    prefix = _d1v2_filename_prefix(period)
    raw = _load_json(_d1v2_dir(period) / f"{prefix}_snapshots.json")
    return raw if isinstance(raw, list) else None


# ===============================================================
# Diagnosis 揭穿 overfit loaders（頁 4 用）
# ===============================================================
@st.cache_data(ttl=600)
def load_edge_diagnosis_md() -> str | None:
    return _load_text(DIAGNOSIS / "2026-04-16_edge_diagnosis.md")


@st.cache_data(ttl=600)
def load_independent_audit_md() -> str | None:
    return _load_text(DIAGNOSIS / "2026-04-16_independent_audit.md")


@st.cache_data(ttl=600)
def load_audit_results() -> dict[str, dict | None]:
    """5 個 audit script 的 JSON 結果。"""
    audit_dir = DIAGNOSIS / "independent_audit"
    return {
        "rolling_alpha": _load_json(audit_dir / "rolling_alpha.json"),
        "regime_permutation": _load_json(audit_dir / "regime_permutation.json"),
        "friction_oddlot": _load_json(audit_dir / "friction_oddlot.json"),
        "passive_evaluation": _load_json(audit_dir / "passive_evaluation.json"),
        "factor_ic_recomputed": _load_json(audit_dir / "factor_ic_recomputed.json"),
    }


@st.cache_data(ttl=600)
def load_sprint_repro_factor_md(factor_name: str) -> str | None:
    """Pro Sprint 重現版 factor IC 報告 markdown。"""
    return _load_text(SPRINT_REPRO / "factor_ic" / f"{factor_name}_ic.md")


@st.cache_data(ttl=600)
def load_sprint_repro_factor_json(factor_name: str) -> dict | None:
    """Pro Sprint 重現版 factor IC raw JSON。"""
    return _load_json(SPRINT_REPRO / "factor_ic" / f"{factor_name}_ic.json")


# ===============================================================
# A11 L6 CI 對照（頁 5 用）
# ===============================================================
@st.cache_data(ttl=600)
def load_a11_l6_ci_comparison_md() -> str | None:
    return _load_text(PHASE_D / "A11_l6_ci_comparison.md")


# ===============================================================
# Date alignment helper（P1#6 修法 — cell_monthly_active_returns 對齊）
# ===============================================================
@st.cache_data(ttl=600)
def get_monthly_active_return_dates() -> list[str] | None:
    """從 d1v2_is snapshots 取 60 個 rebalance dates，後 59 個對應 cell_monthly_active_returns。

    Returns
    -------
    list of 59 ISO date strings (YYYY-MM)，對應 forward 月超額報酬序列。
    """
    snapshots = load_d1v2_snapshots("is")
    if snapshots is None or len(snapshots) < 60:
        return None
    # 60 rebalance dates → 後 59 個是 forward return 期間結算日
    dates = []
    for snap in snapshots[1:]:  # skip first
        rd = snap.get("rebalance_date", "")
        if isinstance(rd, str) and len(rd) >= 10:
            dates.append(rd[:10])  # 取 YYYY-MM-DD
        else:
            dates.append(str(rd))
    return dates


# ===============================================================
# Display helpers（Codex Q4 修法）
# ===============================================================
def format_sole_survivor(value: object) -> str:
    """sole_survivor 顯示。None → 中文「(無 survivor / CONFIRM-NO-GO)」。"""
    if value is None:
        return "(無 survivor / CONFIRM-NO-GO)"
    return str(value)


def gate_pass_count(gates: dict) -> int:
    """從 gates dict 算過幾關 (0-6)。"""
    keys = [
        "L1_ir_ge_0_20",
        "L2_mean_alpha_ge_0_005",
        "L3_te_in_range",
        "L4_max_dd_diff_le_0_05",
        "L5_a1_active_corr_le_0_50",
        "L6_bootstrap_ci_lower_gt_0",
    ]
    return sum(1 for k in keys if gates.get(k, False))


def gate_color_segment(pass_count: int) -> str:
    """過幾關 → 顏色語意 (給視覺化參考)。0-2 紅 / 3-4 橙 / 5 黃 / 6 綠。"""
    if pass_count >= 6:
        return "green"
    if pass_count >= 5:
        return "yellow"
    if pass_count >= 3:
        return "orange"
    return "red"
