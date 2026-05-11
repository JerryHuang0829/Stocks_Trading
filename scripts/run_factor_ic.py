"""Full-universe factor IC research CLI.

Replaces the snapshot-driven `scripts/analyze_factor_ic.py` (truncated top-20).
Computes rank IC across every cached symbol at each rebalance date.

Usage
-----
    docker compose run --rm --entrypoint python portfolio-bot \
        scripts/run_factor_ic.py --factor high_proximity \
        --start 2019-01-01 --end 2025-12-31

Design notes
------------
- Universe: scan `data/cache/ohlcv/*.pkl` (full set of locally cached stocks).
  Carries survivorship bias since delisted tickers are absent from cache.
- Forward return: `close[next_rebalance] / close[rebalance] - 1` (price only,
  no dividend adjustment). Acknowledged bias: under-estimates alpha on high
  dividend-yield names. A2 integration will upgrade to total return.
- Regime: computed from `0050` ADX + SMA via `src.strategy.regime.detect_regime`.
- IC pipeline: `src.analysis.ic_analysis.factor_ic_report`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd
from dotenv import load_dotenv

from src.analysis.ic_analysis import factor_ic_report
from src.backtest.engine import BacktestEngine
from src.features.foreign_investor_v2 import compute_foreign_investor_v2_universe
from src.features.high_proximity import compute_high_proximity_universe
from src.features.margin_short_ratio import compute_margin_short_ratio_universe
from src.features.pead_eps import compute_pead_eps_universe
from src.features.revenue_momentum_v2 import compute_revenue_momentum_v2_universe
from src.utils.config import load_config
from src.utils.paths import resolve_cache_dir
from src.utils.thresholds import get_threshold

# Phase P5 Session 1 / R21 finding F6 fix (2026-05-03):
# 12 helpers extracted to scripts/_factor_ic_helpers.py to enable cross-script
# reuse (phase_b0_lite_spike.py — cleaned up 2026-05-04 — p5_smart_beta_tilt.py,
# future P5+ / Phase D scripts).
# Re-export here for backward-compat with /factor-ic skill + manual CLI.
from scripts._factor_ic_helpers import (  # noqa: F401
    REGIME_SYMBOL,
    MIN_UNIVERSE_SIZE,
    PANEL_DIRS_FOR_INTERSECTION,
    DEFAULT_MIN_OBS_PER_SYMBOL,
    DEFAULT_MAX_GAP_DAYS,
    _normalise_index,
    _load_ohlcv,
    _load_universe_ohlcv,
    _load_universe_revenue,
    _load_universe_timeseries,
    _load_issued_capital,            # DEPRECATED — see _load_issued_capital_panel
    _load_issued_capital_panel,      # 2026-05-10 P1-A: PIT panel loader
    _issued_capital_asof,            # 2026-05-10 P1-A: as-of lookup
    _load_industry_labels,
    _load_market_value,              # DEPRECATED — see _load_market_value_panel
    _load_market_value_panel,        # 2026-05-10 P0-A: PIT panel loader
    _market_value_asof,              # 2026-05-10 P0-A: as-of lookup
    _resolve_price_asof,
    _forward_return,
    _compute_intersection_universe,
    _compute_regimes,
)


# Each factor declares:
#   panel_type: which primary per-symbol cache to load (ohlcv/revenue/margin/
#               institutional_v2/quarterly_eps)
#   aux_panel:  optional secondary panel ("issued_capital" / "market_value" /
#               None) passed to the factor as kwarg aux_panel
#   default_min_history: units vary by factor (days / months / quarters)
FACTOR_REGISTRY: dict[str, dict] = {
    "high_proximity": {
        "fn": compute_high_proximity_universe,
        "panel_type": "ohlcv",
        "aux_panel": None,
        "default_min_history": 126,   # trading days
    },
    "revenue_momentum_v2": {
        "fn": compute_revenue_momentum_v2_universe,
        "panel_type": "revenue",
        "aux_panel": None,
        "default_min_history": 15,    # months
    },
    "margin_short_ratio": {
        "fn": compute_margin_short_ratio_universe,
        "panel_type": "margin_short",
        "aux_panel": "issued_capital",
        "default_min_history": 40,    # trading days
    },
    "foreign_investor_v2": {
        "fn": compute_foreign_investor_v2_universe,
        "panel_type": "institutional_v2",
        "aux_panel": "market_value",
        "default_min_history": 60,    # trading days
    },
    "pead_eps": {
        "fn": compute_pead_eps_universe,
        "panel_type": "quarterly_eps",
        "aux_panel": None,
        "default_min_history": 12,    # quarters
    },
}




def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full-universe factor IC analysis"
    )
    parser.add_argument("--factor", required=True, choices=list(FACTOR_REGISTRY.keys()))
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--output-dir", default="reports/factor_ic")
    parser.add_argument("--rebalance-day", type=int, default=12)
    parser.add_argument("--n-permutation", type=int, default=300)
    parser.add_argument(
        "--min-history",
        type=int,
        default=None,
        help="Override factor's default min_history (units depend on factor)",
    )
    parser.add_argument(
        "--universe-mode",
        choices=("intersection", "per_factor"),
        default="intersection",
        help=(
            "intersection: intersect every populated factor cache so cross-factor "
            "IC / FDR is comparable (P1-新1). per_factor: legacy — each factor "
            "uses its own panel universe."
        ),
    )
    parser.add_argument(
        "--max-gap-days",
        type=int,
        default=DEFAULT_MAX_GAP_DAYS,
        help="Max stale-price tolerance for forward return (P1-新2)",
    )
    parser.add_argument(
        "--min-obs-per-symbol",
        type=int,
        default=None,
        help=(
            "Uniform min rows per symbol override for intersection universe. "
            "Default: per-panel thresholds from config/factor_thresholds.yaml "
            "(daily panels 250, revenue 24, quarterly_eps 12). "
            "Pass an int here to apply the same threshold to every panel "
            "(legacy behaviour before follow-up-4)."
        ),
    )
    parser.add_argument(
        "--dsr-n-trials",
        type=int,
        default=5,
        help="Number of candidate strategies deflating the Sharpe ratio (P1-新5)",
    )
    parser.add_argument(
        "--bootstrap-block-len",
        type=float,
        default=3.0,
        help="Average block length for stationary block bootstrap (P1-新3A)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("run_factor_ic")

    config = load_config(args.config)
    cache_dir = resolve_cache_dir()
    log.info("Cache dir: %s", cache_dir)

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    factor_meta = FACTOR_REGISTRY[args.factor]
    factor_fn = factor_meta["fn"]
    panel_type = factor_meta["panel_type"]
    aux_panel_kind = factor_meta.get("aux_panel")
    min_history = args.min_history if args.min_history is not None else factor_meta["default_min_history"]
    log.info(
        "Factor: %s  panel=%s  aux=%s  min_history=%d",
        args.factor, panel_type, aux_panel_kind, min_history,
    )

    # OHLCV is always needed (for forward returns + regime detection)
    log.info("Loading universe OHLCV from cache...")
    ohlcv_by_symbol = _load_universe_ohlcv(cache_dir)
    log.info("Loaded %d OHLCV symbols", len(ohlcv_by_symbol))
    if len(ohlcv_by_symbol) < MIN_UNIVERSE_SIZE:
        log.error(
            "OHLCV universe too small (%d < %d) — aborting",
            len(ohlcv_by_symbol), MIN_UNIVERSE_SIZE,
        )
        sys.exit(1)

    # Primary panel per factor
    panel_by_symbol: dict
    if panel_type == "ohlcv":
        panel_by_symbol = ohlcv_by_symbol
    elif panel_type == "revenue":
        log.info("Loading universe revenue from cache...")
        panel_by_symbol = _load_universe_timeseries(cache_dir / "revenue")
    elif panel_type == "margin_short":
        log.info("Loading universe margin_short from cache...")
        panel_by_symbol = _load_universe_timeseries(cache_dir / "margin_short")
    elif panel_type == "institutional_v2":
        log.info("Loading universe institutional_v2 from cache...")
        panel_by_symbol = _load_universe_timeseries(cache_dir / "institutional_v2")
    elif panel_type == "quarterly_eps":
        log.info("Loading universe quarterly_eps from cache...")
        panel_by_symbol = _load_universe_timeseries(cache_dir / "quarterly_eps")
    else:
        log.error("Unknown panel_type: %s", panel_type)
        sys.exit(1)
    log.info("Loaded %d %s symbols", len(panel_by_symbol), panel_type)
    if len(panel_by_symbol) < MIN_UNIVERSE_SIZE:
        log.error(
            "%s universe too small (%d < %d) — aborting",
            panel_type, len(panel_by_symbol), MIN_UNIVERSE_SIZE,
        )
        sys.exit(1)

    # Optional aux panel — 2026-05-10 P0-A / P1-A: load PIT panel, build
    # as-of dict per rebalance date inside the loop instead of taking latest
    # once outside (which violated PIT discipline; see R26 audit).
    aux_mv_panel: pd.DataFrame | None = None
    aux_issued_panel: pd.DataFrame | None = None
    if aux_panel_kind == "issued_capital":
        log.info("Loading issued_capital PIT panel...")
        aux_issued_panel = _load_issued_capital_panel(cache_dir)
        log.info(
            "Loaded issued_capital panel: %d rows, %d unique symbols",
            len(aux_issued_panel),
            aux_issued_panel["stock_id"].nunique() if not aux_issued_panel.empty else 0,
        )
        if aux_issued_panel.empty:
            raise RuntimeError(
                "issued_capital panel empty — run cache_fill_new_factors.py "
                "with --seed-issued-capital first."
            )
    elif aux_panel_kind == "market_value":
        log.info("Loading market_value PIT panel...")
        aux_mv_panel = _load_market_value_panel(cache_dir)
        log.info(
            "Loaded market_value panel: %d rows, %d unique symbols, range %s ~ %s",
            len(aux_mv_panel),
            aux_mv_panel["stock_id"].nunique() if not aux_mv_panel.empty else 0,
            aux_mv_panel["date"].min() if not aux_mv_panel.empty else "n/a",
            aux_mv_panel["date"].max() if not aux_mv_panel.empty else "n/a",
        )
        if aux_mv_panel.empty:
            raise RuntimeError(
                "market_value panel empty — required for foreign_investor_v2 / "
                "future market_value-dependent factors."
            )

    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    if benchmark is None or benchmark.empty:
        log.error("Benchmark %s OHLCV missing — cannot compute regime", REGIME_SYMBOL)
        sys.exit(1)

    # P1-新1 + follow-up-4: compute cross-factor intersection universe when
    # requested. When `per_factor` is set, `universe_filter` stays None and
    # the factor sees its native panel universe (legacy behaviour).
    universe_filter: set[str] | None = None
    min_universe_size = int(
        get_threshold("universe", "min_universe_size", default=MIN_UNIVERSE_SIZE)
    )
    if args.universe_mode == "intersection":
        intersection = _compute_intersection_universe(
            cache_dir,
            min_obs_per_symbol=args.min_obs_per_symbol,
            log=log,
        )
        if len(intersection) < min_universe_size:
            log.error(
                "Intersection universe too small (%d < %d) — reduce --min-obs-per-symbol "
                "or use --universe-mode per_factor",
                len(intersection), min_universe_size,
            )
            sys.exit(1)
        universe_filter = set(intersection)
        log.info("Intersection universe: %d symbols", len(universe_filter))

    close_by_symbol: dict[str, pd.Series] = {
        s: df["close"].copy() for s, df in ohlcv_by_symbol.items()
    }

    all_dates = sorted({idx for df in ohlcv_by_symbol.values() for idx in df.index})
    trading_days = pd.DatetimeIndex(all_dates)
    rebalance_dates = BacktestEngine._generate_rebalance_dates(
        start_dt, end_dt, args.rebalance_day, trading_days=trading_days
    )
    log.info("Generated %d rebalance dates", len(rebalance_dates))

    strategy_cfg = config.get("default_strategy", {})
    regimes = _compute_regimes(benchmark, rebalance_dates, strategy_cfg)
    regime_counts = pd.Series([r or "unknown" for r in regimes]).value_counts().to_dict()
    log.info("Regime distribution: %s", regime_counts)

    period_data: list = []
    total_dropped_by_gap = 0
    total_attempted = 0
    for i, date in enumerate(rebalance_dates[:-1]):
        as_of = pd.Timestamp(date)
        if as_of.tz is not None:
            as_of = as_of.tz_convert(None)
        next_ts = pd.Timestamp(rebalance_dates[i + 1])
        if next_ts.tz is not None:
            next_ts = next_ts.tz_convert(None)

        factor_kwargs: dict = {"as_of": as_of, "min_history": min_history}
        # 2026-05-10 P0-A / P1-A: build as-of dict per rebalance date (PIT-correct).
        # Replaces taking latest once outside the loop.
        if aux_mv_panel is not None:
            factor_kwargs["aux_panel"] = _market_value_asof(aux_mv_panel, as_of)
        elif aux_issued_panel is not None:
            factor_kwargs["aux_panel"] = _issued_capital_asof(aux_issued_panel, as_of)
        # P0-B: foreign_investor_v2 cum_foreign 改金額制需要 close panel
        if args.factor == "foreign_investor_v2":
            factor_kwargs["close_by_symbol"] = close_by_symbol
        factor_scores = factor_fn(panel_by_symbol, **factor_kwargs)
        if universe_filter is not None:
            factor_scores = factor_scores[factor_scores.index.isin(universe_filter)]
        if factor_scores.empty:
            log.warning("Period %s: empty factor scores — skipping", as_of.date())
            continue

        returns: dict[str, float] = {}
        dropped_this_period = 0
        for sym in factor_scores.index:
            total_attempted += 1
            r = _forward_return(
                close_by_symbol, sym, as_of, next_ts,
                max_gap_days=args.max_gap_days,
            )
            if r is None:
                dropped_this_period += 1
                continue
            returns[sym] = r
        total_dropped_by_gap += dropped_this_period
        returns_series = pd.Series(returns, dtype=float)
        if len(returns_series) < 10:
            log.warning(
                "Period %s: only %d forward returns — skipping (dropped %d by gap)",
                as_of.date(), len(returns_series), dropped_this_period,
            )
            continue
        period_data.append((as_of, factor_scores, returns_series, regimes[i]))

    if total_attempted > 0:
        log.info(
            "forward-return gap filter: %d / %d (%.1f%%) dropped (max_gap_days=%d)",
            total_dropped_by_gap, total_attempted,
            100.0 * total_dropped_by_gap / total_attempted,
            args.max_gap_days,
        )

    if not period_data:
        log.error("No usable periods — aborting")
        sys.exit(1)

    log.info("Running factor_ic_report on %d periods", len(period_data))
    # Load industry labels for effective_n clustering if available (stock_info cache)
    industry_labels = _load_industry_labels(cache_dir)
    if industry_labels:
        log.info("Loaded %d industry labels for effective_n clustering", len(industry_labels))
    else:
        log.info("No industry labels available — effective_n falls back to n * 0.5")

    # factor_ic_report auto-appends standard boilerplate (survivorship +
    # price-only + bootstrap/permutation/DSR/effective_n notes). We only
    # supply caller-specific run-time facts here to avoid duplicate wording
    # (external audit C5 / Round 3.5).
    biases = [
        "regime computed from 0050 benchmark (not per-stock)",
    ]
    if args.universe_mode == "intersection":
        biases.append(
            f"universe=intersection across populated factor panels "
            f"(min_obs={args.min_obs_per_symbol})"
        )
    biases.append(f"forward-return gap filter: max_gap_days={args.max_gap_days}")

    result = factor_ic_report(
        factor_name=args.factor,
        period_data=period_data,
        return_basis="price_only",
        n_permutation=args.n_permutation,
        known_biases=biases,
        bootstrap_avg_block_len=args.bootstrap_block_len,
        dsr_n_trials=args.dsr_n_trials,
        industry_labels=industry_labels,
    )

    out_path = Path(args.output_dir) / f"{args.factor}_ic.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Wrote %s", out_path)

    ov = result.overall
    perm = result.permutation
    print("=" * 60)
    print(f"  Factor IC: {args.factor}")
    print("=" * 60)
    print(f"  periods               : {result.n_periods}")
    print(f"  n_symbols_avg         : {result.n_symbols_avg:.1f}")
    print(f"  mean_ic               : {ov.get('mean_ic')}")
    print(f"  ic_ir                 : {ov.get('ic_ir')}")
    print(f"  t_stat                : {ov.get('t_stat')}")
    print(f"  p_value               : {ov.get('p_value')}")
    print(f"  bootstrap 95%% (block): {ov.get('bootstrap_ci_95')}")
    print(f"  bootstrap 95%% (iid)  : {ov.get('bootstrap_ci_95_iid')}")
    print(f"  DSR (confidence)      : {result.deflated_sharpe_ratio}  (n_trials={result.deflated_sharpe_n_trials}; BLdP 2014: >=0.95 significant, NOT a p-value)")
    print(f"  effective_n           : {result.effective_n}")
    print(f"  permutation           : {perm.get('conclusion')} (p_emp={perm.get('p_value_empirical')})")
    print("  regime ICs            :")
    for regime, stats in sorted(result.by_regime.items()):
        print(f"    {regime:15s} mean={stats.get('mean_ic')} n={stats.get('n')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
