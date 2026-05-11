"""Margin / short ratio factor (反向訊號 reverse-coded).

Theory: high margin balance + fast margin-balance increase ≈ retail chasing
high prices, historically correlated with subsequent underperformance.
Short-sale balance is a weaker reverse signal (short-covering pressure).

Composite (reverse-coded inside formula — **higher factor score = lower margin
ratio = higher expected return**, because the score uses negative weights to
flip the raw inverse-alpha signal into a positive-alpha score):

    margin_ratio      = (MarginPurchaseTodayBalance - ShortSaleTodayBalance) / issued_shares
    margin_change_20d = MarginPurchaseTodayBalance_today / MarginPurchaseTodayBalance_20d_ago - 1
    score             = -0.5 * zscore_cross(margin_ratio) - 0.5 * zscore_cross(margin_change_20d)

So when used in IC pipeline:
  - mean IC > 0 → expected (the factor predicts return positively as designed)
  - decile rho > 0 (D9 > D0) → expected long-only behavior

Sign-convention sanity check (Codex R28-4 release):
  Old docstring "higher factor score = lower expected return" was wrong-sign.
  Empirical fresh rerun 2026-05-10: mean IC = +0.0387, but per-period IC vs
  per-period (D9-D0 spread) Spearman = 0.946 (high consistency). Cross-period
  AVERAGE IC and AVERAGE spread don't have to match in sign — that's a
  statistical property, not a bug. Factor sign-convention itself is correct.

- PIT: rows with ``date > as_of - MARGIN_LAG_DAYS`` are dropped (T+2 conservative)
- Edge: zero issued_shares / missing panel / < min_history days all drop the symbol
- Units: TWSE margin balance is in 張 (lots, 1 lot = 1000 shares); issued_shares
  fetched via fetch_twse_issued_capital() is in **shares** (not lots). This
  module re-normalises margin balance from lots → shares before forming the
  ratio so that the denominator is consistent.
- 2026-05-10 R28-2: portfolio caller `tw_stock._load_issued_capital_dict` now
  uses panel + asof helper (same as IC pipeline) instead of latest snapshot.
- ⚠️ Static-snapshot caveat (R28-1): when issued_capital cache lacks date
  column (current state), both IC and portfolio paths fall back to
  pd.Timestamp.min, treating issued_shares as constant across all dates.
  margin_ratio denominator is therefore "PIT approximation", not fully PIT.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from src.utils.constants import MARGIN_LAG_DAYS


DEFAULT_MIN_HISTORY = 40  # trading days, enough for 20D change + buffer
LOTS_TO_SHARES = 1000


def _zscore_with_tolerance(s: pd.Series, tolerance: float = 1e-12) -> pd.Series:
    """Cross-sectional z-score with float-noise tolerance guard.

    Codex R8-1 fix: previously a closure inside
    `compute_margin_short_ratio_universe`, which blocked direct unit testing
    of the guard logic. Extracted to module level so a mutation-proof test
    can import it and verify that `std` below `tolerance` (e.g. 1e-13 from
    float-accumulation noise on a near-constant series) correctly collapses
    to 0.0 rather than producing pathological z-scores.

    Behaviour unchanged from the previous inline version:
    - n < 3 observations → all 0.0 (insufficient data)
    - std < tolerance → all 0.0 (near-constant, NaN-safe)
    - otherwise → (s - mean) / std
    """
    if len(s) < 3:
        return pd.Series(0.0, index=s.index)
    std = s.std(ddof=1)
    if std < tolerance or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def _normalise_margin_frame(
    df: pd.DataFrame | None,
    as_of: pd.Timestamp,
    lag_days: int,
) -> pd.DataFrame | None:
    if df is None or df.empty or "date" not in df.columns:
        return None
    working = df.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["date"])
    if working.empty:
        return None

    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        as_of_ts = as_of_ts.tz_convert(None)
    cutoff = as_of_ts - pd.Timedelta(days=lag_days)
    working = working[working["date"] <= cutoff]
    if working.empty:
        return None

    for col in ("MarginPurchaseTodayBalance", "ShortSaleTodayBalance"):
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")
    working = working.dropna(subset=["MarginPurchaseTodayBalance"])
    return working.sort_values("date").reset_index(drop=True)


def _compute_raw_signals(
    frame: pd.DataFrame,
    issued_shares: float,
) -> tuple[float | None, float | None]:
    """Return (margin_ratio, margin_change_20d). Either may be None."""
    if issued_shares is None or issued_shares <= 0 or pd.isna(issued_shares):
        return None, None
    if len(frame) < 21:
        margin_change_20d = None
    else:
        latest = float(frame["MarginPurchaseTodayBalance"].iloc[-1])
        prior = float(frame["MarginPurchaseTodayBalance"].iloc[-21])
        if prior <= 0:
            margin_change_20d = None
        else:
            margin_change_20d = latest / prior - 1.0

    latest_row = frame.iloc[-1]
    margin_lots = float(latest_row["MarginPurchaseTodayBalance"])
    short_lots = float(latest_row.get("ShortSaleTodayBalance", 0.0)) if not pd.isna(
        latest_row.get("ShortSaleTodayBalance", 0.0)
    ) else 0.0
    margin_ratio = (margin_lots - short_lots) * LOTS_TO_SHARES / issued_shares

    return margin_ratio, margin_change_20d


def compute_margin_short_ratio_universe(
    margin_by_symbol: Mapping[str, pd.DataFrame | None],
    issued_by_symbol: Mapping[str, float] | None = None,
    as_of: pd.Timestamp | None = None,
    lag_days: int = MARGIN_LAG_DAYS,
    min_history: int = DEFAULT_MIN_HISTORY,
    aux_panel: Mapping[str, float] | None = None,
) -> pd.Series:
    """Batch compute reversed margin/short composite score.

    Parameters
    ----------
    margin_by_symbol : {symbol: DataFrame}
        Per-symbol margin/short history (columns include MarginPurchaseTodayBalance,
        ShortSaleTodayBalance).
    issued_by_symbol : {symbol: float} | None
        Issued shares (not lots). Either via this keyword or via ``aux_panel``
        (CLI passes aux_panel for uniformity across factors).
    as_of : timestamp
        Reference date; cutoff = as_of - lag_days.
    """
    if aux_panel is not None and issued_by_symbol is None:
        issued_by_symbol = aux_panel
    if issued_by_symbol is None:
        issued_by_symbol = {}
    if as_of is None:
        raise ValueError("as_of is required")

    raw_ratios: dict[str, float] = {}
    raw_changes: dict[str, float] = {}
    for symbol, df in margin_by_symbol.items():
        frame = _normalise_margin_frame(df, as_of=as_of, lag_days=lag_days)
        if frame is None or len(frame) < min_history:
            continue
        issued = issued_by_symbol.get(symbol)
        if issued is None:
            continue
        ratio, change = _compute_raw_signals(frame, float(issued))
        if ratio is not None:
            raw_ratios[symbol] = ratio
        if change is not None:
            raw_changes[symbol] = change

    ratio_series = pd.Series(raw_ratios, dtype=float)
    change_series = pd.Series(raw_changes, dtype=float)
    if ratio_series.empty and change_series.empty:
        return pd.Series(dtype=float)

    # P1-3: use intersection (not union) so symbols with partial coverage do
    # not get a fillna(0.0) shadow signal that distorts the z-score baseline
    # and the composite score. Only symbols with both raw signals present
    # participate in the final ranking.
    common_idx = ratio_series.index.intersection(change_series.index)
    if common_idx.empty:
        return pd.Series(dtype=float)
    ratio_z = _zscore_with_tolerance(ratio_series.loc[common_idx])
    change_z = _zscore_with_tolerance(change_series.loc[common_idx])
    composite = -0.5 * ratio_z - 0.5 * change_z
    return composite.rename(None)


def score_margin_short(
    margin_df: pd.DataFrame | None,
    issued_shares: float | None,
    as_of: pd.Timestamp,
    lag_days: int = MARGIN_LAG_DAYS,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> dict:
    """Per-symbol wrapper (aligned with src/features/institutional.py style).

    Returns dict with keys: score, detail, icon. Score uses raw signals
    without cross-sectional z-score (for standalone inspection only; the
    composite above is the correct score for ranking).
    """
    frame = _normalise_margin_frame(margin_df, as_of=as_of, lag_days=lag_days)
    if frame is None or len(frame) < min_history or issued_shares is None:
        return {"score": None, "detail": "insufficient", "icon": "➖"}
    ratio, change = _compute_raw_signals(frame, float(issued_shares))
    if ratio is None and change is None:
        return {"score": None, "detail": "zero_denominator", "icon": "⚠️"}
    # Standalone score: sign convention identical to batch (higher = more retail overbought)
    standalone = 0.0
    if ratio is not None:
        standalone += 0.5 * ratio * 1000  # scale up for readability only
    if change is not None:
        standalone += 0.5 * change
    return {
        "score": -standalone,  # reversed for buy-low convention
        "detail": f"ratio={ratio} change_20d={change}",
        "icon": "🔻" if standalone > 0 else "✅",
    }
