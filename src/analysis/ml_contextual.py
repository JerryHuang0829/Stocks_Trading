"""v5.0 Step 4b — Contextual features + locked interactions.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §6 Contextual features (LOCKED set + LOCKED interactions)
  §13 conditions 1, 4, 12

Adds the following to the training matrix produced by ml_pooling:
  - Sector dummies (one-hot, TWSE industry; ~20 cols expected)
  - Size decile (integer 1-10 per as_of cross-section, ordinal)
  - Regime one-hot (trending_up / trending_down / ranging — 3 cols)
  - 5 LOCKED interaction columns:
        1. value_ep_sn × trending_up_flag
        2. pead_eps_sn × earnings_season_flag       (month ∈ {3,5,8,11} TW)
        3. idio_vol_max × trending_down_flag
        4. reversal_1m × high_proximity_top_quintile (>= 80th percentile)
        5. value_ep_sn × size_decile_low             (decile <= 3)

Pure join layer (per Codex v5.0 R2 P0/P1 fix discipline):
  - Does NOT load cache or compute features.
  - Takes pre-computed industry_map + size_panel_by_date + regime_by_date.
  - PIT is delegated to upstream provider (size = close[as_of-1d] × shares;
    regime = adjust-splits-aware classifier — already fixed in R1 P1-4).

Locked interactions LOCKED per pre-reg §6 — adding new interaction OR
removing one of these 5 = violation of pre-commit discipline §13 condition 1.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Regime labels per src/strategy/regime.detect_regime (validated existing)
REGIME_TRENDING_UP = "trending_up"
REGIME_TRENDING_DOWN = "trending_down"
REGIME_RANGING = "ranging"
REGIME_LABELS = (REGIME_TRENDING_UP, REGIME_TRENDING_DOWN, REGIME_RANGING)

# TW EPS announcement months per QUARTERLY_EPS_LAG_DAYS convention
# (Q1 disclosed by ~May 15, Q2 by ~Aug 14, Q3 by ~Nov 14, Q4 by ~Mar 31).
# Pre-reg §6 fixed list: {3, 5, 8, 11}.
DEFAULT_EARNINGS_SEASON_MONTHS = (3, 5, 8, 11)
DEFAULT_SIZE_N_BUCKETS = 10
DEFAULT_SIZE_LOW_DECILE_THRESHOLD = 3   # decile <= 3 = "small"
DEFAULT_HIGH_PROX_TOP_QUINTILE_THRESHOLD = 0.8   # >= 80th pctile

# pre-reg §6 LOCKED interaction list. Order is fixed for downstream column
# ordering reproducibility.
LOCKED_INTERACTION_NAMES = (
    "interact_value_ep_sn_x_trending_up",
    "interact_pead_eps_sn_x_earnings_season",
    "interact_idio_vol_max_x_trending_down",
    "interact_reversal_1m_x_high_prox_top_quintile",
    "interact_value_ep_sn_x_size_decile_low",
)


@dataclass
class ContextualConfig:
    """v5.0 contextual feature config (pre-reg §6 LOCK)."""
    earnings_season_months: tuple[int, ...] = DEFAULT_EARNINGS_SEASON_MONTHS
    size_n_buckets: int = DEFAULT_SIZE_N_BUCKETS
    size_low_decile_threshold: int = DEFAULT_SIZE_LOW_DECILE_THRESHOLD
    high_prox_top_quintile_threshold: float = DEFAULT_HIGH_PROX_TOP_QUINTILE_THRESHOLD
    sector_unknown_label: str = "_UNKNOWN"
    sector_other_label: str = "_OTHER"
    sector_min_size: int = 3   # pool industries smaller than this into _OTHER


@dataclass
class ContextualDiagnostics:
    n_sector_columns: int = 0
    n_unknown_sector_rows: int = 0
    n_pooled_other_sector_rows: int = 0
    n_regime_unknown_periods: int = 0
    n_size_missing_rows: int = 0


# ---------------------------------------------------------------------------
# Sector / size / regime helpers
# ---------------------------------------------------------------------------
def _compute_sector_label(
    symbol: str,
    industry_map: Mapping[str, str],
    industry_counts: dict[str, int],
    config: ContextualConfig,
) -> str:
    """Resolve symbol → sector label with pooling for small industries."""
    raw = industry_map.get(symbol)
    if not raw:
        return config.sector_unknown_label
    if industry_counts.get(raw, 0) < config.sector_min_size:
        return config.sector_other_label
    return raw


def _compute_size_decile_per_period(
    market_caps: pd.Series,
    n_buckets: int,
) -> pd.Series:
    """Per-period cross-section → integer decile 1-N (lowest = 1).

    Uses log market_cap to compress fat-tail distribution before quantile cut.
    Returns int (NaN-safe — symbols with missing market_cap stay NaN).
    """
    cleaned = market_caps[market_caps > 0].astype(float)
    if cleaned.empty:
        return pd.Series(dtype="Int64", index=market_caps.index)
    logs = np.log(cleaned)
    try:
        deciles = pd.qcut(logs, n_buckets, labels=False, duplicates="drop") + 1
    except ValueError:
        # Insufficient unique values for n_buckets → coarser binning
        unique_n = logs.nunique()
        if unique_n < 2:
            return pd.Series(1, index=cleaned.index, dtype="Int64")
        deciles = pd.qcut(logs, min(n_buckets, unique_n),
                          labels=False, duplicates="drop") + 1
    out = pd.Series(deciles, index=cleaned.index, dtype="Int64")
    return out.reindex(market_caps.index)   # NaN where market_cap missing


# ---------------------------------------------------------------------------
# Main: add contextual features + locked interactions
# ---------------------------------------------------------------------------
def add_contextual_features(
    training_df: pd.DataFrame,
    industry_map: Mapping[str, str],
    size_panel_by_date: Mapping[pd.Timestamp, Mapping[str, float]],
    regime_by_date: Mapping[pd.Timestamp, str | None],
    config: ContextualConfig | None = None,
) -> tuple[pd.DataFrame, ContextualDiagnostics]:
    """Augment ml_pooling training matrix with contextual features + interactions.

    Parameters
    ----------
    training_df : pd.DataFrame
        Output of `ml_pooling.build_training_matrix()`.
        Must include columns: symbol, as_of, label_end, idio_vol_max,
        high_proximity, value_ep_sn, pead_eps_sn, reversal_1m,
        forward_return, label_top_decile.
    industry_map : {symbol: industry_label}
        Static snapshot (Option B per pre-reg known_biases). Symbols absent
        from map → _UNKNOWN; industries with < config.sector_min_size members
        → _OTHER.
    size_panel_by_date : {as_of: {symbol: market_cap}}
        Caller-provided PIT-aware market_cap per (as_of, symbol).
        Use close[as_of - 1d] × issued_shares[as_of] (same as size_factor.py).
    regime_by_date : {as_of: 'trending_up' | 'trending_down' | 'ranging' | None}
        Regime label per as_of from `_compute_regimes` (must be the
        adjust_splits_ohlc-corrected version; Codex v5.0 R1 P1-4 fix).
    config : ContextualConfig | None
        If None, uses defaults.

    Returns
    -------
    (augmented_df, diagnostics)

    Notes
    -----
    pre-reg §6 LOCK enforcement:
      - Sector dummies: one-hot per industry seen in training (column name
        prefix: 'sector_').
      - Size: SINGLE column 'size_decile' (integer 1-10, ordinal).
      - Regime: 3 one-hot columns (regime_trending_up / regime_trending_down /
        regime_ranging).
      - 5 LOCKED interaction columns (see LOCKED_INTERACTION_NAMES).
      - Output column order: training_df cols + sector_* + size_decile
        + regime_* + interaction columns.
    """
    if config is None:
        config = ContextualConfig()
    if training_df.empty:
        raise ValueError("training_df is empty")

    required_cols = {"symbol", "as_of", "label_end", "idio_vol_max",
                     "high_proximity", "value_ep_sn", "pead_eps_sn",
                     "reversal_1m", "forward_return", "label_top_decile"}
    missing = required_cols - set(training_df.columns)
    if missing:
        raise ValueError(
            f"training_df missing required columns from ml_pooling: {sorted(missing)}"
        )

    diag = ContextualDiagnostics()
    out = training_df.copy()

    # ----- 1. Sector dummies (one-hot) -----
    industry_counts: dict[str, int] = {}
    for sym in industry_map.values():
        if sym:
            industry_counts[sym] = industry_counts.get(sym, 0) + 1

    sector_labels = out["symbol"].apply(
        lambda s: _compute_sector_label(s, industry_map, industry_counts, config)
    )
    diag.n_unknown_sector_rows = int((sector_labels == config.sector_unknown_label).sum())
    diag.n_pooled_other_sector_rows = int((sector_labels == config.sector_other_label).sum())

    sector_dummies = pd.get_dummies(sector_labels, prefix="sector").astype(int)
    diag.n_sector_columns = sector_dummies.shape[1]
    out = pd.concat([out, sector_dummies], axis=1)

    # ----- 2. Size decile (per-period cross-section, integer 1-N) -----
    size_deciles_per_row: list = []
    n_size_missing = 0
    for as_of, group in out.groupby("as_of", sort=False):
        period_caps_map = size_panel_by_date.get(pd.Timestamp(as_of), {})
        period_caps = pd.Series({s: period_caps_map.get(s, np.nan)
                                 for s in group["symbol"]})
        deciles = _compute_size_decile_per_period(period_caps, config.size_n_buckets)
        n_size_missing += int(deciles.isna().sum())
        size_deciles_per_row.append(pd.Series(deciles.values, index=group.index))

    size_decile_col = pd.concat(size_deciles_per_row).reindex(out.index)
    # Cast Int64 (nullable) → float64 so downstream .astype(float) on the full
    # DataFrame doesn't break on pd.NA. NaN here is informative — caller can
    # drop rows or impute before ML.
    out["size_decile"] = size_decile_col.astype(float)
    diag.n_size_missing_rows = n_size_missing

    # ----- 3. Regime one-hot (per as_of) -----
    n_regime_unknown = 0
    regime_per_row = []
    for as_of, group in out.groupby("as_of", sort=False):
        reg = regime_by_date.get(pd.Timestamp(as_of))
        if reg is None or reg not in REGIME_LABELS:
            reg = None
            n_regime_unknown += 1
        regime_per_row.append(pd.Series([reg] * len(group), index=group.index))
    regime_col = pd.concat(regime_per_row).reindex(out.index)
    for label in REGIME_LABELS:
        out[f"regime_{label}"] = (regime_col == label).astype(int)
    diag.n_regime_unknown_periods = n_regime_unknown

    # ----- 4. LOCKED 5 interactions -----
    # 4.1 value_ep_sn × trending_up
    out["interact_value_ep_sn_x_trending_up"] = (
        out["value_ep_sn"] * out["regime_trending_up"]
    )

    # 4.2 pead_eps_sn × earnings_season_flag
    earnings_season_flag = (
        out["as_of"].dt.month.isin(config.earnings_season_months).astype(int)
    )
    out["interact_pead_eps_sn_x_earnings_season"] = (
        out["pead_eps_sn"] * earnings_season_flag
    )

    # 4.3 idio_vol_max × trending_down
    out["interact_idio_vol_max_x_trending_down"] = (
        out["idio_vol_max"] * out["regime_trending_down"]
    )

    # 4.4 reversal_1m × high_proximity_top_quintile
    # high_proximity per-period top-quintile (>= 80th pctile of that period)
    high_prox_top_per_row = []
    for as_of, group in out.groupby("as_of", sort=False):
        ranks = group["high_proximity"].rank(method="average", pct=True)
        top_flag = (ranks >= config.high_prox_top_quintile_threshold).astype(int)
        high_prox_top_per_row.append(pd.Series(top_flag.values, index=group.index))
    high_prox_top_col = pd.concat(high_prox_top_per_row).reindex(out.index)
    out["interact_reversal_1m_x_high_prox_top_quintile"] = (
        out["reversal_1m"] * high_prox_top_col
    )

    # 4.5 value_ep_sn × size_decile_low (decile <= threshold)
    size_low_flag = (
        out["size_decile"].fillna(99).astype(float)
        <= config.size_low_decile_threshold
    ).astype(int)
    out["interact_value_ep_sn_x_size_decile_low"] = (
        out["value_ep_sn"] * size_low_flag
    )

    # ----- Final column ordering: base + sector + size + regime + interactions -----
    base_cols = list(training_df.columns)
    sector_cols = sorted([c for c in out.columns if c.startswith("sector_")])
    regime_cols = [f"regime_{l}" for l in REGIME_LABELS]
    interaction_cols = list(LOCKED_INTERACTION_NAMES)
    ordered = base_cols + sector_cols + ["size_decile"] + regime_cols + interaction_cols
    out = out[ordered]

    logger.info(
        "add_contextual_features: %d sectors, %d unknown_sector_rows, "
        "%d size_missing, %d regime_unknown_periods → %d total cols",
        diag.n_sector_columns, diag.n_unknown_sector_rows,
        diag.n_size_missing_rows, diag.n_regime_unknown_periods,
        out.shape[1],
    )
    return out, diag
