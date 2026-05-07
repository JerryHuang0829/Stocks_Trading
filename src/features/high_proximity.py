"""52-week high proximity factor.

Proximity to 52-week (252 trading days) high, measured as:

    proximity = close_as_of / rolling_max(close[:as_of - 1d], window) - 1

- shift=1: the as_of day's close is EXCLUDED from the rolling max window
  to keep the factor point-in-time.
- Window defaults to 252 (≈ 1 trading year in TWSE).
- New IPOs with < min_history days of data return None.
- Splits: assumed already forward-adjusted via metrics.adjust_splits before
  caching. This function does not re-adjust.

Motivation: George & Hwang (2004) — stocks near their 52-week high
outperform in subsequent months (anchoring / underreaction to good news).
Retail-tractable: requires only cached OHLCV.
"""

from __future__ import annotations

from typing import Mapping

import pandas as pd


DEFAULT_WINDOW = 252
DEFAULT_MIN_HISTORY = 126  # relaxed for new IPOs (~6 months)


def _close_on_or_before(close: pd.Series, as_of: pd.Timestamp) -> float | None:
    view = close[close.index <= as_of]
    view = view.dropna()
    if view.empty:
        return None
    value = view.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _normalise_close(ohlcv: pd.DataFrame | None) -> pd.Series | None:
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return None
    close = pd.to_numeric(ohlcv["close"], errors="coerce")
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    return close.sort_index()


def compute_high_proximity_universe(
    ohlcv_by_symbol: Mapping[str, pd.DataFrame | None],
    as_of: pd.Timestamp,
    window: int = DEFAULT_WINDOW,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> pd.Series:
    """Batch-compute 52-week high proximity for all symbols at `as_of`.

    Returns a Series indexed by symbol. Symbols with insufficient history
    or missing data are dropped from the result (not included as NaN).
    """
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_convert(None) if as_of_ts.tz else as_of_ts
        as_of_ts = as_of_ts.tz_localize(None) if as_of_ts.tz is not None else as_of_ts

    results: dict[str, float] = {}
    for symbol, ohlcv in ohlcv_by_symbol.items():
        close = _normalise_close(ohlcv)
        if close is None:
            continue
        # Anchor on last valid close <= as_of (handles halted stocks with trailing NaN)
        valid = close[close.index <= as_of_ts].dropna()
        if valid.empty:
            continue
        anchor_day = valid.index[-1]
        today_close = float(valid.iloc[-1])
        if today_close <= 0:
            continue
        # Rolling window strictly BEFORE the anchor day (shift=1 semantics)
        history = valid.iloc[:-1]
        if len(history) < min_history:
            continue
        window_slice = history.tail(window)
        if window_slice.empty:
            continue
        rolling_high = float(window_slice.max())
        if rolling_high <= 0:
            continue
        results[symbol] = (today_close / rolling_high) - 1.0

    return pd.Series(results, dtype=float)


def score_high_proximity(
    ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
    window: int = DEFAULT_WINDOW,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> dict:
    """Per-symbol wrapper (aligned with src.features.institutional signature).

    Returns dict with keys: score, detail, icon, high_price.
    Score is the raw proximity value (negative = below high); caller
    is responsible for cross-sectional ranking.
    """
    series = compute_high_proximity_universe(
        {"__one__": ohlcv}, as_of=as_of, window=window, min_history=min_history
    )
    if series.empty:
        return {"score": None, "detail": "insufficient_history", "icon": "➖"}
    value = float(series.iloc[0])
    if value >= -0.03:
        icon = "🔥"  # within 3% of 52W high
    elif value >= -0.15:
        icon = "✅"
    elif value >= -0.30:
        icon = "⚠️"
    else:
        icon = "🔻"
    return {
        "score": value,
        "detail": f"proximity_{window}d",
        "icon": icon,
    }
