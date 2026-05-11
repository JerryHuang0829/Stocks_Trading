"""Foreign investor factor v2 (外資法人 aggregate; 三大法人 no 分點).

2026-05-11 rename (R30 misnomer fix): 舊名 foreign_broker_v2 → 改為
foreign_investor_v2. 舊名 "broker" 字面對應 FinMind ``Foreign_Dealer_Self``
（外資自營商），但因子實際讀 ``Foreign_Investor``（外國法人 / QFII），所以
"investor" 更精準。Function / filename / config key / JSON factor_name 全
sync rename（archived JSON 內 factor_name 保留舊名為歷史 evidence）。

Improves over legacy `src/features/institutional.py::score_institutional`
(實測 IC = -0.053 failed) by combining four persistence-oriented sub-signals
rather than a single day-level net-flow snapshot.

Data: FinMind TaiwanStockInstitutionalInvestorsBuySell per symbol.
    Long-format rows (date, stock_id, name, buy, sell) where name ∈
    {Foreign_Investor, Investment_Trust, Dealer_self, Dealer_Hedging,
    Foreign_Dealer_Self}.

Sub-signals (all higher = more bullish):

    1. foreign_cum_ratio (weight 0.50)
       20D cumulative foreign dollar net / market_value (percent of mcap
       bought). 2026-05-10 P0-B: changed from shares/NTD (= 1/price; bad
       cross-section bias) to dollar/NTD (dimensionless ratio) per external audit
       R26 audit. Weight 0.40 → 0.50 after consistency drop (P1-D).

    2. persistence (weight 0.25, was 0.20)
       Fraction of last 20 days where foreign_net > 0.

    3. rank_stability (weight 0.25, was 0.20)
       Fraction of last 60 days the symbol ranked in the top-20% by daily
       foreign DOLLAR net / market_value. 2026-05-10 P0-B: also dollar-
       denominated (was shares/NTD). Weight 0.20 → 0.25 after consistency
       drop (P1-D).

    4. consistency (weight 0.0, was 0.20)
       Fraction of last 20 days where BOTH Foreign_Investor AND
       Investment_Trust were on the same side (net positive). 2026-05-10
       P1-D 修法: deprecated weight to 0 per R26 (78% symbols have
       consistency=0, std 0.094 vs persistence 0.171; low SNR).

Composite = z-score(each sub-signal cross-sectionally) × weight, summed,
and rescaled by actual covered weight (P1-E). Symbols requiring < 50%
effective weight covered are dropped.

PIT: rows with date > as_of - INSTITUTIONAL_LAG_DAYS dropped. Stale-data
guard (P1-C): symbols whose last 20 trading rows span > 35 calendar days
are dropped (R26 reported max stale span 1475 days under old code).
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

# 2026-05-11 R31 finding 4 fix: these module-level constants are now
# FALLBACK DEFAULTS only. The live source of truth is
# `config/factor_thresholds.yaml :: factor_specific.foreign_investor_v2`,
# read at call time via `_subsignal_weights()` / `_last20_max_calendar_span_days()`
# / `_rank_stability_top_pct()` (same pattern as `_rank_stability_min_universe()`).
# Editing the yaml changes behaviour without touching code. The constants below
# are kept (a) as fallbacks if the yaml lookup fails, and (b) as stable import
# targets for tests/test_foreign_investor_v2_dollar_ratio.py.
SUBSIGNAL_WEIGHTS = {
    "foreign_cum_ratio": 0.50,   # 0.40 → 0.50 (P1-D 2026-05-10: redistribute consistency weight)
    "persistence": 0.25,         # 0.20 → 0.25
    "rank_stability": 0.25,      # 0.20 → 0.25
    "consistency": 0.0,          # 0.20 → 0.0 (P1-D 2026-05-10: deprecated; R26 78% zero, low SNR)
}

# 2026-05-10 P1-C: stale-data guard. 20 trading days ≈ 28 calendar days
# normally; allow 25% slack at 35d. Symbols whose last20 calendar span
# exceeds this are dropped (R26 reported max span 1475 days under
# old code, indicating delisted / very stale series).
LAST20_MAX_CALENDAR_SPAN_DAYS = 35

# rank_stability "top-pct" cut fallback (yaml: rank_stability_top_pct)
RANK_STABILITY_TOP_PCT = 0.20


def _zscore_with_tolerance(col: pd.Series, tolerance: float = 1e-12) -> pd.Series:
    """Cross-sectional z-score with float-noise tolerance guard.

    R8-1 fix: previously a closure inside
    `compute_foreign_investor_v2_universe`, which blocked direct unit testing
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


