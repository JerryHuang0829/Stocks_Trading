"""Factor-threshold loader for `config/factor_thresholds.yaml`.

Layered defaults:
    1. Hard-coded DEFAULTS below (single source of truth for fallback)
    2. `config/factor_thresholds.yaml` (optional; deep-merged on top)

Callers (what actually reads which section — kept accurate per R32):
    - `src.analysis.ic_analysis` reads bootstrap / permutation / DSR / effective_n
    - `src.features.foreign_investor_v2` reads `factor_specific.foreign_investor_v2`
      weights / last20_max_calendar_span_days / rank_stability_top_pct + rank_stability
      min_universe_size (yaml-driven via module helpers; R31-4 fix)
    - `src.features.revenue_momentum_v2` reads `factor_specific.revenue_momentum_v2.weights`
      (yaml-driven via module helper; R32 fix)
    - `scripts.run_factor_ic` reads per-panel `min_obs_per_symbol` and forward_return

    NOT yet yaml-driven (yaml section exists as a SPEC MIRROR only — the module
    hard-codes the values; edit BOTH if changing). These params are hypothesis-locked
    structural constants so the spec mirror is acceptable for now (R33 may
    decide whether to wire them):
    - `factor_specific.high_proximity` (rolling_max_days=252 / shift=1)
    - `factor_specific.pead_eps` (baseline_quarters / lag_days_q4 / lag_days_other)
    - `factor_specific.margin_short_ratio` (ratio_weight / change_weight /
      change_lookback_days / use_trading_day_offset)

Yaml missing → defaults only; yaml parse error logged and defaults used.
One-time lazy load (`_cache`); callers can pass `reload=True` for tests.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Single source of truth for fallback defaults. Keep parallel with
# `config/factor_thresholds.yaml` so yaml can override selectively without
# requiring full-tree restatement.
DEFAULTS: dict[str, Any] = {
    "factor_ic": {
        "bootstrap": {
            "method": "stationary_block",
            "n": 1000,
            "avg_block_len": 3.0,
            "seed": 42,
            "alpha": 0.05,
        },
        "permutation": {
            "n_iterations": 300,
            "base_seed": 42,
            "method": "shuffle_factor_keep_returns",
        },
        "dsr": {
            "n_trials_default": 5,
        },
        "effective_n": {
            "method": "industry_cluster",
            "fallback_ratio": 0.5,
        },
        "min_universe_size": {
            "cross_sectional": 30,
            "rank_stability": 50,
        },
    },
    "factor_specific": {
        "high_proximity": {
            "rolling_max_days": 252,
            "shift": 1,
        },
        "revenue_momentum_v2": {
            # 2026-05-11 R32 finding: keys accel_3m3m→accel, pct_24m→percentile
            # to match src/features/revenue_momentum_v2.py::SUBSIGNAL_WEIGHTS (was a
            # silent config drift — module hardcoded + yaml/default keys mismatched).
            # `weights` is yaml-driven; `seasonal_window_months` is SPEC MIRROR
            # (module hard-codes DEFAULT_SEASONAL_LOOKBACK_MONTHS=24).
            # `yoy_strict_month_matching` removed 2026-05-11 (R33 B2): P1-新6
            # removed the ±45-day tolerance path → module is always strict, no knob.
            "weights": {
                "yoy": 0.50,
                "accel": 0.20,
                "percentile": 0.15,
                "seasonal_z": 0.15,
            },
            "seasonal_window_months": 24,
        },
        "margin_short_ratio": {
            "ratio_weight": -0.5,
            "change_weight": -0.5,
            "change_lookback_days": 20,
            "use_trading_day_offset": True,
        },
        "foreign_investor_v2": {
            # 2026-05-11 R30-3 fix (R30): weights 跟 yaml +
            # `src/features/foreign_investor_v2.py::SUBSIGNAL_WEIGHTS` 對齊（之前是
            # silent drift：yaml/module 改 0.50/0.25/0.25/0.0 但此 default 殘留
            # 舊 0.40/0.20/0.20/0.20，hierarchy fallback 會用錯權重）.
            "weights": {
                "foreign_cum_ratio": 0.50,    # 0.40 → 0.50 (P1-D 重分配 consistency 後)
                "persistence": 0.25,          # 0.20 → 0.25
                "rank_stability": 0.25,       # 0.20 → 0.25
                "consistency": 0.0,           # 0.20 → 0.0 (P1-D 78% sparsity deprecation)
            },
            "foreign_cum_lookback_days": 20,
            "rank_stability_lookback_days": 60,
            "rank_stability_top_pct": 0.20,
            "dedup_method": "last",
        },
        "pead_eps": {
            "baseline_quarters": 8,
            "lag_days_q4": 90,
            "lag_days_other": 45,
        },
    },
    "universe": {
        "mode": "intersection",
        # Per-panel min_obs. Quarterly panels have ~28 rows over 7Y so the
        # global 250 bar would drop them entirely (this was exactly the
        # external audit-confirmed intersection bug).
        "min_obs_per_symbol": {
            "default": 250,
            "ohlcv": 250,
            "revenue": 24,          # monthly, ~12*years
            "market_value": 250,
            "margin_short": 250,
            "institutional_v2": 250,
            "quarterly_eps": 12,    # quarterly, ~4*years
        },
        "min_universe_size": 50,
    },
    "forward_return": {
        "method": "explicit_asof",
        "max_gap_days": 5,
        "log_gap_stats": True,
    },
    "phase_a1": {
        "go_a2_min_factors": 2,
        "go_a2_ir_threshold": 0.5,
        "borderline_ir_threshold": 0.3,
        "smart_beta_benchmark": "0050",
        "paper_evaluation_deadline": "2026-10-31",
        "min_sharpe_diff_vs_0050": 0.3,
    },
}


# One-time process-level cache
_cache: dict[str, Any] | None = None
_cache_source: str | None = None  # "defaults" or "yaml:<abs path>"


def _yaml_path() -> Path:
    # <repo>/src/utils/thresholds.py -> <repo>/config/factor_thresholds.yaml
    return Path(__file__).resolve().parents[2] / "config" / "factor_thresholds.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive merge: override keys win, but nested dicts merge instead of replace."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_factor_thresholds(*, reload: bool = False) -> dict[str, Any]:
    """Return the merged thresholds dict. Safe to call from hot paths (cached)."""
    global _cache, _cache_source
    if _cache is not None and not reload:
        return _cache
    merged = DEFAULTS
    source = "defaults"
    path = _yaml_path()
    if path.is_file():
        try:
            import yaml  # Lazy: only when yaml file actually present
        except ImportError:
            logger.warning(
                "PyYAML not installed; thresholds falling back to hard-coded defaults."
            )
            _cache = merged
            _cache_source = source
            return _cache
        try:
            with path.open("r", encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            if not isinstance(user, dict):
                logger.warning(
                    "factor_thresholds.yaml root is not a mapping; using defaults."
                )
            else:
                merged = _deep_merge(DEFAULTS, user)
                source = f"yaml:{path}"
        except Exception as exc:
            logger.warning("Failed to parse %s: %s. Using defaults.", path, exc)
    _cache = merged
    _cache_source = source
    return _cache


def source() -> str | None:
    """For diagnostics: where did the active thresholds come from?"""
    if _cache is None:
        load_factor_thresholds()
    return _cache_source


def get_threshold(*keys: str, default: Any = None) -> Any:
    """Nested lookup: `get_threshold("factor_ic", "bootstrap", "n", default=1000)`."""
    node: Any = load_factor_thresholds()
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def per_panel_min_obs(panel_name: str) -> int:
    """`universe.min_obs_per_symbol.<panel>` with fallback to `default`.

    Defensive: if someone writes `min_obs_per_symbol: 250` (scalar) in YAML
    instead of a dict (external audit Round 5 R5-1 regression mode), we still treat
    that scalar as the `default` for every panel and log a warning once so
    the mistake surfaces without silently flattening quarterly panels.
    """
    panel_map = get_threshold("universe", "min_obs_per_symbol", default=None)
    if isinstance(panel_map, (int, float)):
        # YAML override is a scalar — collapse it to a synthetic default.
        # Warn once per process so repeated calls don't spam.
        #
        # History (R6-1 / R7-1): earlier revisions of this warning had
        # two distinct bugs — a literal inaccuracy (claimed every scalar
        # collapses to 250) and a logical self-contradiction when the YAML
        # scalar happened to match a panel's default (the previous phrasing
        # would assert a value vs an `intended` value with both sides equal).
        # Current phrasing avoids contrastive framing: it states (a) the
        # scalar value, (b) that it is applied uniformly, (c) the per-panel
        # defaults being overridden, and (d) the exact YAML fix. No
        # branching needed.
        if not getattr(per_panel_min_obs, "_warned_scalar", False):
            scalar_value = int(panel_map)
            logger.warning(
                "universe.min_obs_per_symbol is a scalar (%d) and is being "
                "applied UNIFORMLY to every panel regardless of frequency. "
                "This ignores the per-panel defaults (daily panels normally "
                "use 250, monthly revenue 24, quarterly_eps 12). "
                "To restore frequency-aware thresholds, convert YAML to a "
                "dict, e.g.: "
                "universe.min_obs_per_symbol: "
                "{default: 250, revenue: 24, quarterly_eps: 12}",
                scalar_value,
            )
            per_panel_min_obs._warned_scalar = True  # type: ignore[attr-defined]
        return int(panel_map)
    if not isinstance(panel_map, dict):
        return 250
    if panel_name in panel_map:
        return int(panel_map[panel_name])
    return int(panel_map.get("default", 250))
