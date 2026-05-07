"""Independent audit: precise rolling OLS alpha on daily returns.

Task B: test whether the prior-round rolling alpha (which looks like exact
OLS intercept, not an approximation) matches statsmodels.OLS within each
252-day window. Also verify last window matches 2025 metrics.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

INPUT = Path("reports/backtests/backtest_20220101_20251231_daily_returns.json")
OUT_PATH = Path("reports/diagnosis/independent_audit/rolling_alpha.json")
TRADING_DAYS = 252
RISK_FREE_ANNUAL = 0.015
WINDOW = 252


def load_returns() -> pd.DataFrame:
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    p = pd.Series(data["portfolio"])
    b = pd.Series(data["benchmark"])
    p.index = pd.to_datetime(p.index)
    b.index = pd.to_datetime(b.index)
    df = pd.DataFrame({"strategy": p, "benchmark": b}).dropna().sort_index()
    return df


def precise_ols_alpha(strat: pd.Series, bench: pd.Series) -> dict:
    """Manual OLS: strat = alpha + beta * bench + eps.
    Returns alpha (daily), beta, alpha_t, alpha_p, annualized_alpha (two ways)."""
    y = strat.values.astype(float)
    x = bench.values.astype(float)
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    # OLS closed form
    XtX_inv = np.linalg.inv(X.T @ X)
    beta_vec = XtX_inv @ X.T @ y
    alpha_daily = float(beta_vec[0])
    beta = float(beta_vec[1])
    # Residuals
    resid = y - X @ beta_vec
    sigma2 = float(np.sum(resid ** 2) / (n - 2))
    se_alpha = float(math.sqrt(sigma2 * XtX_inv[0, 0]))
    alpha_t = alpha_daily / se_alpha if se_alpha > 0 else float("nan")
    alpha_p = float(2 * sp_stats.t.sf(abs(alpha_t), df=n - 2)) if se_alpha > 0 else float("nan")
    alpha_ann_simple = alpha_daily * TRADING_DAYS
    alpha_ann_compound = (1 + alpha_daily) ** TRADING_DAYS - 1
    # Excess return version (CAPM Jensen alpha)
    rf_daily = RISK_FREE_ANNUAL / TRADING_DAYS
    y2 = y - rf_daily
    x2 = x - rf_daily
    X2 = np.column_stack([np.ones(n), x2])
    beta_vec2 = np.linalg.inv(X2.T @ X2) @ X2.T @ y2
    alpha_excess_daily = float(beta_vec2[0])
    alpha_excess_ann = alpha_excess_daily * TRADING_DAYS
    return {
        "alpha_daily_raw": alpha_daily,
        "beta": beta,
        "alpha_t": alpha_t,
        "alpha_p": alpha_p,
        "alpha_ann_simple": alpha_ann_simple,
        "alpha_ann_compound": alpha_ann_compound,
        "alpha_excess_ann": alpha_excess_ann,
    }


def approx_prior_alpha(strat: pd.Series, bench: pd.Series) -> float:
    """Reproduce scripts/rolling_performance.py:82-92 formula."""
    cov = np.cov(strat.values, bench.values, ddof=1)[0][1]
    var_b = np.var(bench.values, ddof=1)
    beta = cov / var_b
    alpha_daily = strat.mean() - beta * bench.mean()
    return (1 + alpha_daily) ** TRADING_DAYS - 1


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df = load_returns()
    print(f"Loaded {len(df)} trading days from {df.index.min().date()} to {df.index.max().date()}")

    # 1. Full-period (2022-2025) sanity-check vs metrics.json annualized_alpha
    whole = precise_ols_alpha(df["strategy"], df["benchmark"])
    whole_approx = approx_prior_alpha(df["strategy"], df["benchmark"])
    print(f"\nFull period (2022-01-12 .. 2025-12-xx):")
    print(f"  precise OLS alpha_ann_simple     = {whole['alpha_ann_simple']:+.4%}")
    print(f"  precise OLS alpha_ann_compound   = {whole['alpha_ann_compound']:+.4%}")
    print(f"  precise OLS alpha_excess_ann     = {whole['alpha_excess_ann']:+.4%}")
    print(f"  beta = {whole['beta']:.4f}, t = {whole['alpha_t']:.3f}, p = {whole['alpha_p']:.4f}")
    print(f"  prior-round approx (compound)    = {whole_approx:+.4%}")

    # Published metrics.json value: annualized_alpha = 0.03395 (3.4%)
    print(f"  (metrics.json says annualized_alpha = 0.03395)")

    # 2. Rolling 252d windows, compare precise vs prior approx
    rows = []
    for end in df.index:
        window = df.loc[:end].tail(WINDOW)
        if len(window) < WINDOW:
            continue
        if window.index[-1] != end:
            continue
        precise = precise_ols_alpha(window["strategy"], window["benchmark"])
        approx = approx_prior_alpha(window["strategy"], window["benchmark"])
        rows.append({
            "date": end.strftime("%Y-%m-%d"),
            "precise_alpha_simple": round(precise["alpha_ann_simple"], 6),
            "precise_alpha_compound": round(precise["alpha_ann_compound"], 6),
            "precise_beta": round(precise["beta"], 4),
            "precise_t": round(precise["alpha_t"], 3),
            "precise_p": round(precise["alpha_p"], 5),
            "prior_approx_compound": round(approx, 6),
            "diff_vs_prior": round(precise["alpha_ann_compound"] - approx, 6),
        })

    # Sample month-ends only for output compactness
    rolling_df = pd.DataFrame(rows).set_index(pd.to_datetime(pd.DataFrame(rows)["date"]))
    month_ends = rolling_df.resample("ME").last().reset_index(drop=True).to_dict("records")

    # 3. Last 252d window ending ~2025-12-xx (matches 2025 full-year)
    last = rows[-1]
    print(f"\nLast 252d window ending {last['date']}:")
    print(f"  precise alpha_ann_compound     = {last['precise_alpha_compound']:+.4%}")
    print(f"  precise alpha_ann_simple       = {last['precise_alpha_simple']:+.4%}")
    print(f"  precise beta                   = {last['precise_beta']}")
    print(f"  precise t-stat                 = {last['precise_t']}")
    print(f"  precise p-value                = {last['precise_p']}")
    print(f"  prior-round approx (compound)  = {last['prior_approx_compound']:+.4%}")
    print(f"  diff (precise - approx)        = {last['diff_vs_prior']:+.6f}")
    print(f"  (backtest_20250101_20251231_metrics.json annualized_alpha = -18.44%)")

    # 4. Max diff between precise and prior
    max_diff = max(abs(r["diff_vs_prior"]) for r in rows)
    print(f"\nMax |precise - approx| across all rolling windows: {max_diff:+.6f}")

    # 5. Worst rolling window (lowest alpha) and last 6
    sorted_rows = sorted(rows, key=lambda r: r["precise_alpha_compound"])
    print(f"\nWorst 3 rolling alpha windows (precise compound):")
    for r in sorted_rows[:3]:
        print(f"  {r['date']}: alpha={r['precise_alpha_compound']:+.4%} t={r['precise_t']} p={r['precise_p']}")

    print(f"\nLast 6 month-ends (precise):")
    print(f"{'date':<12}{'precise_alpha':>14}{'prior_approx':>14}{'diff':>10}{'t':>8}{'p':>8}")
    for r in month_ends[-6:]:
        print(f"{r['date']:<12}{r['precise_alpha_compound']:+14.2%}{r['prior_approx_compound']:+14.2%}"
              f"{r['diff_vs_prior']:+10.4f}{r['precise_t']:>8.2f}{r['precise_p']:>8.4f}")

    # 2025-12 specifically
    dec2025 = [r for r in rows if r["date"].startswith("2025-12")]
    if dec2025:
        print(f"\n2025-12 rolling windows:")
        for r in dec2025[-3:]:
            print(f"  {r['date']}: precise={r['precise_alpha_compound']:+.4%} approx={r['prior_approx_compound']:+.4%}")

    out = {
        "input": str(INPUT),
        "window_days": WINDOW,
        "n_rows": len(rows),
        "full_period": whole,
        "full_period_prior_approx_compound": whole_approx,
        "published_metrics_annualized_alpha": 0.03395,
        "last_window": last,
        "worst_3_windows": sorted_rows[:3],
        "monthly_rolling": month_ends,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
