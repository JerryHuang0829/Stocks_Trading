"""Rolling Out-of-Sample (OOS) validation framework.

NOTE: This is NOT a true Walk-Forward optimization — the train window is
specified for documentation purposes only. Every test window uses the same
fixed config (settings.yaml) rather than re-fitting parameters on each
train window. The result is a rolling OOS backtest that assesses strategy
robustness across different market regimes.

Splits the full period into rolling train/test windows and runs the
backtest engine on each test window independently. Aggregates OOS
metrics to evaluate consistency.

Usage:
    # Default: 18-month train + 6-month test, 2019-2025
    docker compose run --rm --entrypoint python portfolio-bot scripts/walk_forward.py

    # Custom windows
    docker compose run --rm --entrypoint python portfolio-bot scripts/walk_forward.py \
        --train-months 18 --test-months 6 --start 2019-01-01 --end 2025-12-31

    # Skip preflight (use cached data only)
    docker compose run --rm --entrypoint python portfolio-bot scripts/walk_forward.py --skip-preflight
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from src.backtest.engine import BacktestEngine
from src.data.finmind import FinMindSource
from src.utils.config import load_config


def _generate_windows(
    start: datetime,
    end: datetime,
    train_months: int,
    test_months: int,
    step_months: int | None = None,
) -> list[dict]:
    """Generate rolling train/test windows.

    Each window:
      train: [train_start, train_end)
      test:  [test_start, test_end)

    Windows slide forward by ``step_months`` each step. If None, defaults
    to ``test_months`` (Phase A2 behavior — non-overlapping windows).

    Phase A3.1.3 (2026-04-22): `step_months=1` gives monthly-stride rolling
    windows that overlap, producing ~48 slices for 2019-2025 / 36mo train /
    12mo test — enough statistical power for bootstrap CI vs the previous
    4-slice non-overlapping scheme.
    """
    if step_months is None:
        step_months = test_months

    windows = []
    cursor = start
    idx = 1
    while True:
        train_start = cursor
        train_end = cursor + relativedelta(months=train_months)
        test_start = train_end
        test_end = test_start + relativedelta(months=test_months)

        if test_end > end:
            # 最後一個視窗：test_end 截斷到 end
            test_end = end
            if test_start >= test_end:
                break

        windows.append({
            "window": idx,
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
        })

        cursor += relativedelta(months=step_months)
        idx += 1

        if test_end >= end:
            break

    return windows


def _bootstrap_sharpe_ci(sharpes: list[float], n_bootstrap: int = 10000, ci: float = 0.95) -> dict:
    """Bootstrap confidence interval for mean Sharpe ratio.

    Resamples the window-level Sharpe ratios *n_bootstrap* times and
    returns the *ci* confidence interval.  If the CI includes 0, the
    strategy's outperformance is not statistically significant.
    """
    if len(sharpes) < 3:
        return {"bootstrap_sharpe_ci_lo": None, "bootstrap_sharpe_ci_hi": None,
                "bootstrap_sharpe_significant": None}
    arr = np.array(sharpes)
    rng = np.random.default_rng(42)
    boot_means = np.array([
        rng.choice(arr, size=len(arr), replace=True).mean()
        for _ in range(n_bootstrap)
    ])
    alpha = (1 - ci) / 2
    lo, hi = float(np.percentile(boot_means, alpha * 100)), float(np.percentile(boot_means, (1 - alpha) * 100))
    return {
        "bootstrap_sharpe_ci_lo": round(lo, 4),
        "bootstrap_sharpe_ci_hi": round(hi, 4),
        "bootstrap_sharpe_significant": lo > 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-Forward validation")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--train-months", type=int, default=18, help="Training window (months)")
    parser.add_argument("--test-months", type=int, default=6, help="Test window (months)")
    parser.add_argument(
        "--step-months", type=int, default=None,
        help=(
            "Stride (months) between successive windows. Default: test-months "
            "(Phase A2 non-overlapping). Pass 1 for monthly-stride 48-slice WF "
            "(Phase A3.1.3)."
        ),
    )
    parser.add_argument("--start", default="2019-01-01", help="Earliest train start date")
    parser.add_argument("--end", default="2025-12-31", help="Latest test end date")
    parser.add_argument("--benchmark", default="0050")
    parser.add_argument("--output-dir", default="reports/walk_forward")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()

    load_dotenv()

    config = load_config(args.config)
    token = os.getenv("FINMIND_TOKEN")
    source = FinMindSource(token=token, backtest_mode=True)

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")

    windows = _generate_windows(
        start, end, args.train_months, args.test_months, args.step_months,
    )
    if not windows:
        print("ERROR: No valid windows generated. Check start/end dates.")
        sys.exit(1)

    print("=" * 60)
    print("  Rolling OOS Validation (fixed config, no re-fitting)")
    print("=" * 60)
    print(f"  Train: {args.train_months} months, Test: {args.test_months} months")
    print(f"  Range: {args.start} → {args.end}")
    print(f"  Windows: {len(windows)}")
    print("=" * 60)

    results = []
    for w in windows:
        label = f"W{w['window']}: Test {w['test_start']:%Y-%m} → {w['test_end']:%Y-%m}"
        print(f"\n--- {label} ---")

        try:
            engine = BacktestEngine(source, config)
            result = engine.run(
                w["test_start"], w["test_end"],
                benchmark_symbol=args.benchmark,
            )
            metrics = result.get("metrics", {})

            entry = {
                "window": w["window"],
                "train_start": w["train_start"].strftime("%Y-%m-%d"),
                "train_end": w["train_end"].strftime("%Y-%m-%d"),
                "test_start": w["test_start"].strftime("%Y-%m-%d"),
                "test_end": w["test_end"].strftime("%Y-%m-%d"),
                "sharpe": metrics.get("sharpe_ratio"),
                "annualized_return": metrics.get("annualized_return"),
                "annualized_alpha": metrics.get("annualized_alpha"),
                "max_drawdown": metrics.get("max_drawdown"),
                "annualized_volatility": metrics.get("annualized_volatility"),
                "beta": metrics.get("beta"),
                "n_rebalances": metrics.get("n_rebalances"),
                "data_degraded": metrics.get("data_degraded"),
                "degraded_periods": metrics.get("degraded_periods", 0),
            }
            results.append(entry)

            print(f"  Sharpe: {entry['sharpe']:.2f}  "
                  f"Alpha: {entry['annualized_alpha']:.2%}  "
                  f"MDD: {entry['max_drawdown']:.2%}")

        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({
                "window": w["window"],
                "test_start": w["test_start"].strftime("%Y-%m-%d"),
                "test_end": w["test_end"].strftime("%Y-%m-%d"),
                "error": str(exc),
            })

    # --- 匯總統計 ---
    valid = [r for r in results if "sharpe" in r and r["sharpe"] is not None]

    if valid:
        sharpes = [r["sharpe"] for r in valid]
        alphas = [r["annualized_alpha"] for r in valid if r.get("annualized_alpha") is not None]
        mdds = [r["max_drawdown"] for r in valid if r.get("max_drawdown") is not None]

        summary = {
            "config": {
                "train_months": args.train_months,
                "test_months": args.test_months,
                "start": args.start,
                "end": args.end,
                "total_windows": len(windows),
                "valid_windows": len(valid),
            },
            "aggregate": {
                "mean_sharpe": round(float(np.mean(sharpes)), 4),
                "median_sharpe": round(float(np.median(sharpes)), 4),
                "std_sharpe": round(float(np.std(sharpes)), 4),
                "min_sharpe": round(float(np.min(sharpes)), 4),
                "max_sharpe": round(float(np.max(sharpes)), 4),
                "win_rate": round(sum(1 for s in sharpes if s > 0) / len(sharpes), 4),
                "mean_alpha": round(float(np.mean(alphas)), 4) if alphas else None,
                "worst_mdd": round(float(np.min(mdds)), 4) if mdds else None,
                **_bootstrap_sharpe_ci(sharpes),
            },
            "windows": results,
        }
    else:
        summary = {
            "config": {
                "train_months": args.train_months,
                "test_months": args.test_months,
                "start": args.start,
                "end": args.end,
            },
            "aggregate": {"error": "No valid windows completed"},
            "windows": results,
        }

    # --- 輸出 ---
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "summary.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # 印出摘要
    print("\n" + "=" * 60)
    print("  Walk-Forward Summary")
    print("=" * 60)

    if valid:
        agg = summary["aggregate"]
        print(f"\n  視窗數:       {len(valid)} / {len(windows)}")
        print(f"  平均 Sharpe:  {agg['mean_sharpe']:.2f}")
        print(f"  中位 Sharpe:  {agg['median_sharpe']:.2f}")
        print(f"  Sharpe 範圍:  {agg['min_sharpe']:.2f} ~ {agg['max_sharpe']:.2f}")
        print(f"  勝率:         {agg['win_rate']:.0%}（Sharpe > 0 的比例）")
        if agg.get("mean_alpha") is not None:
            print(f"  平均 Alpha:   {agg['mean_alpha']:.2%}")
        if agg.get("worst_mdd") is not None:
            print(f"  最差 MDD:     {agg['worst_mdd']:.2%}")

        # Bootstrap Sharpe CI
        ci_lo = agg.get("bootstrap_sharpe_ci_lo")
        ci_hi = agg.get("bootstrap_sharpe_ci_hi")
        if ci_lo is not None:
            sig = agg.get("bootstrap_sharpe_significant", False)
            sig_str = "✅ 顯著（CI 不含 0）" if sig else "⚠️ 不顯著（CI 包含 0）"
            print(f"  Bootstrap 95% CI: [{ci_lo:.2f}, {ci_hi:.2f}]  {sig_str}")

        print(f"\n  --- 各視窗明細 ---")
        for r in results:
            if "sharpe" in r and r["sharpe"] is not None:
                print(f"  W{r['window']:2d}: {r['test_start']} → {r['test_end']}  "
                      f"Sharpe {r['sharpe']:+6.2f}  "
                      f"Alpha {r.get('annualized_alpha', 0):+7.2%}  "
                      f"MDD {r.get('max_drawdown', 0):+7.2%}")
            else:
                print(f"  W{r['window']:2d}: {r.get('test_start','?')} → {r.get('test_end','?')}  ERROR: {r.get('error','?')}")

        # 判定
        print(f"\n  --- 判定 ---")
        if agg["mean_sharpe"] >= 0.7 and agg["win_rate"] >= 0.7:
            print("  ✅ 通過：平均 Sharpe ≥ 0.7 且勝率 ≥ 70%")
        elif agg["mean_sharpe"] >= 0.5:
            print("  ⚠️  邊緣：平均 Sharpe 0.5-0.7，需更多數據觀察")
        else:
            print("  ❌ 不通過：平均 Sharpe < 0.5，策略可能在多數市場環境無效")
    else:
        print("\n  ❌ 無有效結果")

    print(f"\n  已儲存: {json_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
