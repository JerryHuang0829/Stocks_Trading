"""v5.0 architecture review:
  - Fair baseline: z-score equal-weight on FULL 57 features (vs LOCKED baseline 5)
  - Single-factor strategy: idio_vol_max only (SHAP top feature)
  - Compare both to ML best cell (xgboost top_n=15)

Answers Codex-style red flags:
  #1 Baseline using only 5 features → unfair vs ML's 57 features
  #3 idio_vol_max SHAP 54% dominance → does single factor capture most?

Builds OOS matrix on the fly (~5-7 min). Does NOT touch IS (faster than
production rerun).
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys
from datetime import datetime

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from scripts._factor_ic_helpers import (  # noqa: E402
    REGIME_SYMBOL,
    _compute_intersection_universe,
    _compute_regimes,
    _load_industry_labels,
    _load_ohlcv,
    _load_universe_ohlcv,
    _load_universe_timeseries,
)
from src.analysis.ml_contextual import ContextualConfig, add_contextual_features  # noqa: E402
from src.analysis.ml_features import LOCKED_FEATURE_NAMES, compute_feature_panel  # noqa: E402
from src.analysis.ml_models import baseline_score  # noqa: E402
from src.analysis.ml_optuna import _portfolio_monthly_returns_from_scores, _sharpe_ratio  # noqa: E402
from src.analysis.ml_pooling import PoolingConfig, build_training_matrix  # noqa: E402
from src.backtest.engine import BacktestEngine  # noqa: E402
from src.backtest.metrics import adjust_splits_ohlc  # noqa: E402
from src.data.pit_helpers import _issued_capital_asof, _load_issued_capital_panel  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logger = logging.getLogger("v5_fair_baseline")

OOS_START = datetime(2025, 1, 1)
OOS_END = datetime(2025, 12, 31)
REBALANCE_DAY = 12


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    cache_dir = resolve_cache_dir()

    logger.info("loading minimal cache for OOS-only run ...")
    ohlcv_by_symbol = _load_universe_ohlcv(cache_dir)
    close_by_symbol = {s: df["close"].copy() for s, df in ohlcv_by_symbol.items()}
    eps_by_symbol = _load_universe_timeseries(cache_dir / "quarterly_eps")
    industry_map = _load_industry_labels(cache_dir) or {}
    issued_panel = _load_issued_capital_panel(cache_dir)
    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    market_returns = adjust_splits_ohlc(benchmark)["close"].pct_change().dropna()
    intersection = _compute_intersection_universe(cache_dir, log=logger)
    universe_filter = set(intersection) if intersection else None

    all_dates = sorted({idx for df in ohlcv_by_symbol.values() for idx in df.index})
    oos_dates = BacktestEngine._generate_rebalance_dates(
        OOS_START, OOS_END, REBALANCE_DAY,
        trading_days=pd.DatetimeIndex(all_dates),
    )
    logger.info("OOS rebalance dates: %d", len(oos_dates))

    # OOS panels
    logger.info("building OOS panels ...")
    oos_panels = {}
    for d in oos_dates:
        ts = pd.Timestamp(d)
        if ts.tz is not None:
            ts = ts.tz_convert(None)
        try:
            panel = compute_feature_panel(
                as_of=ts, ohlcv_by_symbol=ohlcv_by_symbol,
                eps_by_symbol=eps_by_symbol, market_returns=market_returns,
                industry_map=industry_map, universe_filter=universe_filter,
            )
            oos_panels[ts] = panel
        except Exception as exc:
            logger.warning("panel %s failed: %s", ts.date(), exc)

    # OOS training matrix
    config = PoolingConfig(
        feature_names=LOCKED_FEATURE_NAMES,
        forbidden_oos_start=pd.Timestamp("2027-01-01"),   # generous for OOS-mode
    )
    oos_df, _ = build_training_matrix(
        oos_panels, close_by_symbol, sorted(oos_panels.keys()), config,
    )
    logger.info("OOS matrix: %d rows", len(oos_df))

    # Build size + regime panels for contextual
    size_panel = {}
    for d in oos_dates:
        ts = pd.Timestamp(d)
        if ts.tz is not None:
            ts = ts.tz_convert(None)
        shares = _issued_capital_asof(issued_panel, ts)
        cutoff = ts - pd.Timedelta(days=1)
        period = {}
        for sym, n_shares in shares.items():
            cs = close_by_symbol.get(sym)
            if cs is None or cs.empty:
                continue
            view = cs[cs.index <= cutoff].dropna()
            if view.empty:
                continue
            price = float(view.iloc[-1])
            if price > 0 and n_shares > 0:
                period[sym] = price * float(n_shares)
        size_panel[ts] = period

    oos_regimes = _compute_regimes(benchmark, oos_dates, {})
    regime_by_date = {
        pd.Timestamp(d): r for d, r in zip(oos_dates, oos_regimes)
    }

    oos_full, _ = add_contextual_features(
        oos_df, industry_map, size_panel, regime_by_date, ContextualConfig(),
    )

    # Complete-case drop
    base_cols = {"symbol", "as_of", "label_end", "forward_return", "label_top_decile"}
    feat_cols_all = [c for c in oos_full.columns if c not in base_cols]
    oos_clean = oos_full.dropna(subset=feat_cols_all).reset_index(drop=True)
    logger.info("OOS clean: %d rows × %d feature cols", len(oos_clean), len(feat_cols_all))

    # =========================================================
    # Test A: Fair baseline — 57 features z-score equal weight
    # =========================================================
    logger.info("running FAIR baseline (57 features z-score equal weight) ...")
    fair_results = {}
    for top_n in (15, 20, 25, 30):
        scores = baseline_score(oos_clean, feat_cols_all)
        monthly = _portfolio_monthly_returns_from_scores(
            scores.values, oos_clean["as_of"].values,
            oos_clean["forward_return"].values, top_n,
        )
        fair_results[top_n] = {
            "sharpe": _sharpe_ratio(monthly, 12),
            "n_periods": len(monthly),
        }

    # =========================================================
    # Test B: Single factor idio_vol_max
    # =========================================================
    logger.info("running SINGLE FACTOR idio_vol_max ...")
    single_results = {}
    for top_n in (15, 20, 25, 30):
        scores = oos_clean["idio_vol_max"]
        monthly = _portfolio_monthly_returns_from_scores(
            scores.values, oos_clean["as_of"].values,
            oos_clean["forward_return"].values, top_n,
        )
        single_results[top_n] = {
            "sharpe": _sharpe_ratio(monthly, 12),
            "n_periods": len(monthly),
        }

    # =========================================================
    # Compare to ML cell results (from production cell_summary)
    # =========================================================
    cs = json.load(open("reports/phase_d_v5/v5_ml_cell_summary.json", encoding="utf-8"))
    ml_xgb = {c["top_n"]: c["oos_sharpe"]
              for c in cs["ml_cells"] if c["model_name"] == "xgboost"}
    ml_lmart = {c["top_n"]: c["oos_sharpe"]
                for c in cs["ml_cells"] if c["model_name"] == "lambdamart"}
    locked_baseline = {b["top_n"]: b["oos_sharpe"]
                       for b in cs["baseline_cells"]}

    # =========================================================
    # Output
    # =========================================================
    out = {
        "run_date": datetime.now().isoformat(timespec="seconds"),
        "oos_rows_clean": len(oos_clean),
        "n_total_features": len(feat_cols_all),
        "fair_baseline_57feat": fair_results,
        "single_factor_idio_vol_max": single_results,
        "ml_xgboost": ml_xgb,
        "ml_lambdamart": ml_lmart,
        "locked_baseline_5feat": locked_baseline,
    }
    out_path = pathlib.Path("reports/phase_d_v5/v5_architecture_check.json")
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %s", out_path)

    print()
    print("=" * 80)
    print("v5.0 ARCHITECTURE CHECK — OOS Sharpe across strategies")
    print("=" * 80)
    print(f"{'top_n':>5s} | {'XGB':>7s} | {'LMart':>7s} | {'fair_bl_57f':>11s} | "
          f"{'single_idiovol':>13s} | {'locked_bl_5f':>11s}")
    print("-" * 80)
    for top_n in (15, 20, 25, 30):
        xgb = ml_xgb.get(top_n, float('nan'))
        lmart = ml_lmart.get(top_n, float('nan'))
        fair = fair_results[top_n]["sharpe"]
        single = single_results[top_n]["sharpe"]
        locked = locked_baseline.get(top_n, float('nan'))
        print(f"{top_n:5d} | {xgb:+7.4f} | {lmart:+7.4f} | {fair:+11.4f} | "
              f"{single:+13.4f} | {locked:+11.4f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
