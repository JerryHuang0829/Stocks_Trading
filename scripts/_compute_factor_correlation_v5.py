"""v5.0 7-feature correlation matrix (2026-05-25).

Computes cross-feature Spearman correlation for the locked ML feature set:
  5 raw : idio_vol_max, high_proximity, pead_eps, value_ep, reversal_1m
  2 SN  : value_ep_sn, pead_eps_sn

Uses same per-period method as scripts/compute_factor_correlation.py
(canonical 8-factor v2.x snapshot — kept untouched for audit reference).

This is the v5.0 pre-ML checkpoint: identify pairs with |ρ|>0.7 (redundant,
candidate for dropping) and |ρ|>0.5 (correlated, ML should not double-count).

Output:
    reports/factor_ic/factor_correlation_matrix_v5.json
    reports/factor_ic/factor_correlation_matrix_v5.md
"""
from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPORTS_DIR = Path("reports/factor_ic")

V5_FEATURES = [
    # 5 raw KEEP factors
    "idio_vol_max",
    "high_proximity",
    "pead_eps",
    "value_ep",
    "reversal_1m",
    # 2 sector-neutralized (Option X — additional features, not replacements)
    "value_ep_sn",
    "pead_eps_sn",
]

SHORT_NAMES = {
    "idio_vol_max":   "IdioVol",
    "high_proximity": "52W_High",
    "pead_eps":       "PEAD",
    "value_ep":       "Value_EP",
    "reversal_1m":    "Rev_1m",
    "value_ep_sn":    "Value_SN",
    "pead_eps_sn":    "PEAD_SN",
}


def _load_factor_scores(factor_name: str) -> list[dict]:
    path = REPORTS_DIR / f"{factor_name}_ic.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    scores = data.get("period_factor_scores")
    if not scores:
        raise ValueError(f"{factor_name}: period_factor_scores missing/empty")
    return scores


def _period_pairwise_corr(
    a_scores: dict[str, float], b_scores: dict[str, float],
) -> tuple[float | None, int]:
    common = sorted(set(a_scores) & set(b_scores))
    if len(common) < 10:
        return None, len(common)
    a = np.asarray([a_scores[s] for s in common], dtype=float)
    b = np.asarray([b_scores[s] for s in common], dtype=float)
    if np.all(a == a[0]) or np.all(b == b[0]):
        return None, len(common)
    rho, _p = stats.spearmanr(a, b)
    if pd.isna(rho):
        return None, len(common)
    return float(rho), len(common)


def compute_correlation_matrix() -> dict:
    per_factor = {f: _load_factor_scores(f) for f in V5_FEATURES}
    factor_by_date = {
        f: {p["rebalance_date"]: p["scores"] for p in periods}
        for f, periods in per_factor.items()
    }

    matrix: dict[str, dict[str, float | None]] = {f: {} for f in V5_FEATURES}
    period_counts: dict[str, dict[str, int]] = {f: {} for f in V5_FEATURES}
    symbol_counts: dict[str, dict[str, float]] = {f: {} for f in V5_FEATURES}

    for f in V5_FEATURES:
        matrix[f][f] = 1.0
        period_counts[f][f] = len(factor_by_date[f])
        symbol_counts[f][f] = float(
            np.mean([len(s["scores"]) for s in per_factor[f]]) if per_factor[f] else 0
        )

    for a, b in combinations(V5_FEATURES, 2):
        common_dates = sorted(set(factor_by_date[a]) & set(factor_by_date[b]))
        rhos, sym_counts = [], []
        for date in common_dates:
            rho, n = _period_pairwise_corr(
                factor_by_date[a][date], factor_by_date[b][date]
            )
            if rho is not None:
                rhos.append(rho)
                sym_counts.append(n)
        if rhos:
            mean_rho = float(np.mean(rhos))
            matrix[a][b] = matrix[b][a] = round(mean_rho, 4)
            period_counts[a][b] = period_counts[b][a] = len(rhos)
            symbol_counts[a][b] = symbol_counts[b][a] = round(float(np.mean(sym_counts)), 1)
        else:
            matrix[a][b] = matrix[b][a] = None
            period_counts[a][b] = period_counts[b][a] = 0
            symbol_counts[a][b] = symbol_counts[b][a] = 0

    return {
        "factors": V5_FEATURES,
        "matrix": matrix,
        "period_counts": period_counts,
        "symbol_counts": symbol_counts,
        "method": "per_period_spearman_then_average",
        "version": "v5.0_ml_feature_set",
        "notes": (
            "v5.0 ML feature checkpoint. Same method as 8-factor v2.x snapshot. "
            "Identify redundant pairs (|ρ|>0.7) and correlated pairs (|ρ|>0.5) "
            "before locking pre-registration."
        ),
    }


