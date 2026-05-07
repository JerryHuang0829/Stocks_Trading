"""Compute cross-factor Spearman correlation matrix from Phase A1 IC JSONs.

Phase A2 Step 4-prep: closes the 'correlation matrix skipped' limitation
documented in reports/factor_ic/phase_a1_summary.md. Reads the newly-added
`period_factor_scores` field from each factor's IC JSON, pairs symbols
per period, computes rank correlation, averages across periods.

Usage:
    docker compose run --rm --entrypoint python portfolio-bot \\
        scripts/compute_factor_correlation.py

Writes:
    reports/factor_ic/factor_correlation_matrix.json
    reports/factor_ic/factor_correlation_matrix.md (human-readable table)
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPORTS_DIR = Path("reports/factor_ic")

FACTORS = [
    "high_proximity",
    "pead_eps",
    "margin_short_ratio",
    "revenue_momentum_v2",
    "foreign_broker_v2",
]


def _load_factor_scores(factor_name: str) -> list[dict]:
    path = REPORTS_DIR / f"{factor_name}_ic.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run /factor-ic {factor_name}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    scores = data.get("period_factor_scores")
    if not scores:
        raise ValueError(
            f"{factor_name}: period_factor_scores missing/empty — this IC JSON "
            f"was produced BEFORE Phase A2 Step 4-prep canonical fix. Re-run "
            f"`/factor-ic {factor_name}` to regenerate with the new field."
        )
    return scores


def _period_pairwise_corr(
    a_scores: dict[str, float],
    b_scores: dict[str, float],
) -> tuple[float | None, int]:
    """Spearman correlation between two factors' scores for the same period.

    Only symbols present in both factors contribute. Returns (corr, n) where
    n is the number of symbols used. None if n < 10 (unreliable)."""
    common = sorted(set(a_scores) & set(b_scores))
    if len(common) < 10:
        return None, len(common)
    a = np.asarray([a_scores[s] for s in common], dtype=float)
    b = np.asarray([b_scores[s] for s in common], dtype=float)
    if np.all(a == a[0]) or np.all(b == b[0]):  # constant column
        return None, len(common)
    rho, _p = stats.spearmanr(a, b)
    if pd.isna(rho):
        return None, len(common)
    return float(rho), len(common)


def compute_correlation_matrix() -> dict:
    """Average per-period Spearman correlation across all overlapping periods.

    Returns dict with:
      - matrix: 5x5 dict-of-dict of mean correlation
      - period_counts: how many periods each pair overlapped
      - symbol_counts: avg symbols per period per pair
    """
    per_factor = {f: _load_factor_scores(f) for f in FACTORS}

    # Build date -> scores mapping per factor for easy lookup
    factor_by_date: dict[str, dict[str, dict[str, float]]] = {}
    for f, periods in per_factor.items():
        factor_by_date[f] = {p["rebalance_date"]: p["scores"] for p in periods}

    matrix: dict[str, dict[str, float | None]] = {f: {} for f in FACTORS}
    period_counts: dict[str, dict[str, int]] = {f: {} for f in FACTORS}
    symbol_counts: dict[str, dict[str, float]] = {f: {} for f in FACTORS}

    # Self-correlation = 1.0
    for f in FACTORS:
        matrix[f][f] = 1.0
        period_counts[f][f] = len(factor_by_date[f])
        symbol_counts[f][f] = float(
            np.mean([len(s["scores"]) for s in per_factor[f]]) if per_factor[f] else 0
        )

    # Pairwise
    for a, b in combinations(FACTORS, 2):
        common_dates = sorted(set(factor_by_date[a]) & set(factor_by_date[b]))
        rhos: list[float] = []
        sym_counts: list[int] = []
        for date in common_dates:
            rho, n = _period_pairwise_corr(
                factor_by_date[a][date], factor_by_date[b][date]
            )
            if rho is not None:
                rhos.append(rho)
                sym_counts.append(n)

        if rhos:
            mean_rho = float(np.mean(rhos))
            matrix[a][b] = round(mean_rho, 4)
            matrix[b][a] = round(mean_rho, 4)
            period_counts[a][b] = len(rhos)
            period_counts[b][a] = len(rhos)
            symbol_counts[a][b] = round(float(np.mean(sym_counts)), 1)
            symbol_counts[b][a] = round(float(np.mean(sym_counts)), 1)
        else:
            matrix[a][b] = None
            matrix[b][a] = None
            period_counts[a][b] = 0
            period_counts[b][a] = 0
            symbol_counts[a][b] = 0
            symbol_counts[b][a] = 0

    return {
        "factors": FACTORS,
        "matrix": matrix,
        "period_counts": period_counts,
        "symbol_counts": symbol_counts,
        "method": "per_period_spearman_then_average",
        "notes": (
            "Correlation = average of per-period Spearman ρ across all periods "
            "where both factors have scores for at least 10 common symbols. "
            "High |ρ| (>0.5) means factors pick similar stocks cross-sectionally; "
            "low |ρ| (<0.2) means they are near-independent signals."
        ),
    }


def render_markdown(result: dict) -> str:
    factors = result["factors"]
    matrix = result["matrix"]
    pc = result["period_counts"]

    lines = []
    lines.append("# Phase A1 Factor Correlation Matrix\n")
    lines.append(
        "**Method**: per-period Spearman rank correlation, averaged across "
        "all overlapping periods with ≥10 common symbols.\n"
    )
    lines.append(
        "**產出**：closes `phase_a1_summary.md` 「相關性矩陣 — skip」 known "
        "limitation（Phase A2 Step 4-prep canonical fix 2026-04-21）。\n"
    )
    lines.append("## 5×5 相關係數 (Spearman ρ)\n")

    short_names = {
        "high_proximity": "52W_High",
        "pead_eps": "PEAD_EPS",
        "margin_short_ratio": "Margin_Short",
        "revenue_momentum_v2": "Rev_v2",
        "foreign_broker_v2": "Foreign_v2",
    }

    # Header
    header = "| Factor | " + " | ".join(short_names[f] for f in factors) + " |"
    sep = "|---|" + "---|" * len(factors)
    lines.append(header)
    lines.append(sep)

    for a in factors:
        row = [short_names[a]]
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

    lines.append("\n**標示**：🔴 高相關（|ρ|>0.7，冗餘）/ ⚠️ 中相關（|ρ|>0.5，需注意）\n")

    lines.append("\n## 每對 factor 的採樣期數\n")
    lines.append("| Factor | " + " | ".join(short_names[f] for f in factors) + " |")
    lines.append(sep)
    for a in factors:
        row = [short_names[a]]
        for b in factors:
            row.append(str(pc[a].get(b, 0)))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n## Weight 建議原則\n")
    lines.append("1. **|ρ|>0.7 的兩 factor**：二選一（選 IR 較高者），另者 weight=0 省成本")
    lines.append("2. **|ρ|<0.3 的兩 factor**：可放心同時賦權，diversification 效益明顯")
    lines.append("3. **0.3 ≤ |ρ| ≤ 0.5**：共用權重分配，但總 weight 不宜過集中於這對")
    lines.append("4. **|ρ| > 0.5**：若一定要都用，考慮其中一個給 0.5× 權重以示減量")
    lines.append("\n## 下一步\n")
    lines.append("依此 correlation 矩陣 + 5 factor IR 數據進 Step 4 weight 討論；")
    lines.append("user 決定 config D1-D5 後跑 Step 5 IS/OOS backtest + walk-forward。\n")

    return "\n".join(lines)


def main() -> None:
    result = compute_correlation_matrix()

    json_path = REPORTS_DIR / "factor_correlation_matrix.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Wrote {json_path}")

    md_path = REPORTS_DIR / "factor_correlation_matrix.md"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote {md_path}")

    # Console preview
    print()
    print("=== Correlation Matrix Preview ===")
    factors = result["factors"]
    matrix = result["matrix"]
    short_names = {
        "high_proximity": "52W_High",
        "pead_eps": "PEAD_EPS",
        "margin_short_ratio": "Margin_S",
        "revenue_momentum_v2": "Rev_v2",
        "foreign_broker_v2": "Foreign_v2",
    }
    header = f"{'':<12s} " + " ".join(f"{short_names[f]:>10s}" for f in factors)
    print(header)
    for a in factors:
        row_vals = []
        for b in factors:
            v = matrix[a].get(b)
            row_vals.append("    —    " if v is None else f"{v:+.4f}")
        print(f"{short_names[a]:<12s} " + " ".join(f"{v:>10s}" for v in row_vals))


if __name__ == "__main__":
    main()
