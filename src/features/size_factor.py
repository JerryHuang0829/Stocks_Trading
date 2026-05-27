"""Size factor — log market cap, sign-flipped for SMB convention.

Theory
------
Fama-French SMB (1992/1993): small-cap stocks have positive expected
alpha over large-caps. The classical proxy is log(market_cap), sign-
flipped so that smaller = higher score (matches project convention
"higher score = expected positive alpha").

Formula
-------
    Price(t)  = close at (t - 1 trading day)        # shift=1 PIT
    MCap(t)   = Price(t) × issued_shares(t)         # PIT-as-of lookup
    Score(t)  = -log(MCap(t))                       # SMB sign convention

PIT discipline
--------------
- Price: shift=1 — uses close on or before (as_of - 1 day). Mirrors
  value_ep / high_proximity semantics.
- issued_shares: PIT lookup via _issued_capital_asof helper. KNOWN
  caveat (R28-1 / R29-4): if cache lacks 'date' column, falls back to
  static-snapshot (pit_helpers warns at load time). Effect on Size
  factor is small because issued_shares moves slowly (few corporate
  actions per year per symbol).

Filters
-------
- Drop missing issued_shares (no panel record at as_of).
- Drop Price ≤ 0 or NaN.
- Drop MCap ≤ 0 or non-finite.

Output
------
pd.Series indexed by symbol, value = -log(market_cap).
Sign convention: higher = smaller cap = expected positive alpha (SMB).
If IC turns negative in TW research, that indicates an anti-SMB regime
(meaningful finding, not factor failure).
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


def _price_asof(
    ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
) -> float | None:
    """PIT-safe close on or before (as_of - 1 day). Shift=1 semantics."""
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return None
    idx = pd.to_datetime(ohlcv.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close = ohlcv["close"].copy()
    close.index = idx
    cutoff = pd.Timestamp(as_of)
    if cutoff.tz is not None:
        cutoff = cutoff.tz_convert(None)
    cutoff = cutoff - pd.Timedelta(days=1)
    valid = close[close.index <= cutoff].dropna()
    if valid.empty:
        return None
    price = float(valid.iloc[-1])
    if not np.isfinite(price) or price <= 0:
        return None
    return price


def compute_size_universe(
    ohlcv_by_symbol: Mapping[str, pd.DataFrame | None],
    aux_panel: Mapping[str, float] | None = None,
    as_of: pd.Timestamp | None = None,
    min_history: int = 0,
) -> pd.Series:
    """Batch compute Size factor (-log market cap) across the universe.

    Parameters
    ----------
    ohlcv_by_symbol : {symbol: OHLCV DataFrame}
        Per-symbol OHLCV with at least ``close`` column.
    aux_panel : {symbol: issued_shares_at_as_of}
        Resolved by ``_issued_capital_asof`` upstream (run_factor_ic.py
        dispatcher, same as margin_short_ratio).
    as_of : timestamp
        Rebalance reference date.
    min_history : int
        Unused (no rolling window needed). Kept for FACTOR_REGISTRY signature
        parity.

    Returns
    -------
    pd.Series indexed by symbol, value = -log(market_cap). Symbols missing
    issued_shares, price, or with non-positive market_cap are excluded.
    """
    if as_of is None:
        raise ValueError("as_of is required")
    if not aux_panel:
        return pd.Series(dtype=float)

    scores: dict[str, float] = {}
    for symbol, ohlcv in ohlcv_by_symbol.items():
        shares = aux_panel.get(symbol)
        if shares is None or not np.isfinite(shares) or shares <= 0:
            continue
        price = _price_asof(ohlcv, as_of)
        if price is None:
            continue
        mcap = price * float(shares)
        if not np.isfinite(mcap) or mcap <= 0:
            continue
        scores[symbol] = -float(np.log(mcap))

    return pd.Series(scores, dtype=float)