def _subsignal_weights() -> dict[str, float]:
    """Resolve sub-signal weights from yaml (fallback to SUBSIGNAL_WEIGHTS).

    2026-05-11 R31 finding 4 fix: was hardcoded SUBSIGNAL_WEIGHTS only;
    now reads `config/factor_thresholds.yaml :: factor_specific.foreign_investor_v2.weights`
    so the H_a1 amendment (and any future re-weighting) lives in one place.

    Falls back to the module constant if the yaml section is missing or fails
    validation: (a) must be a dict with exactly the 4 known sub-signal keys,
    (b) total weight must sum to ≈ 1.0 (within float tolerance) so a malformed
    yaml can't silently de-normalise the composite.
    """
    try:
        from src.utils.thresholds import get_threshold
        weights = get_threshold(
            "factor_specific", "foreign_investor_v2", "weights",
            default=None,
        )
        if isinstance(weights, dict) and set(weights) == set(SUBSIGNAL_WEIGHTS):
            vals = {k: float(v) for k, v in weights.items()}
            if abs(sum(vals.values()) - 1.0) < 1e-6 and all(v >= 0 for v in vals.values()):
                return vals
    except Exception:
        pass
    return dict(SUBSIGNAL_WEIGHTS)


def _last20_max_calendar_span_days() -> int:
    """Resolve last20 stale-guard span (days) from yaml (fallback to constant)."""
    try:
        from src.utils.thresholds import get_threshold
        value = get_threshold(
            "factor_specific", "foreign_investor_v2", "last20_max_calendar_span_days",
            default=LAST20_MAX_CALENDAR_SPAN_DAYS,
        )
        return int(value)
    except Exception:
        return LAST20_MAX_CALENDAR_SPAN_DAYS


def _rank_stability_top_pct() -> float:
    """Resolve rank_stability top-pct cut from yaml (fallback to constant)."""
    try:
        from src.utils.thresholds import get_threshold
        value = get_threshold(
            "factor_specific", "foreign_investor_v2", "rank_stability_top_pct",
            default=RANK_STABILITY_TOP_PCT,
        )
        return float(value)
    except Exception:
        return RANK_STABILITY_TOP_PCT


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
    close_panel: pd.Series | None,
    as_of: pd.Timestamp,
    lag_days: int,
    min_history: int,
) -> dict:
    """Return dict with three or four sub-signals (floats or None).

    2026-05-10 changes:
      - P0-B: foreign_cum_ratio dollar-denominated (requires ``close_panel``).
        Skipped if close_panel missing rather than falling back to legacy
        shares/NTD ratio (which is dimensionally incorrect).
      - P1-C: drop symbol if last20 calendar span > LAST20_MAX_CALENDAR_SPAN_DAYS.
      - P1-D: consistency still computed (for inspection) but weight=0 in
        SUBSIGNAL_WEIGHTS so it won't contribute to composite.
    """
    if market_value is None or market_value <= 0 or pd.isna(market_value):
        return {}
    wide = _pivot_long_to_wide(long_df)
    if wide is None:
        return {}
    wide = _truncate_by_date(wide, as_of=as_of, lag_days=lag_days)
    if len(wide) < min_history:
        return {}

    last20 = wide.tail(20)

    # P1-C 2026-05-10: stale-data guard. last20 spanning > N calendar days
    # (yaml-driven; default 35) indicates delisted / dormant ticker — its
    # sub-signals should not pollute the cross-section.
    if len(last20) >= 2:
        span_days = (last20.index[-1] - last20.index[0]).days
        if span_days > _last20_max_calendar_span_days():
            return {}

    foreign_last20 = last20["foreign_net"]
    trust_last20 = last20["trust_net"]

    out: dict[str, float] = {}

    # 1) foreign_cum_ratio — P0-B 2026-05-10: dollar-denominated.
    #    cum (foreign_net × close) / market_value. Both numerator & denominator
    #    in NTD → dimensionless ratio (was shares ÷ NTD = 1/price under
    #    legacy code, which biased low-price stocks high in cross-section).
    if len(foreign_last20) >= 5:
        if close_panel is not None and not close_panel.empty:
            close_aligned = close_panel.reindex(last20.index)
            covered = close_aligned.notna()
            if covered.sum() >= 5:
                cum_dollar = float(
                    (foreign_last20 * close_aligned.fillna(0.0)).sum()
                )
                out["foreign_cum_ratio"] = cum_dollar / float(market_value)
        # close_panel missing: skip foreign_cum_ratio rather than use legacy
        # shares/NTD ratio. Caller's covered-weight rescale (P1-E) handles
        # missing sub-signals.

    # 2) persistence
    if len(foreign_last20) >= 10:
        out["persistence"] = float((foreign_last20 > 0).sum()) / len(foreign_last20)

    # 3) consistency — P1-D weight=0; computation kept for inspection only.
    if len(foreign_last20) >= 10:
        same_side = ((foreign_last20 > 0) & (trust_last20 > 0)).sum()
        out["consistency"] = float(same_side) / len(foreign_last20)

    return out


