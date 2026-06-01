"""IC pipeline must split-adjust the forward-return label and ratio factors.

The cached OHLCV is raw (unadjusted); a split inside the holding window makes
the raw forward-return show a fake ~-50% (1:2) / -75% (1:4) drop that contaminates
the IC target. These tests pin the split-adjust helpers and prove the adjusted
forward-return is correct where the raw one is wrong.

Mutation guard: if `run_factor_ic` reverts to feeding raw close to
`_forward_return`, the label equals the fake raw value asserted below.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts._factor_ic_helpers import (
    _forward_return,
    load_dividends_list,
    split_adjust_close_panel,
    split_adjust_ohlcv_panel,
    total_return_adjust_close_panel,
)


def _clean_close(n: int = 360, start: str = "2023-06-01", base: float = 100.0,
                 daily: float = 0.0005) -> pd.Series:
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(base * (1 + daily) ** np.arange(n), index=idx)


def _inject_1to2_split(close: pd.Series, split_date: pd.Timestamp) -> pd.Series:
    """Raw (unadjusted) 1:2 split: pre-split prices sit at 2x the continuous level."""
    out = close.copy()
    out.loc[out.index < split_date] *= 2.0
    return out


def test_split_adjust_makes_series_continuous():
    clean = _clean_close()
    split_day = clean.index[-90]
    raw = _inject_1to2_split(clean, split_day)
    adj = split_adjust_close_panel({"X": raw})["X"]
    # Adjustment should recover the continuous series (within rounding of the
    # detected ratio): the pre-split region is scaled back ~0.5x.
    pre = clean.index < split_day
    rel_err = ((adj[pre] - clean[pre]) / clean[pre]).abs().max()
    assert rel_err < 0.01, f"pre-split region not recovered (max rel err {rel_err})"


def test_forward_return_raw_is_wrong_adjusted_is_correct():
    clean = _clean_close()
    split_day = clean.index[-90]
    raw = _inject_1to2_split(clean, split_day)
    start = clean.index[-120]  # before split (raw price doubled)
    end = clean.index[-60]     # after split

    raw_fr = _forward_return({"X": raw}, "X", start, end, max_gap_days=5)
    adj = split_adjust_close_panel({"X": raw})
    adj_fr = _forward_return(adj, "X", start, end, max_gap_days=5)

    # Raw label carries the fake ~-50% split drop (the contamination).
    assert raw_fr is not None and raw_fr < -0.45
    # Adjusted label reflects the true (continuous) ~+small return, no fake drop.
    assert adj_fr is not None and adj_fr > -0.05
    # The two must differ materially — proves the fix is not a no-op.
    assert abs(adj_fr - raw_fr) > 0.4


def test_ohlcv_panel_adjustment_scales_all_columns():
    clean = _clean_close()
    split_day = clean.index[-90]
    raw_close = _inject_1to2_split(clean, split_day)
    df = pd.DataFrame({
        "open": raw_close * 0.999,
        "high": raw_close * 1.004,
        "low": raw_close * 0.996,
        "close": raw_close,
        "volume": [50_000_000] * len(raw_close),
    })
    out = split_adjust_ohlcv_panel({"X": df})["X"]
    # Pre-split OHLC scaled down ~0.5x; ratios between columns preserved.
    pre = df.index < split_day
    assert (out.loc[pre, "close"] < df.loc[pre, "close"] * 0.55).all()
    assert np.allclose(out["high"] / out["close"], df["high"] / df["close"])


def test_no_split_series_is_unchanged():
    clean = _clean_close()
    adj = split_adjust_close_panel({"X": clean})["X"]
    assert np.allclose(adj.values, clean.values)


# --- Increment 2: dividend (total-return label) ---

def test_total_return_label_adds_back_dividend():
    """A cash dividend inside the holding window must be added back to the
    label (total-return), not show up as the raw ex-dividend price drop."""
    idx = pd.bdate_range("2024-01-01", periods=60)
    ex_i = 30
    close = pd.Series(
        [100.0] * ex_i + [95.0] * (len(idx) - ex_i), index=idx,  # -5 drop on ex-date
    )
    divs = [{"stock_id": "X", "ex_date": idx[ex_i].strftime("%Y-%m-%d"),
             "cash_dividend": 5.0, "close_before": 100.0}]
    start, end = idx[20], idx[40]  # straddle the ex-date

    raw_fr = _forward_return({"X": close}, "X", start, end, max_gap_days=5)
    tr = total_return_adjust_close_panel({"X": close}, divs)
    tr_fr = _forward_return(tr, "X", start, end, max_gap_days=5)

    assert raw_fr is not None and abs(raw_fr - (-0.05)) < 1e-6   # raw shows fake -5%
    assert tr_fr is not None and abs(tr_fr) < 1e-6               # total-return ~0%
    assert tr_fr - raw_fr > 0.04                                 # dividend added back


def test_total_return_no_dividend_equals_split_only():
    """With no dividends for a symbol, total-return == split-only (no-op)."""
    clean = _clean_close()
    tr = total_return_adjust_close_panel({"X": clean}, [])["X"]
    split_only = split_adjust_close_panel({"X": clean})["X"]
    assert np.allclose(tr.values, split_only.values)


def test_load_dividends_list_missing_returns_empty(tmp_path):
    assert load_dividends_list(tmp_path) == []  # no dividends/_global.pkl present
