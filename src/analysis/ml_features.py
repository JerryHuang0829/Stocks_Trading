"""v5.0 Step 4a-2 — PIT-aware 5-feature panel provider.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §4 Locked feature set (5 features)
  §13 conditions 1, 4, 12 (5 features LOCKED, 2025 strict OOS, PIT 護欄)

This module is the **PIT-aware upstream provider** that produces the per-as_of
feature panels consumed by `ml_pooling.build_training_matrix()`. Per Codex
v5.0 R2 P0/P1 fix, pooling stays pure (join + label + guard) and feature
computation lives here, where PIT is enforced by delegating to each factor
function's own `as_of` truncation logic (each factor has its own unit tests
covering PIT correctness).

LOCKED feature dispatch (per pre-reg §4):
  - `idio_vol_max`  → `compute_idio_vol_max_panel`(needs `market_returns`)
  - `high_proximity` → `compute_high_proximity_universe`
  - `value_ep_sn`    → `compute_value_ep_universe` → `sector_neutralize`
  - `pead_eps_sn`    → `compute_pead_eps_universe` → `sector_neutralize`
  - `reversal_1m`    → `compute_reversal_1m_universe`

The 5-feature set is locked by pre-reg §13 condition 1 — `LOCKED_FEATURE_NAMES`
is the single source of truth (anywhere else referencing 5 features must
import from this module).

Upstream of this module (orchestration):
  - Cache loaders (already in `scripts/_factor_ic_helpers.py`):
    `_load_universe_ohlcv`, `_load_universe_timeseries`, `_load_industry_labels`,
    `_load_ohlcv` (for benchmark).

Downstream:
  - `ml_pooling.build_training_matrix()` consumes panels[as_of] → DataFrame.
"""
from __future__ import annotations

import logging
from typing import Mapping

import pandas as pd

from src.features.high_proximity import compute_high_proximity_universe
from src.features.idio_vol_max import compute_idio_vol_max_panel
from src.features.pead_eps import compute_pead_eps_universe
from src.features.reversal_1m import compute_reversal_1m_universe
from src.features.value_ep import compute_value_ep_universe
from src.utils.factor_neutralize import sector_neutralize

logger = logging.getLogger(__name__)


# pre-reg §4 + §13 condition 1: 5-feature lock.  ANY change requires
# pre-registration amendment + re-sign-off.  Order defines column order
# in the training matrix.
LOCKED_FEATURE_NAMES: list[str] = [
    "idio_vol_max",
    "high_proximity",
    "value_ep_sn",
    "pead_eps_sn",
    "reversal_1m",
]


def compute_feature_panel(
    as_of: pd.Timestamp,
    *,
    ohlcv_by_symbol: Mapping[str, pd.DataFrame],
    eps_by_symbol: Mapping[str, pd.DataFrame],
    market_returns: pd.Series,
    industry_map: Mapping[str, str],
    universe_filter: set[str] | None = None,
) -> pd.DataFrame:
    """Compute the 5-feature cross-section panel at one rebalance date.

    Each factor function is called with the same ``as_of`` so PIT discipline
    is uniform — provider does not bypass any factor's internal truncation.

    Parameters
    ----------
    as_of : pd.Timestamp
        Rebalance date. Each factor will only use data with timestamp ≤ as_of
        (or strictly < as_of per individual factor's shift=1 semantics).
    ohlcv_by_symbol : {symbol: OHLCV DataFrame}
        Used by idio_vol_max / high_proximity / reversal_1m.
    eps_by_symbol : {symbol: quarterly EPS DataFrame}
        Used by value_ep / pead_eps (which feed value_ep_sn / pead_eps_sn).
    market_returns : pd.Series
        Daily market returns (typically 0050) used by idio_vol_max.
        Caller should pass SPLIT-ADJUSTED returns (post Codex v5.0 R1 P1-4 fix
        — `adjust_splits_ohlc` before computing returns).
    industry_map : {symbol: industry_label}
        Used by sector_neutralize for value_ep_sn + pead_eps_sn. KNOWN PIT
        caveat: industry labels typically static snapshot (Option B in
        pre-reg known_biases). Acceptable per pre-reg disclosure.
    universe_filter : set[str] | None
        If provided, only symbols in this set are kept in the output panel.
        Typically the intersection universe from
        `_factor_ic_helpers._compute_intersection_universe`.

    Returns
    -------
    pd.DataFrame indexed by symbol with columns matching LOCKED_FEATURE_NAMES.
    Symbols missing on ANY feature are kept with NaN in that column; the
    downstream pooling builder does complete-case drop (per Codex feedback:
    don't impute here — let the consumer decide).
    """
    # 1. idio_vol_max (panel, needs market_returns)
    idio_panel = compute_idio_vol_max_panel(
        ohlcv_panel=dict(ohlcv_by_symbol),
        market_returns=market_returns,
        as_of=as_of,
    )

    # 2. high_proximity
    high_prox_panel = compute_high_proximity_universe(
        ohlcv_by_symbol, as_of=as_of,
    )

    # 3. value_ep_sn = sector_neutralize(value_ep_raw)
    value_ep_raw = compute_value_ep_universe(
        eps_by_symbol,
        ohlcv_by_symbol=ohlcv_by_symbol,
        as_of=as_of,
    )
    value_ep_sn_panel = sector_neutralize(value_ep_raw, industry_map)

    # 4. pead_eps_sn = sector_neutralize(pead_eps_raw)
    pead_raw = compute_pead_eps_universe(
        eps_by_symbol, as_of=as_of,
    )
    pead_eps_sn_panel = sector_neutralize(pead_raw, industry_map)

    # 5. reversal_1m
    reversal_panel = compute_reversal_1m_universe(
        ohlcv_by_symbol, as_of=as_of,
    )

    # Assemble panel: index = union of all symbols appearing in any factor;
    # missing values stay NaN (downstream complete-case drop).
    panel = pd.DataFrame({
        "idio_vol_max":   idio_panel,
        "high_proximity": high_prox_panel,
        "value_ep_sn":    value_ep_sn_panel,
        "pead_eps_sn":    pead_eps_sn_panel,
        "reversal_1m":    reversal_panel,
    })

    if universe_filter is not None:
        panel = panel[panel.index.isin(universe_filter)]

    # Enforce locked column order (caller may rely on it for schema validation
    # downstream in ml_pooling._validate_panel_schema).
    panel = panel[LOCKED_FEATURE_NAMES]
    return panel
