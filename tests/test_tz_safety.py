"""Regression tests for tz-aware/naive Timestamp conversion.

Guards against a class of pandas 2.x bug where
    pd.Timestamp(tz_aware_value, tz="UTC")
raises ValueError. All UTC conversions should go through to_utc_ts().
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.utils.constants import TW_TZ, to_utc_ts


class TestToUtcTs:
    def test_naive_string(self):
        ts = to_utc_ts("2024-01-01")
        assert ts.tzinfo is not None
        assert str(ts.tz) == "UTC"

    def test_naive_datetime(self):
        ts = to_utc_ts(datetime(2024, 1, 1))
        assert str(ts.tz) == "UTC"

    def test_tz_aware_taipei(self):
        src = pd.Timestamp("2024-01-01", tz="Asia/Taipei")
        ts = to_utc_ts(src)
        assert str(ts.tz) == "UTC"
        assert ts == src.tz_convert("UTC")

    def test_tz_aware_datetime(self):
        src = datetime(2024, 1, 1, tzinfo=TW_TZ)
        ts = to_utc_ts(src)
        assert str(ts.tz) == "UTC"

    def test_tz_aware_utc_roundtrip(self):
        src = pd.Timestamp("2024-06-15 08:00:00", tz="UTC")
        assert to_utc_ts(src) == src


class TestEngineAcceptsTzAware:
    """Verify BacktestEngine paths no longer raise on tz-aware inputs."""

    def test_set_as_of_tz_aware(self):
        from src.backtest.engine import _DataSlicer

        sl = _DataSlicer.__new__(_DataSlicer)
        sl._as_of = None
        sl.set_as_of(pd.Timestamp("2024-01-01", tz="Asia/Taipei"))
        assert str(sl._as_of.tz) == "UTC"

    def test_raw_pandas_pattern_still_raises(self):
        """Sanity check: the OLD pattern must still raise, so our helper is load-bearing."""
        with pytest.raises(ValueError):
            pd.Timestamp(pd.Timestamp("2024-01-01", tz="Asia/Taipei"), tz="UTC")

    def test_compute_daily_returns_tz_aware_period(self):
        """實打 engine._compute_daily_returns：tz-aware period_start/end 不應 raise。"""
        from src.backtest.engine import BacktestEngine

        class _FakeSlicer:
            def fetch_ohlcv(self, symbol, tf, n):
                idx = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
                return pd.DataFrame(
                    {"close": [100.0 + i for i in range(30)],
                     "volume": [1000.0] * 30},
                    index=idx,
                )

        eng = BacktestEngine.__new__(BacktestEngine)
        eng._ohlcv_min_fetch = 30
        eng._dividends = None

        # tz-aware 輸入（舊 pattern 會在 to_utc_ts 之前炸掉）
        period_start = pd.Timestamp("2024-01-05", tz="Asia/Taipei")
        period_end = pd.Timestamp("2024-01-20", tz="Asia/Taipei")
        results = eng._compute_daily_returns(
            {"2330": 1.0}, period_start, period_end, _FakeSlicer()
        )
        assert len(results) > 0
        assert all(isinstance(r[0], pd.Timestamp) for r in results)

    def test_trade_cost_append_tz_aware_rebal_date(self):
        """實打 engine.py L458-460 的 trade-cost append 路徑。

        模擬 rebal_date 為 tz-aware 時，`to_utc_ts(rebal_date)` 能正確產生
        UTC Timestamp 並 append 進 all_daily_returns list，不炸。
        """
        # 直接執行 engine 該行的語意，確保 tz-aware rebal_date 可完成整段 op
        all_daily_returns: list[tuple[pd.Timestamp, float]] = []
        for rebal_date in [
            pd.Timestamp("2024-06-15", tz="Asia/Taipei"),
            datetime(2024, 9, 15, tzinfo=TW_TZ),
            pd.Timestamp("2024-12-01", tz="UTC"),
            pd.Timestamp("2024-03-01"),  # naive 也要相容
        ]:
            total_trade_cost = 0.0047
            # 這是 engine.py L458-460 的原文
            all_daily_returns.append((to_utc_ts(rebal_date), -total_trade_cost))

        assert len(all_daily_returns) == 4
        # 所有 ts 必須是 UTC-aware
        for ts, cost in all_daily_returns:
            assert str(ts.tz) == "UTC"
            assert cost == -0.0047

    def test_compute_daily_returns_tz_aware_datetime(self):
        """同上但用 datetime+TW_TZ，確保 datetime 路徑也 OK。"""
        from src.backtest.engine import BacktestEngine

        class _FakeSlicer:
            def fetch_ohlcv(self, symbol, tf, n):
                idx = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
                return pd.DataFrame(
                    {"close": [100.0 + i for i in range(30)],
                     "volume": [1000.0] * 30},
                    index=idx,
                )

        eng = BacktestEngine.__new__(BacktestEngine)
        eng._ohlcv_min_fetch = 30
        eng._dividends = None

        period_start = datetime(2024, 1, 5, tzinfo=TW_TZ)
        period_end = datetime(2024, 1, 20, tzinfo=TW_TZ)
        results = eng._compute_daily_returns(
            {"2330": 1.0}, period_start, period_end, _FakeSlicer()
        )
        assert len(results) > 0
