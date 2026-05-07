"""Rolling performance diagnostic.

Reads daily returns JSON (both strategy and benchmark), computes rolling
12M / 6M Sharpe, Alpha, MDD over time. Outputs:
  - Structured JSON with rolling stats per month-end
  - Human-readable text summary with edge-erosion timeline

Usage:
    python scripts/rolling_performance.py \
        --input reports/backtests/backtest_20220101_20251231_daily_returns.json \
        --output reports/diagnosis/rolling_performance.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

DEFAULT_INPUT = "reports/backtests/backtest_20220101_20251231_daily_returns.json"
DEFAULT_OUTPUT = "reports/diagnosis/rolling_performance.json"
TRADING_DAYS = 252
RISK_FREE_ANNUAL = 0.015  # TW 10y yield proxy


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--windows", nargs="+", default=["126", "252"],
                        help="Rolling windows in trading days (default: 126=6M, 252=12M)")
    return parser.parse_args()


def _load_returns(path: Path) -> pd.DataFrame:
    """Load daily returns JSON.

    Expected format: {"portfolio": {date_str: ret, ...}, "benchmark": {...}}
    Falls back to list-of-dicts or dict-of-lists formats for forward compat.
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, dict) and "portfolio" in data and "benchmark" in data:
        p = data["portfolio"]
        b = data["benchmark"]
        # Inner may be {date: ret} dict or list
        if isinstance(p, dict):
            port_s = pd.Series(p)
            port_s.index = pd.to_datetime(port_s.index)
            bench_s = pd.Series(b)
            bench_s.index = pd.to_datetime(bench_s.index)
            # Align: only keep dates that exist in BOTH (backtest period)
            df = pd.DataFrame({"strategy": port_s, "benchmark": bench_s}).dropna()
        else:
            df = pd.DataFrame({
                "date": pd.to_datetime(data.get("dates", [])),
                "strategy": p,
                "benchmark": b,
            }).set_index("date")
    elif isinstance(data, list):
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    else:
        raise ValueError(f"Unrecognized daily_returns format at {path}")

    return df.sort_index()


def _rolling_sharpe(returns: pd.Series, window: int) -> pd.Series:
    """Annualized Sharpe over rolling window. Returns are daily simple returns."""
    rf_daily = RISK_FREE_ANNUAL / TRADING_DAYS
    excess = returns - rf_daily
    mean = excess.rolling(window).mean()
    std = excess.rolling(window).std(ddof=1)
    return (mean / std) * math.sqrt(TRADING_DAYS)


def _rolling_alpha(strategy: pd.Series, benchmark: pd.Series, window: int) -> pd.Series:
    """Annualized alpha via rolling OLS (1-factor CAPM)."""
    # Rolling simple diff-of-means approximation for speed:
    # alpha_daily ≈ mean(strategy) - beta * mean(benchmark)
    # beta = cov(s,b) / var(b), all rolling
    cov = strategy.rolling(window).cov(benchmark)
    var_b = benchmark.rolling(window).var()
    beta = cov / var_b
    alpha_daily = strategy.rolling(window).mean() - beta * benchmark.rolling(window).mean()
    # Annualize (approximate)
    return ((1 + alpha_daily) ** TRADING_DAYS - 1)


def _rolling_mdd(returns: pd.Series, window: int) -> pd.Series:
    """Rolling max drawdown (worst peak-to-trough in window)."""
    # Compound within window
    def _mdd(x):
        cumret = (1 + pd.Series(x)).cumprod()
        peak = cumret.cummax()
        dd = (cumret - peak) / peak
        return dd.min()
    return returns.rolling(window).apply(_mdd, raw=True)


def _format_pct(v):
    return "n/a" if pd.isna(v) else f"{v:+.2%}"


def _format_num(v, fmt="+.3f"):
    return "n/a" if pd.isna(v) else f"{v:{fmt}}"


