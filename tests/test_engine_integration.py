"""Integration tests for BacktestEngine.run() using synthetic data.

These tests verify the full backtest pipeline end-to-end without hitting any API.
A FakeSource provides deterministic synthetic price/revenue data so we can
validate output structure, rebalance timing, daily return series, and metric
calculation.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine


# ---------------------------------------------------------------------------
# Fake data source
# ---------------------------------------------------------------------------

def _make_ohlcv(n_days: int, start: str = "2021-01-01", base_price: float = 100.0,
                daily_return: float = 0.0008, volume: int = 50_000_000,
                seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame with a steady uptrend.

    The uptrend ensures SMA20 > SMA60 and momentum_6m > 0, making stocks
    eligible for the portfolio selection filters.
    """
    dates = pd.bdate_range(start, periods=n_days, tz="Asia/Taipei")
    prices = base_price * (1 + daily_return) ** np.arange(n_days)
    # Small deterministic noise (seeded per stock for variety)
    rng = np.random.RandomState(seed)
    noise = 1 + rng.normal(0, 0.003, n_days)
    prices = prices * noise
    return pd.DataFrame({
        "open": prices * 0.999,
        "high": prices * 1.005,
        "low": prices * 0.995,
        "close": prices,
        "volume": [volume] * n_days,
    }, index=dates)


def _make_revenue(symbol: str, months: int = 15) -> pd.DataFrame:
    """Generate synthetic monthly revenue data."""
    dates = pd.date_range(end="2024-06-01", periods=months, freq="MS")
    base = 1_000_000_000  # 10 億
    # Mild YoY growth
    values = [base * (1.08 ** (i / 12)) for i in range(months)]
    return pd.DataFrame({
        "date": dates,
        "stock_id": symbol,
        "revenue": values,
    })


class FakeSource:
    """Minimal data source that satisfies BacktestEngine requirements."""

    def __init__(self, n_stocks: int = 12, n_days: int = 1000):
        self._n_days = n_days
        self._stocks = self._build_stock_list(n_stocks)
        self._ohlcv_cache: dict[str, pd.DataFrame] = {}

    @staticmethod
    def _build_stock_list(n: int) -> list[dict]:
        industries = ["半導體業", "電子工業", "金融保險業", "塑膠工業"]
        stocks = []
        for i in range(n):
            sid = str(2330 + i)
            stocks.append({
                "stock_id": sid,
                "stock_name": f"Test_{sid}",
                "industry_category": industries[i % len(industries)],
                "type": "twse",
            })
        return stocks

    def fetch_stock_info(self) -> pd.DataFrame:
        return pd.DataFrame(self._stocks)

    def fetch_delisting(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["stock_id", "date"])

    def fetch_market_value(self, days: int = 2500) -> pd.DataFrame | None:
        return None

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame | None:
        if symbol not in self._ohlcv_cache:
            # Each stock gets slightly different base price, return, and noise seed
            idx = hash(symbol) % 20
            base = 80 + idx * 5
            ret = 0.0005 + (idx % 5) * 0.0003
            self._ohlcv_cache[symbol] = _make_ohlcv(
                self._n_days, base_price=base, daily_return=ret, seed=idx,
            )
        # Return all data — let _DataSlicer handle truncation via as_of
        return self._ohlcv_cache[symbol]

    def fetch_month_revenue(self, symbol: str, months: int = 15) -> pd.DataFrame:
        return _make_revenue(symbol, months)

    def fetch_institutional(self, symbol: str) -> pd.DataFrame | None:
        return None

    def fetch_combined_turnover(self, date_str: str | None = None) -> pd.DataFrame | None:
        return None


# ---------------------------------------------------------------------------
# Minimal config
# ---------------------------------------------------------------------------

