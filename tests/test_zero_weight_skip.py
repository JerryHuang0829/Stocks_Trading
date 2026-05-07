"""Tests for zero-weight factor skip behavior.

When a factor's weight is 0, the corresponding fetch function should NOT be called.
This saves API quota and reduces failure surface.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.tw_stock import _analyze_symbol


def _make_source(ohlcv_len=300):
    """Create a mock source with enough OHLCV data to pass history check."""
    import pandas as pd
    import numpy as np

    source = MagicMock()

    # Build realistic OHLCV DataFrame
    dates = pd.date_range("2023-01-01", periods=ohlcv_len, freq="B")
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(ohlcv_len) * 0.5)
    close = np.maximum(close, 20)  # keep above min_price
    df = pd.DataFrame({
        "date": dates,
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "close": close,
        "volume": np.random.randint(1_000_000, 10_000_000, ohlcv_len),
    })
    source.fetch_ohlcv.return_value = df
    source.fetch_institutional.return_value = pd.DataFrame()
    source.fetch_financial_quality.return_value = {"roe": 0.15, "gross_margin": 0.3}
    source.fetch_month_revenue.return_value = pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=15, freq="MS"),
        "revenue": [1e9] * 15,
    })
    return source


def _make_sym_config():
    return {
        "symbol": "2330",
        "name": "台積電",
        "industry": "半導體業",
        "strategy": {"use_institutional": True},
    }


class TestQualityZeroWeight:
    """quality weight=0 should NOT call fetch_financial_quality."""

    def test_quality_zero_skips_fetch(self):
        source = _make_source()
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {
                "price_momentum": 0.55,
                "trend_quality": 0.20,
                "revenue_momentum": 0.25,
                "quality": 0.00,
            },
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        source.fetch_financial_quality.assert_not_called()
        assert result["quality_raw"] is None

    def test_quality_positive_calls_fetch(self):
        source = _make_source()
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {
                "price_momentum": 0.45,
                "trend_quality": 0.15,
                "revenue_momentum": 0.25,
                "quality": 0.15,
            },
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        source.fetch_financial_quality.assert_called_once()
        assert result["quality_raw"] is not None

    def test_quality_missing_from_weights_skips_fetch(self):
        """quality not in score_weights at all → treat as 0."""
        source = _make_source()
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {
                "price_momentum": 0.55,
                "trend_quality": 0.20,
                "revenue_momentum": 0.25,
            },
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        source.fetch_financial_quality.assert_not_called()


class TestInstitutionalZeroWeight:
    """institutional_flow weight=0 should NOT call fetch_institutional."""

    def test_inst_zero_skips_fetch(self):
        source = _make_source()
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {
                "price_momentum": 0.55,
                "trend_quality": 0.20,
                "revenue_momentum": 0.25,
                "institutional_flow": 0.00,
            },
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        source.fetch_institutional.assert_not_called()
        assert result["institutional_raw"] == 0

    def test_inst_positive_calls_fetch(self):
        source = _make_source()
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {
                "price_momentum": 0.45,
                "trend_quality": 0.20,
                "revenue_momentum": 0.25,
                "institutional_flow": 0.10,
            },
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        source.fetch_institutional.assert_called_once()


class TestQualityGracefulDegradation:
    """quality_raw should safely degrade when data is missing or malformed."""

    def test_fetch_returns_none(self):
        source = _make_source()
        source.fetch_financial_quality.return_value = None
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {"quality": 0.15, "price_momentum": 0.55,
                              "trend_quality": 0.15, "revenue_momentum": 0.15},
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        assert result["quality_raw"] is None

    def test_fetch_missing_roe(self):
        source = _make_source()
        source.fetch_financial_quality.return_value = {"gross_margin": 0.3}
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {"quality": 0.15, "price_momentum": 0.55,
                              "trend_quality": 0.15, "revenue_momentum": 0.15},
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        assert result["quality_raw"] is None

    def test_fetch_missing_gross_margin(self):
        source = _make_source()
        source.fetch_financial_quality.return_value = {"roe": 0.15}
        config = {
            "history_limit": 320,
            "use_monthly_revenue": True,
            "score_weights": {"quality": 0.15, "price_momentum": 0.55,
                              "trend_quality": 0.15, "revenue_momentum": 0.15},
        }
        result = _analyze_symbol(
            _make_sym_config(), source, {}, config,
            as_of=datetime(2024, 12, 31),
        )
        assert result["quality_raw"] is None
