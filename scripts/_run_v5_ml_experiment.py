"""v5.0 Step 5 — Run ML experiment + write deliverables.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §13 condition 14 deliverables:
    - reports/phase_d_v5/v5_ml_cell_summary.json
    - reports/phase_d_v5/v5_ml_vs_baseline.md
    - reports/phase_d_v5/v5_dsr_audit.json
    - reports/phase_d_v5/v5_shap_summary.json
    - reports/phase_d_v5/v5_outcome.md

Two run modes:
  --smoke      n_trials=5, fast(~10-30 min)— validate end-to-end
  --production n_trials=50(LOCKED per pre-reg §8.3)— 多小時 production
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
    REGIME_SYMBOL,
    _compute_intersection_universe,
    _compute_regimes,
    _load_industry_labels,
    _load_ohlcv,
    _load_universe_ohlcv,
    _load_universe_timeseries,
    load_dividends_list,
    split_adjust_close_panel,
    split_adjust_ohlcv_panel,
    total_return_adjust_close_panel,
)
from src.analysis.cpcv import CPCVConfig  # noqa: E402
from src.analysis.ml_contextual import (  # noqa: E402
    ContextualConfig,
    add_contextual_features,
)
from src.analysis.ml_experiment import (  # noqa: E402
    build_vs_baseline_summary,
    run_baseline_cells,
    run_ml_cells,
)
from src.analysis.ml_features import LOCKED_FEATURE_NAMES, compute_feature_panel  # noqa: E402
from src.analysis.ml_optuna import OptunaConfig  # noqa: E402
from src.analysis.ml_pooling import PoolingConfig, build_training_matrix  # noqa: E402
from src.analysis.ml_shap import build_shap_summary  # noqa: E402
from src.backtest.engine import BacktestEngine  # noqa: E402
from src.backtest.metrics import adjust_splits_ohlc  # noqa: E402
from src.data.pit_helpers import _issued_capital_asof, _load_issued_capital_panel  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logger = logging.getLogger("v5_ml_experiment")

IS_START = datetime(2020, 1, 1)
IS_END = datetime(2024, 12, 31)
OOS_START = datetime(2025, 1, 1)
OOS_END = datetime(2025, 12, 31)
FORBIDDEN_OOS_START = pd.Timestamp("2025-01-01")
REBALANCE_DAY = 12


def _build_market_returns(benchmark_ohlcv: pd.DataFrame) -> pd.Series:
    adjusted = adjust_splits_ohlc(benchmark_ohlcv)
    return adjusted["close"].pct_change().dropna()


def _build_size_panel_by_date(
    issued_panel: pd.DataFrame,
    close_by_symbol: dict[str, pd.Series],
    as_of_dates: list[pd.Timestamp],
) -> dict[pd.Timestamp, dict[str, float]]:
    """market_cap = close[as_of - 1d] × issued_shares[as_of]."""
    out: dict[pd.Timestamp, dict[str, float]] = {}
    for date in as_of_dates:
        ts = pd.Timestamp(date)
        if ts.tz is not None:
            ts = ts.tz_convert(None)
        shares = _issued_capital_asof(issued_panel, ts)
        cutoff = ts - pd.Timedelta(days=1)
        period: dict[str, float] = {}
        for sym, n_shares in shares.items():
            close = close_by_symbol.get(sym)
            if close is None or close.empty:
                continue
            view = close[close.index <= cutoff].dropna()
            if view.empty:
                continue
            price = float(view.iloc[-1])
            if price > 0 and n_shares > 0:
                period[sym] = price * float(n_shares)
        out[ts] = period
    return out


def _build_rebalance_dates(ohlcv_by_symbol, start, end):
    all_dates = sorted({idx for df in ohlcv_by_symbol.values() for idx in df.index})
    return BacktestEngine._generate_rebalance_dates(
        start, end, REBALANCE_DAY, trading_days=pd.DatetimeIndex(all_dates),
    )


def _build_matrix(
    as_of_dates: list[pd.Timestamp],
    ohlcv_by_symbol, eps_by_symbol, market_returns, industry_map,
    universe_filter, label_close_by_symbol, label_name: str,
    *, adjusted_ohlcv_by_symbol=None,
):
    # ohlcv_by_symbol = RAW (value_ep price-level); adjusted_ohlcv_by_symbol =
    # split-adjusted (ratio/return features); label_close_by_symbol =
    # total-return (forward-return label, symmetric with adjusted benchmark).
    panels = {}
    for date in as_of_dates:
        ts = pd.Timestamp(date)
        if ts.tz is not None:
            ts = ts.tz_convert(None)
        try:
            panel = compute_feature_panel(
                as_of=ts, ohlcv_by_symbol=ohlcv_by_symbol,
                adjusted_ohlcv_by_symbol=adjusted_ohlcv_by_symbol,
                eps_by_symbol=eps_by_symbol, market_returns=market_returns,
                industry_map=industry_map, universe_filter=universe_filter,
            )
        except Exception as exc:
            logger.warning("[%s] panel %s failed: %s", label_name, ts.date(), exc)
            continue
        panels[ts] = panel
    config = PoolingConfig(
        feature_names=LOCKED_FEATURE_NAMES,
        forbidden_oos_start=FORBIDDEN_OOS_START,
    )
    accepted = sorted(panels.keys())
    # Need at least 2 as_ofs to build labels
    if len(accepted) < 2:
        raise RuntimeError(f"[{label_name}] only {len(accepted)} valid panels")
    df, diag = build_training_matrix(panels, label_close_by_symbol, accepted, config)
    return df, diag, panels


def _add_oos_panel_at_label_end(panels, df, ohlcv_by_symbol, eps_by_symbol,
                                  market_returns, industry_map, universe_filter,
                                  close_by_symbol):
    """For OOS, label_end may be beyond the last accepted as_of (since OOS
    runs 2025-01..2025-12). Ensure the last as_of has a forward window."""
    # No-op for now — caller handles ranges
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="v5.0 Step 5 ML experiment (smoke or production)"
    )
    parser.add_argument("--mode", choices=["smoke", "production"], required=True)
    parser.add_argument("--output-dir", type=pathlib.Path,
                        default=pathlib.Path("reports/phase_d_v5"))
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    cache_dir = resolve_cache_dir()
    logger.info("cache: %s", cache_dir)
    logger.info("mode: %s", args.mode)

    # Per-mode config
    if args.mode == "smoke":
        n_trials = 5
        inner_cv_n_splits = 3
        top_n_values = (15, 30)   # 2 instead of 4 for smoke
        models = ("xgboost",)     # 1 instead of 2 for smoke
    else:
        n_trials = 50              # pre-reg §8.3 LOCK
        inner_cv_n_splits = 5      # pre-reg §8.3 LOCK
        top_n_values = (15, 20, 25, 30)
        models = ("xgboost", "lambdamart")

    optuna_config = OptunaConfig(
        n_trials=n_trials,
        inner_cv_n_splits=inner_cv_n_splits,
        early_stopping_rounds=20,
    )

    # 1. Load all data
    logger.info("loading OHLCV ...")
    ohlcv_by_symbol = _load_universe_ohlcv(cache_dir)
    close_by_symbol = {s: df["close"].copy() for s, df in ohlcv_by_symbol.items()}
    # Split-adjusted OHLC for ratio/return features; total-return close for the
    # forward-return label (symmetric with the split+dividend-adjusted 0050
    # benchmark). RAW close_by_symbol / ohlcv_by_symbol stay for value_ep (E/P)
    # and the size factor (market cap = price x shares) — price-level inputs
    # whose unit consistency with EPS / shares must not be broken.
    adjusted_ohlcv_by_symbol = split_adjust_ohlcv_panel(ohlcv_by_symbol)
    dividends_list = load_dividends_list(cache_dir)
    if dividends_list:
        label_close_by_symbol = total_return_adjust_close_panel(
            close_by_symbol, dividends_list
        )
        logger.info(
            "forward-return label: total-return (split+dividend), %d dividend records",
            len(dividends_list),
        )
    else:
        label_close_by_symbol = split_adjust_close_panel(close_by_symbol)
        logger.warning(
            "dividends cache empty — label is split-only (price return), NOT "
            "total-return"
        )
    eps_by_symbol = _load_universe_timeseries(cache_dir / "quarterly_eps")
    industry_map = _load_industry_labels(cache_dir) or {}
    issued_panel = _load_issued_capital_panel(cache_dir)
    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    market_returns = _build_market_returns(benchmark)
    intersection = _compute_intersection_universe(cache_dir, log=logger)
    universe_filter = set(intersection) if intersection else None

    # 2. Build IS + OOS as_of date ranges + panels + training matrices
    logger.info("building IS rebalance dates ...")
    is_dates = _build_rebalance_dates(ohlcv_by_symbol, IS_START, IS_END)
    is_dates_kept = [d for d in is_dates if pd.Timestamp(d) < FORBIDDEN_OOS_START]
    logger.info("  IS as_of dates: %d", len(is_dates_kept))

    logger.info("building OOS rebalance dates ...")
    oos_dates = _build_rebalance_dates(ohlcv_by_symbol, OOS_START, OOS_END)
    logger.info("  OOS as_of dates: %d", len(oos_dates))

    # IS training matrix
    logger.info("building IS training matrix ...")
    is_df, is_diag, is_panels = _build_matrix(
        is_dates_kept, ohlcv_by_symbol, eps_by_symbol, market_returns,
        industry_map, universe_filter, label_close_by_symbol, "IS",
        adjusted_ohlcv_by_symbol=adjusted_ohlcv_by_symbol,
    )
    logger.info("  IS matrix: %d rows", len(is_df))

    # OOS matrix — for OOS prediction we also need PANELS at each OOS as_of
    # but OOS pooling uses a relaxed OOS guard (label_end may extend to 2026)
    logger.info("building OOS feature panels ...")
    # Build OOS panels separately; pool with the next month as label
    oos_panels = {}
    for date in oos_dates:
        ts = pd.Timestamp(date)
        if ts.tz is not None:
            ts = ts.tz_convert(None)
        try:
            panel = compute_feature_panel(
                as_of=ts, ohlcv_by_symbol=ohlcv_by_symbol,
                adjusted_ohlcv_by_symbol=adjusted_ohlcv_by_symbol,
                eps_by_symbol=eps_by_symbol, market_returns=market_returns,
                industry_map=industry_map, universe_filter=universe_filter,
            )
        except Exception as exc:
            logger.warning("[OOS] panel %s failed: %s", ts.date(), exc)
            continue
        oos_panels[ts] = panel
    # Use a separate PoolingConfig where OOS boundary is set beyond OOS_END
    # so OOS pairs (t, t_next) can pass the guard.
    oos_config = PoolingConfig(
        feature_names=LOCKED_FEATURE_NAMES,
        forbidden_oos_start=pd.Timestamp("2027-01-01"),   # generous for OOS use
    )
    oos_accepted = sorted(oos_panels.keys())
    if len(oos_accepted) < 2:
        raise RuntimeError(f"only {len(oos_accepted)} OOS panels — cannot build pairs")
    oos_df, oos_diag = build_training_matrix(
        oos_panels, label_close_by_symbol, oos_accepted, oos_config,
    )
    logger.info("  OOS matrix: %d rows", len(oos_df))

    # 3. Add contextual features
    logger.info("adding contextual features ...")
    is_size_panel = _build_size_panel_by_date(issued_panel, close_by_symbol,
                                              [pd.Timestamp(d) for d in is_dates_kept])
    oos_size_panel = _build_size_panel_by_date(issued_panel, close_by_symbol,
                                                [pd.Timestamp(d) for d in oos_dates])
    is_regimes_list = _compute_regimes(benchmark, is_dates_kept, {})
    oos_regimes_list = _compute_regimes(benchmark, oos_dates, {})
    is_regime_by_date = {
        pd.Timestamp(d): r for d, r in zip(is_dates_kept, is_regimes_list)
    }
    oos_regime_by_date = {
        pd.Timestamp(d): r for d, r in zip(oos_dates, oos_regimes_list)
    }

    ctx_config = ContextualConfig()
    is_full_df, is_ctx_diag = add_contextual_features(
        is_df, industry_map, is_size_panel, is_regime_by_date, ctx_config,
    )
    oos_full_df, oos_ctx_diag = add_contextual_features(
        oos_df, industry_map, oos_size_panel, oos_regime_by_date, ctx_config,
    )

    # 4. Identify all feature columns (5 base + sector + size + regime + interactions)
    base_cols = {"symbol", "as_of", "label_end", "forward_return", "label_top_decile"}
    is_feature_cols = [c for c in is_full_df.columns if c not in base_cols]
    oos_feature_cols = [c for c in oos_full_df.columns if c not in base_cols]
    # Align: keep intersection of columns (sector dummies may differ between IS/OOS)
    common_feature_cols = [c for c in is_feature_cols if c in oos_feature_cols]
    logger.info("feature cols (IS=%d, OOS=%d, common=%d)",
                len(is_feature_cols), len(oos_feature_cols), len(common_feature_cols))

    # Drop rows with any NaN in feature_cols (complete-case for ML training).
    # Most common NaN source: size_decile when issued_capital missing for symbol.
    is_clean = is_full_df.dropna(subset=common_feature_cols).reset_index(drop=True)
    oos_clean = oos_full_df.dropna(subset=common_feature_cols).reset_index(drop=True)
    logger.info("dropping NaN-row complete-case: IS %d → %d; OOS %d → %d",
                len(is_full_df), len(is_clean), len(oos_full_df), len(oos_clean))

    # 5. Run ML cells + baseline cells
    logger.info("running %d ML cells × %d Optuna trials each (mode=%s) ...",
                len(models) * len(top_n_values), n_trials, args.mode)
    # production MUST use CPCV (k=5 / n_test=2 / embargo=1m) per pre-reg §9.
    # smoke mode uses smaller k=3 / n_test=1 to stay fast.
    if args.mode == "production":
        cpcv_config = CPCVConfig(n_splits=5, n_test_splits=2, embargo_months=1)
    else:
        cpcv_config = CPCVConfig(n_splits=3, n_test_splits=1, embargo_months=1)
    logger.info("CPCV config: k=%d, n_test=%d, embargo=%d → %d paths",
                cpcv_config.n_splits, cpcv_config.n_test_splits,
                cpcv_config.embargo_months, cpcv_config.n_paths)

    ml_cells = run_ml_cells(
        is_clean, oos_clean, common_feature_cols,
        optuna_config=optuna_config, top_n_values=top_n_values, models=models,
        cpcv_config=cpcv_config,
    )
    logger.info("running %d baseline cells ...", len(top_n_values))
    baseline_cells = run_baseline_cells(
        oos_clean, list(LOCKED_FEATURE_NAMES), top_n_values=top_n_values,
    )

    # 6. Vs-baseline comparison
    vs_df = build_vs_baseline_summary(ml_cells, baseline_cells)

    # 7. Write deliverables
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    cell_summary = {
        "run_date": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "n_trials": n_trials,
        "inner_cv_n_splits": inner_cv_n_splits,
        "is_window": [IS_START.date().isoformat(), IS_END.date().isoformat()],
        "oos_window": [OOS_START.date().isoformat(), OOS_END.date().isoformat()],
        "models": list(models),
        "top_n_values": list(top_n_values),
        "is_rows": int(len(is_full_df)),
        "oos_rows": int(len(oos_full_df)),
        "common_feature_cols": common_feature_cols,
        "ml_cells": [c.to_dict() for c in ml_cells],
        "baseline_cells": [b.to_dict() for b in baseline_cells],
    }
    suffix = "_smoke" if args.mode == "smoke" else ""
    cell_path = out_dir / f"v5_ml_cell_summary{suffix}.json"
    cell_path.write_text(json.dumps(cell_summary, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    logger.info("wrote %s", cell_path)

    vs_path = out_dir / f"v5_ml_vs_baseline{suffix}.md"
    vs_md = ["# v5.0 ML vs Baseline OOS Comparison",
             f"**Mode**: {args.mode}",
             f"**Run**: {cell_summary['run_date']}", "",
             vs_df.to_markdown(index=False)]
    vs_path.write_text("\n".join(vs_md), encoding="utf-8")
    logger.info("wrote %s", vs_path)

    # 8. SHAP on best ML cell (highest oos_sharpe)
    if ml_cells:
        best = max(ml_cells, key=lambda c: c.oos_sharpe)
        logger.info("running SHAP on best cell: %s top_n=%d",
                    best.model_name, best.top_n)
        from src.analysis.ml_models import LambdaMARTWrapper, XGBoostClassifierWrapper
        if best.model_name == "xgboost":
            model = XGBoostClassifierWrapper(**best.best_hyperparams)
            X_is = is_clean[common_feature_cols].values.astype(float)
            y_is = is_clean["label_top_decile"].values.astype(int)
            model.fit(X_is, y_is)
        else:
            model = LambdaMARTWrapper(**best.best_hyperparams)
            X_is = is_clean[common_feature_cols].values.astype(float)
            y_is = is_clean["label_top_decile"].values.astype(int)
            from src.analysis.ml_experiment import _make_groups
            model.fit(X_is, y_is, group_train=_make_groups(is_clean["as_of"].values))
        X_oos_for_shap = oos_clean[common_feature_cols].values.astype(float)
        # Locked interaction pairs from pre-reg §6
        interaction_pairs = [
            ("value_ep_sn", "regime_trending_up"),
            ("pead_eps_sn", "high_proximity"),     # earnings season → use high_prox as approx
            ("idio_vol_max", "regime_trending_down"),
            ("reversal_1m", "high_proximity"),
            ("value_ep_sn", "size_decile"),
        ]
        shap_summary = build_shap_summary(
            model, X_oos_for_shap, common_feature_cols,
            interaction_pairs=interaction_pairs,
            max_interaction_samples=500,
        )
        shap_path = out_dir / f"v5_shap_summary{suffix}.json"
        shap_path.write_text(json.dumps(shap_summary.to_dict(), indent=2,
                                       ensure_ascii=False),
                             encoding="utf-8")
        logger.info("wrote %s", shap_path)

    # 9. Outcome verdict (preliminary)
    n_pass_l_gates = sum(int(c.oos_sharpe >= 0.2) for c in ml_cells)
    best_diff = vs_df["sharpe_diff"].max() if not vs_df.empty else 0.0
    outcome = ["# v5.0 Outcome (preliminary, mode=" + args.mode + ")",
               f"**Run**: {cell_summary['run_date']}",
               "",
               "## Headline",
               f"- ML cells: {len(ml_cells)}",
               f"- Baseline cells: {len(baseline_cells)}",
               f"- Best ML vs baseline Sharpe diff: {best_diff:+.4f}",
               "  (pre-reg §1 requires ≥ +0.05)",
               f"- ML cells with OOS Sharpe ≥ 0.20 (rough L1 proxy): {n_pass_l_gates}",
               "",
               "## Detail",
               vs_df.to_markdown(index=False),
               "",
               "## Notes",
               "- This is a smoke / preliminary outcome — full L1-L6 gate evaluation",
               "  + DSR n_trials=400 correction belongs in Step 6 audit.",
               ]
    outcome_path = out_dir / f"v5_outcome{suffix}.md"
    outcome_path.write_text("\n".join(outcome), encoding="utf-8")
    logger.info("wrote %s", outcome_path)

    print()
    print("=" * 60)
    print(f"v5.0 ML Experiment Summary (mode={args.mode})")
    print("=" * 60)
    print(f"  IS rows               : {len(is_full_df)}")
    print(f"  OOS rows              : {len(oos_full_df)}")
    print(f"  feature cols          : {len(common_feature_cols)}")
    print(f"  ML cells              : {len(ml_cells)}")
    print(f"  baseline cells        : {len(baseline_cells)}")
    print(f"  best ML vs baseline   : {best_diff:+.4f}")
    print(f"  cells with Sh >= 0.20 : {n_pass_l_gates}")
    print(f"  deliverables written  : {out_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
