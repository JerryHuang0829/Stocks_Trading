"""Phase D single-factor IC wrapper for {quality_v3, industry_momentum, idio_vol_max}.

Built 2026-05-11 to fill the gap: Phase A1 5 factors have single-factor IC reports
(reports/factor_ic/*_ic.json) but the 3 Phase D candidate factors (quality_v3 /
industry_momentum / idio_vol_max) only appeared inside the v7 cell sweep aggregate.
This wrapper reuses CellSweepContext data builders + `factor_ic_report` pipeline
so the resulting JSONs share schema with the Phase A1 fresh-rerun JSONs.

Design
------
- Data sources: CellSweepContext from `scripts/d_cell_sweep_v7_real.py`
    * quality_v3       <- ctx.financial_history
    * industry_momentum <- ctx.ohlcv_panel + ctx.industry_label_map
    * idio_vol_max     <- ctx.ohlcv_panel + ctx.market_returns
- Rebalance schedule: same convention as `scripts/run_factor_ic.py`
    (BacktestEngine._generate_rebalance_dates with --rebalance-day 12,
     71 periods 2020-01-13 ~ 2025-11-12 by default).
- Forward returns: `_forward_return` from `scripts/_factor_ic_helpers.py`
    (max_gap_days stale tolerance, close from ctx.ohlcv_panel).
- Universe: natural per-factor universe (no intersection with the Phase A1
    panels) because the Phase D factor inputs are different data sources.
    Cross-factor comparison across A1+D is universe-asymmetric; this is
    documented as a known_biases entry on each output JSON.

Usage
-----
    python scripts/run_phase_d_factor_ic.py --factor quality_v3
    python scripts/run_phase_d_factor_ic.py --factor industry_momentum
    python scripts/run_phase_d_factor_ic.py --factor idio_vol_max

Output
------
    reports/factor_ic/{factor}_ic.json — full schema parity with Phase A1
    5-factor JSONs: after writing result.to_dict(), the JSON is enriched
    in-place via scripts/_enrich_factor_ic_diagnostics.py with
    decile_returns_per_period / decile_avg_returns_across_periods /
    monotonicity_spearman_rho / peak_in_middle_t_stats /
    price_score_corr_per_period / price_score_corr_summary / pit_violation /
    enriched_diagnostics_date. Pass --no-enrich to skip (raw IC only).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._factor_ic_helpers import (  # noqa: E402
    DEFAULT_MAX_GAP_DAYS,
    REGIME_SYMBOL,
    _compute_regimes,
    _forward_return,
    _load_industry_labels,
    _load_ohlcv,
)
from scripts.d_cell_sweep_v7_real import (  # noqa: E402
    CellSweepContext,
    _compute_factor_panel,
)
from src.analysis.ic_analysis import factor_ic_report  # noqa: E402
from src.backtest.engine import BacktestEngine  # noqa: E402
from src.utils.config import load_config  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

PHASE_D_FACTORS = ("quality_v3", "industry_momentum", "idio_vol_max")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase D single-factor IC for quality_v3 / industry_momentum / idio_vol_max",
    )
    parser.add_argument("--factor", required=True, choices=PHASE_D_FACTORS)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--output-dir", default="reports/factor_ic")
    parser.add_argument("--rebalance-day", type=int, default=12)
    parser.add_argument("--n-permutation", type=int, default=300)
    parser.add_argument(
        "--max-gap-days",
        type=int,
        default=DEFAULT_MAX_GAP_DAYS,
        help="Max stale-price tolerance for forward return",
    )
    parser.add_argument("--dsr-n-trials", type=int, default=5)
    parser.add_argument("--bootstrap-block-len", type=float, default=3.0)
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Skip the post-IC enrichment step (decile / monotonicity / "
        "peak-in-middle / price-score-corr / pit_violation diagnostics).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("run_phase_d_factor_ic")

    config = load_config(args.config)
    cache_dir = resolve_cache_dir()
    log.info("Cache dir: %s", cache_dir)

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    log.info("Building CellSweepContext (loading universe + factor data sources)...")
    ctx = CellSweepContext(cache_dir, start_dt, end_dt, require_dividend_adjust=False)

    # Force lazy-load of factor-specific data source up front (so we can fail-fast)
    if args.factor == "quality_v3":
        fh = ctx.financial_history
        if fh.empty:
            log.error("financial_history empty — quality_v3 cannot run")
            sys.exit(1)
        log.info(
            "financial_history: %d rows, %d symbols, period_end %s ~ %s",
            len(fh), fh.index.nunique(),
            fh["period_end"].min(), fh["period_end"].max(),
        )
    elif args.factor == "industry_momentum":
        ilm = ctx.industry_label_map
        if not ilm:
            log.error("industry_label_map empty — industry_momentum cannot run")
            sys.exit(1)
        log.info("industry_label_map: %d symbols", len(ilm))
    elif args.factor == "idio_vol_max":
        mr = ctx.market_returns
        if mr.empty:
            log.error("market_returns empty — idio_vol_max cannot run")
            sys.exit(1)
        log.info("market_returns: %d trading days", len(mr))

    log.info("ohlcv_panel: %d symbols", len(ctx.ohlcv_panel))

    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    if benchmark is None or benchmark.empty:
        log.error("Benchmark %s OHLCV missing — cannot compute regime", REGIME_SYMBOL)
        sys.exit(1)

    close_by_symbol: dict[str, pd.Series] = {
        s: df["close"].copy() for s, df in ctx.ohlcv_panel.items()
    }

    all_dates = sorted({idx for df in ctx.ohlcv_panel.values() for idx in df.index})
    trading_days = pd.DatetimeIndex(all_dates)
    rebalance_dates = BacktestEngine._generate_rebalance_dates(
        start_dt, end_dt, args.rebalance_day, trading_days=trading_days,
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

        factor_scores = _compute_factor_panel(args.factor, ctx, as_of)
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
    industry_labels = _load_industry_labels(cache_dir)
    if industry_labels:
        log.info("Loaded %d industry labels for effective_n clustering", len(industry_labels))

    biases = [
        "regime computed from 0050 benchmark (not per-stock)",
        "universe=per-factor natural universe (NOT intersection with Phase A1 5 panels)",
        f"forward-return gap filter: max_gap_days={args.max_gap_days}",
    ]
    if args.factor == "quality_v3":
        biases.append(
            "financial_history derived from quarterly_financial_full + balance_sheet caches; "
            "NetIncome filled-back from IncomeAfterTaxes when missing (V0.26 fix)"
        )
    elif args.factor == "industry_momentum":
        biases.append(
            "industry_label_map: current stock_info snapshot (no historical industry "
            "membership; per Phase D v7 V0.14 R14 Option B)"
        )
    elif args.factor == "idio_vol_max":
        biases.append(
            "market_returns benchmark: 0050 price-only (no dividend adjustment) for "
            "residual vol regression"
        )

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

    # 2026-05-11 R31 finding 1 fix: enrich the JSON in-place with the
    # same decile / monotonicity / peak-in-middle / price-score-corr /
    # pit_violation diagnostics the Phase A1 5-factor JSONs carry, so the
    # Phase D 3-factor JSONs have full schema parity (was only writing
    # result.to_dict() which lacks these). Reuses scripts/_enrich_factor_ic_diagnostics.py.
    if not args.no_enrich:
        log.info("Enriching %s with Phase-A1-parity diagnostics...", out_path)
        from scripts._enrich_factor_ic_diagnostics import enrich as _enrich
        _enrich(out_path)
        log.info("Enriched %s", out_path)

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
    print(f"  DSR                   : {result.deflated_sharpe_ratio}  (n_trials={result.deflated_sharpe_n_trials})")
    print(f"  effective_n           : {result.effective_n}")
    print(f"  permutation           : {perm.get('conclusion')} (p_emp={perm.get('p_value_empirical')})")
    print("=" * 60)


if __name__ == "__main__":
    main()