def _make_config(top_n: int = 5, rebalance_day: int = 12) -> dict:
    return {
        "system": {"mode": "tw_stock_portfolio"},
        "default_strategy": {
            "sma_fast": 20,
            "sma_slow": 60,
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bb_period": 20,
            "bb_std": 2,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "atr_period": 14,
            "adx_period": 14,
            "volume_ma_period": 20,
            "volume_breakout_ratio": 1.5,
        },
        "backtest": {
            "benchmark_lookback_days": 500,
            "ohlcv_min_fetch_days": 400,
            "market_value_fetch_days": 500,
            "institutional_fallback_days": 100,
            "error_rate_threshold": 0.2,
            "factor_coverage_threshold": 0.3,
        },
        "portfolio": {
            "profile": "tw_3m_stable",
            "enabled": True,
            "rebalance_frequency": "monthly",
            "rebalance_day": rebalance_day,
            "rebalance_after_close_hour": 14,
            "top_n": top_n,
            "hold_buffer": 3,
            "hold_score_floor": 60,
            "max_position_weight": 0.20,
            "min_holdings": 2,
            "market_proxy_symbol": "0050",
            "history_limit": 320,
            "monthly_revenue_months": 15,
            "min_price": 10,
            "min_avg_turnover": 1_000_000,
            "exclude_etf": True,
            "use_monthly_revenue": True,
            "use_auto_universe": True,
            "auto_universe_size": 12,
            "auto_universe_markets": ["twse"],
            "auto_universe_exclude_industries": [],
            "auto_universe_pre_filter_size": 0,
            "auto_universe_include_symbols": [],
            "auto_universe_exclude_symbols": [],
            "exposure": {
                "risk_on": 0.96,
                "caution": 0.70,
                "risk_off": 0.35,
            },
            "turnover_cost": 0.0047,
            "turnover_score_threshold": 6.0,
            "max_same_industry": 3,
            "weight_mode": "score_weighted",
            "min_eligible_ratio": 0.3,
            "score_weights": {
                "price_momentum": 0.55,
                "trend_quality": 0.20,
                "revenue_momentum": 0.25,
                "institutional_flow": 0.00,
            },
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBacktestEngineRun:
    """End-to-end integration tests for BacktestEngine.run()."""

    @pytest.fixture
    def engine(self):
        source = FakeSource(n_stocks=12, n_days=1000)
        config = _make_config(top_n=5)
        return BacktestEngine(source, config)

    @pytest.fixture
    def result(self, engine):
        return engine.run(
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 4, 30),
            benchmark_symbol="0050",
        )

    def test_returns_dict_with_required_keys(self, result):
        assert isinstance(result, dict)
        for key in ("metrics", "report", "monthly_snapshots"):
            assert key in result, f"Missing key: {key}"

    def test_metrics_has_core_fields(self, result):
        m = result["metrics"]
        required = [
            "sharpe_ratio", "annualized_return", "annualized_volatility",
            "max_drawdown", "total_return", "trading_days",
            "n_rebalances", "data_degraded", "degraded_periods",
            "total_one_way_turnover", "total_trade_cost",
        ]
        for field in required:
            assert field in m, f"Missing metric: {field}"

    def test_rebalance_count_matches_months(self, result):
        # Jan, Feb, Mar, Apr = 4 rebalances
        n = result["metrics"]["n_rebalances"]
        assert n == 4, f"Expected 4 rebalances, got {n}"

    def test_monthly_snapshots_structure(self, result):
        snaps = result["monthly_snapshots"]
        assert len(snaps) == 4
        for snap in snaps:
            assert "rebalance_date" in snap
            assert "market_signal" in snap
            assert "positions" in snap
            assert "data_degraded" in snap
            assert "factor_coverage" in snap
            assert "universe_fingerprint" in snap

    def test_positions_have_weights(self, result):
        for snap in result["monthly_snapshots"]:
            for pos in snap["positions"]:
                assert "symbol" in pos
                assert "weight" in pos
                assert 0 < pos["weight"] <= 0.20

    def test_daily_returns_key_exists(self, result):
        m = result["metrics"]
        assert m["trading_days"] > 0

    def test_report_is_nonempty_string(self, result):
        assert isinstance(result["report"], str)
        assert len(result["report"]) > 50

    def test_data_degraded_is_bool(self, result):
        assert isinstance(result["metrics"]["data_degraded"], bool)

    def test_degraded_periods_is_int(self, result):
        dp = result["metrics"]["degraded_periods"]
        assert isinstance(dp, int)
        assert dp >= 0

    def test_sharpe_is_finite(self, result):
        s = result["metrics"]["sharpe_ratio"]
        assert np.isfinite(s), f"Sharpe is not finite: {s}"

    def test_max_drawdown_nonpositive(self, result):
        mdd = result["metrics"]["max_drawdown"]
        assert mdd <= 0, f"MDD should be <= 0, got {mdd}"

    def test_benchmark_fields_present(self, result):
        m = result["metrics"]
        assert "benchmark_annualized_return" in m
        assert "beta" in m
        assert "benchmark_type" in m
        assert m["benchmark_type"] == "price_only"

    def test_theme_concentration_in_snapshots(self, result):
        for snap in result["monthly_snapshots"]:
            tc = snap.get("theme_concentration")
            assert tc is not None, "Missing theme_concentration"
            assert "tech_weight" in tc


class TestBacktestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_very_short_range_single_rebalance(self):
        source = FakeSource(n_stocks=8, n_days=1000)
        config = _make_config(top_n=3)
        engine = BacktestEngine(source, config)
        result = engine.run(
            start_date=datetime(2024, 3, 1),
            end_date=datetime(2024, 3, 31),
        )
        assert result["metrics"]["n_rebalances"] == 1

    def test_no_rebalance_dates_returns_empty(self):
        source = FakeSource(n_stocks=8, n_days=1000)
        config = _make_config(top_n=3, rebalance_day=28)
        engine = BacktestEngine(source, config)
        # Range too short to include any 28th
        result = engine.run(
            start_date=datetime(2024, 3, 1),
            end_date=datetime(2024, 3, 15),
        )
        assert result["metrics"] == {} or result["metrics"].get("n_rebalances", 0) == 0

    def test_trade_cost_is_nonnegative(self):
        source = FakeSource(n_stocks=10, n_days=1000)
        config = _make_config(top_n=4)
        engine = BacktestEngine(source, config)
        result = engine.run(
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 6, 30),
        )
        assert result["metrics"]["total_trade_cost"] >= 0

    def test_turnover_per_rebalance_reasonable(self):
        source = FakeSource(n_stocks=10, n_days=1000)
        config = _make_config(top_n=4)
        engine = BacktestEngine(source, config)
        result = engine.run(
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 6, 30),
        )
        avg_turnover = result["metrics"]["avg_turnover_per_rebalance"]
        # Turnover should be between 0 and 1 (0% to 100%) per rebalance
        assert 0 <= avg_turnover <= 1.0, f"Unreasonable avg turnover: {avg_turnover}"
