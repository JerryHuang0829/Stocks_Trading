"""v5.0 ML pooling integration smoke: full 2020-2024 IS training matrix.

End-to-end exercise of `ml_pooling.build_training_matrix()` +
`ml_features.compute_feature_panel()` against real cache data.

Purpose:
  - Verify the full pipeline produces the expected ~75-78K row matrix
  - Validate schema + OOS boundary + diagnostics work on real data
  - Surface universe drift / missing-feature attrition for review
  - Pre-engineering sanity check before contextual features + CPCV + ML models

NOT a production run — output is for ML pooling smoke validation only.

Output:
  reports/phase_d_v5/v5_4a_smoke_summary.json (row count, drop counts, per-period)
  reports/phase_d_v5/v5_4a_smoke_sample.parquet (training matrix, smoke artifact)

Usage:
  PowerShell:
    $env:PYTHONPATH='.'; & "<conda quant python>" -u scripts/_run_ml_pooling_smoke.py
  Bash:
    PYTHONPATH=. python -u scripts/_run_ml_pooling_smoke.py
"""
from __future__ import annotations

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
    _load_industry_labels,
    _load_ohlcv,
    _load_universe_ohlcv,
    _load_universe_timeseries,
)
from src.analysis.ml_features import LOCKED_FEATURE_NAMES, compute_feature_panel  # noqa: E402
from src.analysis.ml_pooling import PoolingConfig, build_training_matrix  # noqa: E402
from src.backtest.engine import BacktestEngine  # noqa: E402
from src.backtest.metrics import adjust_splits_ohlc  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logger = logging.getLogger("ml_pooling_smoke")

# IS window per pre-reg §13 condition 8 (2020-2024).
# forbidden_oos_start = 2025-01-01 per pre-reg §5.3.
IS_START = datetime(2020, 1, 1)
IS_END = datetime(2024, 12, 31)
FORBIDDEN_OOS_START = pd.Timestamp("2025-01-01")
REBALANCE_DAY = 12


