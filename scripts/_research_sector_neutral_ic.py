"""Sector-neutralized factor IC research (v5.0 Pro pre-step).

For Value (E/P) and PEAD (EPS surprise) — both fundamental factors that ride
sector tilts heavily — we want to see if subtracting the industry-mean factor
value purifies the signal (a standard Fama-French / Barra preprocessing).

Compares:
  raw    : `compute_<factor>_universe(...)`
  sn     : raw → `sector_neutralize(panel, industry_map)`

Output: reports/factor_ic/<factor>_sn_ic.json (same schema as raw IC JSON).

Sibling concept: a prior v2.2 attempt did sector-neutral COMPOSITE
(failed — over-constrained alpha). This v5.0 step is FACTOR-LEVEL purification,
a different intervention (clean signal vs constrained portfolio).
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
    _load_industry_labels,
    _load_ohlcv,
    _load_universe_ohlcv,
    _load_universe_timeseries,
)
from src.analysis.ic_analysis import factor_ic_report  # noqa: E402
from src.backtest.engine import BacktestEngine  # noqa: E402
from src.features.pead_eps import compute_pead_eps_universe  # noqa: E402
from src.features.value_ep import compute_value_ep_universe  # noqa: E402
from src.utils.config import load_config  # noqa: E402
from src.utils.factor_neutralize import sector_neutralize  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logger = logging.getLogger("research_sn_ic")


FACTOR_DISPATCH = {
    "value_ep": {
        "fn": compute_value_ep_universe,
        "panel_dir": "quarterly_eps",
        "needs_close_panel": True,
        "min_history": 4,
    },
    "pead_eps": {
        "fn": compute_pead_eps_universe,
        "panel_dir": "quarterly_eps",
        "needs_close_panel": False,
        "min_history": 12,
    },
}


def _run_sn_ic(
    factor_name: str,
    start: datetime,
    end: datetime,
    output_dir: pathlib.Path,
    *,
    config_path: str = "config/settings.yaml",
    rebalance_day: int = 12,
) -> None:
    if factor_name not in FACTOR_DISPATCH:
        raise ValueError(f"Unknown factor: {factor_name}")
    meta = FACTOR_DISPATCH[factor_name]
    factor_fn = meta["fn"]
    panel_dir = meta["panel_dir"]
    needs_close = meta["needs_close_panel"]
    min_history = meta["min_history"]

    load_dotenv()
    config = load_config(config_path)
    cache_dir = resolve_cache_dir()
    logger.info("Cache: %s", cache_dir)

    logger.info("Loading universe OHLCV ...")
    ohlcv_by_symbol = _load_universe_ohlcv(cache_dir)
    logger.info("Loaded %d OHLCV symbols", len(ohlcv_by_symbol))
    close_by_symbol = {s: df["close"].copy() for s, df in ohlcv_by_symbol.items()}

    logger.info("Loading panel %s ...", panel_dir)
    panel_by_symbol = _load_universe_timeseries(cache_dir / panel_dir)
    logger.info("Loaded %d %s symbols", len(panel_by_symbol), panel_dir)

    logger.info("Loading industry labels ...")
    industry_map = _load_industry_labels(cache_dir) or {}
    logger.info("Industry labels for %d symbols", len(industry_map))
    if not industry_map:
        raise RuntimeError("industry_map empty — cannot sector-neutralize")

    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    if benchmark is None or benchmark.empty:
        raise RuntimeError(f"Benchmark {REGIME_SYMBOL} missing")

    # rebalance dates aligned to benchmark trading calendar
    all_dates = sorted({idx for df in ohlcv_by_symbol.values() for idx in df.index})
    trading_days = pd.DatetimeIndex(all_dates)
    rebalance_dates = BacktestEngine._generate_rebalance_dates(
        start, end, rebalance_day, trading_days=trading_days,
    )
    logger.info("Rebalance dates: %d", len(rebalance_dates))

    strategy_cfg = config.get("default_strategy", {})
    regimes = _compute_regimes(benchmark, rebalance_dates, strategy_cfg)

    # intersection universe (same as run_factor_ic.py default)
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

        factor_kwargs = {"as_of": as_of, "min_history": min_history}
        if needs_close:
            factor_kwargs["close_by_symbol"] = close_by_symbol
        raw = factor_fn(panel_by_symbol, **factor_kwargs)
        if universe_filter is not None:
            raw = raw[raw.index.isin(universe_filter)]
        if raw.empty:
            n_skipped += 1
            continue

        # the key step: sector-neutralize the raw panel
        sn = sector_neutralize(raw, industry_map)

        # forward return per symbol
        returns: dict[str, float] = {}
        for sym in sn.index:
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
        period_data.append((as_of, sn, fwd, regimes[i] if i < len(regimes) else None))
        n_kept += 1
        if n_kept % 12 == 0:
            logger.info("[%s] %d periods kept", factor_name, n_kept)

    logger.info(
        "[%s] sampling done: kept %d / skipped %d",
        factor_name, n_kept, n_skipped,
    )
    if n_kept < 10:
        raise RuntimeError(f"[{factor_name}] too few periods ({n_kept})")

    result = factor_ic_report(
        factor_name=f"{factor_name}_sn",
        period_data=period_data,
        return_basis="price_only",
        n_permutation=300,
        dsr_n_trials=5,
        known_biases=[
            "sector-neutralized at factor level (subtract industry mean per period)",
            "industry labels from current stock_info snapshot (Option B; small drift caveat)",
            "intersection universe (same as run_factor_ic.py default)",
        ],
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{factor_name}_sn_ic.json"
    out_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ov = result.overall
    logger.info(
        "[%s_sn] DONE → %s | mean_IC=%s IC_IR=%s t=%s p=%s ci95=%s",
        factor_name, out_path,
        ov.get("mean_ic"), ov.get("ic_ir"), ov.get("t_stat"),
        ov.get("p_value"), ov.get("bootstrap_ci_95"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sector-neutralized factor IC research (v5.0 pre-step)"
    )
    parser.add_argument("--factor", choices=list(FACTOR_DISPATCH.keys()),
                        help="Single factor; mutually exclusive with --all")
    parser.add_argument("--all", action="store_true",
                        help="Run all SN-applicable factors (value_ep + pead_eps)")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--output-dir", default="reports/factor_ic",
                        type=pathlib.Path)
    args = parser.parse_args()

    if not args.all and not args.factor:
        parser.error("Either --factor or --all is required")
    if args.all and args.factor:
        parser.error("--factor and --all are mutually exclusive")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")

    factors = list(FACTOR_DISPATCH.keys()) if args.all else [args.factor]
    for f in factors:
        _run_sn_ic(f, start, end, args.output_dir)


if __name__ == "__main__":
    main()
