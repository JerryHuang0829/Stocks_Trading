"""Unit tests for src.features.high_proximity (52W high proximity factor)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.high_proximity import (
    compute_high_proximity_universe,
    score_high_proximity,
)


def _make_ohlcv(close_values, start="2022-01-03", tz="UTC") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(close_values))
    if tz:
        idx = idx.tz_localize(tz)
    return pd.DataFrame(
        {
            "open": close_values,
            "high": [v * 1.01 for v in close_values],
            "low": [v * 0.99 for v in close_values],
            "close": close_values,
            "volume": [1_000_000] * len(close_values),
        },
        index=idx,
    )


def test_proximity_at_new_high():
    # 300 days, strictly ascending close; as_of at the last day → proximity≈0
    values = np.linspace(50.0, 200.0, 300)
    ohlcv = _make_ohlcv(values.tolist())
    as_of = ohlcv.index[-1].tz_convert(None)
    series = compute_high_proximity_universe({"AAA": ohlcv}, as_of=as_of)
    assert "AAA" in series
    # close[-1]=200, rolling_high over preceding 252 days max < 200
    # so proximity > 0 (close exceeds prior 252-day high)
    assert series["AAA"] > 0


def test_proximity_50pct_below_high():
    # First 252 days ramp to 100; then 50-day drawdown to 50 → proximity ≈ -0.5
    ramp = np.linspace(50.0, 100.0, 252).tolist()
    drawdown = np.linspace(100.0, 50.0, 50).tolist()
    values = ramp + drawdown
    ohlcv = _make_ohlcv(values)
    as_of = ohlcv.index[-1].tz_convert(None)
    series = compute_high_proximity_universe({"AAA": ohlcv}, as_of=as_of, window=252)
    assert "AAA" in series
    # close_today=50, rolling_high ≈ 100 → proximity ≈ -0.5
    assert series["AAA"] == pytest.approx(-0.5, abs=0.02)


def test_proximity_pit_no_lookahead():
    # Construct series where the biggest close is precisely on as_of day.
    # The shift=1 semantics must EXCLUDE that day from the rolling max,
    # so proximity should be strictly positive (today exceeds prior max).
    values = [50.0] * 260 + [1000.0]  # spike on last bar
    ohlcv = _make_ohlcv(values)
    as_of = ohlcv.index[-1].tz_convert(None)
    series = compute_high_proximity_universe({"AAA": ohlcv}, as_of=as_of)
    assert "AAA" in series
    # rolling_high over [prev 252] = 50; close_today=1000 → proximity = 19.0
    assert series["AAA"] == pytest.approx(1000.0 / 50.0 - 1.0, abs=1e-6)


def test_proximity_new_ipo_insufficient_history():
    # Only 100 days of history (< min_history=126) → dropped from result
    values = np.linspace(50.0, 80.0, 100).tolist()
    ohlcv = _make_ohlcv(values)
    as_of = ohlcv.index[-1].tz_convert(None)
    series = compute_high_proximity_universe(
        {"NEW": ohlcv}, as_of=as_of, min_history=126
    )
    assert "NEW" not in series


def test_proximity_handles_nan_close():
    # Inject NaN cluster near the end; should use last valid close
    values = np.linspace(50.0, 100.0, 260).tolist()
    values[-5:] = [np.nan] * 5
    ohlcv = _make_ohlcv(values)
    as_of = ohlcv.index[-1].tz_convert(None)
    series = compute_high_proximity_universe({"AAA": ohlcv}, as_of=as_of)
    # Should still produce a value, using last valid close (values[-6])
    assert "AAA" in series
    last_valid = values[-6]
    expected = last_valid / max(values[:-6]) - 1.0
    assert series["AAA"] == pytest.approx(expected, abs=1e-6)


def test_proximity_universe_batch():
    # Three symbols with different histories
    ohlcv_by_symbol = {
        "AAA": _make_ohlcv(np.linspace(50, 100, 260).tolist()),
        "BBB": _make_ohlcv(np.linspace(100, 50, 260).tolist()),   # declining
        "CCC": _make_ohlcv(np.linspace(50, 70, 100).tolist()),     # too short
    }
    as_of = pd.Timestamp("2023-01-03")  # well after all series end
    series = compute_high_proximity_universe(ohlcv_by_symbol, as_of=as_of, min_history=126)
    assert set(series.index) == {"AAA", "BBB"}
    # AAA end at 100 vs high 100 → ~0
    # BBB end at 50 vs high 100 → ~-0.5
    assert series["BBB"] < series["AAA"]


def test_score_high_proximity_wrapper_matches_batch():
    values = np.linspace(50.0, 100.0, 260).tolist()
    ohlcv = _make_ohlcv(values)
    as_of = ohlcv.index[-1].tz_convert(None)
    result = score_high_proximity(ohlcv, as_of=as_of)
    assert result["score"] is not None
    assert result["detail"] == "proximity_252d"
    assert result["icon"] in {"🔥", "✅", "⚠️", "🔻"}


def test_score_high_proximity_insufficient_history_returns_none():
    values = np.linspace(50.0, 80.0, 50).tolist()
    ohlcv = _make_ohlcv(values)
    as_of = ohlcv.index[-1].tz_convert(None)
    result = score_high_proximity(ohlcv, as_of=as_of)
    assert result["score"] is None
    assert result["detail"] == "insufficient_history"
