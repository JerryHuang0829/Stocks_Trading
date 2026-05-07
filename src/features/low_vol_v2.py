"""Low-volatility tilt factor (252d realized vol).

Score per symbol at `as_of`:

    score = -std(daily_log_return[as_of - window : as_of - 1d])

- shift=1: the as_of day's close is EXCLUDED from the std window to keep the
  factor point-in-time (mirrors high_proximity.py shift=1 semantics).
- Window defaults to 252 (≈ 1 trading year in TWSE).
- min_history=200 (rejects new IPOs and recently-listed stocks where the
  rolling-vol estimate is noisy).
- **Reverse-direction factor**: high std → low score. Caller's cross-sectional
  ranking interprets "high score = good" so symbols with low realized vol get
  the highest ranks.

Splits: assumed already forward-adjusted via metrics.adjust_splits before
caching. This function does not re-adjust.

Motivation: AQR Frazzini & Pedersen 2014 ("Betting Against Beta") and the
broader low-vol anomaly literature document that low-realized-vol stocks
deliver risk-adjusted returns above CAPM-implied. **This implementation is a
realized-vol tilt, NOT BAB itself**: BAB requires beta estimation + leverage
to a vol target. Treat as a low-vol proxy / tilt signal, not as a faithful
BAB replication.

Retail-tractable: requires only cached OHLCV.

Phase B0-Lite (2026-05-03): exists for single-factor IC spike under
H_lite_preregistration. PIT discipline relies entirely on shift=1 + caller
passing as_of (no separate cache layer; ohlcv cache already PIT-truncated by
_DataSlicer in backtest mode).
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


DEFAULT_WINDOW = 252
DEFAULT_MIN_HISTORY = 200


def _normalise_close(ohlcv: pd.DataFrame | None) -> pd.Series | None:
    """Strip tz / coerce to numeric / sort. Mirrors high_proximity._normalise_close."""
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return None
    close = pd.to_numeric(ohlcv["close"], errors="coerce")
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    return close.sort_index()


def compute_low_vol_v2_universe(
    ohlcv_by_symbol: Mapping[str, pd.DataFrame | None],
    as_of: pd.Timestamp,
    window: int = DEFAULT_WINDOW,
    min_history: int = DEFAULT_MIN_HISTORY,
    *,
    return_diagnostics: bool = False,
) -> "pd.Series | tuple[pd.Series, dict]":
    """Batch-compute realized-vol low-vol score for all symbols at `as_of`.

    Returns a Series indexed by symbol. Symbols with insufficient history
    or zero/NaN std are dropped from the result (not included as NaN).

    Score = -std(daily_log_return) so that high score = low realized vol
    (caller-friendly direction; aligns with `compute_high_proximity_universe`
    convention where higher = better).

    Phase P5 Session 1 / R21 finding F5 fix (2026-05-03):
        Bad data handling: explicit `close > 0` filter applied within the
        rolling window (some halted / delisted stocks have stray close=0
        rows that produce log(0)=-inf). Diagnostics counts dropped by reason.

    Args:
        return_diagnostics: if True, return tuple (scores, diagnostics_dict).
            diagnostics keys: bad_data_count (any reason),
            dropped_for_no_close, dropped_for_zero_close,
            dropped_for_insufficient_history, dropped_for_zero_std.
            Default False keeps backward-compat with B0-Lite spike (single
            return value = pd.Series).
    """
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_localize(None) if as_of_ts.tz is not None else as_of_ts

    results: dict[str, float] = {}
    diagnostics = {
        "dropped_for_no_close": 0,
        "dropped_for_zero_close": 0,
        "dropped_for_insufficient_history": 0,
        "dropped_for_zero_std": 0,
    }
    for symbol, ohlcv in ohlcv_by_symbol.items():
        close = _normalise_close(ohlcv)
        if close is None:
            diagnostics["dropped_for_no_close"] += 1
            continue
        # Anchor on rows with index <= as_of (handles halted stocks with trailing NaN)
        valid = close[close.index <= as_of_ts].dropna()
        if valid.empty:
            diagnostics["dropped_for_no_close"] += 1
            continue
        # F5: explicit close > 0 filter (halted/delisted stocks may have
        # close=0 rows that propagate as log(0)=-inf into the std calc;
        # the std_val<=0 guard catches it but we want explicit count).
        n_before_zero_filter = len(valid)
        valid = valid[valid > 0]
        if len(valid) < n_before_zero_filter:
            diagnostics["dropped_for_zero_close"] += 1
            # Don't `continue` — symbol may still have enough history after filter
        if valid.empty:
            continue
        # shift=1: window is STRICTLY BEFORE the anchor day. The as_of close
        # itself does not enter the std calculation.
        history = valid.iloc[:-1]
        if len(history) < min_history:
            diagnostics["dropped_for_insufficient_history"] += 1
            continue
        window_slice = history.tail(window)
        if len(window_slice) < min_history:
            diagnostics["dropped_for_insufficient_history"] += 1
            continue
        # Daily log returns within the window slice. Need at least 2 prices to
        # compute 1 return; with min_history=200 prices we get ~199 returns.
        log_ret = np.log(window_slice).diff().dropna()
        if len(log_ret) < min_history - 1:
            diagnostics["dropped_for_insufficient_history"] += 1
            continue
        std_val = float(log_ret.std(ddof=1))
        if not np.isfinite(std_val) or std_val <= 0:
            diagnostics["dropped_for_zero_std"] += 1
            continue
        # Reverse direction: high std → low score.
        results[symbol] = -std_val

    diagnostics["bad_data_count"] = sum(
        v for k, v in diagnostics.items() if k != "bad_data_count"
    )
    series = pd.Series(results, dtype=float)
    if return_diagnostics:
        return series, diagnostics
    return series


def score_low_vol_v2(
    ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
    window: int = DEFAULT_WINDOW,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> dict:
    """Per-symbol wrapper (aligned with src.features.high_proximity.score signature).

    Returns dict with keys: score, detail, icon.
    Score is `-std`; caller is responsible for cross-sectional ranking.
    """
    series = compute_low_vol_v2_universe(
        {"__one__": ohlcv}, as_of=as_of, window=window, min_history=min_history
    )
    if series.empty:
        return {"score": None, "detail": "insufficient_history", "icon": "➖"}
    value = float(series.iloc[0])
    annualised = -value * np.sqrt(252)  # back to positive annualised vol for display
    if annualised <= 0.20:
        icon = "🟢"  # low vol
    elif annualised <= 0.35:
        icon = "✅"
    elif annualised <= 0.50:
        icon = "⚠️"
    else:
        icon = "🔥"  # high vol (low score)
    return {
        "score": value,
        "annualised_vol": annualised,
        "detail": f"realized_vol_{window}d",
        "icon": icon,
    }
