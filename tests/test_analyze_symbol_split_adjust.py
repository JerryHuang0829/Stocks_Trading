"""_analyze_symbol() must forward-adjust individual-stock splits.

The cached OHLCV is raw (unadjusted) — the free FinMind feed has no paid
adjusted-price access — so a name with a split inside the momentum / SMA window
carries a fake price discontinuity. The benchmark proxy already adjusts; these
tests pin that individual names get the same treatment.

Mutation guard: delete the `adjust_splits` loop in `_analyze_symbol` and the
split-vs-clean assertions below fail (split stock shows a fake ~-50% momentum
and drops out of the risk_on eligibility filters).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from src.portfolio.tw_stock import _analyze_symbol


def _uptrend_ohlcv(n: int = 340, start: str = "2023-01-02",
                   base: float = 100.0, daily: float = 0.0011) -> pd.DataFrame:
    """Smooth deterministic uptrend; tz-aware DatetimeIndex (production shape)."""
    idx = pd.bdate_range(start, periods=n, tz="Asia/Taipei")
    close = base * (1 + daily) ** np.arange(n)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": [60_000_000] * n,
        },
        index=idx,
    )


def _inject_unadjusted_split(df: pd.DataFrame, bars_before_end: int = 100,
                             factor: float = 2.0) -> pd.DataFrame:
    """Simulate a raw (unadjusted) 1:2 forward split.

    Pre-split rows sit at `factor`x the continuous level, so the split day shows
    a ~-50% drop — exactly what an unadjusted cache stores (cf. 0050 2025-06-18).
    """
    out = df.copy()
    split_day = out.index[-bars_before_end]
    pre = out.index < split_day
    out.loc[pre, ["open", "high", "low", "close"]] *= factor
    return out


class _SingleSource:
    """Minimal source exposing only fetch_ohlcv (price-momentum-only config)."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def fetch_ohlcv(self, symbol, timeframe, limit):  # noqa: ARG002
        return self._df


_CFG = {
    "score_weights": {
        "price_momentum": 1.0,
        "trend_quality": 0.0,
        "revenue_momentum": 0.0,
        "institutional_flow": 0.0,
    },
    "exclude_etf": True,
    "min_price": 20.0,
    "min_avg_turnover": 50_000_000.0,
    "use_monthly_revenue": False,
}
_SYM = {"symbol": "1234", "name": "SplitCo", "industry": "電子工業"}
_AS_OF = datetime(2024, 5, 1)


def _analyze(df):
    return _analyze_symbol(_SYM, _SingleSource(df), {}, _CFG, _AS_OF,
                           market_signal="risk_on")


def test_split_corrected_momentum_matches_clean():
    clean = _uptrend_ohlcv()
    split = _inject_unadjusted_split(clean)

    a_clean = _analyze(clean)
    a_split = _analyze(split)

    # Both momentum windows span the split day; after adjustment the split
    # stock's momentum must track the clean stock (recovered to one series),
    # not the fake ~-50% the raw discontinuity would produce.
    assert a_split["momentum_6m"] is not None and a_split["momentum_6m"] > 0
    assert a_split["momentum_12_1"] is not None and a_split["momentum_12_1"] > 0
    assert abs(a_split["price_momentum_raw"] - a_clean["price_momentum_raw"]) < 0.05


def test_split_stock_stays_eligible_under_risk_on():
    split = _inject_unadjusted_split(_uptrend_ohlcv())
    a_split = _analyze(split)
    # Without adjustment the fake drop trips below_sma_slow / momentum_6m gates.
    assert a_split["eligible"] is True, a_split["filters"]


def test_no_split_series_is_unchanged_by_adjustment():
    # A clean series has no >40% single-day move, so adjustment is a no-op:
    # confirms the fix does not perturb the common (no-split) path.
    clean = _uptrend_ohlcv()
    a = _analyze(clean)
    assert a["eligible"] is True
    assert a["price_momentum_raw"] > 0
