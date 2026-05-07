"""Independent audit: recompute factor IC from OHLCV cache.

Reproduces the price_momentum_raw formula from src/portfolio/tw_stock.py:653
on the FULL analyzed universe (not just factor_detail top-20) for every
rebalance date in the snapshots.

Outputs reports/diagnosis/independent_audit/factor_ic_recomputed.json:
- truncated_top20: IC using ONLY factor_detail (reproduce prior-round script)
- eligible_only: IC using eligible_list (27 avg)
- full_universe: IC using union(eligible_list, rejected_*) ≈ 80 symbols
- bucket stats (annual + half-year) with t-distribution p-values
- Bonferroni / BH-FDR corrected significance for 4 factors

Run inside Docker:
  docker compose run --rm --entrypoint python portfolio-bot \
    reports/diagnosis/independent_audit/audit_ic.py
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd
from scipy import stats as sp_stats

SNAPSHOTS = Path("reports/backtests/backtest_20220101_20251231_snapshots.json")
CACHE_DIR = Path("data/cache/ohlcv")
OUT_PATH = Path("reports/diagnosis/independent_audit/factor_ic_recomputed.json")

MOMENTUM_3M = 63
MOMENTUM_6M = 126
MOMENTUM_12M = 252
SKIP_DAYS = 21

PRICE_CACHE: dict[str, pd.Series | None] = {}


def load_close(symbol: str) -> pd.Series | None:
    if symbol in PRICE_CACHE:
        return PRICE_CACHE[symbol]
    path = CACHE_DIR / f"{symbol}.pkl"
    if not path.exists():
        PRICE_CACHE[symbol] = None
        return None
    df = pd.read_pickle(path)
    if df is None or df.empty or "close" not in df.columns:
        PRICE_CACHE[symbol] = None
        return None
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    close = close.sort_index()
    PRICE_CACHE[symbol] = close
    return close


def period_return(series: pd.Series, periods: int) -> float | None:
    if len(series) <= periods:
        return None
    start = float(series.iloc[-periods - 1])
    end = float(series.iloc[-1])
    if start == 0:
        return None
    return (end / start) - 1.0


def skip_period_return(series: pd.Series, total: int, skip: int) -> float | None:
    if len(series) <= total + skip:
        return None
    start = float(series.iloc[-(total + skip + 1)])
    end = float(series.iloc[-(skip + 1)])
    if start == 0:
        return None
    return (end / start) - 1.0


def weighted_average(pairs: list[tuple[float | None, float]]) -> float | None:
    valid = [(v, w) for v, w in pairs if v is not None]
    if not valid:
        return None
    wsum = sum(w for _, w in valid)
    if wsum == 0:
        return None
    return sum(v * w for v, w in valid) / wsum


def price_momentum_at(symbol: str, as_of: pd.Timestamp) -> float | None:
    """Reproduce src/portfolio/tw_stock.py:653 price_momentum_raw formula."""
    close = load_close(symbol)
    if close is None or close.empty:
        return None
    view = close[close.index <= as_of]
    if len(view) < 275:
        return None
    m3 = period_return(view, MOMENTUM_3M)
    m6 = period_return(view, MOMENTUM_6M)
    m121 = skip_period_return(view, MOMENTUM_12M, SKIP_DAYS)
    return weighted_average([(m3, 0.20), (m6, 0.35), (m121, 0.45)])


def price_on_or_before(symbol: str, date: pd.Timestamp) -> float | None:
    close = load_close(symbol)
    if close is None or close.empty:
        return None
    view = close[close.index <= date]
    if view.empty:
        return None
    return float(view.iloc[-1])


def forward_return(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    p0 = price_on_or_before(symbol, start)
    p1 = price_on_or_before(symbol, end)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def universe_from_snapshot(snap: dict, mode: str) -> list[str]:
    """Return list of symbols for the snapshot under different universe modes."""
    if mode == "truncated_top20":
        return [x.get("symbol") for x in snap.get("factor_detail", []) if x.get("symbol")]
    if mode == "eligible_only":
        return list(snap.get("eligible_list", []))
    if mode == "full_universe":
        syms: set[str] = set(snap.get("eligible_list", []))
        for key in (
            "rejected_not_selected",
            "rejected_by_top_n",
            "rejected_by_turnover",
            "rejected_by_price",
            "rejected_by_history",
            "rejected_by_trend",
            "rejected_by_industry",
        ):
            syms.update(snap.get(key, []))
        return sorted(syms)
    raise ValueError(f"unknown mode {mode}")


def bucket_for(ts: pd.Timestamp) -> str:
    year = ts.year
    half = "H1" if ts.month <= 6 else "H2"
    if year in (2024, 2025):
        return f"{year}-{half}"
    return str(year)


def spearman_ic(factor_vals: list[float], returns: list[float]) -> float | None:
    if len(factor_vals) < 3:
        return None
    s1 = pd.Series(factor_vals).rank(method="average")
    s2 = pd.Series(returns).rank(method="average")
    c = s1.corr(s2, method="pearson")
    return None if pd.isna(c) else float(c)


def bucket_stats(ics: list[float]) -> dict:
    clean = [float(x) for x in ics if x is not None and not pd.isna(x)]
    n = len(clean)
    if n == 0:
        return {"mean_ic": None, "std_ic": None, "ic_ir": None, "t_stat": None, "p_value": None, "n": 0}
    mu = sum(clean) / n
    if n == 1:
        return {"mean_ic": mu, "std_ic": None, "ic_ir": None, "t_stat": None, "p_value": None, "n": 1}
    sd = math.sqrt(sum((v - mu) ** 2 for v in clean) / (n - 1))
    if sd == 0:
        return {"mean_ic": mu, "std_ic": 0.0, "ic_ir": None, "t_stat": None, "p_value": None, "n": n}
    ic_ir = mu / sd
    t_stat = mu / sd * math.sqrt(n)
    p_two = 2 * sp_stats.t.sf(abs(t_stat), df=n - 1)
    return {
        "mean_ic": round(mu, 5),
        "std_ic": round(sd, 5),
        "ic_ir": round(ic_ir, 4),
        "t_stat": round(t_stat, 3),
        "p_value": round(float(p_two), 5),
        "n": n,
    }


def bootstrap_ci(ics: list[float], n_boot: int = 1000, seed: int = 42) -> list:
    clean = [float(x) for x in ics if x is not None and not pd.isna(x)]
    if len(clean) < 3:
        return [None, None]
    rng = random.Random(seed)
    n = len(clean)
    means = []
    for _ in range(n_boot):
        s = [clean[rng.randrange(n)] for _ in range(n)]
        means.append(sum(s) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot)]
    return [round(lo, 5), round(hi, 5)]


def bh_fdr(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg FDR correction. Returns significance flags."""
    m = len(p_values)
    indexed = sorted(enumerate(p_values), key=lambda kv: kv[1])
    significant = [False] * m
    for rank, (orig_i, p) in enumerate(indexed, start=1):
        if p <= alpha * rank / m:
            for k in range(rank):
                significant[indexed[k][0]] = True
    return significant


