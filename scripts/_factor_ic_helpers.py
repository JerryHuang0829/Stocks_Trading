"""Shared utility helpers for factor IC research scripts.

Phase P5 Session 1 / R21 finding F6 fix (2026-05-03):
    Extracted from `scripts/run_factor_ic.py` (lines 104-398) so that
    cross-script callers (originally `scripts/phase_b0_lite_spike.py` —
    cleaned up 2026-05-04 — and future P5+ / Phase D scripts) no longer
    import private functions across script boundaries (anti-pattern).

`scripts/run_factor_ic.py` retains a thin re-export shim so existing
callers (`/factor-ic` skill, manual CLI) keep working unchanged.

Functions exported here (12):
    _normalise_index
    _load_ohlcv
    _load_universe_ohlcv
    _load_universe_revenue
    _load_universe_timeseries
    _load_issued_capital
    _load_industry_labels
    _load_market_value
    _resolve_price_asof
    _forward_return
    _compute_intersection_universe
    _compute_regimes
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import pandas as pd

from src.strategy.indicators import calculate_indicators
from src.strategy.regime import detect_regime
from src.utils.thresholds import get_threshold, per_panel_min_obs


REGIME_SYMBOL = "0050"
MIN_UNIVERSE_SIZE = 50
PANEL_DIRS_FOR_INTERSECTION = (
    "ohlcv",
    "revenue",
    "margin_short",
    "institutional_v2",
    "quarterly_eps",
)
DEFAULT_MIN_OBS_PER_SYMBOL = 250
DEFAULT_MAX_GAP_DAYS = 5


def _normalise_index(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    out = df.copy()
    out.index = idx
    return out.sort_index()


def _load_ohlcv(cache_dir: Path, symbol: str) -> pd.DataFrame | None:
    path = cache_dir / "ohlcv" / f"{symbol}.pkl"
    if not path.exists():
        return None
    try:
        df = pd.read_pickle(path)
    except Exception:
        return None
    if df is None or df.empty or "close" not in df.columns:
        return None
    return _normalise_index(df)


def _load_universe_ohlcv(cache_dir: Path) -> dict[str, pd.DataFrame]:
    ohlcv_dir = cache_dir / "ohlcv"
    if not ohlcv_dir.is_dir():
        raise FileNotFoundError(f"OHLCV cache not found: {ohlcv_dir}")
    result: dict[str, pd.DataFrame] = {}
    for path in ohlcv_dir.iterdir():
        if path.suffix != ".pkl":
            continue
        symbol = path.stem
        # 4-digit (TWSE/TPEX common stock) only; excludes 00xx ETF family
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        df = _load_ohlcv(cache_dir, symbol)
        if df is None:
            continue
        result[symbol] = df
    return result


def _load_universe_revenue(cache_dir: Path) -> dict[str, pd.DataFrame]:
    return _load_universe_timeseries(cache_dir / "revenue")


def _load_universe_timeseries(panel_dir: Path) -> dict[str, pd.DataFrame]:
    """Generic per-symbol pickle loader (used for revenue / margin_short /
    institutional_v2 / quarterly_eps caches).
    """
    if not panel_dir.is_dir():
        raise FileNotFoundError(f"Panel cache not found: {panel_dir}")
    result: dict[str, pd.DataFrame] = {}
    for path in panel_dir.iterdir():
        if path.suffix != ".pkl":
            continue
        symbol = path.stem
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        try:
            df = pd.read_pickle(path)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        result[symbol] = df
    return result


def _load_issued_capital(cache_dir: Path) -> dict[str, float]:
    """Load issued shares per symbol from market_value cache (contains shares column).

    Falls back to reading the market_value/_global.pkl snapshot and extracting
    the latest issued_shares per symbol. Values in units of shares (not lots).
    """
    path = cache_dir / "market_value" / "_global.pkl"
    if not path.exists():
        raise FileNotFoundError(f"market_value cache missing: {path}")
    df = pd.read_pickle(path)
    if df is None or df.empty:
        return {}
    if "issued_shares" in df.columns:
        latest = df.sort_values("date").drop_duplicates("stock_id", keep="last")
        return dict(zip(latest["stock_id"].astype(str), latest["issued_shares"].astype(float)))
    capital_path = cache_dir / "issued_capital" / "_global.pkl"
    if capital_path.exists():
        cap = pd.read_pickle(capital_path)
        if cap is not None and not cap.empty and "stock_id" in cap.columns:
            col = "issued_shares" if "issued_shares" in cap.columns else None
            if col is None:
                for candidate in ("shares_issued", "capital_shares", "shares"):
                    if candidate in cap.columns:
                        col = candidate
                        break
            if col:
                return dict(zip(cap["stock_id"].astype(str), cap[col].astype(float)))
    raise RuntimeError(
        "issued_shares unavailable: market_value cache lacks column AND "
        "no issued_capital fallback cache. Run cache_fill_new_factors.py "
        "with --seed-issued-capital first."
    )


def _load_industry_labels(cache_dir: Path) -> dict[str, str] | None:
    """Return {symbol: industry_category} from stock_info cache, or None."""
    path = cache_dir / "stock_info" / "_global.pkl"
    if not path.exists():
        return None
    try:
        df = pd.read_pickle(path)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    col = None
    for candidate in ("industry_category", "industry", "IndustryCategory"):
        if candidate in df.columns:
            col = candidate
            break
    if col is None or "stock_id" not in df.columns:
        return None
    cleaned = df[["stock_id", col]].dropna().drop_duplicates("stock_id", keep="last")
    return dict(zip(cleaned["stock_id"].astype(str), cleaned[col].astype(str)))


def _load_market_value(cache_dir: Path) -> dict[str, float]:
    """Latest market value per symbol from market_value/_global.pkl."""
    path = cache_dir / "market_value" / "_global.pkl"
    if not path.exists():
        return {}
    df = pd.read_pickle(path)
    if df is None or df.empty:
        return {}
    if "stock_id" not in df.columns or "market_value" not in df.columns:
        return {}
    latest = df.sort_values("date").drop_duplicates("stock_id", keep="last")
    return dict(zip(latest["stock_id"].astype(str), latest["market_value"].astype(float)))


def _resolve_price_asof(
    series: pd.Series,
    target_date: pd.Timestamp,
    *,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> tuple[float, pd.Timestamp] | None:
    """P1-新2: return (price, anchor_date) at target_date or the last non-NaN
    trading day within `max_gap_days`, else None.

    Using the prior ``dropna()`` approach silently backfills stale prices from
    arbitrary lookback and treats halted symbols as if they had fresh prints,
    inducing selection bias. Rejecting gaps beyond `max_gap_days` keeps the
    realised holding period consistent across the universe.
    """
    if series is None or series.empty:
        return None
    view = series[series.index <= target_date].dropna()
    if view.empty:
        return None
    last_date = view.index[-1]
    if (target_date - last_date).days > max_gap_days:
        return None
    return float(view.iloc[-1]), last_date


def _forward_return(
    close_by_symbol: dict[str, pd.Series],
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> float | None:
    series = close_by_symbol.get(symbol)
    start_resolved = _resolve_price_asof(series, start, max_gap_days=max_gap_days)
    end_resolved = _resolve_price_asof(series, end, max_gap_days=max_gap_days)
    if start_resolved is None or end_resolved is None:
        return None
    sp, _ = start_resolved
    ep, _ = end_resolved
    if sp <= 0:
        return None
    return (ep / sp) - 1.0


def _compute_intersection_universe(
    cache_dir: Path,
    *,
    panel_names: Sequence[str] = PANEL_DIRS_FOR_INTERSECTION,
    min_obs_per_symbol: "int | dict[str, int] | None" = None,
    log: logging.Logger | None = None,
) -> list[str]:
    """P1-新1 + follow-up-4 (codex-confirmed): intersection universe with
    **per-panel** `min_obs_per_symbol` so quarterly panels (~28 rows/symbol
    over 7Y) are not dropped by the daily-frequency threshold of 250.

    Args:
        cache_dir: root of `data/cache/`.
        panel_names: which per-symbol panels to intersect.
        min_obs_per_symbol:
            - None (default): read per-panel threshold from
              `config/factor_thresholds.yaml :: universe.min_obs_per_symbol.<panel>`
              with yaml `default` / hard-coded 250 as fallback.
            - int: apply the same threshold to every panel (legacy / tests).
            - dict: `{panel_name: int}`; panels missing from the dict fall back
              to yaml default.

    Panels with fewer than `min_universe_size` qualifying symbols are dropped
    from the intersection (logged as a warning) so the caller knows the
    resulting universe is not strictly "5-factor clean".
    """
    min_universe_size = int(
        get_threshold("universe", "min_universe_size", default=MIN_UNIVERSE_SIZE)
    )

    def _threshold_for(panel: str) -> int:
        if isinstance(min_obs_per_symbol, dict):
            if panel in min_obs_per_symbol:
                return int(min_obs_per_symbol[panel])
        if isinstance(min_obs_per_symbol, int):
            return int(min_obs_per_symbol)
        return per_panel_min_obs(panel)

    per_panel: dict[str, set[str]] = {}
    for name in panel_names:
        panel_dir = cache_dir / name
        if not panel_dir.is_dir():
            if log:
                log.info("intersection-universe: panel missing — skipping %s", name)
            continue
        threshold = _threshold_for(name)
        symbols: set[str] = set()
        for pkl in panel_dir.iterdir():
            if pkl.suffix != ".pkl" or pkl.stem.startswith("_"):
                continue
            if not (pkl.stem.isdigit() and len(pkl.stem) == 4):
                continue
            try:
                df = pd.read_pickle(pkl)
            except Exception:
                continue
            if df is None or len(df) < threshold:
                continue
            symbols.add(pkl.stem)
        if len(symbols) < min_universe_size:
            if log:
                log.warning(
                    "intersection-universe: panel %s has only %d qualifying symbols "
                    "(threshold %d, min %d); dropping from intersection",
                    name, len(symbols), threshold, min_universe_size,
                )
            continue
        per_panel[name] = symbols
        if log:
            log.info(
                "intersection-universe: %s contributes %d symbols (threshold %d)",
                name, len(symbols), threshold,
            )
    if not per_panel:
        return []
    universe: "set[str] | None" = None
    for symbols in per_panel.values():
        universe = symbols if universe is None else (universe & symbols)
    return sorted(universe or [])


def _compute_regimes(
    benchmark_ohlcv: pd.DataFrame,
    rebalance_dates: list,
    strategy_cfg: dict,
) -> list[str | None]:
    full = calculate_indicators(benchmark_ohlcv.copy(), strategy_cfg)
    full_idx = full.index
    if getattr(full_idx, "tz", None) is not None:
        full = full.copy()
        full.index = full_idx.tz_convert(None)
    regimes: list[str | None] = []
    for date in rebalance_dates:
        ts = pd.Timestamp(date)
        if ts.tz is not None:
            ts = ts.tz_convert(None)
        view = full[full.index <= ts]
        if view.empty:
            regimes.append(None)
            continue
        try:
            regimes.append(detect_regime(view))
        except Exception:
            regimes.append(None)
    return regimes
