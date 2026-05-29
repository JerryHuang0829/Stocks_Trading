"""Gross Profitability factor IC research (v5.0 Candidate 2).

Novy-Marx (2013) GP/Assets quality factor. Bypasses run_factor_ic.py
because GP needs two caches (quarterly_financial_full + balance_sheet)
that aren't first-class panel_types in the dispatcher yet.

Same Pro methodology as run_factor_ic.py:
  - intersection universe across populated factor caches
  - Spearman IC per period
  - stationary block bootstrap (Politis-Romano 1994)
  - DSR (Bailey-LdP 2014)
  - permutation test
  - regime classification (ranging / trending_up / trending_down)

Output: reports/factor_ic/gross_profitability_ic.json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from datetime import datetime

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from scripts._factor_ic_helpers import (  # noqa: E402
    DEFAULT_MAX_GAP_DAYS,
    REGIME_SYMBOL,
    _compute_intersection_universe,
    _compute_regimes,
    _forward_return,
    _load_ohlcv,
    _load_universe_ohlcv,
    _load_universe_timeseries,
)
from src.analysis.ic_analysis import factor_ic_report  # noqa: E402
from src.backtest.engine import BacktestEngine  # noqa: E402
from src.features.gross_profitability import compute_gross_profitability_universe  # noqa: E402
from src.utils.config import load_config  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logger = logging.getLogger("research_gp_ic")


def _run_gp_ic(
    start: datetime,
    end: datetime,
    output_dir: pathlib.Path,
    *,
    config_path: str = "config/settings.yaml",
    rebalance_day: int = 12,
    min_history: int = 4,
) -> None:
    load_dotenv()
    config = load_config(config_path)
    cache_dir = resolve_cache_dir()
    logger.info("Cache: %s", cache_dir)

    logger.info("Loading universe OHLCV ...")
    ohlcv_by_symbol = _load_universe_ohlcv(cache_dir)
    logger.info("Loaded %d OHLCV symbols", len(ohlcv_by_symbol))
    close_by_symbol = {s: df["close"].copy() for s, df in ohlcv_by_symbol.items()}

    logger.info("Loading quarterly_financial_full panel ...")
    qf_by_symbol = _load_universe_timeseries(cache_dir / "quarterly_financial_full")
    logger.info("Loaded %d quarterly_financial_full symbols", len(qf_by_symbol))

    logger.info("Loading balance_sheet panel ...")
    bs_by_symbol = _load_universe_timeseries(cache_dir / "balance_sheet")
    logger.info("Loaded %d balance_sheet symbols", len(bs_by_symbol))

    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    if benchmark is None or benchmark.empty:
        raise RuntimeError(f"Benchmark {REGIME_SYMBOL} missing")

    all_dates = sorted({idx for df in ohlcv_by_symbol.values() for idx in df.index})
    trading_days = pd.DatetimeIndex(all_dates)
    rebalance_dates = BacktestEngine._generate_rebalance_dates(
        start, end, rebalance_day, trading_days=trading_days,
    )
    logger.info("Rebalance dates: %d", len(rebalance_dates))

    strategy_cfg = config.get("default_strategy", {})
    regimes = _compute_regimes(benchmark, rebalance_dates, strategy_cfg)

    intersection = _compute_intersection_universe(cache_dir, log=logger)
    universe_filter = set(intersection) if intersection else None

    period_data = []
    n_kept = n_skipped = 0
    for i, date in enumerate(rebalance_dates[:-1]):
        as_of = pd.Timestamp(date)
        if as_of.tz is not None:
            as_of = as_of.tz_convert(None)
        next_ts = pd.Timestamp(rebalance_dates[i + 1])
        if next_ts.tz is not None:
            next_ts = next_ts.tz_convert(None)

        scores = compute_gross_profitability_universe(
            qf_by_symbol, bs_by_symbol,
            as_of=as_of, min_history=min_history,
        )
        if universe_filter is not None:
            scores = scores[scores.index.isin(universe_filter)]
        if scores.empty:
            n_skipped += 1
            continue

        returns: dict[str, float] = {}
        for sym in scores.index:
            r = _forward_return(
                close_by_symbol, sym, as_of, next_ts,
                max_gap_days=DEFAULT_MAX_GAP_DAYS,
            )
            if r is not None:
                returns[sym] = r
        if not returns:
            n_skipped += 1
            continue
        fwd = pd.Series(returns)
        period_data.append((as_of, scores, fwd, regimes[i] if i < len(regimes) else None))
        n_kept += 1
        if n_kept % 12 == 0:
            logger.info("[gross_profitability] %d periods kept", n_kept)

    logger.info(
        "[gross_profitability] sampling done: kept %d / skipped %d",
        n_kept, n_skipped,
    )
    if n_kept < 10:
        raise RuntimeError(f"[gross_profitability] too few periods ({n_kept})")

    result = factor_ic_report(
        factor_name="gross_profitability",
        period_data=period_data,
        return_basis="price_only",
        n_permutation=300,
        dsr_n_trials=5,
        known_biases=[
            "Novy-Marx 2013 GP/Assets quality factor",
            "TTM GrossProfit (last 4 quarters, quarter-aware lag Q1-3 +45d / Q4 +90d)",
            "TotalAssets: latest balance sheet snapshot disclosed before as_of",
            "intersection universe (same as run_factor_ic.py default)",
        ],
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "gross_profitability_ic.json"
    out_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ov = result.overall
    logger.info(
        "[gross_profitability] DONE → %s | mean_IC=%s IC_IR=%s t=%s p=%s ci95=%s",
        out_path,
        ov.get("mean_ic"), ov.get("ic_ir"), ov.get("t_stat"),
        ov.get("p_value"), ov.get("bootstrap_ci_95"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gross Profitability factor IC research (v5.0 Candidate 2)"
    )
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--output-dir", default="reports/factor_ic",
                        type=pathlib.Path)
    parser.add_argument("--min-history", type=int, default=4,
                        help="Quarters required for TTM (default 4)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    _run_gp_ic(start, end, args.output_dir, min_history=args.min_history)


if __name__ == "__main__":
    main()
