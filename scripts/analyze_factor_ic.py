"""Generic factor rank IC analyzer.

Reads backtest snapshots, loads cached OHLCV data, and computes per-rebalance
rank IC (Spearman) between any factor's raw score and next-period realized
return. Supports price_momentum / revenue_momentum / trend_quality /
institutional_flow.

Extends the original analyze_institutional_ic.py with:
  - Multi-factor support (selectable via --factor)
  - Annual + half-year buckets covering 2019-2025
  - IC_IR (mean/std × √n), t-stat, p-value
  - Bootstrap 95% CI (1000 samples)
  - Text summary + structured JSON

Usage:
    # Single factor
    python scripts/analyze_factor_ic.py --factor price_momentum --snapshot ... --output ...

    # All factors (4 runs, one JSON each)
    python scripts/analyze_factor_ic.py --all --snapshot ...

PIT safety: return measured from price_on_or_before(start) to
price_on_or_before(end); end is the next rebalance date so no future data
leaks relative to the decision made at start.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

# Factor name → raw field in factor_detail
FACTOR_FIELD_MAP = {
    "price_momentum": "price_momentum_raw",
    "revenue_momentum": "revenue_raw",
    "trend_quality": "trend_quality_raw",
    "institutional_flow": "institutional_raw",
}

ALL_FACTORS = list(FACTOR_FIELD_MAP.keys())

DEFAULT_SNAPSHOT = "reports/backtests/backtest_20220101_20251231_snapshots.json"
DEFAULT_CACHE_DIR = "data/cache/ohlcv"
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42


@dataclass
class PeriodIC:
    rebalance_date: str
    bucket: str
    n_symbols: int
    rank_ic: Optional[float]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generic factor rank IC analyzer")
    parser.add_argument(
        "--factor",
        choices=ALL_FACTORS,
        help="Factor to analyze (default: all four)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run IC analysis on all four factors",
    )
    parser.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT,
        help="Path to backtest snapshots JSON",
    )
    parser.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIR,
        help="OHLCV cache directory",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/factor_ic",
        help="Output directory for <factor>_ic.json files",
    )
    return parser.parse_args()


def _bucket_for(ts: pd.Timestamp) -> str:
    """Annual + half-year buckets for 2019-2025."""
    year = ts.year
    half = "H1" if ts.month <= 6 else "H2"
    if year in (2024, 2025):
        return f"{year}-{half}"
    return str(year)


def _load_close_series(cache_dir: Path, symbol: str) -> Optional[pd.Series]:
    path = cache_dir / f"{symbol}.pkl"
    if not path.exists():
        return None
    df = pd.read_pickle(path)
    if df is None or df.empty or "close" not in df.columns:
        return None
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if close.empty:
        return None
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    return close.sort_index()


def _price_on_or_before(close: pd.Series, as_of: pd.Timestamp) -> Optional[float]:
    view = close[close.index <= as_of]
    if view.empty:
        return None
    value = view.iloc[-1]
    return float(value) if pd.notna(value) else None


def _next_period_return(
    cache_dir: Path,
    symbol: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    memo: dict,
) -> Optional[float]:
    if symbol not in memo:
        memo[symbol] = _load_close_series(cache_dir, symbol)
    close = memo[symbol]
    if close is None or close.empty:
        return None
    start_price = _price_on_or_before(close, start_date)
    end_price = _price_on_or_before(close, end_date)
    if start_price is None or end_price is None or start_price <= 0:
        return None
    return (end_price / start_price) - 1.0


def _spearman_corr(x: pd.Series, y: pd.Series) -> Optional[float]:
    """Spearman correlation without scipy."""
    if len(x) < 3 or len(y) < 3:
        return None
    x_rank = x.rank(method="average")
    y_rank = y.rank(method="average")
    corr = x_rank.corr(y_rank, method="pearson")
    if pd.isna(corr):
        return None
    return float(corr)


def _mean(values: list) -> Optional[float]:
    clean = [float(v) for v in values if v is not None and pd.notna(v)]
    if not clean:
        return None
    return float(sum(clean) / len(clean))


def _stdev(values: list) -> Optional[float]:
    clean = [float(v) for v in values if v is not None and pd.notna(v)]
    if len(clean) < 2:
        return None
    mu = sum(clean) / len(clean)
    var = sum((v - mu) ** 2 for v in clean) / (len(clean) - 1)
    return math.sqrt(var)


def _bucket_stats(ic_values: list) -> dict:
    """Compute mean, std, IR, t-stat, p, n for a bucket of ICs."""
    clean = [float(v) for v in ic_values if v is not None and pd.notna(v)]
    n = len(clean)
    if n == 0:
        return {"mean_ic": None, "std_ic": None, "ic_ir": None, "t_stat": None, "p_value": None, "n": 0}
    mu = sum(clean) / n
    if n == 1:
        return {"mean_ic": mu, "std_ic": None, "ic_ir": None, "t_stat": None, "p_value": None, "n": 1}
    sd = math.sqrt(sum((v - mu) ** 2 for v in clean) / (n - 1))
    if sd == 0:
        return {"mean_ic": mu, "std_ic": 0.0, "ic_ir": None, "t_stat": None, "p_value": None, "n": n}
    # IC_IR = mean/std * sqrt(n) is the convention used for annualized IR
    # Here we use the per-observation IR (mean/std); √n scaling is for t-stat
    ic_ir = mu / sd
    t_stat = (mu / sd) * math.sqrt(n)
    # Two-tailed p-value from t-distribution approximation (n-1 dof)
    # Using normal approx for n>=10; exact would need scipy
    if n >= 10:
        # Normal tail approximation
        z = abs(t_stat)
        p_value = 2 * (1 - _phi(z))
    else:
        p_value = None  # Don't approximate with tiny samples
    return {
        "mean_ic": round(mu, 4),
        "std_ic": round(sd, 4),
        "ic_ir": round(ic_ir, 4),
        "t_stat": round(t_stat, 3),
        "p_value": None if p_value is None else round(p_value, 4),
        "n": n,
    }


def _phi(z: float) -> float:
    """Standard normal CDF via erf approximation (Abramowitz & Stegun 7.1.26)."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _bootstrap_ci(ic_values: list, n_bootstrap: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED) -> list:
    """Bootstrap 95% CI for mean IC."""
    clean = [float(v) for v in ic_values if v is not None and pd.notna(v)]
    if len(clean) < 3:
        return [None, None]
    rng = random.Random(seed)
    means = []
    n = len(clean)
    for _ in range(n_bootstrap):
        sample = [clean[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * n_bootstrap)]
    hi = means[int(0.975 * n_bootstrap)]
    return [round(lo, 4), round(hi, 4)]


