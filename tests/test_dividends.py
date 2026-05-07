"""Tests for P4.5 dividend adjustment (adjust_dividends) and TWSE scraper parsing."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics import adjust_dividends, adjust_splits


# ---------------------------------------------------------------------------
# adjust_dividends() unit tests
# ---------------------------------------------------------------------------

class TestAdjustDividends:
    """Verify dividend price adjustment logic."""

    def test_basic_cash_dividend(self):
        """Ex-dividend of $3 on day 3 should adjust prior prices."""
        # Price drops from 100 to 97 on ex-date (cash div $3)
        dates = pd.bdate_range("2023-07-17", periods=4, tz="UTC")
        prices = pd.Series([100.0, 100.0, 97.0, 97.0], index=dates)

        dividends = [
            {"stock_id": "0050", "ex_date": "2023-07-19", "cash_dividend": 3.0},
        ]

        adjusted = adjust_dividends(prices, dividends, "0050")

        # After adjustment, pct_change on ex-date should be ~0%, not -3%
        rets = adjusted.pct_change().dropna()
        assert abs(rets.iloc[1]) < 0.001  # ex-date return ~0%

        # Prior prices should be reduced by factor 97/100
        expected_factor = 97.0 / 100.0
        assert abs(adjusted.iloc[0] - 100.0 * expected_factor) < 0.01
        assert abs(adjusted.iloc[1] - 100.0 * expected_factor) < 0.01
        # Post-ex prices unchanged
        assert abs(adjusted.iloc[2] - 97.0) < 0.01

    def test_no_dividends_for_symbol(self):
        """If no dividends match the symbol, prices are unchanged."""
        dates = pd.bdate_range("2023-01-01", periods=5, tz="UTC")
        prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=dates)

        dividends = [
            {"stock_id": "2330", "ex_date": "2023-01-03", "cash_dividend": 3.0},
        ]

        adjusted = adjust_dividends(prices, dividends, "0050")
        pd.testing.assert_series_equal(adjusted, prices.astype(float))

    def test_empty_dividends(self):
        """Empty dividend list returns prices unchanged."""
        dates = pd.bdate_range("2023-01-01", periods=3, tz="UTC")
        prices = pd.Series([100.0, 105.0, 110.0], index=dates)

        adjusted = adjust_dividends(prices, [], "0050")
        pd.testing.assert_series_equal(adjusted, prices.astype(float))

    def test_multiple_dividends_same_stock(self):
        """Two dividends in one year (like 0050: Jan + Jul)."""
        dates = pd.bdate_range("2023-01-02", periods=6, tz="UTC")
        # Day 0: 100, Day 1: ex-div $2 -> 98, Day 2: 98,
        # Day 3: 100, Day 4: ex-div $1 -> 99, Day 5: 99
        prices = pd.Series([100.0, 98.0, 98.0, 100.0, 99.0, 99.0], index=dates)

        dividends = [
            {"stock_id": "TEST", "ex_date": dates[1].strftime("%Y-%m-%d"), "cash_dividend": 2.0},
            {"stock_id": "TEST", "ex_date": dates[4].strftime("%Y-%m-%d"), "cash_dividend": 1.0},
        ]

        adjusted = adjust_dividends(prices, dividends, "TEST")

        # After adjustment, both ex-dates should show ~0% return
        rets = adjusted.pct_change().dropna()
        assert abs(rets.iloc[0]) < 0.001  # first ex-date
        assert abs(rets.iloc[3]) < 0.001  # second ex-date

    def test_splits_then_dividends_compose(self):
        """adjust_splits + adjust_dividends should compose correctly."""
        dates = pd.bdate_range("2023-01-02", periods=5, tz="UTC")
        # Day 2 has a split: 200 -> 100 (1:2)
        # Day 4 has ex-div $5 from 100 -> 95
        prices = pd.Series([200.0, 200.0, 100.0, 100.0, 95.0], index=dates)

        dividends = [
            {"stock_id": "X", "ex_date": dates[4].strftime("%Y-%m-%d"), "cash_dividend": 5.0},
        ]

        # Step 1: adjust splits
        split_adj = adjust_splits(prices)
        # After split adjustment, pre-split prices should be ~100
        assert abs(split_adj.iloc[0] - 100.0) < 1.0

        # Step 2: adjust dividends on split-adjusted series
        full_adj = adjust_dividends(split_adj, dividends, "X")

        # Ex-dividend return should be ~0%
        rets = full_adj.pct_change().dropna()
        assert abs(rets.iloc[3]) < 0.01  # ex-date return

    def test_close_before_split_safe_formula(self):
        """When close_before is provided, factor uses scale-invariant formula.

        Simulates a 1:4 split: raw prices ~200, split-adjusted to ~50.
        TWSE dividend $4 on raw $200 → factor = 1 - 4/200 = 0.98.
        Without close_before, the fallback would use $50 → factor = 50/54 = 0.926 (wrong).
        """
        dates = pd.bdate_range("2023-07-17", periods=4, tz="UTC")
        # Split-adjusted prices (raw prices were 4x higher)
        prices = pd.Series([50.0, 50.0, 48.0, 48.0], index=dates)

        dividends = [
            {
                "stock_id": "TEST",
                "ex_date": "2023-07-19",
                "cash_dividend": 4.0,       # in original (pre-split) units
                "close_before": 200.0,       # original close before ex-date
            },
        ]

        adjusted = adjust_dividends(prices, dividends, "TEST")

        # factor = 1 - 4/200 = 0.98, prior prices *= 0.98
        expected_prior = 50.0 * 0.98
        assert abs(adjusted.iloc[0] - expected_prior) < 0.01
        assert abs(adjusted.iloc[1] - expected_prior) < 0.01
        # Post-ex prices unchanged
        assert abs(adjusted.iloc[2] - 48.0) < 0.01

    def test_dividend_outside_price_range_ignored(self):
        """Dividends with ex_date not in the price index are skipped."""
        dates = pd.bdate_range("2023-06-01", periods=3, tz="UTC")
        prices = pd.Series([100.0, 101.0, 102.0], index=dates)

        dividends = [
            {"stock_id": "0050", "ex_date": "2023-01-30", "cash_dividend": 2.6},
        ]

        adjusted = adjust_dividends(prices, dividends, "0050")
        pd.testing.assert_series_equal(adjusted, prices.astype(float))


# ---------------------------------------------------------------------------
# TWSE scraper parsing tests
# ---------------------------------------------------------------------------

class TestTwseDividendParsing:
    """Test _parse_roc_date helper."""

    def test_parse_roc_date(self):
        from src.data.twse_scraper import _parse_roc_date

        assert _parse_roc_date("112年07月18日") == "2023-07-18"
        assert _parse_roc_date("113年01月02日") == "2024-01-02"
        assert _parse_roc_date("100年12月31日") == "2011-12-31"

    def test_parse_roc_date_invalid(self):
        from src.data.twse_scraper import _parse_roc_date

        assert _parse_roc_date("") is None
        assert _parse_roc_date("invalid") is None
