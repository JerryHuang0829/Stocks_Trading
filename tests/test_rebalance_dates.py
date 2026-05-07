"""Tests for BacktestEngine._generate_rebalance_dates holiday alignment."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine


_gen = BacktestEngine._generate_rebalance_dates


class TestGenerateRebalanceDatesBasic:
    """Basic calendar generation without trading_days alignment."""

    def test_monthly_dates_within_range(self):
        dates = _gen(datetime(2024, 1, 1), datetime(2024, 6, 30), day=12)
        assert len(dates) == 6
        assert all(d.day == 12 for d in dates)

    def test_start_after_rebalance_day_skips_first_month(self):
        dates = _gen(datetime(2024, 1, 15), datetime(2024, 3, 31), day=12)
        # 1/12 < 1/15, skip; 2/12, 3/12 included
        assert len(dates) == 2
        assert dates[0] == datetime(2024, 2, 12)

    def test_start_on_rebalance_day_includes_it(self):
        dates = _gen(datetime(2024, 1, 12), datetime(2024, 3, 31), day=12)
        assert len(dates) == 3
        assert dates[0] == datetime(2024, 1, 12)

    def test_end_before_rebalance_day_excludes_last(self):
        dates = _gen(datetime(2024, 1, 1), datetime(2024, 3, 10), day=12)
        # 1/12, 2/12 included; 3/12 > 3/10 excluded
        assert len(dates) == 2

    def test_day_31_capped_to_28(self):
        dates = _gen(datetime(2024, 1, 1), datetime(2024, 4, 30), day=31)
        assert all(d.day == 28 for d in dates)

    def test_single_month_range(self):
        dates = _gen(datetime(2024, 6, 1), datetime(2024, 6, 30), day=12)
        assert len(dates) == 1
        assert dates[0] == datetime(2024, 6, 12)


class TestGenerateRebalanceDatesAlignment:
    """Trading day alignment — the critical holiday/weekend logic."""

    @pytest.fixture
    def trading_days_2026_apr(self):
        """Simulated trading days for April 2026 (4/12 is Sunday)."""
        # Mon-Fri only, skip weekends
        days = pd.bdate_range("2026-03-01", "2026-05-31")
        return pd.DatetimeIndex(days)

    def test_sunday_aligns_to_monday(self, trading_days_2026_apr):
        """4/12 (Sun) should align to 4/13 (Mon)."""
        dates = _gen(
            datetime(2026, 4, 1), datetime(2026, 4, 30),
            day=12, trading_days=trading_days_2026_apr,
        )
        assert len(dates) == 1
        assert dates[0] == datetime(2026, 4, 13)

    def test_saturday_aligns_to_monday(self, trading_days_2026_apr):
        """If rebalance day is Saturday, align to next Monday."""
        # 2026-03-14 is Saturday
        dates = _gen(
            datetime(2026, 3, 1), datetime(2026, 3, 31),
            day=14, trading_days=trading_days_2026_apr,
        )
        assert len(dates) == 1
        assert dates[0].weekday() == 0  # Monday
        assert dates[0] == datetime(2026, 3, 16)

    def test_weekday_stays_same(self, trading_days_2026_apr):
        """If rebalance day is a trading day, no shift."""
        # 2026-04-15 is Wednesday
        dates = _gen(
            datetime(2026, 4, 1), datetime(2026, 4, 30),
            day=15, trading_days=trading_days_2026_apr,
        )
        assert len(dates) == 1
        assert dates[0] == datetime(2026, 4, 15)

    def test_multiple_months_alignment(self, trading_days_2026_apr):
        """Multi-month range, each date aligned independently."""
        dates = _gen(
            datetime(2026, 3, 1), datetime(2026, 5, 31),
            day=12, trading_days=trading_days_2026_apr,
        )
        assert len(dates) == 3
        # 3/12 Thu → 3/12 (ok)
        assert dates[0] == datetime(2026, 3, 12)
        # 4/12 Sun → 4/13 Mon
        assert dates[1] == datetime(2026, 4, 13)
        # 5/12 Tue → 5/12 (ok)
        assert dates[2] == datetime(2026, 5, 12)

    def test_no_trading_days_returns_calendar_dates(self):
        """Without trading_days, returns raw calendar dates."""
        dates = _gen(datetime(2026, 4, 1), datetime(2026, 4, 30), day=12)
        assert dates[0] == datetime(2026, 4, 12)  # Sunday, no alignment

    def test_tz_aware_trading_days(self):
        """Trading days with timezone info should still work."""
        days = pd.bdate_range("2026-04-01", "2026-04-30", tz="Asia/Taipei")
        dates = _gen(
            datetime(2026, 4, 1), datetime(2026, 4, 30),
            day=12, trading_days=days,
        )
        assert dates[0] == datetime(2026, 4, 13)  # Sun → Mon

    def test_end_of_range_fallback_to_past(self):
        """If no future trading day exists, fall back to most recent past day."""
        # Trading days only up to 4/10
        short_days = pd.bdate_range("2026-04-01", "2026-04-10")
        dates = _gen(
            datetime(2026, 4, 1), datetime(2026, 4, 30),
            day=12, trading_days=short_days,
        )
        # 4/12 (Sun) has no future trading day in range → fallback to 4/10 (Fri)
        assert len(dates) == 1
        assert dates[0] == datetime(2026, 4, 10)

    def test_empty_trading_days_returns_calendar(self):
        """Empty trading_days index → same as None, return calendar dates."""
        empty = pd.DatetimeIndex([])
        dates = _gen(
            datetime(2026, 4, 1), datetime(2026, 4, 30),
            day=12, trading_days=empty,
        )
        # Empty triggers the len > 0 guard, returns unaligned
        assert dates[0] == datetime(2026, 4, 12)