FACTOR_FIELD = {
    "price_momentum": "price_momentum_raw",
    "revenue_momentum": "revenue_raw",
    "trend_quality": "trend_quality_raw",
    "institutional_flow": "institutional_raw",
}


def compute_truncated_ic(snapshots: list[dict], factor: str) -> dict:
    """IC using ONLY factor_detail top-20 (reproduce prior-round)."""
    raw_field = FACTOR_FIELD[factor]
    per_period = []
    for i in range(len(snapshots) - 1):
        snap = snapshots[i]
        nxt = snapshots[i + 1]
        start = pd.Timestamp(snap["rebalance_date"]).tz_localize(None)
        end = pd.Timestamp(nxt["rebalance_date"]).tz_localize(None)
        rows = []
        for item in snap.get("factor_detail", []):
            sym = item.get("symbol")
            fval = item.get(raw_field)
            if sym is None or fval is None:
                continue
            fr = forward_return(sym, start, end)
            if fr is None:
                continue
            rows.append((float(fval), fr))
        ic = None
        if len(rows) >= 3:
            ic = spearman_ic([r[0] for r in rows], [r[1] for r in rows])
        per_period.append({
            "date": start.strftime("%Y-%m-%d"),
            "bucket": bucket_for(start),
            "n": len(rows),
            "ic": None if ic is None else round(ic, 5),
        })
    return aggregate(per_period)


