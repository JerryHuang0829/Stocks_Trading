"""Independent audit: Task D components.

1. Regime-conditional IC (price_momentum) for full_universe — using each
   snapshot's market_signal field (already computed by engine).
2. Permutation baseline: random 8-stock selection per rebalance, 1000
   Monte Carlo draws, compare realized strategy Sharpe/return to null.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np
import pandas as pd

SNAPSHOTS = Path("reports/backtests/backtest_20220101_20251231_snapshots.json")
DAILY = Path("reports/backtests/backtest_20220101_20251231_daily_returns.json")
CACHE_DIR = Path("data/cache/ohlcv")
OUT_PATH = Path("reports/diagnosis/independent_audit/regime_permutation.json")

MOMENTUM_3M = 63
MOMENTUM_6M = 126
MOMENTUM_12M = 252
SKIP_DAYS = 21
TRADING_DAYS = 252

PRICE_CACHE: dict[str, pd.Series] = {}


def load_close(sym: str) -> pd.Series | None:
    if sym in PRICE_CACHE:
        return PRICE_CACHE[sym]
    path = CACHE_DIR / f"{sym}.pkl"
    if not path.exists():
        PRICE_CACHE[sym] = None
        return None
    df = pd.read_pickle(path)
    if df is None or df.empty or "close" not in df.columns:
        PRICE_CACHE[sym] = None
        return None
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    close = close.sort_index()
    PRICE_CACHE[sym] = close
    return close


def price_on_or_before(sym: str, date: pd.Timestamp) -> float | None:
    close = load_close(sym)
    if close is None or close.empty:
        return None
    view = close[close.index <= date]
    if view.empty:
        return None
    return float(view.iloc[-1])


def forward_return(sym: str, start, end) -> float | None:
    p0 = price_on_or_before(sym, start)
    p1 = price_on_or_before(sym, end)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def price_momentum_at(sym: str, as_of) -> float | None:
    close = load_close(sym)
    if close is None or close.empty:
        return None
    view = close[close.index <= as_of]
    if len(view) < MOMENTUM_12M + SKIP_DAYS + 1:
        return None
    m3 = None if len(view) <= MOMENTUM_3M else view.iloc[-1] / view.iloc[-MOMENTUM_3M - 1] - 1
    m6 = None if len(view) <= MOMENTUM_6M else view.iloc[-1] / view.iloc[-MOMENTUM_6M - 1] - 1
    m121 = None
    if len(view) > MOMENTUM_12M + SKIP_DAYS:
        m121 = view.iloc[-SKIP_DAYS - 1] / view.iloc[-(MOMENTUM_12M + SKIP_DAYS + 1)] - 1
    parts = [(m3, 0.20), (m6, 0.35), (m121, 0.45)]
    valid = [(v, w) for v, w in parts if v is not None]
    if not valid:
        return None
    wsum = sum(w for _, w in valid)
    return sum(v * w for v, w in valid) / wsum


def spearman_ic(xs, ys) -> float | None:
    if len(xs) < 3:
        return None
    s1 = pd.Series(xs).rank(method="average")
    s2 = pd.Series(ys).rank(method="average")
    c = s1.corr(s2, method="pearson")
    return None if pd.isna(c) else float(c)


def regime_conditional_ic(snapshots: list[dict]) -> dict:
    """IC per regime, using full_universe (~80 symbols)."""
    by_regime: dict[str, list[float]] = {"risk_on": [], "caution": [], "risk_off": []}
    period_log = []
    for i in range(len(snapshots) - 1):
        snap = snapshots[i]
        nxt = snapshots[i + 1]
        start = pd.Timestamp(snap["rebalance_date"]).tz_localize(None)
        end = pd.Timestamp(nxt["rebalance_date"]).tz_localize(None)
        regime = snap.get("market_signal", "caution")
        syms: set[str] = set(snap.get("eligible_list", []))
        for key in ("rejected_by_top_n", "rejected_by_turnover", "rejected_by_price",
                    "rejected_by_history", "rejected_by_trend", "rejected_by_industry"):
            syms.update(snap.get(key, []))
        rows = []
        for s in syms:
            pm = price_momentum_at(s, start)
            if pm is None:
                continue
            fr = forward_return(s, start, end)
            if fr is None:
                continue
            rows.append((pm, fr))
        ic = None
        if len(rows) >= 3:
            ic = spearman_ic([r[0] for r in rows], [r[1] for r in rows])
        if ic is not None:
            by_regime.setdefault(regime, []).append(ic)
        period_log.append({"date": start.strftime("%Y-%m-%d"), "regime": regime,
                           "n": len(rows), "ic": ic})

    summary = {}
    for r, ics in by_regime.items():
        n = len(ics)
        if n == 0:
            summary[r] = {"n": 0, "mean_ic": None, "ic_ir": None}
            continue
        mu = sum(ics) / n
        if n == 1:
            summary[r] = {"n": 1, "mean_ic": round(mu, 5), "ic_ir": None, "t": None, "p": None}
            continue
        sd = math.sqrt(sum((v - mu) ** 2 for v in ics) / (n - 1))
        from scipy import stats as sp_stats
        t_stat = mu / sd * math.sqrt(n) if sd > 0 else None
        p = float(2 * sp_stats.t.sf(abs(t_stat), df=n - 1)) if t_stat is not None else None
        summary[r] = {
            "n": n,
            "mean_ic": round(mu, 5),
            "std_ic": round(sd, 5),
            "ic_ir": round(mu / sd, 4) if sd > 0 else None,
            "t": round(t_stat, 3) if t_stat is not None else None,
            "p": round(p, 5) if p is not None else None,
        }
    return {"by_regime": summary, "periods": period_log}


def permutation_baseline(snapshots: list[dict], daily_path: Path, n_sims: int = 1000,
                         seed: int = 42) -> dict:
    """Compare strategy returns to random 8-stock picks from the universe.

    Each Monte Carlo draw: pick top_n symbols uniformly at random from the
    snapshot's eligible_list (or fall back to full analyzed universe if eligible
    too small) and equally weight them, hold until next rebalance.

    Compare distribution of annualized Sharpe to actual strategy Sharpe.
    """
    daily = json.loads(daily_path.read_text(encoding="utf-8"))
    strat_series = pd.Series(daily["portfolio"])
    strat_series.index = pd.to_datetime(strat_series.index)
    strat_series = strat_series.sort_index()
    strat_daily = strat_series.values.astype(float)
    strat_mean = strat_daily.mean()
    strat_std = strat_daily.std(ddof=1)
    strat_sharpe = strat_mean / strat_std * math.sqrt(TRADING_DAYS) if strat_std > 0 else 0

    # Gross exposure from each snapshot
    rng = random.Random(seed)
    sim_sharpes = []
    sim_returns = []
    top_n = 8

    for sim in range(n_sims):
        daily_sim = []
        for i in range(len(snapshots) - 1):
            snap = snapshots[i]
            nxt = snapshots[i + 1]
            start = pd.Timestamp(snap["rebalance_date"]).tz_localize(None)
            end = pd.Timestamp(nxt["rebalance_date"]).tz_localize(None)
            pool = list(set(snap.get("eligible_list", [])) | set(snap.get("rejected_by_top_n", []))
                         | set(snap.get("rejected_by_trend", [])))
            if len(pool) < top_n:
                continue
            gross = snap.get("gross_exposure", 0.96)
            picks = rng.sample(pool, top_n)
            w = gross / top_n

            # Get daily close for each pick over [start, end]
            sym_returns = []
            for s in picks:
                close = load_close(s)
                if close is None:
                    continue
                seg = close[(close.index >= start) & (close.index <= end)]
                if len(seg) < 2:
                    continue
                ret = seg.pct_change().dropna()
                sym_returns.append(ret)
            if not sym_returns:
                continue
            portfolio_daily = pd.concat(sym_returns, axis=1).mean(axis=1) * w * top_n  # sum of weighted = mean * n * (gross/n)
            # Actually: weight per sym = gross/top_n; sum of weights = gross
            # Portfolio return = Σ w_i r_i = (gross/top_n) Σ r_i = gross * mean(r_i)
            port_daily = pd.concat(sym_returns, axis=1).mean(axis=1) * gross
            daily_sim.append(port_daily)

        if not daily_sim:
            continue
        all_daily = pd.concat(daily_sim)
        if len(all_daily) < 30:
            continue
        m = all_daily.mean()
        s = all_daily.std(ddof=1)
        if s <= 0:
            continue
        sharpe = m / s * math.sqrt(TRADING_DAYS)
        sim_sharpes.append(float(sharpe))
        sim_returns.append(float(((1 + all_daily).prod()) ** (TRADING_DAYS / len(all_daily)) - 1))

    sim_sharpes.sort()
    sim_returns.sort()
    n = len(sim_sharpes)
    pct = sum(1 for s in sim_sharpes if s < strat_sharpe) / n if n > 0 else None
    return {
        "n_sims": n,
        "strategy_sharpe": round(strat_sharpe, 4),
        "random_sharpe_mean": round(sum(sim_sharpes) / n, 4) if n > 0 else None,
        "random_sharpe_median": round(sim_sharpes[n // 2], 4) if n > 0 else None,
        "random_sharpe_p05": round(sim_sharpes[int(0.05 * n)], 4) if n > 0 else None,
        "random_sharpe_p95": round(sim_sharpes[int(0.95 * n)], 4) if n > 0 else None,
        "strategy_pctile_vs_random": round(pct, 3) if pct is not None else None,
    }


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshots = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))
    print(f"Loaded {len(snapshots)} snapshots")

    # Count regimes
    regime_counts = {}
    for s in snapshots:
        r = s.get("market_signal", "unknown")
        regime_counts[r] = regime_counts.get(r, 0) + 1
    print(f"Regime distribution: {regime_counts}")

    print("\n=== Regime-conditional IC (full universe PM) ===")
    rc = regime_conditional_ic(snapshots)
    for r, stats in rc["by_regime"].items():
        print(f"  {r}: {stats}")

    print("\n=== Permutation baseline (random 8-stock picks from eligible) ===")
    print("Running 300 simulations (this is slow)...")
    perm = permutation_baseline(snapshots, DAILY, n_sims=300, seed=42)
    print(f"  Strategy Sharpe:        {perm['strategy_sharpe']}")
    print(f"  Random mean Sharpe:     {perm['random_sharpe_mean']}")
    print(f"  Random median Sharpe:   {perm['random_sharpe_median']}")
    print(f"  Random 5-95% Sharpe:    [{perm['random_sharpe_p05']}, {perm['random_sharpe_p95']}]")
    print(f"  Strategy percentile:    {perm['strategy_pctile_vs_random']} (0.5 = median of random)")

    out = {
        "regime_distribution": regime_counts,
        "regime_conditional_ic": rc,
        "permutation_baseline": perm,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