def _compute_rank_stability(
    wide_by_symbol: dict[str, pd.DataFrame],
    market_value_by_symbol: Mapping[str, float],
    *,
    close_by_symbol: Mapping[str, pd.Series] | None = None,
    as_of: pd.Timestamp,
    lag_days: int,
    min_history: int,
    lookback_days: int = 60,
    top_pct: float | None = None,
    min_universe_size: int | None = None,
) -> dict[str, float]:
    """Fraction of last N days each symbol ranked in top-pct by daily foreign DOLLAR net / mv.

    Only symbols with ≥ min_history days of (truncated) data are eligible;
    others are silently dropped (caller's per_signal will also drop them via
    its own min_history check).

    2026-05-10 P0-B: ratio is now dollar-denominated (foreign_net × close ÷
    market_value) instead of shares/NTD. Symbols missing close panel data
    are silently dropped (NaN handling identical to mv missing). Per-day
    universe shrinks to symbols with both mv > 0 AND close available on
    that day.

    `min_universe_size=None` (default) resolves from
    `config/factor_thresholds.yaml :: factor_ic.min_universe_size.rank_stability`
    (follow-up-2 yaml wiring). Pass an int explicitly to pin for tests.

    `top_pct=None` (default) resolves from `config/factor_thresholds.yaml ::
    factor_specific.foreign_investor_v2.rank_stability_top_pct` (2026-05-11
    R31 finding 4 fix). Pass a float explicitly to pin for tests.
    """
    if min_universe_size is None:
        min_universe_size = _rank_stability_min_universe()
    if top_pct is None:
        top_pct = _rank_stability_top_pct()
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
        # P0-B 2026-05-10: dollar denominator requires close panel
        if close_by_symbol is None:
            continue
        close_series = close_by_symbol.get(symbol)
        if close_series is None or len(close_series) == 0:
            continue
        eligible.add(symbol)
        tail = truncated.tail(lookback_days)
        close_aligned = close_series.reindex(tail.index)
        for date, val in tail["foreign_net"].items():
            cl = close_aligned.get(date)
            if cl is None or pd.isna(cl):
                continue
            rows.setdefault(date, {})[symbol] = float(val) * float(cl) / float(mv)

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


