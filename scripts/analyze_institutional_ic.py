"""Analyze the predictive power of institutional_raw using snapshot factor_detail.

This script reads backtest snapshots, loads cached OHLCV data from disk, and
computes per-rebalance rank IC (Spearman correlation) between institutional_raw
and the next-period realized return for the top-20 eligible candidates.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class PeriodIC:
    rebalance_date: str
    bucket: str
    n_symbols: int
    rank_ic: float | None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze institutional_flow rank IC")
    parser.add_argument(
        "--snapshot",
        default="reports/backtests/industry3/backtest_20220101_20241231_snapshots.json",
        help="Path to backtest snapshots JSON",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/cache/ohlcv",
        help="Directory containing cached OHLCV pickle files",
    )
    parser.add_argument(
        "--output",
        default="reports/research/institutional_ic_industry3.json",
        help="Path to write JSON summary",
    )
    return parser.parse_args()


def _bucket_for(ts: pd.Timestamp) -> str:
    if ts.year == 2022:
        return "2022"
    if ts.year == 2023:
        return "2023"
    if ts.year == 2024 and ts.month <= 6:
        return "2024-H1"
    return "2024-H2"


def _load_close_series(cache_dir: Path, symbol: str) -> pd.Series | None:
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


def _price_on_or_before(close: pd.Series, as_of: pd.Timestamp) -> float | None:
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
    memo: dict[str, pd.Series | None],
) -> float | None:
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


def _mean_or_none(values: list[float]) -> float | None:
    clean = [float(v) for v in values if pd.notna(v)]
    if not clean:
        return None
    return float(sum(clean) / len(clean))


def _spearman_corr(x: pd.Series, y: pd.Series) -> float | None:
    """Compute Spearman correlation without requiring scipy."""
    if len(x) < 3 or len(y) < 3:
        return None
    x_rank = x.rank(method="average")
    y_rank = y.rank(method="average")
    corr = x_rank.corr(y_rank, method="pearson")
    if pd.isna(corr):
        return None
    return float(corr)


def _recommend(summary: dict[str, float | None]) -> tuple[str, str]:
    early = _mean_or_none(
        [v for k, v in summary.items() if k in {"2022", "2023"} and v is not None]
    )
    overall = summary.get("all_periods")

    if early is not None and early > 0:
        return ("keep_5pct", "2022-2023 平均 rank IC 為正，保留 institutional_flow 5%")
    if overall is not None and overall <= 0:
        return ("remove_0pct", "全期平均 rank IC 非正，建議移除 institutional_flow")
    if overall is not None and overall > 0:
        return ("keep_5pct", "全期平均 rank IC 為正，但存在時段分化，建議保守保留 5%")
    return ("inconclusive", "可用樣本不足，暫不建議僅憑 IC 變更權重")


def main() -> None:
    args = _parse_args()
    snapshot_path = Path(args.snapshot)
    cache_dir = Path(args.cache_dir)
    output_path = Path(args.output)

    snapshots = json.loads(snapshot_path.read_text(encoding="utf-8"))
    price_cache: dict[str, pd.Series | None] = {}
    period_results: list[PeriodIC] = []

    for idx in range(len(snapshots) - 1):
        snapshot = snapshots[idx]
        next_snapshot = snapshots[idx + 1]
        start_date = pd.Timestamp(snapshot["rebalance_date"]).tz_localize(None)
        end_date = pd.Timestamp(next_snapshot["rebalance_date"]).tz_localize(None)
        bucket = _bucket_for(start_date)

        rows = []
        for item in snapshot.get("factor_detail", []):
            symbol = item.get("symbol")
            if not symbol:
                continue
            inst_raw = item.get("institutional_raw")
            if inst_raw is None:
                continue
            future_ret = _next_period_return(cache_dir, symbol, start_date, end_date, price_cache)
            if future_ret is None:
                continue
            rows.append({"symbol": symbol, "institutional_raw": float(inst_raw), "future_return": future_ret})

        rank_ic = None
        if len(rows) >= 3:
            df = pd.DataFrame(rows)
            rank_ic = _spearman_corr(df["institutional_raw"], df["future_return"])

        period_results.append(
            PeriodIC(
                rebalance_date=start_date.strftime("%Y-%m-%d"),
                bucket=bucket,
                n_symbols=len(rows),
                rank_ic=None if rank_ic is None else float(rank_ic),
            )
        )

    grouped: dict[str, list[float]] = {"2022": [], "2023": [], "2024-H1": [], "2024-H2": []}
    for item in period_results:
        if item.rank_ic is not None:
            grouped.setdefault(item.bucket, []).append(item.rank_ic)

    summary = {
        "2022": _mean_or_none(grouped.get("2022", [])),
        "2023": _mean_or_none(grouped.get("2023", [])),
        "2024-H1": _mean_or_none(grouped.get("2024-H1", [])),
        "2024-H2": _mean_or_none(grouped.get("2024-H2", [])),
        "all_periods": _mean_or_none([p.rank_ic for p in period_results if p.rank_ic is not None]),
        "n_periods": len(period_results),
        "n_valid_periods": len([p for p in period_results if p.rank_ic is not None]),
    }
    recommendation, rationale = _recommend(summary)

    output = {
        "snapshot": str(snapshot_path),
        "cache_dir": str(cache_dir),
        "summary": summary,
        "recommendation": recommendation,
        "rationale": rationale,
        "periods": [p.__dict__ for p in period_results],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 50)
    print("  Institutional Flow Rank IC Analysis")
    print("=" * 50)
    for key in ["2022", "2023", "2024-H1", "2024-H2", "all_periods"]:
        value = summary.get(key)
        print(f"{key:>10}: {'n/a' if value is None else f'{value:.4f}'}")
    print(f"{'valid_periods':>10}: {summary['n_valid_periods']} / {summary['n_periods']}")
    print(f"{'decision':>10}: {recommendation}")
    print(f"{'reason':>10}: {rationale}")
    print(f"{'saved':>10}: {output_path}")


if __name__ == "__main__":
    main()
