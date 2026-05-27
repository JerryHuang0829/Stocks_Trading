"""Daily factor IC pipeline (v5.0 prep, 2026-05-23).

For factors whose cross-section value updates daily, compute IC of
factor[t] vs 1-day-forward return at every Nth trading day in [start, end].

Applicable factors (in v7 _compute_factor_panel dispatch):
    high_proximity, margin_short_ratio, industry_momentum, idio_vol_max

Skipped (factor value doesn't update daily):
    pead_eps (event-driven), quality_v3 (quarterly), revenue_momentum_v2 (monthly)

Skipped (already DROP):
    foreign_investor_v2

Output: reports/factor_ic/daily/<factor>_daily_ic.json (mirrors monthly IC
JSON schema; n_periods ≈ trading_days_in_range / --skip).
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.d_cell_sweep_v7_real import (  # noqa: E402
    CellSweepContext,
    _compute_factor_panel,
)
from src.analysis.ic_analysis import factor_ic_report  # noqa: E402
from src.features.reversal_1m import compute_reversal_1m_universe  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logger = logging.getLogger(__name__)

# v5.0 (2026-05-24): added reversal_1m. value_ep stays excluded — quarterly
# EPS doesn't update daily, so daily IC is mostly noise (same exclusion logic
# as pead_eps).
DAILY_APPLICABLE = ["high_proximity", "margin_short_ratio",
                    "industry_momentum", "idio_vol_max",
                    "reversal_1m"]


def _dispatch_factor_panel(factor_name: str, ctx, as_of):
    """Dispatch factor compute, extending v7 dispatch with v5.0 new factors."""
    if factor_name == "reversal_1m":
        return compute_reversal_1m_universe(ctx.ohlcv_panel, as_of=as_of)
    return _compute_factor_panel(factor_name, ctx, as_of)


def _build_fwd_returns(
    ctx: CellSweepContext, t: pd.Timestamp, t_next: pd.Timestamp,
) -> pd.Series:
    """1-day forward returns per symbol: close[t_next] / close[t] - 1.

    PIT-safe (caller passes t_next > t via trading-day calendar).
    """
    rets: dict[str, float] = {}
    for sid, df in ctx.ohlcv_panel.items():
        h0 = df[df.index <= t]
        h1 = df[df.index <= t_next]
        if h0.empty or h1.empty:
            continue
        c0 = float(h0["close"].iloc[-1])
        c1 = float(h1["close"].iloc[-1])
        if c0 > 0 and np.isfinite(c0) and np.isfinite(c1):
            rets[sid] = c1 / c0 - 1.0
    return pd.Series(rets, dtype=float)


def run_daily_ic(
    factor_name: str,
    ctx: CellSweepContext,
    start: pd.Timestamp,
    end: pd.Timestamp,
    skip: int = 5,
):
    # Use 0050 trading days as the canonical calendar
    bench = ctx.ohlcv_panel.get("0050")
    if bench is None or bench.empty:
        raise RuntimeError("0050 not in ohlcv_panel — cannot derive trading calendar")
    days = bench.index
    days = days[(days >= start) & (days <= end)]
    if len(days) < 2:
        raise RuntimeError(f"Too few trading days in [{start.date()}, {end.date()}]")

    sampled = days[::skip]
    logger.info("[%s] sampling every %d trading days → %d sample dates",
                factor_name, skip, len(sampled))

    period_data: list = []
    n_skipped_empty = 0
    n_skipped_no_fwd = 0
    n_skipped_factor_fail = 0

    for i, t in enumerate(sampled):
        # 1-day forward: t+1 trading day on the FULL calendar (not sampled-next)
        loc = days.get_loc(t)
        if loc + 1 >= len(days):
            continue
        t_next = days[loc + 1]

        try:
            factor_panel = _dispatch_factor_panel(factor_name, ctx, pd.Timestamp(t))
        except Exception as exc:
            logger.debug("[%s] %s factor compute failed: %s",
                         factor_name, t.date(), exc)
            n_skipped_factor_fail += 1
            continue

        if factor_panel is None or factor_panel.empty:
            n_skipped_empty += 1
            continue

        fwd = _build_fwd_returns(ctx, pd.Timestamp(t), pd.Timestamp(t_next))
        if fwd.empty:
            n_skipped_no_fwd += 1
            continue

        period_data.append((pd.Timestamp(t), factor_panel, fwd, None))
        if (i + 1) % 50 == 0:
            logger.info("[%s] %d / %d sample dates processed (kept %d)",
                        factor_name, i + 1, len(sampled), len(period_data))

    logger.info(
        "[%s] sampling done: kept %d / %d (empty=%d no_fwd=%d factor_fail=%d)",
        factor_name, len(period_data), len(sampled),
        n_skipped_empty, n_skipped_no_fwd, n_skipped_factor_fail,
    )

    if len(period_data) < 10:
        raise RuntimeError(
            f"[{factor_name}] too few valid periods ({len(period_data)}) for IC")

    logger.info("[%s] running factor_ic_report ...", factor_name)
    result = factor_ic_report(
        factor_name=factor_name,
        period_data=period_data,
        return_basis="price_only_daily_1d_fwd",
        n_permutation=300,
        dsr_n_trials=5,
        known_biases=[
            "1-day forward return (price-only, no dividend adjustment)",
            f"sampling every {skip} trading days",
            "regime classification skipped (set None per period)",
            "PEAD / quality_v3 / revenue_v2 excluded (factor not daily-updatable)",
            "foreign_investor_v2 excluded (DROP factor + not in v7 dispatch)",
        ],
    )
    return result


def _run_one_factor_and_save(factor_name, ctx, start, end, skip, output_dir):
    """Run daily IC for one factor; save JSON; log summary."""
    try:
        result = run_daily_ic(factor_name, ctx, start, end, skip=skip)
    except Exception as exc:
        logger.error("[%s] FAILED: %s", factor_name, exc)
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{factor_name}_daily_ic.json"
    out.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ov = result.overall
    logger.info(
        "[%s] DONE → %s | mean_IC=%s ic_ir=%s t=%s p=%s n=%s ci95=%s",
        factor_name, out,
        ov.get("mean_ic"), ov.get("ic_ir"), ov.get("t_stat"),
        ov.get("p_value"), ov.get("n"), ov.get("bootstrap_ci_95"),
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily factor IC (v5.0 prep)")
    parser.add_argument("--factor", choices=DAILY_APPLICABLE,
                        help="One factor; mutually exclusive with --all")
    parser.add_argument("--all", action="store_true",
                        help=f"Run all {len(DAILY_APPLICABLE)} applicable factors (single context build)")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--skip", type=int, default=5,
                        help="sample every N trading days (default 5 → ~250 obs/factor for 5y)")
    parser.add_argument("--output-dir", type=pathlib.Path,
                        default=pathlib.Path("reports/factor_ic/daily"))
    args = parser.parse_args()

    if not args.all and not args.factor:
        parser.error("Either --factor or --all is required")
    if args.all and args.factor:
        parser.error("--factor and --all are mutually exclusive")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    logger.info("Building CellSweepContext (one-time; reused across factors) ...")
    ctx = CellSweepContext(
        resolve_cache_dir(),
        start.to_pydatetime(),
        end.to_pydatetime(),
        require_dividend_adjust=False,
    )
    logger.info("Universe: %d OHLCV stocks", len(ctx.ohlcv_panel))

    factors = DAILY_APPLICABLE if args.all else [args.factor]
    for f in factors:
        _run_one_factor_and_save(f, ctx, start, end, args.skip, args.output_dir)

    logger.info("All requested factors done.")


if __name__ == "__main__":
    main()