def _recommend(summary: dict) -> tuple:
    """Judgment based on recent performance + overall stats."""
    overall = summary.get("all_periods", {})
    mean_ic = overall.get("mean_ic")
    ir = overall.get("ic_ir")
    p = overall.get("p_value")
    ci = overall.get("bootstrap_ci_95", [None, None])

    # Recent 2024-H2 + 2025-H1/H2 focus
    recent_keys = ["2024-H2", "2025-H1", "2025-H2"]
    recent_ics = [summary.get(k, {}).get("mean_ic") for k in recent_keys]
    recent_mean = _mean([v for v in recent_ics if v is not None])

    if mean_ic is None:
        return ("inconclusive", "樣本不足，無法下結論")

    flags = []
    if ir is not None and abs(ir) < 0.5:
        flags.append("IC_IR < 0.5（因子訊號弱）")
    if p is not None and p > 0.1:
        flags.append(f"p-value {p:.3f} > 0.1（統計不顯著）")
    if ci[0] is not None and ci[0] <= 0 <= ci[1]:
        flags.append(f"Bootstrap CI [{ci[0]:.3f}, {ci[1]:.3f}] 跨 0")
    if recent_mean is not None and recent_mean < 0:
        flags.append(f"近 3 個 half-year 平均 IC = {recent_mean:.3f}（負）")

    if mean_ic > 0.05 and ir and ir > 0.5 and recent_mean and recent_mean > 0:
        return ("keep", f"全期 IC {mean_ic:.3f}，IR {ir:.2f}，近期 {recent_mean:.3f}，因子仍有效")
    if mean_ic <= 0 or (recent_mean is not None and recent_mean < -0.05):
        return ("drop_or_replace", f"因子失效警訊：{', '.join(flags) if flags else '全期 IC 非正'}")
    if flags:
        return ("watch", f"邊際因子，建議降權或持續觀察：{', '.join(flags)}")
    return ("keep", f"全期 IC {mean_ic:.3f}，因子仍可用")