def main():
    args = _parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    windows = [int(w) for w in args.windows]

    print(f"Loading: {input_path}")
    df = _load_returns(input_path)
    print(f"Loaded {len(df)} daily observations from {df.index.min().date()} to {df.index.max().date()}")
    print(f"Columns: {list(df.columns)}")

    # Compute rolling metrics
    results = {}
    for w in windows:
        label = f"{w}d"
        results[label] = {
            "sharpe_strategy": _rolling_sharpe(df["strategy"], w),
            "sharpe_benchmark": _rolling_sharpe(df["benchmark"], w),
            "alpha": _rolling_alpha(df["strategy"], df["benchmark"], w),
            "mdd_strategy": _rolling_mdd(df["strategy"], w),
            "mdd_benchmark": _rolling_mdd(df["benchmark"], w),
        }

    # Sample at month-ends for output
    month_ends = df.resample("ME").last().index

    # Build output
    output = {
        "input": str(input_path),
        "n_observations": len(df),
        "date_range": {
            "start": df.index.min().strftime("%Y-%m-%d"),
            "end": df.index.max().strftime("%Y-%m-%d"),
        },
        "windows": [f"{w}d" for w in windows],
        "monthly_stats": [],
    }

    for me in month_ends:
        row = {"date": me.strftime("%Y-%m-%d")}
        for w in windows:
            label = f"{w}d"
            r = results[label]
            row[label] = {
                "sharpe_strategy": round(float(r["sharpe_strategy"].get(me, float("nan"))), 4) if not pd.isna(r["sharpe_strategy"].get(me, float("nan"))) else None,
                "sharpe_benchmark": round(float(r["sharpe_benchmark"].get(me, float("nan"))), 4) if not pd.isna(r["sharpe_benchmark"].get(me, float("nan"))) else None,
                "alpha": round(float(r["alpha"].get(me, float("nan"))), 4) if not pd.isna(r["alpha"].get(me, float("nan"))) else None,
                "mdd_strategy": round(float(r["mdd_strategy"].get(me, float("nan"))), 4) if not pd.isna(r["mdd_strategy"].get(me, float("nan"))) else None,
                "mdd_benchmark": round(float(r["mdd_benchmark"].get(me, float("nan"))), 4) if not pd.isna(r["mdd_benchmark"].get(me, float("nan"))) else None,
            }
        output["monthly_stats"].append(row)

    # Detect edge erosion timing (first 12M window where Sharpe drops below threshold)
    if 252 in windows:
        s252 = results["252d"]["sharpe_strategy"]
        a252 = results["252d"]["alpha"]
        # First month where rolling 12M Sharpe goes below 0.5
        below_half_sharpe = s252[s252 < 0.5]
        first_weak = below_half_sharpe.index.min() if not below_half_sharpe.empty else None
        # First month where rolling 12M Alpha goes negative
        below_zero_alpha = a252[a252 < 0]
        first_neg_alpha = below_zero_alpha.index.min() if not below_zero_alpha.empty else None

        output["edge_erosion"] = {
            "first_rolling_12M_sharpe_below_0.5": first_weak.strftime("%Y-%m-%d") if first_weak is not None else None,
            "first_rolling_12M_alpha_negative": first_neg_alpha.strftime("%Y-%m-%d") if first_neg_alpha is not None else None,
            "last_rolling_12M_sharpe": round(float(s252.iloc[-1]), 4) if not pd.isna(s252.iloc[-1]) else None,
            "last_rolling_12M_alpha": round(float(a252.iloc[-1]), 4) if not pd.isna(a252.iloc[-1]) else None,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print summary
    print("\n" + "=" * 70)
    print("  Rolling Performance Summary")
    print("=" * 70)

    if 252 in windows:
        ee = output.get("edge_erosion", {})
        print(f"First month rolling 12M Sharpe < 0.5: {ee.get('first_rolling_12M_sharpe_below_0.5', 'never')}")
        print(f"First month rolling 12M Alpha < 0:   {ee.get('first_rolling_12M_alpha_negative', 'never')}")
        print(f"Current rolling 12M Sharpe:          {_format_num(ee.get('last_rolling_12M_sharpe'))}")
        print(f"Current rolling 12M Alpha:           {_format_pct(ee.get('last_rolling_12M_alpha'))}")

    # Print monthly table for last 18 months
    print(f"\nLast 18 months (252d rolling):")
    print(f"{'date':<12}{'Sharpe':>10}{'Alpha':>10}{'Bench Sharpe':>14}{'MDD':>10}")
    print("-" * 56)
    recent = output["monthly_stats"][-18:]
    for row in recent:
        d = row["date"]
        if "252d" in row:
            r = row["252d"]
            s = _format_num(r.get("sharpe_strategy"), "+.2f")
            a = _format_pct(r.get("alpha"))
            sb = _format_num(r.get("sharpe_benchmark"), "+.2f")
            mdd = _format_pct(r.get("mdd_strategy"))
            print(f"{d:<12}{s:>10}{a:>10}{sb:>14}{mdd:>10}")

    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()
