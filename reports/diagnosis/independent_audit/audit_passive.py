"""Independent audit: Task F - independent evaluation of passive alternatives.

Computes Sharpe/MDD/vol/correlation for:
  - 100% 0050
  - 100% 0056
  - 50/40/10 (Smart Beta, as prior-round recommended)
  - Risk parity (vol-inverse) weights
  - Also explore 00878 / 00919 / VOO if cache has them
"""

from __future__ import annotations

import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
from src.backtest.metrics import adjust_splits, adjust_dividends

CACHE_DIR = Path("data/cache/ohlcv")
DIV_CACHE = Path("data/cache/dividends")
OUT_PATH = Path("reports/diagnosis/independent_audit/passive_evaluation.json")

TRADING_DAYS = 252
RISK_FREE_ANNUAL = 0.015

# Period: 2019-2025 (7 years)
START = pd.Timestamp("2019-01-01")
END = pd.Timestamp("2025-12-31")


def load_dividends() -> list:
    """Load the global dividends cache (pickle of list[dict])."""
    path = DIV_CACHE / "_global.pkl"
    if not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"  dividends cache read fail: {e}")
    return []


def load_close(sym: str, dividends: list | None = None) -> pd.Series | None:
    path = CACHE_DIR / f"{sym}.pkl"
    if not path.exists():
        return None
    df = pd.read_pickle(path)
    if df is None or df.empty or "close" not in df.columns:
        return None
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    close = close.sort_index()
    close = adjust_splits(close)
    if dividends:
        close = adjust_dividends(close, dividends, sym)
    return close


def compute_stats(returns: pd.Series, label: str) -> dict:
    """Annualized Sharpe, CAGR, MDD, vol, best/worst year."""
    rf_daily = RISK_FREE_ANNUAL / TRADING_DAYS
    excess = returns - rf_daily
    sharpe = excess.mean() / excess.std(ddof=1) * math.sqrt(TRADING_DAYS) if excess.std(ddof=1) > 0 else 0

    cumret = (1 + returns).cumprod()
    total_return = cumret.iloc[-1] - 1
    years = len(returns) / TRADING_DAYS
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    peak = cumret.cummax()
    dd = (cumret - peak) / peak
    mdd = dd.min()

    vol_ann = returns.std(ddof=1) * math.sqrt(TRADING_DAYS)

    # Best/worst year
    yearly = (1 + returns).resample("YE").prod() - 1
    worst_year = yearly.min()
    best_year = yearly.max()

    # Recovery time (days from worst trough to new high)
    mdd_date = dd.idxmin()
    peak_before = cumret.loc[:mdd_date].idxmax()
    after = cumret.loc[mdd_date:]
    peak_level = cumret.loc[peak_before]
    recovery = after[after >= peak_level]
    recovery_date = recovery.index[0] if len(recovery) > 0 else None
    recovery_days = (recovery_date - mdd_date).days if recovery_date else None

    return {
        "label": label,
        "n_days": len(returns),
        "years": round(years, 2),
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 4),
        "vol_annual": round(vol_ann, 4),
        "mdd": round(mdd, 4),
        "mdd_date": str(mdd_date.date()) if not pd.isna(mdd_date) else None,
        "recovery_days": recovery_days,
        "worst_year": round(worst_year, 4),
        "best_year": round(best_year, 4),
        "yearly_returns": {str(d.year): round(v, 4) for d, v in yearly.items()},
    }


