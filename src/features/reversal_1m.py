"""Short-term Reversal (1-month) — DeBondt-Thaler reversal anomaly.

Theory
------
Recent winners over-react and revert in the next period; recent losers
bounce back.  DeBondt-Thaler 1985 ("Does the Stock Market Overreact?",
J. Finance) showed cross-sectional 1-3 year reversal; later empirical work
(Jegadeesh 1990, Lehmann 1990) documented the SHORT-HORIZON (~1 month) form
that we use here.  For the v5.0 factor set this is the natural short-horizon
complement to ``high_proximity`` (52-week-high momentum) — and the expected
negative correlation between the two is its main breadth contribution.

Formula
-------
    past_1m_return = close[as_of - 1d] / close[as_of - (lookback+1) days] - 1
    score          = -past_1m_return      # sign flip: low past return → high score

PIT discipline
--------------
- shift=1: ``as_of``'s own close is excluded; anchor is the last valid close
  STRICTLY BEFORE ``as_of``.
- ``lookback_days`` default 21 trading days (~1 calendar month).
- ``min_history`` default ``lookback_days + 1`` valid bars (need anchor +
  lookback close).
- Drops on: ``close ≤ 0``, NaN, insufficient history.

Sign convention
---------------
We negate so "higher score = expected positive alpha" — same as every other
factor in the project (`high_proximity` / `idio_vol_max` etc).  Caller can
just do `nlargest(top_n)` without remembering this factor's direction.
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


DEFAULT_LOOKBACK_DAYS = 21        # ~1 month of trading days


def _normalise_close(ohlcv: pd.DataFrame | None) -> pd.Series | None:
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return None
    close = pd.to_numeric(ohlcv["close"], errors="coerce")
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    return close.sort_index().dropna()


def compute_reversal_1m_universe(
    ohlcv_by_symbol: Mapping[str, pd.DataFrame | None],
    as_of: pd.Timestamp | None = None,
    *,
    aux_panel: Mapping | None = None,     # CLI parity placeholder
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_history: int | None = None,
) -> pd.Series:
    """Batch compute 1-month Reversal score across the universe.

    Parameters
    ----------
    ohlcv_by_symbol : {symbol: OHLCV DataFrame}
        Per-symbol OHLCV with at least ``close`` column.  Index must be
        date-comparable.
    as_of : timestamp
        Reference date (rebalance day).  Required.
    lookback_days : int, default 21
        Trading days for the past-return window.  v3.3 / v5.0 standard is 21.
    min_history : int, optional
        Minimum valid close bars before ``as_of`` (default = lookback_days + 1).

    Returns
    -------
    pd.Series indexed by symbol, value = ``-past_1m_return``.  Higher score
    means the stock has *under-performed* the last ~month and is expected to
    revert positively.  Symbols failing the data filters are excluded.
    """
    if as_of is None:
        raise ValueError("as_of is required")
    if lookback_days < 1:
        raise ValueError(f"lookback_days must be >= 1, got {lookback_days}")
    if min_history is None:
        min_history = lookback_days + 1

    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_convert(None)

    scores: dict[str, float] = {}
    for symbol, ohlcv in ohlcv_by_symbol.items():
        close = _normalise_close(ohlcv)
        if close is None:
            continue
        # shift=1: strictly-before as_of
        valid = close[close.index < as_of_ts]
        # Filter non-positive prices (halted / data-error stocks)
        valid = valid[valid > 0]
        if len(valid) < min_history:
            continue
        c_anchor = float(valid.iloc[-1])
        c_lookback = float(valid.iloc[-min_history])
        if c_lookback <= 0 or not np.isfinite(c_lookback) or not np.isfinite(c_anchor):
            continue
        past_ret = c_anchor / c_lookback - 1.0
        if not np.isfinite(past_ret):
            continue
        scores[symbol] = -past_ret   # reversal sign flip
    return pd.Series(scores, dtype=float)
