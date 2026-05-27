"""v5.0 Step 4a — Cross-sectional pooling pipeline (pure functions).

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §5  Cross-sectional pooling specification (LOCKED)
  §7  Target = top-decile binary
  §13 Pre-commit disciplines 4 (2025 strict OOS) + 12 (PIT 護欄)

This module is **pure**:
  - Does NOT read cache, import scripts/*, or compute features.
  - Accepts pre-computed PIT-aware feature panels + close series.
  - Joins panels per as_of, computes forward return + top-decile label.
  - Enforces 2 OOS guards (as_of < OOS, label_end < OOS).
  - Validates feature panel schema strict.

Upstream (out of 4a scope):
  - `src/analysis/ml_features.py` (or equivalent) — PIT-aware feature panel
    provider, computes panel[as_of] for the 5 locked features.

Downstream (Step 4b-4f):
  - 4b adds contextual features (sector dummies / size decile / regime label /
    interactions) to the matrix returned here.
  - 4c CPCV consumes (symbol, as_of, label_end) tuples for purge + embargo.
  - 4d ML models train on (5 features + contextual) → label_top_decile.
  - 4e Optuna nested CV on top of 4c+4d.
  - 4f SHAP on best model.

Schema (LOCKED — 9 columns):
    symbol           : str
    as_of            : pd.Timestamp (rebalance date, feature timestamp)
    label_end        : pd.Timestamp (forward return measurement date — used by
                                     CPCV for purge / OOS guard evidence)
    <feature_name>   : float        (one column per locked feature in
                                     config.feature_names; default 5)
    forward_return   : float
    label_top_decile : int (0 or 1)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


DEFAULT_FORWARD_RETURN_MAX_GAP_DAYS = 5
DEFAULT_TOP_DECILE_THRESHOLD = 0.9
LOCKED_SCHEMA_BASE_COLS = ("symbol", "as_of", "label_end", "forward_return",
                           "label_top_decile")


@dataclass
class PoolingConfig:
    """v5.0 pooling configuration (pre-reg §5 + §7 + §13 lock).

    Attributes
    ----------
    feature_names : list[str]
        5-feature lock per pre-reg §4 (idio_vol_max / high_proximity /
        value_ep_sn / pead_eps_sn / reversal_1m). Order defines output column
        order in the training matrix.
    forbidden_oos_start : pd.Timestamp
        2025-01-01 strict holdout boundary. ANY as_of or label_end >= this
        date raises ValueError (defense in depth — Codex v5.0 R2 P0 fix:
        original design only checked as_of; this also covers label_end since
        forward return reads close on or near label_end, which would leak OOS
        data into training labels for as_of = 2024-12-XX).
    top_decile_threshold : float
        0.9 per pre-reg §7.1 (top 10% binary target). Per-period independent
        ranking.
    forward_return_max_gap_days : int
        Stale-price guard. If either start-anchor or end-anchor close is
        further than this many calendar days from the requested date, the
        symbol is dropped (per existing `_factor_ic_helpers._forward_return`
        semantics, max_gap_days=5).
    """
    feature_names: list[str]
    forbidden_oos_start: pd.Timestamp
    top_decile_threshold: float = DEFAULT_TOP_DECILE_THRESHOLD
    forward_return_max_gap_days: int = DEFAULT_FORWARD_RETURN_MAX_GAP_DAYS


@dataclass
class PoolingDiagnostics:
    """Per-period drop counts surfaced for audit (Codex v5.0 R2 P1 fix).

    Without these diagnostics, complete-case drop is silent and could mask
    universe drift mid-sample. The training matrix builder always returns
    one of these alongside the DataFrame so users can audit row attrition.
    """
    rows_per_period: dict[pd.Timestamp, int] = field(default_factory=dict)
    drops_missing_feature: dict[pd.Timestamp, int] = field(default_factory=dict)
    drops_stale_forward_return: dict[pd.Timestamp, int] = field(default_factory=dict)
    total_kept: int = 0
    total_dropped_missing_feature: int = 0
    total_dropped_stale_forward_return: int = 0


# ---------------------------------------------------------------------------
# Forward return helpers (PIT-safe, stale-price guard)
# ---------------------------------------------------------------------------
def _resolve_price_asof(
    series: pd.Series | None,
    target_date: pd.Timestamp,
    max_gap_days: int,
) -> tuple[float, pd.Timestamp] | None:
    """Return (price, anchor_date) at or before target_date within gap, else None.

    Mirrors `scripts/_factor_ic_helpers._resolve_price_asof` semantics so that
    forward return computed here matches the IC research pipeline behaviour
    exactly. Stale prices beyond max_gap_days are treated as missing (consistent
    with halt-handling discipline; prevents selection bias from arbitrarily-old
    last prints being treated as fresh).
    """
    if series is None or series.empty:
        return None
    view = series[series.index <= target_date].dropna()
    if view.empty:
        return None
    last_date = view.index[-1]
    if (target_date - last_date).days > max_gap_days:
        return None
    price = float(view.iloc[-1])
    if not np.isfinite(price) or price <= 0:
        return None
    return price, last_date


def _compute_forward_return(
    close_series: pd.Series | None,
    t: pd.Timestamp,
    t_next: pd.Timestamp,
    max_gap_days: int,
) -> float | None:
    """Forward return = close[t_next] / close[t] - 1, with stale-price guard."""
    start_resolved = _resolve_price_asof(close_series, t, max_gap_days)
    end_resolved = _resolve_price_asof(close_series, t_next, max_gap_days)
    if start_resolved is None or end_resolved is None:
        return None
    sp, _ = start_resolved
    ep, _ = end_resolved
    if sp <= 0:
        return None
    return (ep / sp) - 1.0


# ---------------------------------------------------------------------------
# Label engineering (top-decile binary, per-period independent)
# ---------------------------------------------------------------------------
def compute_top_decile_labels(
    forward_returns: pd.Series,
    threshold: float = DEFAULT_TOP_DECILE_THRESHOLD,
) -> pd.Series:
    """Binary label = 1 if rank > threshold percentile, else 0.

    Per-period cross-section: ranking is local to the period (caller passes
    one period at a time). Returns integer Series indexed identical to input.

    Tie handling: `pd.Series.rank(method="average", pct=True) > threshold`,
    so a tie on the boundary may push 0 or all tied rows above; standard
    quantile-rank behaviour. Empty input → empty Series.
    """
    if forward_returns.empty:
        return pd.Series(dtype=int)
    ranks = forward_returns.rank(method="average", pct=True)
    labels = (ranks > threshold).astype(int)
    return labels


# ---------------------------------------------------------------------------
# Schema + OOS guards
# ---------------------------------------------------------------------------
def _validate_panel_schema(
    panel: pd.DataFrame,
    expected_features: list[str],
    as_of: pd.Timestamp,
) -> None:
    """Raise if panel columns don't exactly match expected feature set.

    Strict: missing column OR extra column → ValueError. This prevents silent
    bugs where an upstream provider drops a feature (would silently produce
    rows with missing values, drop them in complete-case, and quietly degrade
    sample size — a Codex audit-flagged failure mode).
    """
    if not isinstance(panel, pd.DataFrame):
        raise TypeError(
            f"feature panel at as_of={as_of.date()} must be DataFrame, "
            f"got {type(panel).__name__}"
        )
    missing = set(expected_features) - set(panel.columns)
    extra = set(panel.columns) - set(expected_features)
    if missing or extra:
        raise ValueError(
            f"feature panel schema mismatch at as_of={as_of.date()}: "
            f"missing={sorted(missing)} extra={sorted(extra)}; "
            f"expected exactly {expected_features}"
        )


def _validate_oos_boundary(
    as_of: pd.Timestamp,
    label_end: pd.Timestamp,
    forbidden_oos_start: pd.Timestamp,
) -> None:
    """Raise if as_of OR label_end falls in OOS holdout.

    Codex v5.0 R2 P0 fix:
    Original 4a design only checked as_of, allowing as_of=2024-12-12 with
    label_end=2025-01-12 to slip through (label reads OOS close → leak).
    Strict: both as_of and label_end must be strictly < forbidden_oos_start.
    """
    if as_of >= forbidden_oos_start:
        raise ValueError(
            f"PIT/OOS violation: as_of={as_of.date()} >= "
            f"forbidden_oos_start={forbidden_oos_start.date()}"
        )
    if label_end >= forbidden_oos_start:
        raise ValueError(
            f"PIT/OOS violation: label_end={label_end.date()} >= "
            f"forbidden_oos_start={forbidden_oos_start.date()} "
            f"(forward return from as_of={as_of.date()} reads OOS close)"
        )


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------
def build_training_matrix(
    feature_panels_by_date: Mapping[pd.Timestamp, pd.DataFrame],
    close_by_symbol: Mapping[str, pd.Series],
    as_of_dates: list[pd.Timestamp],
    config: PoolingConfig,
) -> tuple[pd.DataFrame, PoolingDiagnostics]:
    """Build (symbol × as_of) training matrix from PIT-aware feature panels.

    Per pre-reg §5 + §13 lock:
      - Schema fixed (9 cols when 5 features): symbol, as_of, label_end,
        <5 feature cols>, forward_return, label_top_decile
      - 2025 strict OOS guarded on both as_of AND label_end
      - Complete-case drop with diagnostics surfaced (no silent attrition)
      - Forward return uses _resolve_price_asof stale guard (max_gap_days)
      - Top-decile label per-period independent

    Parameters
    ----------
    feature_panels_by_date :
        Mapping from rebalance date → DataFrame indexed by symbol with feature
        columns. MUST be produced by a PIT-aware upstream provider (factor
        functions accepting as_of). This module does NOT verify PIT inside the
        panel — that contract is the provider's responsibility (verified by
        the provider's own unit tests). This module only validates schema
        + cross-period consistency.
    close_by_symbol :
        Per-symbol close price Series. Used for forward return only — NOT for
        feature computation (which lives upstream).
    as_of_dates :
        Ordered list of rebalance dates. Each consecutive pair (t, t_next)
        defines a label window. The last date in as_of_dates has no t_next
        and is dropped from training.
    config :
        PoolingConfig with feature_names, forbidden_oos_start, threshold, gap.

    Returns
    -------
    (training_df, diagnostics)

    Raises
    ------
    ValueError
        On schema mismatch, OOS boundary violation, or empty inputs.

    Notes
    -----
    Label end = the NEXT as_of (used as forward-return measurement date).
    For 1-month monthly rebalance, label_end ≈ as_of + 1 month.
    """
    if not as_of_dates:
        raise ValueError("as_of_dates is empty")
    if len(as_of_dates) < 2:
        raise ValueError("need at least 2 as_of_dates to form 1 label window")
    if not config.feature_names:
        raise ValueError("config.feature_names is empty")

    sorted_dates = sorted(as_of_dates)
    diagnostics = PoolingDiagnostics()
    rows: list[dict] = []

    for i, as_of in enumerate(sorted_dates[:-1]):
        label_end = sorted_dates[i + 1]
        _validate_oos_boundary(as_of, label_end, config.forbidden_oos_start)

        panel = feature_panels_by_date.get(as_of)
        if panel is None:
            raise ValueError(
                f"feature panel missing for as_of={as_of.date()}; "
                f"upstream provider must supply panels for every as_of in range"
            )
        _validate_panel_schema(panel, config.feature_names, as_of)

        period_returns: dict[str, float] = {}
        drops_missing_feature = 0
        drops_stale = 0
        eligible_rows: list[dict] = []

        for symbol in panel.index:
            feat_row = panel.loc[symbol, config.feature_names]
            if feat_row.isna().any():
                drops_missing_feature += 1
                continue
            close_series = close_by_symbol.get(symbol)
            fwd = _compute_forward_return(
                close_series, as_of, label_end,
                max_gap_days=config.forward_return_max_gap_days,
            )
            if fwd is None:
                drops_stale += 1
                continue
            period_returns[symbol] = fwd
            row = {"symbol": symbol, "as_of": as_of, "label_end": label_end}
            for fname in config.feature_names:
                row[fname] = float(feat_row[fname])
            row["forward_return"] = fwd
            eligible_rows.append(row)

        if eligible_rows:
            fwd_series = pd.Series(period_returns)
            labels = compute_top_decile_labels(
                fwd_series, threshold=config.top_decile_threshold,
            )
            for r in eligible_rows:
                r["label_top_decile"] = int(labels[r["symbol"]])
            rows.extend(eligible_rows)

        diagnostics.rows_per_period[as_of] = len(eligible_rows)
        diagnostics.drops_missing_feature[as_of] = drops_missing_feature
        diagnostics.drops_stale_forward_return[as_of] = drops_stale
        diagnostics.total_kept += len(eligible_rows)
        diagnostics.total_dropped_missing_feature += drops_missing_feature
        diagnostics.total_dropped_stale_forward_return += drops_stale

    if not rows:
        raise ValueError(
            "training matrix empty after all periods processed; "
            "check feature panel coverage + forward-return stale guard"
        )

    cols = (["symbol", "as_of", "label_end"]
            + list(config.feature_names)
            + ["forward_return", "label_top_decile"])
    df = pd.DataFrame(rows)[cols]
    logger.info(
        "build_training_matrix: %d rows kept across %d periods "
        "(dropped missing_feature=%d stale_forward_return=%d)",
        diagnostics.total_kept, len(diagnostics.rows_per_period),
        diagnostics.total_dropped_missing_feature,
        diagnostics.total_dropped_stale_forward_return,
    )
    return df, diagnostics