def _build_market_returns(benchmark_ohlcv: pd.DataFrame) -> pd.Series:
    """0050 daily returns (split-adjusted)."""
    adjusted = adjust_splits_ohlc(benchmark_ohlcv)
    return adjusted["close"].pct_change().dropna()


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    cache_dir = resolve_cache_dir()
    logger.info("cache: %s", cache_dir)

    logger.info("loading universe OHLCV ...")
    ohlcv_by_symbol = _load_universe_ohlcv(cache_dir)
    close_by_symbol = {s: df["close"].copy() for s, df in ohlcv_by_symbol.items()}
    logger.info("  %d OHLCV symbols", len(ohlcv_by_symbol))

    logger.info("loading quarterly_eps ...")
    eps_by_symbol = _load_universe_timeseries(cache_dir / "quarterly_eps")
    logger.info("  %d EPS symbols", len(eps_by_symbol))

    logger.info("loading industry_map ...")
    industry_map = _load_industry_labels(cache_dir) or {}
    logger.info("  %d industry labels", len(industry_map))

    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    if benchmark is None or benchmark.empty:
        raise RuntimeError(f"benchmark {REGIME_SYMBOL} missing")
    market_returns = _build_market_returns(benchmark)
    logger.info("  market_returns: %d days (split-adjusted)", len(market_returns))

    logger.info("intersecting universe ...")
    intersection = _compute_intersection_universe(cache_dir, log=logger)
    universe_filter = set(intersection) if intersection else None
    logger.info("  intersection universe: %d symbols",
                len(universe_filter) if universe_filter else 0)

    # Build IS rebalance dates (2020-01 ~ 2024-12, monthly @ day 12)
    all_dates = sorted({idx for df in ohlcv_by_symbol.values() for idx in df.index})
    trading_days = pd.DatetimeIndex(all_dates)
    rebalance_dates = BacktestEngine._generate_rebalance_dates(
        IS_START, IS_END, REBALANCE_DAY, trading_days=trading_days,
    )
    logger.info("rebalance dates (IS 2020-2024): %d", len(rebalance_dates))

    # Filter so that BOTH as_of and label_end fall before OOS boundary
    # (drop tail dates whose forward label leaks into 2025).
    # as_of_dates passed to builder must satisfy:
    #   as_of[i] < FORBIDDEN_OOS_START
    #   as_of[i+1] < FORBIDDEN_OOS_START  (= label_end)
    # So the LAST as_of we accept is the one whose NEXT as_of is still IS.
    # Concretely: 2024-12-12 has label_end ~ 2025-01-12 → DROP. Last kept = 2024-11-12.
    accepted = [d for d in rebalance_dates
                if pd.Timestamp(d) < FORBIDDEN_OOS_START]
    # Trim tail until label_end also IS
    while len(accepted) >= 2 and pd.Timestamp(accepted[-1]) >= FORBIDDEN_OOS_START:
        accepted.pop()
    # Need the SECOND-TO-LAST as_of's label_end to be < OOS, meaning the LAST
    # element of accepted is the label_end for the second-to-last as_of.
    # The last element is itself unused (no further label_end available).
    logger.info("accepted as_of dates (after OOS boundary trim): %d", len(accepted))

    # Compute per-as_of feature panels
    logger.info("computing per-as_of feature panels (this is the slow step) ...")
    panels_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
    for i, date in enumerate(accepted):
        ts = pd.Timestamp(date)
        if ts.tz is not None:
            ts = ts.tz_convert(None)
        try:
            panel = compute_feature_panel(
                as_of=ts,
                ohlcv_by_symbol=ohlcv_by_symbol,
                eps_by_symbol=eps_by_symbol,
                market_returns=market_returns,
                industry_map=industry_map,
                universe_filter=universe_filter,
            )
        except Exception as exc:
            logger.warning("[%s] compute_feature_panel failed: %s", ts.date(), exc)
            continue
        panels_by_date[ts] = panel
        if (i + 1) % 12 == 0:
            logger.info("  %d/%d panels (latest: %s rows=%d)",
                        i + 1, len(accepted), ts.date(), len(panel))

    logger.info("panels built: %d / %d", len(panels_by_date), len(accepted))

    # Build training matrix
    config = PoolingConfig(
        feature_names=LOCKED_FEATURE_NAMES,
        forbidden_oos_start=FORBIDDEN_OOS_START,
    )
    accepted_ts = [pd.Timestamp(d) for d in accepted]
    df, diagnostics = build_training_matrix(
        feature_panels_by_date=panels_by_date,
        close_by_symbol=close_by_symbol,
        as_of_dates=accepted_ts,
        config=config,
    )

    # Output
    out_dir = pathlib.Path("reports/phase_d_v5")
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "ml_pooling_smoke_run_date": datetime.now().isoformat(timespec="seconds"),
        "is_window": [IS_START.date().isoformat(), IS_END.date().isoformat()],
        "forbidden_oos_start": FORBIDDEN_OOS_START.date().isoformat(),
        "n_rebalance_dates_total": len(rebalance_dates),
        "n_as_of_accepted": len(accepted),
        "n_panels_built": len(panels_by_date),
        "n_periods_with_rows": len(diagnostics.rows_per_period),
        "total_rows_kept": diagnostics.total_kept,
        "total_dropped_missing_feature": diagnostics.total_dropped_missing_feature,
        "total_dropped_stale_forward_return": diagnostics.total_dropped_stale_forward_return,
        "feature_names": LOCKED_FEATURE_NAMES,
        "schema_columns": list(df.columns),
        "label_1_count": int(df["label_top_decile"].sum()),
        "label_1_pct": round(float(df["label_top_decile"].mean()), 4),
        "rows_per_period_sample": {
            str(k.date()): v
            for k, v in list(diagnostics.rows_per_period.items())[:5]
        },
    }
    summary_path = out_dir / "v5_4a_smoke_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("wrote %s", summary_path)

    sample_path = out_dir / "v5_4a_smoke_sample.parquet"
    df.to_parquet(sample_path, index=False)
    logger.info("wrote %s (%d rows, %d cols)",
                sample_path, len(df), len(df.columns))

    print()
    print("=" * 60)
    print("v5.0 Step 4a-3 ML Pooling Smoke Summary")
    print("=" * 60)
    print(f"  rebalance dates total      : {len(rebalance_dates)}")
    print(f"  as_of dates accepted       : {len(accepted)}")
    print(f"  panels built               : {len(panels_by_date)}")
    print(f"  training matrix rows       : {len(df)}")
    print(f"  training matrix cols       : {len(df.columns)}  ({df.columns.tolist()})")
    print(f"  total dropped (missing f.) : {diagnostics.total_dropped_missing_feature}")
    print(f"  total dropped (stale fwd)  : {diagnostics.total_dropped_stale_forward_return}")
    print(f"  label=1 count / pct        : {int(df['label_top_decile'].sum())} "
          f"({df['label_top_decile'].mean():.2%})")
    print(f"  saved summary              : {summary_path}")
    print(f"  saved sample matrix        : {sample_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
