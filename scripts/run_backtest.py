"""CLI entrypoint for running backtests locally or inside Docker."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from src.backtest.engine import BacktestEngine
from src.data.finmind import FinMindSource
from src.utils.config import load_config


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Taiwan stock portfolio backtest")
    parser.add_argument("--config", default="config/settings.yaml", help="Path to YAML config")
    parser.add_argument("--start", required=True, help="Backtest start date in YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Backtest end date in YYYY-MM-DD")
    parser.add_argument("--benchmark", default="0050", help="Benchmark symbol")
    parser.add_argument(
        "--output-dir",
        default="reports/backtests",
        help="Directory for metrics/report artifacts",
    )
    parser.add_argument(
        "--slippage-bps",
        type=int,
        default=10,  # 對齊 settings.yaml:72 (R19 fix 補；舊 default=5 造成 D1_v2 baseline cost 低估 ~17%)
        help="Per-trade slippage assumption in basis points",
    )
    return parser.parse_args()


def _preflight_check(source, benchmark_symbol: str = "0050") -> bool:
    """回測前檢查關鍵 API 是否可用，避免在長時間運算後才 fail-fast。

    Returns True if all critical checks pass, False otherwise.
    """
    print("=" * 50)
    print("  Preflight Check — FinMind API Availability")
    print("=" * 50)
    ok = True

    # 1. TaiwanStockInfo — 必要（universe 建構）
    try:
        df = source.fetch_stock_info()
        if df is not None and not df.empty:
            print(f"  [OK] TaiwanStockInfo: {len(df)} rows")
        else:
            print("  [FAIL] TaiwanStockInfo: empty response")
            ok = False
    except Exception as exc:
        print(f"  [FAIL] TaiwanStockInfo: {exc}")
        ok = False

    # 2. Benchmark OHLCV — 必要（benchmark 比較）
    try:
        df = source.fetch_ohlcv(benchmark_symbol, "D", 10)
        if df is not None and not df.empty:
            print(f"  [OK] Benchmark OHLCV ({benchmark_symbol}): {len(df)} rows")
        else:
            print(f"  [FAIL] Benchmark OHLCV ({benchmark_symbol}): empty response")
            ok = False
    except Exception as exc:
        print(f"  [FAIL] Benchmark OHLCV ({benchmark_symbol}): {exc}")
        ok = False

    # 3. Institutional — 警告（factor 會降級為 0，但不阻斷回測）
    try:
        df = source.fetch_institutional("2330", days=10)
        if df is not None and not df.empty:
            print(f"  [OK] Institutional (2330): {len(df)} rows")
        else:
            print("  [WARN] Institutional (2330): empty — factor scores will be zero")
    except Exception as exc:
        print(f"  [WARN] Institutional (2330): {exc} — factor scores will be zero")

    # 4. Month Revenue — 警告（revenue_momentum 降級為 0）
    try:
        df = source.fetch_month_revenue("2330", months=6)
        if df is not None and not df.empty:
            print(f"  [OK] MonthRevenue (2330): {len(df)} rows")
        else:
            print("  [WARN] MonthRevenue (2330): empty — revenue factor will be zero")
    except Exception as exc:
        print(f"  [WARN] MonthRevenue (2330): {exc} — revenue factor will be zero")

    # 5. Market Value — 監控用（不影響選股，P7 改用 close×volume 排序）
    try:
        df = source.fetch_market_value(days=5)
        if df is not None and not df.empty:
            print(f"  [OK] MarketValue (monitoring): {len(df)} rows")
        else:
            print("  [INFO] MarketValue: empty — not used for selection (monitoring only)")
    except Exception as exc:
        print(f"  [INFO] MarketValue: {exc} — not used for selection (monitoring only)")

    # 6. Delisting — 警告（survivorship bias 風險）
    try:
        df = source.fetch_delisting() if hasattr(source, "fetch_delisting") else None
        if df is not None and not df.empty:
            print(f"  [OK] Delisting: {len(df)} rows")
        else:
            print("  [WARN] Delisting: empty — survivorship bias possible")
    except Exception as exc:
        print(f"  [WARN] Delisting: {exc} — survivorship bias possible")

    print("=" * 50)
    if not ok:
        print("  PREFLIGHT FAILED — FinMind API unavailable.")
        print("  Likely cause: 600 req/hr quota exceeded.")
        print("  Suggestion: wait until the next hour boundary and retry.")
        print("  This run will NOT produce KPI artifacts.")
        print("=" * 50)
    else:
        print("  PREFLIGHT PASSED — proceeding with backtest.")
        print("=" * 50)
    print()
    return ok


def _resolve_token_and_source(benchmark: str) -> FinMindSource:
    """嘗試多個 FinMind token，回傳第一個通過 preflight 的 source。"""
    token_keys = ["FINMIND_TOKEN", "FINMIND_TOKEN2", "FINMIND_TOKEN3"]
    tokens = [(k, os.getenv(k)) for k in token_keys if os.getenv(k)]

    if not tokens:
        print("WARNING: 未設定任何 FINMIND_TOKEN，使用匿名模式（配額極低）")
        source = FinMindSource(token=None, backtest_mode=True)
        if _preflight_check(source, benchmark_symbol=benchmark):
            return source
        raise SystemExit(1)

    for env_key, token in tokens:
        print(f"\n嘗試 {env_key} ...")
        source = FinMindSource(token=token, backtest_mode=True)
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_preflight_check, source, benchmark)
                passed = future.result(timeout=30)
        except concurrent.futures.TimeoutError:
            print(f"{env_key} preflight 超時 (30s)，嘗試下一個 token...\n")
            continue
        if passed:
            print(f"使用 {env_key} 進行回測\n")
            return source
        print(f"{env_key} preflight 失敗，嘗試下一個 token...\n")

    print("所有 token 均 preflight 失敗，請等待下一個配額窗口。")
    raise SystemExit(1)


def main() -> None:
    args = _parse_args()
    load_dotenv()

    config = load_config(args.config)
    source = _resolve_token_and_source(args.benchmark)

    engine = BacktestEngine(source, config, slippage_bps=args.slippage_bps)

    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")
    result = engine.run(start_date, end_date, benchmark_symbol=args.benchmark)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"backtest_{start_date:%Y%m%d}_{end_date:%Y%m%d}"

    metrics_path = output_dir / f"{stem}_metrics.json"
    report_path = output_dir / f"{stem}_report.txt"
    snapshots_path = output_dir / f"{stem}_snapshots.json"

    metrics_path.write_text(
        json.dumps(result.get("metrics", {}), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    report_path.write_text(result.get("report", ""), encoding="utf-8")
    snapshots_path.write_text(
        json.dumps(result.get("monthly_snapshots", []), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # 日頻報酬序列（dashboard 累積報酬曲線用）
    daily_returns_path = output_dir / f"{stem}_daily_returns.json"
    portfolio_rets = result.get("portfolio_returns")
    benchmark_rets = result.get("benchmark_returns")
    daily_data = {}
    if portfolio_rets is not None and not portfolio_rets.empty:
        daily_data["portfolio"] = {
            str(d.date()) if hasattr(d, "date") else str(d): round(float(r), 8)
            for d, r in portfolio_rets.items()
        }
    if benchmark_rets is not None and not benchmark_rets.empty:
        daily_data["benchmark"] = {
            str(d.date()) if hasattr(d, "date") else str(d): round(float(r), 8)
            for d, r in benchmark_rets.items()
        }
    if daily_data:
        daily_returns_path.write_text(
            json.dumps(daily_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(result.get("report", ""))
    print(f"\nSaved metrics to {metrics_path}")
    print(f"Saved report to {report_path}")
    print(f"Saved snapshots to {snapshots_path}")
    if daily_data:
        print(f"Saved daily returns to {daily_returns_path}")


if __name__ == "__main__":
    main()
