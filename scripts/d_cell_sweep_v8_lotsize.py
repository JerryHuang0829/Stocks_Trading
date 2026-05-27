"""v8.1 — whole-lot (整張) position sizing realism sweep.

Re-runs the v7 18-cell setup with **whole-lot position sizing** instead of v7's
continuous equal weights, to quantify the realism gap a NT$1,000,000 retail
account actually faces (1 lot = 1,000 shares; a single lot of a high-priced
stock can exceed the per-name budget — see plan stock-swirling-elephant.md).

This module **imports and reuses** the v7 sweep machinery and does NOT modify
`d_cell_sweep_v7_real.py` — the v7 archived run stays byte-reproducible.

Design locks (v8.1, do not retune):
- Fixed equal slice = NAV / top_n; floor whole lots; no substitution; slice
  residual -> cash (0%).
- Dynamic NAV: starts at NT$1,000,000, compounds with net return each month.
- Whole-lot only (no intraday odd-lot — separate v8.1b sensitivity).
- Turnover cost frozen at v7's `top_syms` symmetric-difference definition.
- Benchmark 0050 is NOT lot-rounded (ETF is DCA-able; v7 benchmark is a
  continuous total-return series — only the portfolio leg gets the constraint).

Output (per `--output-dir`):
- cell_metrics.json          — 7-key schema, identical to v7 (d_cell_aggregate_v7
                               reusable unchanged)
- cell_monthly_active_returns.json — for walk_forward_d_v7 L6 bootstrap CI
- cell_feasibility.json      — per-cell feasibility (avg lots held / invested
                               ratio / weight deviation / final NAV)
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.composite_backtest import (  # noqa: E402
    _is_above_min_price_at,
    _next_month_return,
)
from scripts.d_cell_sweep_v7 import (  # noqa: E402
    CANDIDATE_FACTOR_SETS,
    TOP_N_VALUES,
    load_candidate_config,
)
from scripts.d_cell_sweep_v7_real import (  # noqa: E402
    TW_ROUND_TRIP_COST,
    CellSweepContext,
    _compute_cell_metrics,
    _compute_factor_panel,
    _z_score,
)
from src.backtest.lot_sizing import (  # noqa: E402
    LOT_SIZE,
    compute_gross_return,
    size_whole_lots,
)

logger = logging.getLogger(__name__)

# v8.1 retail baseline capital (NT$).
INITIAL_CAPITAL = 1_000_000.0

_EMPTY_METRICS = {
    "ir": 0.0, "mean_alpha_monthly": 0.0, "te": 0.0,
    "max_dd_diff_vs_0050": 0.0, "active_corr": 0.0,
    "beta_adj_alpha_t": 0.0, "sharpe_for_dsr": 0.0,
}


def run_cell_sweep_v8(
    candidate_id: str,
    top_n: int,
    start_date: datetime,
    end_date: datetime,
    *,
    ctx: CellSweepContext,
    initial_capital: float = INITIAL_CAPITAL,
    lot_size: int = LOT_SIZE,
) -> tuple[dict[str, float], list[float], dict[str, Any]]:
    """Run one (candidate_id, top_n) cell with whole-lot position sizing.

    Steps 1-3 (factor panels / composite / top_n selection) are copied verbatim
    from `d_cell_sweep_v7_real.run_cell_sweep_real` so the ONLY changed variable
    is Step 4: continuous equal weight -> whole-lot sizing.

    `lot_size=1` recovers (up to sub-1-share rounding) the v7 continuous limit —
    used by the equivalence verification.

    Returns:
        (metrics_dict, monthly_active_returns_list, feasibility_dict)
    """
    if candidate_id not in CANDIDATE_FACTOR_SETS:
        raise ValueError(
            f"candidate_id {candidate_id} not in CANDIDATE_FACTOR_SETS "
            f"{CANDIDATE_FACTOR_SETS}; D-A pre-disqualified per V0.13 Assertion 2."
        )
    if top_n not in TOP_N_VALUES:
        raise ValueError(
            f"top_n {top_n} not in TOP_N_VALUES {TOP_N_VALUES} (pre-commit #7 frozen)"
        )

    cfg = load_candidate_config(candidate_id)
    weights: dict[str, float] = dict(cfg["factors"])

    month_ends = ctx.month_ends
    bench_monthly = ctx.benchmark_monthly_returns

    monthly_port_rets: list[float] = []
    monthly_active_rets: list[float] = []
    monthly_bench_rets: list[float] = []
    held_prev: set[str] = set()
    nav = float(initial_capital)

    # Feasibility logs (only appended for months where a portfolio was formed).
    feas_n_feasible: list[int] = []
    feas_invested_ratio: list[float] = []
    feas_weight_dev: list[float] = []
    # Count months where a selected stock had no forward return. v7's np.mean
    # renormalises over available-return stocks; v8's compute_gross_return holds
    # the missing name flat (0%). They diverge only in these months — surfacing
    # the count makes that v7-vs-v8 difference auditable rather than silent.
    n_months_missing_return = 0

    for i in range(len(month_ends) - 1):
        rebal = month_ends[i]
        next_rebal = month_ends[i + 1]
        rebal_ts = pd.Timestamp(rebal).normalize()

        # --- Step 1: factor panels (verbatim from v7) ---
        factor_panels: dict[str, pd.Series] = {}
        for fname in weights:
            try:
                panel = _compute_factor_panel(fname, ctx, rebal_ts)
            except Exception as exc:
                logger.warning("[%s/%s/%s] factor %s failed: %s",
                               candidate_id, top_n, rebal.date(), fname, exc)
                panel = pd.Series(dtype=float)
            factor_panels[fname] = _z_score(panel)

        # --- Step 2: intersection universe + PIT-safe min-price filter (verbatim) ---
        common_syms = None
        for s in factor_panels.values():
            common_syms = set(s.index) if common_syms is None else common_syms & set(s.index)
        if common_syms is None or len(common_syms) < top_n:
            monthly_port_rets.append(0.0)
            monthly_bench_rets.append(0.0)
            monthly_active_rets.append(0.0)
            continue
        common_syms = {
            sid for sid in common_syms
            if sid in ctx.ohlcv_panel
            and _is_above_min_price_at(ctx.ohlcv_panel[sid], rebal_ts)
        }
        if len(common_syms) < top_n:
            monthly_port_rets.append(0.0)
            monthly_bench_rets.append(0.0)
            monthly_active_rets.append(0.0)
            continue

        composite = pd.Series(0.0, index=list(common_syms))
        for fname, panel in factor_panels.items():
            composite += weights[fname] * panel.reindex(composite.index).fillna(0.0)

        # --- Step 3: select top_n by composite (verbatim) ---
        top_syms = composite.nlargest(top_n).index.tolist()

        # --- Step 4 (v8.1): whole-lot position sizing (replaces v7 np.mean) ---
        # forward return per stock — same _next_month_return as v7
        stock_rets: dict[str, float] = {}
        for sid in top_syms:
            if sid in ctx.ohlcv_panel:
                r = _next_month_return(ctx.ohlcv_panel[sid], rebal, next_rebal)
                if r is not None:
                    stock_rets[sid] = r
        if not stock_rets:
            # mirror v7: no forward returns for any selected stock -> skip month
            monthly_port_rets.append(0.0)
            monthly_bench_rets.append(0.0)
            monthly_active_rets.append(0.0)
            continue
        if len(stock_rets) < len(top_syms):
            n_months_missing_return += 1

        # PIT-safe close price at rebalance (NaN if unavailable -> infeasible)
        prices: dict[str, float] = {}
        for sid in top_syms:
            px = float("nan")
            df = ctx.ohlcv_panel.get(sid)
            if df is not None:
                hist = df[df.index <= rebal_ts]
                if not hist.empty:
                    px = float(hist["close"].iloc[-1])
            prices[sid] = px

        target_weights = {sid: 1.0 / top_n for sid in top_syms}
        sized = size_whole_lots(target_weights, prices, nav, lot_size=lot_size)
        gross_ret = compute_gross_return(sized.actual_weights, stock_rets)

        # turnover cost — FROZEN at v7's top_syms symmetric-difference definition
        new_set = set(top_syms)
        turnover = (
            len(new_set.symmetric_difference(held_prev)) / (2 * top_n)
            if held_prev else 1.0
        )
        net_ret = gross_ret - turnover * TW_ROUND_TRIP_COST
        held_prev = new_set

        # dynamic NAV compounding
        nav = nav * (1.0 + net_ret)

        bench_ts = pd.Timestamp(next_rebal).normalize()
        bench_ret = float(bench_monthly.get(bench_ts, np.nan))
        if pd.isna(bench_ret):
            bench_ret = 0.0

        monthly_port_rets.append(net_ret)
        monthly_bench_rets.append(bench_ret)
        monthly_active_rets.append(net_ret - bench_ret)
        feas_n_feasible.append(sized.n_feasible)
        feas_invested_ratio.append(sized.invested_ratio)
        feas_weight_dev.append(sized.weight_deviation_l1)

    # --- metrics (reuse v7 _compute_cell_metrics unchanged) ---
    rebal_idx = pd.DatetimeIndex(
        [pd.Timestamp(d).normalize() for d in month_ends[1:1 + len(monthly_port_rets)]]
    )
    p_series = pd.Series(monthly_port_rets, index=rebal_idx)
    b_series = pd.Series(monthly_bench_rets, index=rebal_idx)
    a_series = pd.Series(monthly_active_rets, index=rebal_idx)
    metrics = _compute_cell_metrics(a_series, p_series, b_series)

    feasibility = {
        "top_n": top_n,
        "n_months_active": len(feas_n_feasible),
        "avg_n_feasible": float(np.mean(feas_n_feasible)) if feas_n_feasible else 0.0,
        "avg_invested_ratio": float(np.mean(feas_invested_ratio)) if feas_invested_ratio else 0.0,
        "avg_cash_drag": (
            1.0 - float(np.mean(feas_invested_ratio)) if feas_invested_ratio else 0.0
        ),
        "avg_weight_deviation_l1": float(np.mean(feas_weight_dev)) if feas_weight_dev else 0.0,
        "n_months_missing_return": n_months_missing_return,
        "final_nav": nav,
        "lot_size": lot_size,
    }
    return metrics, monthly_active_rets, feasibility


def run_full_18_cell_sweep_v8(
    start_date: datetime,
    end_date: datetime,
    output_dir: pathlib.Path,
    cache_dir: pathlib.Path | None = None,
    *,
    initial_capital: float = INITIAL_CAPITAL,
    lot_size: int = LOT_SIZE,
) -> dict[str, Any]:
    """Run all 18 v7 cells with whole-lot sizing; persist incrementally.

    Outputs cell_metrics.json (7-key, v7-schema) + cell_monthly_active_returns.json
    + cell_feasibility.json under output_dir.
    """
    from src.utils.paths import resolve_cache_dir

    if cache_dir is None:
        cache_dir = resolve_cache_dir()

    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "cell_metrics.json"
    returns_path = output_dir / "cell_monthly_active_returns.json"
    feas_path = output_dir / "cell_feasibility.json"

    logger.info("Building CellSweepContext (loading universe + factor data sources)...")
    ctx = CellSweepContext(cache_dir, start_date, end_date)
    logger.info(
        "Universe: %d OHLCV stocks; benchmark: %d monthly returns; capital: NT$%.0f",
        len(ctx.ohlcv_panel), len(ctx.benchmark_monthly_returns), initial_capital,
    )

    cell_metrics: dict[str, dict[str, float]] = {}
    cell_returns: dict[str, list[float]] = {}
    cell_feasibility: dict[str, dict[str, Any]] = {}

    for candidate_id in CANDIDATE_FACTOR_SETS:
        for top_n in TOP_N_VALUES:
            cell_key = f"{candidate_id}|{top_n}"
            logger.info("Running cell %s (whole-lot) ...", cell_key)
            try:
                metrics, returns, feasibility = run_cell_sweep_v8(
                    candidate_id, top_n, start_date, end_date,
                    ctx=ctx, initial_capital=initial_capital, lot_size=lot_size,
                )
            except Exception as exc:
                logger.error("Cell %s FAILED: %s", cell_key, exc)
                metrics = {**_EMPTY_METRICS, "error": str(exc)}
                returns = []
                feasibility = {"error": str(exc)}
            cell_metrics[cell_key] = metrics
            cell_returns[cell_key] = returns
            cell_feasibility[cell_key] = feasibility
            # incremental persistence (resilience against mid-run crash)
            metrics_path.write_text(
                json.dumps(cell_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
            returns_path.write_text(
                json.dumps(cell_returns, ensure_ascii=False, indent=2), encoding="utf-8")
            feas_path.write_text(
                json.dumps(cell_feasibility, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(
                "Cell %s: ir=%.4f mean_α=%.4f te=%.4f | avg_invested=%.1f%% avg_lots_held=%.1f/%d",
                cell_key, metrics.get("ir", 0.0), metrics.get("mean_alpha_monthly", 0.0),
                metrics.get("te", 0.0), feasibility.get("avg_invested_ratio", 0.0) * 100,
                feasibility.get("avg_n_feasible", 0.0), top_n,
            )

    logger.info("All 18 cells done (whole-lot). Output: %s", output_dir)
    return {
        "cell_metrics": cell_metrics,
        "cell_returns": cell_returns,
        "cell_feasibility": cell_feasibility,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="v8.1 whole-lot position-sizing 18-cell sweep")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output-dir", type=pathlib.Path,
                        help="Output dir (required unless --smoke)")
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL,
                        help="Initial NT$ capital (default 1,000,000)")
    parser.add_argument("--lot-size", type=int, default=LOT_SIZE,
                        help="Shares per lot (default 1000; set 1 for v7-equivalence check)")
    parser.add_argument("--smoke", action="store_true",
                        help="Smoke test: D-B / top_n=8 / single context")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")

    if args.smoke:
        logger.info("Smoke: D-B / top_n=8 / lot_size=%d / %s ~ %s",
                    args.lot_size, start.date(), end.date())
        from src.utils.paths import resolve_cache_dir
        ctx = CellSweepContext(resolve_cache_dir(), start, end)
        metrics, returns, feasibility = run_cell_sweep_v8(
            "D-B", 8, start, end, ctx=ctx,
            initial_capital=args.capital, lot_size=args.lot_size,
        )
        logger.info("Smoke metrics: %s", metrics)
        logger.info("Smoke feasibility: %s", feasibility)
        logger.info("Smoke returns (n=%d): %s", len(returns), returns[:5])
    else:
        if args.output_dir is None:
            parser.error("--output-dir is required unless --smoke")
        result = run_full_18_cell_sweep_v8(
            start, end, args.output_dir,
            initial_capital=args.capital, lot_size=args.lot_size,
        )
        logger.info("Done. Cells: %d", len(result["cell_metrics"]))


if __name__ == "__main__":
    main()
