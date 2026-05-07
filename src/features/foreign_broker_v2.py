"""Foreign broker factor v2 (三大法人 aggregate, no 分點).

Improves over legacy `src/features/institutional.py::score_institutional`
(實測 IC = -0.053 failed) by combining four persistence-oriented sub-signals
rather than a single day-level net-flow snapshot.

Data: FinMind TaiwanStockInstitutionalInvestorsBuySell per symbol.
    Long-format rows (date, stock_id, name, buy, sell) where name ∈
    {Foreign_Investor, Investment_Trust, Dealer_self, Dealer_Hedging,
    Foreign_Dealer_Self}.

Sub-signals (all higher = more bullish):

    1. foreign_cum_ratio (weight 0.40)
       20D cumulative foreign net / market_value (percent of float bought).

    2. persistence (weight 0.20)
       Fraction of last 20 days where foreign_net > 0.

    3. rank_stability (weight 0.20)
       Fraction of last 60 days the symbol ranked in the top-20% by
       daily foreign_net_ratio (net / market_value).

    4. consistency (weight 0.20)
       Fraction of last 20 days where BOTH Foreign_Investor AND
       Investment_Trust were on the same side (net positive).

Composite = z-score(each sub-signal cross-sectionally) × weight, summed.

PIT: rows with date > as_of - INSTITUTIONAL_LAG_DAYS dropped.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from src.utils.constants import INSTITUTIONAL_LAG_DAYS


DEFAULT_MIN_HISTORY = 60  # need at least 60 days for rank_stability
# P1-新7 + follow-up-2: module fallback; actual default now yaml-driven via
# `_rank_stability_min_universe()` so `config/factor_thresholds.yaml` edits
# change behaviour without code changes.
MIN_UNIVERSE_FOR_RANK_STABILITY = 50

SUBSIGNAL_WEIGHTS = {
    "foreign_cum_ratio": 0.40,
    "persistence": 0.20,
    "rank_stability": 0.20,
    "consistency": 0.20,
}


def _zscore_with_tolerance(col: pd.Series, tolerance: float = 1e-12) -> pd.Series:
    """Cross-sectional z-score with float-noise tolerance guard.

    Codex R8-1 fix: previously a closure inside
    `compute_foreign_broker_v2_universe`, which blocked direct unit testing
    of the guard logic. Extracted to module level so a mutation-proof test
    can import it and verify that `std` below `tolerance` (e.g. 1e-13 from
    float-accumulation noise on a near-constant column) correctly collapses
    to 0.0 rather than producing pathological z-scores.

    Behaviour unchanged from the previous inline version:
    - n < 3 observations → all 0.0 (insufficient data)
    - std < tolerance → all 0.0 (near-constant, NaN-safe)
    - otherwise → (col - mean) / std
    """
    clean = col.dropna()
    if len(clean) < 3:
        return pd.Series(0.0, index=col.index)
    std = clean.std(ddof=1)
    if std < tolerance or pd.isna(std):
        return pd.Series(0.0, index=col.index)
    return (col - clean.mean()) / std


def _rank_stability_min_universe() -> int:
    """Resolve MIN_UNIVERSE_FOR_RANK_STABILITY from yaml (fallback hard-coded)."""
    try:
        from src.utils.thresholds import get_threshold
        value = get_threshold(
            "factor_ic", "min_universe_size", "rank_stability",
            default=MIN_UNIVERSE_FOR_RANK_STABILITY,
        )
        return int(value)
    except Exception:
        return MIN_UNIVERSE_FOR_RANK_STABILITY


def _pivot_long_to_wide(frame: pd.DataFrame) -> pd.DataFrame | None:
    """Pivot (date, name, buy, sell) → per-day rows with foreign_net / trust_net.

    Returns a DataFrame indexed by date with columns:
      foreign_net, trust_net, dealer_self_net, dealer_hedge_net
    (in shares). Rows where foreign is missing are dropped.
    """
    if frame is None or frame.empty or "name" not in frame.columns:
        return None
    working = frame.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["date"])
    for col in ("buy", "sell"):
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce").fillna(0)
    working["net"] = working["buy"] - working["sell"]

    # P1-4: drop duplicate (date, name) rows before pivot so upstream double
    # publications cannot silently double-count a day's net flow. Keep the last
    # occurrence, matching FinMind "latest revision wins" semantics.
    working = working.drop_duplicates(subset=["date", "name"], keep="last")
    pivot = working.pivot_table(
        index="date", columns="name", values="net", aggfunc="last", fill_value=0,
    )
    out = pd.DataFrame(index=pivot.index)
    out["foreign_net"] = pivot.get("Foreign_Investor", 0)
    out["trust_net"] = pivot.get("Investment_Trust", 0)
    out["dealer_self_net"] = pivot.get("Dealer_self", 0)
    out["dealer_hedge_net"] = pivot.get("Dealer_Hedging", 0)
    out = out.sort_index()
    # Drop rows that are all zero AND no Foreign_Investor column was present —
    # means truly no data for that date.
    if "Foreign_Investor" not in pivot.columns:
        return None
    return out


def _truncate_by_date(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    lag_days: int,
) -> pd.DataFrame:
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_convert(None)
    cutoff = as_of_ts - pd.Timedelta(days=lag_days)
    return frame[frame.index <= cutoff]


def _compute_symbol_signals(
    long_df: pd.DataFrame | None,
    market_value: float,
    as_of: pd.Timestamp,
    lag_days: int,
    min_history: int,
) -> dict:
    """Return dict with four sub-signals (floats or None)."""
    if market_value is None or market_value <= 0 or pd.isna(market_value):
        return {}
    wide = _pivot_long_to_wide(long_df)
    if wide is None:
        return {}
    wide = _truncate_by_date(wide, as_of=as_of, lag_days=lag_days)
    if len(wide) < min_history:
        return {}

    last20 = wide.tail(20)
    foreign_last20 = last20["foreign_net"]
    trust_last20 = last20["trust_net"]

    out: dict[str, float] = {}
    # 1) foreign_cum_ratio
    if len(foreign_last20) >= 5:
        cum_foreign = float(foreign_last20.sum())
        out["foreign_cum_ratio"] = cum_foreign / market_value
    # 2) persistence
    if len(foreign_last20) >= 10:
        out["persistence"] = float((foreign_last20 > 0).sum()) / len(foreign_last20)
    # 4) consistency: both foreign AND trust positive (same bullish side)
    if len(foreign_last20) >= 10:
        same_side = ((foreign_last20 > 0) & (trust_last20 > 0)).sum()
        out["consistency"] = float(same_side) / len(foreign_last20)

    return out


def _compute_rank_stability(
    wide_by_symbol: dict[str, pd.DataFrame],
    market_value_by_symbol: Mapping[str, float],
    as_of: pd.Timestamp,
    lag_days: int,
    min_history: int,
    lookback_days: int = 60,
    top_pct: float = 0.20,
    min_universe_size: int | None = None,
) -> dict[str, float]:
    """Fraction of last N days each symbol ranked in top-pct by daily foreign_net/mv.

    Only symbols with ≥ min_history days of (truncated) data are eligible;
    others are silently dropped (caller's per_signal will also drop them via
    its own min_history check).

    `min_universe_size=None` (default) resolves from
    `config/factor_thresholds.yaml :: factor_ic.min_universe_size.rank_stability`
    (follow-up-2 yaml wiring). Pass an int explicitly to pin for tests.
    """
    if min_universe_size is None:
        min_universe_size = _rank_stability_min_universe()
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    eligible: set[str] = set()
    for symbol, wide in wide_by_symbol.items():
        if wide is None:
            continue
        mv = market_value_by_symbol.get(symbol)
        if mv is None or mv <= 0:
            continue
        truncated = _truncate_by_date(wide, as_of=as_of, lag_days=lag_days)
        if len(truncated) < min_history:
            continue
        eligible.add(symbol)
        tail = truncated.tail(lookback_days)
        for date, val in tail["foreign_net"].items():
            rows.setdefault(date, {})[symbol] = float(val) / float(mv)

    if not rows:
        return {}

    counts: dict[str, int] = {}
    day_counts: dict[str, int] = {}
    for date, sym_map in rows.items():
        if not sym_map:
            continue
        # Drop ties at zero — all-zero days give noise ranking that distorts
        # tie-break downstream; require strictly-positive net to count as "top".
        positive = {s: v for s, v in sym_map.items() if v > 0}
        # P1-新7: skip days whose positive-net cross-section is too small to
        # produce a reliable top-20% cut (early universe, low coverage, etc.).
        if len(positive) < min_universe_size:
            continue
        series = pd.Series(positive).sort_values(ascending=False)
        cutoff_idx = max(1, int(len(series) * top_pct))
        top_symbols = set(series.head(cutoff_idx).index)
        for symbol in sym_map:
            day_counts[symbol] = day_counts.get(symbol, 0) + 1
            if symbol in top_symbols:
                counts[symbol] = counts.get(symbol, 0) + 1

    return {s: counts.get(s, 0) / day_counts[s] for s in eligible if day_counts.get(s, 0) >= lookback_days // 2}


def compute_foreign_broker_v2_universe(
    institutional_by_symbol: Mapping[str, pd.DataFrame | None],
    market_value_by_symbol: Mapping[str, float] | None = None,
    as_of: pd.Timestamp | None = None,
    lag_days: int = INSTITUTIONAL_LAG_DAYS,
    min_history: int = DEFAULT_MIN_HISTORY,
    aux_panel: Mapping[str, float] | None = None,
) -> pd.Series:
    """Cross-sectional composite score for foreign_broker_v2.

    Each sub-signal is z-scored across the eligible universe, then weighted-summed.
    """
    if aux_panel is not None and market_value_by_symbol is None:
        market_value_by_symbol = aux_panel
    if market_value_by_symbol is None:
        market_value_by_symbol = {}
    if as_of is None:
        raise ValueError("as_of is required")

    # Precompute wide-format data per symbol (reused for both per-symbol signals
    # and rank_stability).
    wide_by_symbol: dict[str, pd.DataFrame] = {}
    for symbol, df in institutional_by_symbol.items():
        wide = _pivot_long_to_wide(df)
        if wide is not None:
            wide_by_symbol[symbol] = wide

    # Per-symbol signals (foreign_cum_ratio, persistence, consistency)
    per_signal: dict[str, dict] = {}
    for symbol, wide in wide_by_symbol.items():
        mv = market_value_by_symbol.get(symbol)
        if mv is None or mv <= 0:
            continue
        truncated = _truncate_by_date(wide, as_of=as_of, lag_days=lag_days)
        if len(truncated) < min_history:
            continue
        last20 = truncated.tail(20)
        signals: dict[str, float] = {}
        if len(last20) >= 5:
            signals["foreign_cum_ratio"] = float(last20["foreign_net"].sum()) / float(mv)
        if len(last20) >= 10:
            signals["persistence"] = float((last20["foreign_net"] > 0).sum()) / len(last20)
            same_side = ((last20["foreign_net"] > 0) & (last20["trust_net"] > 0)).sum()
            signals["consistency"] = float(same_side) / len(last20)
        if signals:
            per_signal[symbol] = signals

    # Rank stability (needs cross-symbol panel per day)
    rank_stab = _compute_rank_stability(
        wide_by_symbol, market_value_by_symbol,
        as_of=as_of, lag_days=lag_days, min_history=min_history,
    )
    for symbol, v in rank_stab.items():
        per_signal.setdefault(symbol, {})["rank_stability"] = v

    if not per_signal:
        return pd.Series(dtype=float)

    # Build per-signal cross-sectional z-scored frame
    df = pd.DataFrame.from_dict(per_signal, orient="index")

    composite = pd.Series(0.0, index=df.index)
    for signal_name, weight in SUBSIGNAL_WEIGHTS.items():
        if signal_name not in df.columns:
            continue
        composite = composite + weight * _zscore_with_tolerance(df[signal_name]).fillna(0.0)

    # Drop symbols where EVERY sub-signal was missing (unlikely since per_signal
    # required at least one signal per symbol, but keep guard)
    keep = df.notna().any(axis=1)
    return composite[keep].rename(None)