def compute_recomputed_ic(snapshots: list[dict], factor: str, mode: str) -> dict:
    """Recompute factor values from OHLCV, then IC on the mode-selected universe."""
    if factor != "price_momentum":
        # Only price_momentum has a from-OHLCV-only formula; others need
        # revenue / institutional cache, out of scope for this independent audit
        return {"error": f"recompute not implemented for {factor} in this script"}
    per_period = []
    for i in range(len(snapshots) - 1):
        snap = snapshots[i]
        nxt = snapshots[i + 1]
        start = pd.Timestamp(snap["rebalance_date"]).tz_localize(None)
        end = pd.Timestamp(nxt["rebalance_date"]).tz_localize(None)
        syms = universe_from_snapshot(snap, mode)
        rows = []
        for sym in syms:
            pm = price_momentum_at(sym, start)
            if pm is None:
                continue
            fr = forward_return(sym, start, end)
            if fr is None:
                continue
            rows.append((pm, fr))
        ic = None
        if len(rows) >= 3:
            ic = spearman_ic([r[0] for r in rows], [r[1] for r in rows])
        per_period.append({
            "date": start.strftime("%Y-%m-%d"),
            "bucket": bucket_for(start),
            "n": len(rows),
            "ic": None if ic is None else round(ic, 5),
        })
    return aggregate(per_period)


def aggregate(per_period: list[dict]) -> dict:
    buckets: dict[str, list[float]] = {}
    for p in per_period:
        if p["ic"] is not None:
            buckets.setdefault(p["bucket"], []).append(p["ic"])
    summary = {}
    for b in ["2022", "2023", "2024-H1", "2024-H2", "2025-H1", "2025-H2"]:
        summary[b] = bucket_stats(buckets.get(b, []))
    all_ics = [p["ic"] for p in per_period if p["ic"] is not None]
    summary["all_periods"] = bucket_stats(all_ics)
    summary["all_periods"]["bootstrap_ci_95"] = bootstrap_ci(all_ics)
    summary["_periods"] = per_period
    return summary


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshots = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))
    print(f"Loaded {len(snapshots)} snapshots")

    # Cross-factor table: only price_momentum gets full recompute
    results: dict = {
        "methodology": {
            "formula_ref": "src/portfolio/tw_stock.py:653",
            "formula": "PM_raw = 0.20*mom_3m + 0.35*mom_6m + 0.45*mom_12_1 (auto-reweight on missing)",
            "momentum_periods": [MOMENTUM_3M, MOMENTUM_6M, MOMENTUM_12M],
            "skip_days": SKIP_DAYS,
            "universe_modes": {
                "truncated_top20": "factor_detail only (reproduces prior-round IC)",
                "eligible_only": "eligible_list (avg ~27 symbols)",
                "full_universe": "eligible + all rejected_* (avg ~80 symbols)",
            },
            "p_value_method": "two-tailed t-distribution (df = n - 1)",
            "bootstrap": {"n": 1000, "seed": 42},
        },
    }

    # 1. price_momentum across 3 universes
    print("\n=== price_momentum: 3-level universe comparison ===")
    for mode in ("truncated_top20", "eligible_only", "full_universe"):
        print(f"\n--- mode: {mode} ---")
        if mode == "truncated_top20":
            res = compute_truncated_ic(snapshots, "price_momentum")
        else:
            res = compute_recomputed_ic(snapshots, "price_momentum", mode)
        results.setdefault("price_momentum", {})[mode] = res
        ap = res["all_periods"]
        print(f"all_periods: mean_ic={ap['mean_ic']} n={ap['n']} t={ap['t_stat']} p={ap['p_value']}")
        print(f"bootstrap 95% CI: {ap.get('bootstrap_ci_95')}")

    # 2. Truncated IC for the other 3 factors (reproduce prior-round)
    print("\n=== truncated_top20 IC for other factors (reproduces prior) ===")
    all_pvals: dict[str, float] = {}
    for factor in ("price_momentum", "revenue_momentum", "trend_quality", "institutional_flow"):
        if factor == "price_momentum":
            p = results["price_momentum"]["truncated_top20"]["all_periods"].get("p_value")
        else:
            r = compute_truncated_ic(snapshots, factor)
            results.setdefault(factor, {})["truncated_top20"] = r
            p = r["all_periods"].get("p_value")
        print(f"{factor}: p_value (truncated) = {p}")
        if p is not None:
            all_pvals[factor] = p

    # 3. FDR correction
    if all_pvals:
        names = list(all_pvals.keys())
        pvals = list(all_pvals.values())
        bh_sig = bh_fdr(pvals, alpha=0.05)
        bonf_sig = [p <= 0.05 / len(pvals) for p in pvals]
        results["multiple_testing"] = {
            "alpha": 0.05,
            "n_factors": len(pvals),
            "raw_p_values": dict(zip(names, pvals)),
            "bonferroni_significant": dict(zip(names, bonf_sig)),
            "bh_fdr_significant": dict(zip(names, bh_sig)),
        }
        print("\n=== FDR correction ===")
        for n, p, bo, bh in zip(names, pvals, bonf_sig, bh_sig):
            print(f"  {n}: p={p:.5f} bonferroni_sig={bo} bh_sig={bh}")

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
