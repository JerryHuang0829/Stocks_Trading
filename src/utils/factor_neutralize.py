"""Factor neutralization helpers — sector / size demean (Fama-French style).

When a factor (e.g. Value, PEAD, Quality) is computed raw, its cross-section
mixes the **factor signal** with the **industry tilt** and **size tilt** of
the universe.  Subtracting the industry / size-bucket mean from each stock's
raw value extracts the **within-group** factor variation — which is what the
factor literature actually claims as alpha.

This is the standard Fama-French / Barra preprocessing for fundamentals.

Distinct from v3.3 Phase A3.1's failed "sector-neutral COMPOSITE" experiment:
that work forced sector diversification at the *portfolio* level (and over-
constrained alpha).  These helpers operate at the *factor* level — purifying
the signal, not constraining the portfolio.  Different concept, different
effect.

Sign / scale preservation: output preserves the input scale (just subtracts a
group mean).  Downstream cross-sectional z-scoring still applies as before.

PIT: pure cross-sectional operation — no time dimension — therefore PIT-safe
by construction (caller is responsible for using PIT-correct ``industry_map``
and ``market_cap_map`` at ``as_of``).
"""
from __future__ import annotations

from collections import Counter
from typing import Mapping

import numpy as np
import pandas as pd

DEFAULT_MIN_INDUSTRY_SIZE = 3      # smaller industries pool into "_OTHER"
DEFAULT_SIZE_N_BUCKETS = 10        # market-cap deciles


def sector_neutralize(
    panel: pd.Series,
    industry_map: Mapping[str, str],
    *,
    min_industry_size: int = DEFAULT_MIN_INDUSTRY_SIZE,
) -> pd.Series:
    """Subtract industry-mean factor value from each stock.

    Parameters
    ----------
    panel : pd.Series
        Raw factor values indexed by symbol.
    industry_map : {symbol: industry_label}
        Per-symbol industry (caller must use a PIT-correct snapshot).
        Symbols not present in the map are pooled into the "_UNKNOWN" group;
        symbols in industries with < ``min_industry_size`` members are pooled
        into "_OTHER" (mirrors tw_stock.py's small-sector handling).
    min_industry_size : int
        Industries with fewer members than this are pooled.

    Returns
    -------
    pd.Series indexed by symbol, value = raw − group_mean.  Same length and
    scale as input; NaN inputs propagate (NaN − group_mean = NaN).
    """
    if panel.empty:
        return panel.copy()

    # Map each symbol to an effective group (real industry / _OTHER / _UNKNOWN)
    raw_labels = {s: industry_map.get(s) for s in panel.index}
    # Count members of each real industry to decide pooling
    counts = Counter(lbl for lbl in raw_labels.values() if lbl)
    effective: dict[str, str] = {}
    for s, lbl in raw_labels.items():
        if not lbl:
            effective[s] = "_UNKNOWN"
        elif counts[lbl] < min_industry_size:
            effective[s] = "_OTHER"
        else:
            effective[s] = lbl

    grp = pd.Series(effective, name="industry").reindex(panel.index)
    group_means = panel.groupby(grp).transform("mean")
    return panel - group_means


def size_neutralize(
    panel: pd.Series,
    market_cap_map: Mapping[str, float],
    *,
    n_buckets: int = DEFAULT_SIZE_N_BUCKETS,
) -> pd.Series:
    """Subtract size-bucket mean factor value from each stock.

    Parameters
    ----------
    panel : pd.Series
        Raw factor values indexed by symbol.
    market_cap_map : {symbol: market_cap}
        PIT-correct market cap (close × shares).  Symbols missing from the map
        are pooled into a "_UNKNOWN_SIZE" bucket.
    n_buckets : int
        Number of equal-count buckets to split the cap distribution into
        (10 = deciles, F-F standard).

    Returns
    -------
    pd.Series indexed by symbol, value = raw − bucket_mean.
    """
    if panel.empty:
        return panel.copy()

    caps = pd.Series(
        {s: market_cap_map.get(s, np.nan) for s in panel.index},
        dtype=float,
    )
    valid_mask = caps.notna() & np.isfinite(caps)

    # Assign deciles only to symbols with valid cap; others go to _UNKNOWN_SIZE
    bucket = pd.Series(["_UNKNOWN_SIZE"] * len(panel), index=panel.index)
    if valid_mask.any():
        try:
            # Try qcut with the requested number of buckets;
            # if not enough unique caps, fall back to fewer
            valid_caps = caps[valid_mask]
            actual_buckets = min(n_buckets, valid_caps.nunique())
            if actual_buckets >= 2:
                deciles = pd.qcut(
                    valid_caps, q=actual_buckets,
                    labels=[f"size_{i}" for i in range(actual_buckets)],
                    duplicates="drop",
                )
                bucket.loc[valid_mask] = deciles.astype(str)
            else:
                # All caps identical → single bucket
                bucket.loc[valid_mask] = "size_0"
        except ValueError:
            # qcut failure (degenerate distribution) → single valid bucket
            bucket.loc[valid_mask] = "size_0"

    group_means = panel.groupby(bucket).transform("mean")
    return panel - group_means