def render_markdown(result: dict) -> str:
    factors = result["factors"]
    matrix = result["matrix"]
    pc = result["period_counts"]
    sc = result["symbol_counts"]

    lines = []
    lines.append("# v5.0 ML Feature Correlation Matrix（7 features）\n")
    lines.append("**Date**: 2026-05-25\n")
    lines.append("**Method**: per-period Spearman ρ, averaged across periods (≥10 common symbols).\n")
    lines.append(
        "**Purpose**: v5.0 ML pre-registration checkpoint — verify 5 raw + 2 SN "
        "features are not redundant before feeding into XGBoost / LambdaMART / "
        "Optuna nested CV.\n"
    )

    lines.append("## 7×7 Correlation (Spearman ρ)\n")
    header = "| Feature | " + " | ".join(SHORT_NAMES[f] for f in factors) + " |"
    sep = "|---|" + "---|" * len(factors)
    lines.append(header)
    lines.append(sep)
    for a in factors:
        row = [SHORT_NAMES[a]]
        for b in factors:
            val = matrix[a].get(b)
            if val is None:
                row.append("—")
            elif a == b:
                row.append("**1.00**")
            else:
                marker = " 🔴" if abs(val) > 0.7 else (" ⚠️" if abs(val) > 0.5 else "")
                row.append(f"{val:+.3f}{marker}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("\n**Legend**:🔴 |ρ|>0.7 redundant(consider drop) / ⚠️ |ρ|>0.5 correlated\n")

    lines.append("\n## Period overlap (n periods used per pair)\n")
    lines.append(header)
    lines.append(sep)
    for a in factors:
        row = [SHORT_NAMES[a]]
        for b in factors:
            row.append(str(pc[a].get(b, 0)))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n## Symbol overlap (avg symbols per period per pair)\n")
    lines.append(header)
    lines.append(sep)
    for a in factors:
        row = [SHORT_NAMES[a]]
        for b in factors:
            v = sc[a].get(b, 0)
            row.append(f"{v:.0f}" if v else "—")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n## Pairs of interest（high correlation）\n")
    flagged = []
    for a, b in combinations(factors, 2):
        v = matrix[a].get(b)
        if v is not None and abs(v) > 0.5:
            flagged.append((a, b, v))
    if not flagged:
        lines.append("**No pair |ρ|>0.5 — all 7 features sufficiently independent.** ✅")
    else:
        flagged.sort(key=lambda x: -abs(x[2]))
        lines.append("| Pair | ρ | Severity |")
        lines.append("|---|---:|---|")
        for a, b, v in flagged:
            sev = "🔴 redundant" if abs(v) > 0.7 else "⚠️ correlated"
            lines.append(f"| {SHORT_NAMES[a]} × {SHORT_NAMES[b]} | {v:+.3f} | {sev} |")

    lines.append("\n## ML interpretation\n")
    lines.append("- |ρ|>0.7 → ML 會難分辨,SHAP importance 失真;考慮 drop 一個")
    lines.append("- 0.5<|ρ|<0.7 → ML 可處理但 regularization 要注意")
    lines.append("- |ρ|<0.3 → 高度互補,加進 feature set 有 diversification 效益")
    lines.append("- raw vs SN(自我配對)→ 預期中度正相關(SN 是 raw 的清洗版)")

    lines.append("\n## Pre-registration checkpoint\n")
    lines.append("依此矩陣 + 7 因子 IC 數據 → user review:")
    lines.append("1. 確認 7 個 feature 全留(無 drop)")
    lines.append("2. 或建議 drop 哪些 → 更新 plan + pre-registration")
    lines.append("3. sign-off 後進 Step 3:撰寫 `H_v5_0_ml_oddlot_preregistration.md`")

    return "\n".join(lines)


def main() -> None:
    result = compute_correlation_matrix()

    json_path = REPORTS_DIR / "factor_correlation_matrix_v5.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Wrote {json_path}")

    md_path = REPORTS_DIR / "factor_correlation_matrix_v5.md"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote {md_path}")

    print()
    print("=== v5.0 7-Feature Correlation Matrix ===")
    factors = result["factors"]
    matrix = result["matrix"]
    header = f"{'':<10s} " + " ".join(f"{SHORT_NAMES[f]:>9s}" for f in factors)
    print(header)
    for a in factors:
        row_vals = []
        for b in factors:
            v = matrix[a].get(b)
            row_vals.append("    —   " if v is None else f"{v:+.4f}")
        print(f"{SHORT_NAMES[a]:<10s} " + " ".join(f"{v:>9s}" for v in row_vals))


if __name__ == "__main__":
    main()
