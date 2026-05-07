"""Paper trading performance evaluator.

Reads reports/paper_trading/history.json, fetches actual prices,
and calculates each month's return + cumulative performance.

Only uses official records (is_rerun=false) for evaluation.

Usage:
    docker compose run --rm --entrypoint python portfolio-bot scripts/paper_trade_eval.py
    python scripts/paper_trade_eval.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.data.finmind import FinMindSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

PERF_PATH = Path("reports/paper_trading/history.json")
BENCHMARK = "0050"


def _get_official_records(history: list[dict]) -> list[dict]:
    """從 history 中取出每月的正式紀錄（非 rerun），按 month_key 排序。"""
    seen_months: set[str] = set()
    official: list[dict] = []
    # 按 month_key + run_timestamp 排序，取每月第一筆非 rerun
    sorted_history = sorted(
        history,
        key=lambda x: (x["month_key"], x.get("run_timestamp", "")),
    )
    for rec in sorted_history:
        if rec.get("is_rerun", False):
            continue
        mk = rec["month_key"]
        if mk not in seen_months:
            seen_months.add(mk)
            official.append(rec)
    return official


def _get_price_on(source: FinMindSource, symbol: str, target_date: str) -> float | None:
    """Get closing price on or near target_date."""
    df = source.fetch_ohlcv(symbol, "D", 30)
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index)
    target = pd.Timestamp(target_date)
    # Find closest date <= target
    valid = df[df.index <= target]
    if valid.empty:
        return None
    return float(valid.iloc[-1]["close"])


def main():
    load_dotenv()
    token = os.getenv("FINMIND_TOKEN")
    source = FinMindSource(token=token)

    if not PERF_PATH.exists():
        print("No history.json found. Run paper_trade.py first.")
        sys.exit(1)

    with open(PERF_PATH, "r", encoding="utf-8") as f:
        history = json.load(f)

    official = _get_official_records(history)

    if len(official) < 2:
        total_rerun = sum(1 for h in history if h.get("is_rerun", False))
        print(f"正式紀錄: {len(official)} 筆（另有 {total_rerun} 筆 rerun）")
        print(f"至少需要 2 個月的正式紀錄才能計算報酬。")
        print("請在下個月再平衡後再次執行。")
        sys.exit(0)

    updated = False
    cumulative = 1.0
    benchmark_cum = 1.0

    print("\n" + "=" * 60)
    print("  Paper Trading Performance (Official Records Only)")
    print("=" * 60)

    for i in range(len(official) - 1):
        rec = official[i]
        next_rec = official[i + 1]
        buy_date = rec["date"]
        sell_date = next_rec["date"]

        if rec.get("actual_return") is not None:
            # Already calculated
            cumulative *= (1 + rec["actual_return"])
            if rec.get("benchmark_return") is not None:
                benchmark_cum *= (1 + rec["benchmark_return"])
            src_tag = f" [{rec.get('source', '?')}]"
            print(f"  {rec['month_key']}{src_tag}: 投組 {rec['actual_return']:+.2%}  "
                  f"0050 {rec.get('benchmark_return', 0):+.2%}  "
                  f"(已計算)")
            continue

        # Calculate portfolio return
        port_return = 0.0
        positions = rec.get("positions", [])

        for pos in positions:
            buy_price = _get_price_on(source, pos["symbol"], buy_date)
            sell_price = _get_price_on(source, pos["symbol"], sell_date)
            if buy_price and sell_price and buy_price > 0:
                stock_return = (sell_price - buy_price) / buy_price
                port_return += pos["weight"] * stock_return

        # Benchmark return
        bench_buy = _get_price_on(source, BENCHMARK, buy_date)
        bench_sell = _get_price_on(source, BENCHMARK, sell_date)
        bench_return = 0.0
        if bench_buy and bench_sell and bench_buy > 0:
            bench_return = (bench_sell - bench_buy) / bench_buy

        rec["actual_return"] = round(port_return, 6)
        rec["benchmark_return"] = round(bench_return, 6)
        updated = True

        cumulative *= (1 + port_return)
        benchmark_cum *= (1 + bench_return)

        alpha = port_return - bench_return
        src_tag = f" [{rec.get('source', '?')}]"
        print(f"  {rec['month_key']}{src_tag}: 投組 {port_return:+.2%}  "
              f"0050 {bench_return:+.2%}  "
              f"Alpha {alpha:+.2%}")

    print(f"\n  --- 累積績效 ---")
    print(f"  投組累積報酬:   {cumulative - 1:+.2%}")
    print(f"  0050 累積報酬:  {benchmark_cum - 1:+.2%}")
    if benchmark_cum > 0:
        print(f"  累積 Alpha:     {(cumulative - benchmark_cum) / benchmark_cum:+.2%}")
    print(f"  正式紀錄月數:   {len(official)}")
    print(f"  可計算月數:     {len(official) - 1}")

    # Rerun 統計
    total_rerun = sum(1 for h in history if h.get("is_rerun", False))
    if total_rerun > 0:
        print(f"  Rerun 紀錄:     {total_rerun} 筆（不參與績效計算）")

    # Warn if approaching evaluation threshold
    if len(official) >= 7:
        print(f"\n  ⚠ 已累積 {len(official)} 個月正式紀錄，可進行初步評估")
    elif len(official) >= 4:
        print(f"\n  累積 {len(official)} 個月，再 {7 - len(official)} 個月可初步評估")

    print("=" * 60)

    if updated:
        # 把計算結果寫回 history.json（保留所有紀錄包含 rerun）
        # 找到 official 中被更新的紀錄，回寫到 history
        official_by_key = {
            (r["month_key"], r.get("run_timestamp", "")): r
            for r in official
        }
        for h in history:
            key = (h["month_key"], h.get("run_timestamp", ""))
            if key in official_by_key:
                src = official_by_key[key]
                if src.get("actual_return") is not None and h.get("actual_return") is None:
                    h["actual_return"] = src["actual_return"]
                    h["benchmark_return"] = src.get("benchmark_return")

        with open(PERF_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"\n  已更新: {PERF_PATH}")


if __name__ == "__main__":
    main()
