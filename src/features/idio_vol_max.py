"""V0.13 idio_vol_max (D-G candidate factor): 0.5/0.5 IdioVol residual + MAX lottery.

Phase 2 Session 3 (2026-05-05) — H_d_v6 V0.13 §"3 New factor PIT lag spec":
- residual std lookback = 60 trading days strict-before
- MAX lottery = top-5 daily return in past 1m (~22 trading days)
- shift=1 semantics (mirrors high_proximity / low_vol_v2)
- 0.5/0.5 weights split per H_d_v6:58 D-G + R24 §設計-6

Definition (per H_d_v6:58 D-G row):
    composite = 0.5 × z(-residual_std) + 0.5 × z(-max_lottery)
    (negative sign: both are anti-features for long-only — low residual / low
     MAX = high quality; cross-section z-scored)

IdioVol residual: stock_std × √(1 - corr(stock, market)²) — simple proxy
    for OLS residual std (avoids per-stock OLS fit cost; corr-based formula
    matches mathematical derivation: residual variance = total variance ×
    (1 - R²) where R² = corr² for univariate regression).

MAX lottery: top-5 daily returns in past 22 trading days (Bali-Cakici-Whitelaw
    2011 "Maxing Out: Stocks as Lotteries"). Average of top-5 captures
    lottery-like skewness exposure.

A6 cross-correlation 監控 (per V0.13 §"A6 cross-correlation 監控擴展"):
    若 D-G idio_vol_max 與 low_vol_v2 |ρ| > 0.6 → D-G drop or weight halve
    (Phase 2 S6 cell sweep run 階段檢查 + report)

Caller wires (Phase 2 S6 cache fresh-rerun + S5 cell sweep CLI):
    from src.features.idio_vol_max import compute_idio_vol_max_panel
    panel = compute_idio_vol_max_panel(
        ohlcv_panel=ohlcv_dict,
        market_returns=market_daily_returns,
        as_of=rebalance_date,
    )
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_RESIDUAL_LOOKBACK_DAYS: int = 60
DEFAULT_MAX_LOOKBACK_DAYS: int = 22
DEFAULT_MAX_TOP_K: int = 5
DEFAULT_WEIGHTS: tuple[float, float] = (0.5, 0.5)
DEFAULT_Z_CLIP: float = 3.0
MIN_OBS_FOR_REGRESSION: int = 10


def _compute_residual_std(stock_returns: pd.Series, market_returns: pd.Series) -> float:
    """Simple residual std proxy: stock_std × √(1 - corr²).

    Mathematically: residual variance = total variance × (1 - R²) where for
    univariate regression R² = corr². Avoids per-stock OLS fit cost.

    Returns NaN if insufficient observations or non-finite correlation.
    """
    aligned_stock, aligned_market = stock_returns.align(market_returns, join="inner")
    if len(aligned_stock) < MIN_OBS_FOR_REGRESSION:
        return float("nan")
    corr = float(aligned_stock.corr(aligned_market))
    if not np.isfinite(corr):
        return float("nan")
    stock_std = float(aligned_stock.std(ddof=1))
    if not np.isfinite(stock_std) or stock_std < 0:
        return float("nan")
    return stock_std * np.sqrt(max(0.0, 1.0 - corr * corr))


def _compute_max_lottery(daily_returns: pd.Series, top_k: int = DEFAULT_MAX_TOP_K) -> float:
    """Mean of top-k daily returns in lookback window.

    Returns NaN if insufficient observations.
    """
    clean = daily_returns.dropna()
    if len(clean) < top_k:
        return float("nan")
    return float(clean.nlargest(top_k).mean())


def compute_idio_vol_max_panel(
    ohlcv_panel: dict[str, pd.DataFrame],
    market_returns: pd.Series,
    as_of: pd.Timestamp,
    *,
    residual_lookback_days: int = DEFAULT_RESIDUAL_LOOKBACK_DAYS,
    max_lookback_days: int = DEFAULT_MAX_LOOKBACK_DAYS,
    max_top_k: int = DEFAULT_MAX_TOP_K,
    weights: tuple[float, float] = DEFAULT_WEIGHTS,
    z_clip: float = DEFAULT_Z_CLIP,
) -> pd.Series:
    """Compute D-G idio_vol_max cross-section panel at rebalance date.

    Args:
        ohlcv_panel: dict[symbol -> OHLCV DataFrame with DatetimeIndex];
            each DataFrame must contain 'close' column.
        market_returns: pd.Series of market (typically 0050) daily returns
            indexed by date.
        as_of: rebalance date.
        residual_lookback_days: 60 trading days per V0.13 lock.
        max_lookback_days: ~22 trading days (1 month) per V0.13.
        max_top_k: top-5 per Bali-Cakici-Whitelaw 2011.
        weights: (idio_weight, max_weight); default (0.5, 0.5) per H_d_v6:58.
        z_clip: cross-section z-score outlier clip.

    Returns:
        pd.Series indexed by symbol, value = composite z-score (higher =
        better; both components negated since low residual / low MAX = good).
        Empty Series when no valid symbols.

    Raises:
        ValueError: if weights don't sum to 1.0.

    PIT semantics:
        - Both lookbacks end at (as_of - 1d) [shift=1 strict-before]
        - Stock daily returns: close.pct_change() within lookback window
    """
    if abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError(f"weights must sum to 1.0; got {sum(weights)}")

    cutoff = as_of - pd.Timedelta(days=1)  # shift=1 strict-before

    # Market returns lookback (need at least max(residual, max) days)
    market_clean = market_returns.dropna()
    market_pre_cutoff = market_clean[market_clean.index < cutoff]
    needed_market_days = max(residual_lookback_days, max_lookback_days)
    if len(market_pre_cutoff) < needed_market_days:
        return pd.Series(dtype=float)
    market_residual_window = market_pre_cutoff.iloc[-residual_lookback_days:]

    # Per-symbol IdioVol + MAX
    sym_idio_vol: dict[str, float] = {}
    sym_max_lottery: dict[str, float] = {}

    for sym, df in ohlcv_panel.items():
        if df is None or df.empty:
            continue
        if "close" not in df.columns:
            continue
        df_pre_cutoff = df[df.index < cutoff]
        if len(df_pre_cutoff) < needed_market_days:
            continue

        # Residual std (60-day lookback)
        residual_window = df_pre_cutoff.iloc[-residual_lookback_days:]
        stock_returns_residual = residual_window["close"].pct_change().dropna()
        idio = _compute_residual_std(stock_returns_residual, market_residual_window)
        if np.isfinite(idio):
            sym_idio_vol[sym] = idio

        # MAX lottery (22-day lookback)
        max_window = df_pre_cutoff.iloc[-max_lookback_days:]
        stock_returns_max = max_window["close"].pct_change().dropna()
        mx = _compute_max_lottery(stock_returns_max, top_k=max_top_k)
        if np.isfinite(mx):
            sym_max_lottery[sym] = mx

    common_syms = set(sym_idio_vol) & set(sym_max_lottery)
    if not common_syms:
        return pd.Series(dtype=float)

    # Negate both: low residual / low MAX = high quality (long-only good)
    idio_series = pd.Series({s: -sym_idio_vol[s] for s in common_syms})
    max_series = pd.Series({s: -sym_max_lottery[s] for s in common_syms})

    # Drop non-finite
    valid = (
        idio_series.notna() & max_series.notna()
        & np.isfinite(idio_series) & np.isfinite(max_series)
    )
    idio_series, max_series = idio_series[valid], max_series[valid]
    if len(idio_series) == 0:
        return pd.Series(dtype=float)

    def _z(s: pd.Series) -> pd.Series:
        sd = float(s.std(ddof=1))
        if sd <= 1e-12 or not np.isfinite(sd):
            return pd.Series(0.0, index=s.index, dtype=float)
        return ((s - s.mean()) / sd).clip(-z_clip, z_clip)

    w_idio, w_max = weights
    composite = w_idio * _z(idio_series) + w_max * _z(max_series)
    return composite.dropna()