def analyze_factor(
    snapshots: list,
    factor_name: str,
    raw_field: str,
    cache_dir: Path,
) -> dict:
    """Compute IC for one factor across all snapshot pairs."""
    price_cache: dict = {}
    period_results: list = []

    for idx in range(len(snapshots) - 1):
        snap = snapshots[idx]
        next_snap = snapshots[idx + 1]
        start_date = pd.Timestamp(snap["rebalance_date"]).tz_localize(None)
        end_date = pd.Timestamp(next_snap["rebalance_date"]).tz_localize(None)
        bucket = _bucket_for(start_date)

        rows = []
        for item in snap.get("factor_detail", []):
            symbol = item.get("symbol")
            if not symbol:
                continue
            factor_val = item.get(raw_field)
            if factor_val is None:
                continue
            future_ret = _next_period_return(cache_dir, symbol, start_date, end_date, price_cache)
            if future_ret is None:
                continue
            rows.append({"symbol": symbol, "factor": float(factor_val), "future_return": future_ret})

        rank_ic = None
        if len(rows) >= 3:
            df = pd.DataFrame(rows)
            rank_ic = _spearman_corr(df["factor"], df["future_return"])

        period_results.append(
            PeriodIC(
                rebalance_date=start_date.strftime("%Y-%m-%d"),
                bucket=bucket,
                n_symbols=len(rows),
                rank_ic=None if rank_ic is None else float(round(rank_ic, 4)),
            )
        )

    # Aggregate by bucket
    buckets: dict = {}
    for p in period_results:
        if p.rank_ic is not None:
            buckets.setdefault(p.bucket, []).append(p.rank_ic)

    all_ics = [p.rank_ic for p in period_results if p.rank_ic is not None]

    # All expected buckets (fill missing with None)
    expected_buckets = []
    for y in range(2019, 2026):
        if y in (2024, 2025):
            expected_buckets.extend([f"{y}-H1", f"{y}-H2"])
        else:
            expected_buckets.append(str(y))

    summary: dict = {}
    for b in expected_buckets:
        summary[b] = _bucket_stats(buckets.get(b, []))
    summary["all_periods"] = _bucket_stats(all_ics)
    summary["all_periods"]["bootstrap_ci_95"] = _bootstrap_ci(all_ics)

    recommendation, rationale = _recommend(summary)

    return {
        "factor": factor_name,
        "raw_field": raw_field,
        "summary": summary,
        "recommendation": recommendation,
        "rationale": rationale,
        "periods": [asdict(p) for p in period_results],
    }


def print_summary(result: dict) -> None:
    print("=" * 60)
    print(f"  Rank IC Analysis: {result['factor']}")
    print("=" * 60)
    s = result["summary"]
    expected = []
    for y in range(2019, 2026):
        if y in (2024, 2025):
            expected.extend([f"{y}-H1", f"{y}-H2"])
        else:
            expected.append(str(y))
    expected.append("all_periods")

    print(f"{'bucket':<14}{'mean_IC':>10}{'IR':>8}{'t':>8}{'p':>8}{'n':>5}")
    print("-" * 60)
    for b in expected:
        stats = s.get(b, {})
        mu = stats.get("mean_ic")
        ir = stats.get("ic_ir")
        t = stats.get("t_stat")
        p = stats.get("p_value")
        n = stats.get("n", 0)
        mu_s = "n/a" if mu is None else f"{mu:+.4f}"
        ir_s = "n/a" if ir is None else f"{ir:+.2f}"
        t_s = "n/a" if t is None else f"{t:+.2f}"
        p_s = "n/a" if p is None else f"{p:.3f}"
        print(f"{b:<14}{mu_s:>10}{ir_s:>8}{t_s:>8}{p_s:>8}{n:>5}")
    print("-" * 60)
    ci = s["all_periods"].get("bootstrap_ci_95", [None, None])
    if ci[0] is not None:
        print(f"Bootstrap 95% CI: [{ci[0]:+.4f}, {ci[1]:+.4f}]")
    print(f"Decision: {result['recommendation']}")
    print(f"Rationale: {result['rationale']}")


def main() -> None:
    args = _parse_args()
    snapshot_path = Path(args.snapshot)
    cache_dir = Path(args.cache_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshots = json.loads(snapshot_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(snapshots)} snapshots from {snapshot_path}")

    factors_to_run = ALL_FACTORS if args.all else ([args.factor] if args.factor else ALL_FACTORS)

    for factor in factors_to_run:
        raw_field = FACTOR_FIELD_MAP[factor]
        result = analyze_factor(snapshots, factor, raw_field, cache_dir)
        result["snapshot"] = str(snapshot_path)
        result["cache_dir"] = str(cache_dir)

        print_summary(result)

        out_path = output_dir / f"{factor}_ic.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved: {out_path}\n")


if __name__ == "__main__":
    main()