def portfolio_returns(components: dict[str, pd.Series], weights: dict[str, float],
                      rebalance_freq: str = "M") -> pd.Series:
    """Monthly rebalanced portfolio with given weights."""
    df = pd.DataFrame(components)
    df = df.dropna(how="all")

    rets = df.pct_change().dropna()

    # Simple: drift-aware rebalanced monthly
    # Build dollar value: each period reset to weights
    if rebalance_freq == "M":
        period_key = rets.index.to_period("M")
    elif rebalance_freq == "Y":
        period_key = rets.index.to_period("Y")
    else:
        raise ValueError(rebalance_freq)

    portfolio = []
    for pname, group in rets.groupby(period_key):
        dollar = pd.Series(weights, dtype=float)
        for date, row in group.iterrows():
            # Today's return given current dollar allocations
            day_ret = (row * dollar).sum() / dollar.sum() if dollar.sum() > 0 else 0
            # Update dollars
            dollar = dollar * (1 + row)
            portfolio.append((date, day_ret))

    s = pd.Series(dict(portfolio))
    s.index = pd.DatetimeIndex(s.index).normalize()
    return s


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load dividends (global cache)
    divs = load_dividends()
    print(f"Loaded {len(divs)} dividend records")

    # Load ETFs (split + dividend adjusted)
    candidates = ["0050", "0056", "00878", "00919", "00713"]
    etfs: dict[str, pd.Series] = {}
    for s in candidates:
        c = load_close(s, dividends=divs)
        if c is None:
            print(f"  {s}: NOT IN CACHE")
            continue
        c = c[(c.index >= START) & (c.index <= END)]
        if len(c) < 250:
            print(f"  {s}: only {len(c)} days, skipping")
            continue
        etfs[s] = c
        print(f"  {s}: {len(c)} days, {c.index.min().date()} .. {c.index.max().date()}, "
              f"close range {c.min():.2f} .. {c.max():.2f}")

    if "0050" not in etfs or "0056" not in etfs:
        print("ERROR: 0050/0056 missing from cache")
        return

    # Align to common date range
    rets = pd.DataFrame({k: v.pct_change() for k, v in etfs.items()}).dropna(how="any")
    print(f"\nCommon date range: {rets.index.min().date()} .. {rets.index.max().date()} ({len(rets)} days)")

    # 1. 100% 0050
    s_0050 = compute_stats(rets["0050"], "100% 0050")
    # 2. 100% 0056
    s_0056 = compute_stats(rets["0056"], "100% 0056")
    # 3. Smart Beta 50/40/10
    etfs_sb = {"0050": etfs["0050"], "0056": etfs["0056"]}
    w_sb = {"0050": 0.5, "0056": 0.4}  # cash 10% = 0 return
    ret_sb = portfolio_returns(etfs_sb, w_sb)
    # cash drag: 10% cash at 0% return (ignoring rf for simplicity)
    ret_sb = ret_sb * 0.9  # scale by (invested fraction)
    s_sb = compute_stats(ret_sb, "Smart Beta 50/40/10 (prior-round rec)")

    # 4. Risk parity: inverse vol
    vol_0050 = rets["0050"].std()
    vol_0056 = rets["0056"].std()
    inv_total = 1 / vol_0050 + 1 / vol_0056
    w_rp = {"0050": (1 / vol_0050) / inv_total, "0056": (1 / vol_0056) / inv_total}
    ret_rp = portfolio_returns(etfs_sb, w_rp)
    s_rp = compute_stats(ret_rp, f"Risk parity ({w_rp['0050']:.0%}/{w_rp['0056']:.0%})")

    # 5. 60/40
    w_60 = {"0050": 0.6, "0056": 0.4}
    ret_60 = portfolio_returns(etfs_sb, w_60)
    s_60 = compute_stats(ret_60, "60/40 0050/0056")

    # 6. Correlation
    corr = rets[["0050", "0056"]].corr().iloc[0, 1]

    # 7. Also include 00878 if present
    extras = []
    for extra in ("00878", "00919", "00713"):
        if extra in etfs:
            s_extra = compute_stats(rets[extra], f"100% {extra}")
            extras.append(s_extra)

    # 8. Strategy comparison baseline (from 2022-2025 metrics.json)
    #    strategy 4Y: Sharpe 0.638, alpha 3.4%, bench 12% CAGR
    #    2025: Sharpe 0.661, alpha -18.4%, bench 34.2% CAGR
    # For apples-to-apples, let's compute 2022-2025 passive performance
    rets_4y = rets.loc["2022-01-01":"2025-12-31"]
    s_0050_4y = compute_stats(rets_4y["0050"], "100% 0050 (2022-2025)")
    s_0056_4y = compute_stats(rets_4y["0056"], "100% 0056 (2022-2025)")
    ret_sb_4y_full = portfolio_returns({k: etfs[k] for k in ("0050", "0056")},
                                        {"0050": 0.5, "0056": 0.4}) * 0.9
    ret_sb_4y = ret_sb_4y_full.loc["2022-01-01":"2025-12-31"]
    s_sb_4y = compute_stats(ret_sb_4y, "Smart Beta 50/40/10 (2022-2025)")

    # Print summary
    print("\n" + "=" * 95)
    print(f"  Passive alternatives (2019-2025 full period; correlation(0050,0056) = {corr:.4f})")
    print("=" * 95)
    print(f"{'Option':<40}{'CAGR':>8}{'Sharpe':>8}{'VolAnn':>8}{'MDD':>8}{'WorstYr':>10}{'BestYr':>8}")
    print("-" * 95)
    for s in [s_0050, s_0056, s_sb, s_rp, s_60] + extras:
        print(f"{s['label']:<40}{s['cagr']:>+8.2%}{s['sharpe']:>+8.2f}{s['vol_annual']:>8.2%}"
              f"{s['mdd']:>+8.2%}{s['worst_year']:>+10.2%}{s['best_year']:>+8.2%}")

    print(f"\n=== 2022-2025 comparison (match strategy backtest period) ===")
    print(f"{'Option':<40}{'CAGR':>8}{'Sharpe':>8}{'VolAnn':>8}{'MDD':>8}")
    print("-" * 75)
    for s in [s_0050_4y, s_0056_4y, s_sb_4y]:
        print(f"{s['label']:<40}{s['cagr']:>+8.2%}{s['sharpe']:>+8.2f}{s['vol_annual']:>8.2%}"
              f"{s['mdd']:>+8.2%}")
    print(f"{'Strategy (from metrics.json)':<40}{0.1547:>+8.2%}{0.6379:>+8.2f}{'n/a':>8}"
          f"{-0.30:>+8.2%}")
    print("(strategy 2022-2025 CAGR = bench(12.07%) + alpha(3.4%) = 15.47% approx)")

    out = {
        "period_full": "2019-01 .. 2025-12",
        "period_4y": "2022-01 .. 2025-12",
        "correlation_0050_0056": round(float(corr), 4),
        "risk_parity_weights": {k: round(v, 4) for k, v in w_rp.items()},
        "stats_full_period": {
            "0050_100pct": s_0050,
            "0056_100pct": s_0056,
            "smart_beta_50_40_10": s_sb,
            "risk_parity": s_rp,
            "60_40": s_60,
            "extras": extras,
        },
        "stats_2022_2025": {
            "0050_100pct": s_0050_4y,
            "0056_100pct": s_0056_4y,
            "smart_beta_50_40_10": s_sb_4y,
        },
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