def compute_foreign_investor_v2_universe(
    institutional_by_symbol: Mapping[str, pd.DataFrame | None],
    market_value_by_symbol: Mapping[str, float] | None = None,
    as_of: pd.Timestamp | None = None,
    lag_days: int = INSTITUTIONAL_LAG_DAYS,
    min_history: int = DEFAULT_MIN_HISTORY,
    aux_panel: Mapping[str, float] | None = None,
    close_by_symbol: Mapping[str, pd.Series] | None = None,
) -> pd.Series:
    """Cross-sectional composite score for foreign_investor_v2.

    Each sub-signal is z-scored across the eligible universe and weighted-
    summed; composite is then rescaled by actual covered weight (P1-E).

    2026-05-10 changes:
      - P0-B: ``close_by_symbol`` required for dollar-denominated cum_ratio
        and rank_stability ratios. Pass {symbol: close_series} from caller.
        If omitted, foreign_cum_ratio + rank_stability sub-signals are
        skipped (composite then driven by persistence + consistency, which
        is degenerate — only useful for tests).
      - P1-D: consistency weight=0 in SUBSIGNAL_WEIGHTS (computed but no
        longer contributes to composite).
      - P1-E: composite rescaled by covered-weight; symbols with < 50%
        effective weight covered are dropped instead of relying on
        fillna(0.0) without rescale (which biased symbols missing
        rank_stability toward zero composite).
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

    # Per-symbol signals — call helper for reusable PIT + stale-guard logic.
    per_signal: dict[str, dict] = {}
    for symbol, wide in wide_by_symbol.items():
        mv = market_value_by_symbol.get(symbol)
        if mv is None or mv <= 0:
            continue
        # P0-B: extract per-symbol close series for dollar denominator
        close_panel = None
        if close_by_symbol is not None:
            cs = close_by_symbol.get(symbol)
            if cs is not None and len(cs) > 0:
                close_panel = cs
        # Rebuild the long_df-equivalent wide via direct call to helper to
        # leverage P1-C stale guard. Pass the wide we already have via a
        # one-symbol institutional dict.
        signals = _compute_symbol_signals(
            long_df=institutional_by_symbol.get(symbol),
            market_value=mv,
            close_panel=close_panel,
            as_of=as_of,
            lag_days=lag_days,
            min_history=min_history,
        )
        if signals:
            per_signal[symbol] = signals

    # Rank stability (needs cross-symbol panel per day) — P0-B requires close panel
    rank_stab = _compute_rank_stability(
        wide_by_symbol,
        market_value_by_symbol,
        close_by_symbol=close_by_symbol,
        as_of=as_of,
        lag_days=lag_days,
        min_history=min_history,
    )
    for symbol, v in rank_stab.items():
        per_signal.setdefault(symbol, {})["rank_stability"] = v

    if not per_signal:
        return pd.Series(dtype=float)

    # Build per-signal cross-sectional z-scored frame
    df = pd.DataFrame.from_dict(per_signal, orient="index")

    # P1-E 2026-05-10: covered-weight rescale.
    # Old code: composite += weight * z.fillna(0.0). Symbols missing a
    # sub-signal got weight worth of 0, biasing them toward "average". New
    # code tracks per-symbol effective weight and rescales, so a symbol
    # missing rank_stability (25%) is rescaled by 0.75 not blended with
    # zero.
    composite = pd.Series(0.0, index=df.index)
    total_weight = pd.Series(0.0, index=df.index)
    for signal_name, weight in _subsignal_weights().items():
        if weight == 0 or signal_name not in df.columns:
            continue
        z = _zscore_with_tolerance(df[signal_name])
        covered = df[signal_name].notna()
        composite = composite + (weight * z).fillna(0.0)
        total_weight = total_weight + covered.astype(float) * weight

    # Avoid divide-by-zero for symbols missing all weighted sub-signals
    safe_total = total_weight.where(total_weight > 0)
    composite = composite / safe_total

    # Require ≥ 50% effective weight covered (drops symbols missing both
    # cum_ratio AND rank_stability — together 75% of effective weight)
    keep = total_weight >= 0.5
    return composite[keep].dropna().rename(None)
