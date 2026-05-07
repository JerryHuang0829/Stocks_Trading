"""Tests for compute_metrics() and adjust_splits() in metrics.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.metrics import adjust_splits, compute_metrics


class TestComputeMetrics:
    """compute_metrics: KPI calculation from daily return series."""

    def test_empty_series(self):
        result = compute_metrics(pd.Series(dtype="float64"))
        assert result == {}

    def test_positive_returns(self):
        # 100 days of +1% daily
        rets = pd.Series([0.01] * 100, index=pd.date_range("2024-01-01", periods=100))
        result = compute_metrics(rets)
        assert result["total_return"] > 0
        assert result["annualized_return"] > 0
        assert result["sharpe_ratio"] > 0
        assert result["max_drawdown"] == 0.0  # Never draws down

    def test_negative_returns(self):
        # 100 days of -0.5% daily
        rets = pd.Series([-0.005] * 100, index=pd.date_range("2024-01-01", periods=100))
        result = compute_metrics(rets)
        assert result["total_return"] < 0
        assert result["max_drawdown"] < 0  # Negative = drawdown

    def test_zero_returns(self):
        rets = pd.Series([0.0] * 50, index=pd.date_range("2024-01-01", periods=50))
        result = compute_metrics(rets)
        assert result["total_return"] == 0.0
        assert result["annualized_volatility"] == 0.0

    def test_with_benchmark(self):
        dates = pd.date_range("2024-01-01", periods=100)
        portfolio = pd.Series([0.01] * 100, index=dates)
        benchmark = pd.Series([0.005] * 100, index=dates)
        result = compute_metrics(portfolio, benchmark)
        assert "annualized_alpha" in result
        assert result["annualized_alpha"] > 0  # Portfolio beats benchmark
        assert "beta" in result
        assert "tracking_error" in result
        assert "benchmark_type" in result
        assert result["benchmark_type"] == "price_only"

    def test_no_benchmark_skips_relative(self):
        rets = pd.Series([0.01] * 50, index=pd.date_range("2024-01-01", periods=50))
        result = compute_metrics(rets)
        assert "annualized_alpha" not in result
        assert "beta" not in result

    def test_trading_days_count(self):
        rets = pd.Series([0.01] * 252, index=pd.date_range("2024-01-01", periods=252))
        result = compute_metrics(rets)
        assert result["trading_days"] == 252
        assert abs(result["years"] - 1.0) < 0.01

    def test_max_drawdown_calculation(self):
        # Up 10%, then down 20%, then recover
        rets = pd.Series(
            [0.05, 0.05, -0.10, -0.10, 0.05, 0.05],
            index=pd.date_range("2024-01-01", periods=6),
        )
        result = compute_metrics(rets)
        assert result["max_drawdown"] < 0
        assert result["max_drawdown"] > -1.0

    def test_sortino_uses_downside_only(self):
        # Mix of positive and negative returns
        rets = pd.Series(
            [0.02, -0.01, 0.03, -0.005, 0.01],
            index=pd.date_range("2024-01-01", periods=5),
        )
        result = compute_metrics(rets)
        # Sortino should be >= Sharpe (less penalty for upside volatility)
        if result.get("sharpe_ratio", 0) > 0:
            assert result["sortino_ratio"] >= result["sharpe_ratio"]


class TestKnownAnswerMetrics:
    """Known-answer tests: 用手算驗證精確值，防止 KPI 公式靜默錯誤。"""

    def test_constant_daily_return_sharpe(self):
        """每天固定 +1%，標準差=0 → Sharpe 趨近無窮大但 std>0 因浮點。
        改用可手算的情境：252 天均勻報酬。"""
        daily_ret = 0.01
        n = 252
        rets = pd.Series([daily_ret] * n, index=pd.date_range("2024-01-01", periods=n))
        result = compute_metrics(rets, risk_free_rate=0.0)

        # 總報酬 = (1.01)^252 - 1 ≈ 11.2783
        expected_total = (1 + daily_ret) ** n - 1
        assert abs(result["total_return"] - expected_total) < 0.001

        # 年化報酬 = (1+total)^(1/1) - 1 = total（因為剛好 252 天 = 1 年）
        assert abs(result["annualized_return"] - expected_total) < 0.001

        # MDD = 0（永遠上漲）
        assert result["max_drawdown"] == 0.0

    def test_known_drawdown(self):
        """手動構造已知 MDD 的序列。
        價格走勢：100 → 110 → 88 → 96.8
        MDD = (88 - 110) / 110 = -20%
        """
        # 日報酬：+10%, -20%, +10%
        rets = pd.Series(
            [0.10, -0.20, 0.10],
            index=pd.date_range("2024-01-01", periods=3),
        )
        result = compute_metrics(rets)
        # MDD 應為 -20%（從 110 跌到 88）
        assert abs(result["max_drawdown"] - (-0.20)) < 0.001

    def test_known_alpha_and_beta(self):
        """投組每天 +1%，benchmark 每天 +0.5%，252 天。
        Beta = Cov(P,B)/Var(B)。常數報酬 → Cov≈0, Var≈0 → 用 mixed 版本。
        """
        n = 252
        np.random.seed(42)
        bench_daily = np.random.normal(0.0005, 0.01, n)
        port_daily = bench_daily * 1.2 + 0.0003  # beta≈1.2, alpha≈0.0003/day

        dates = pd.date_range("2024-01-01", periods=n)
        port = pd.Series(port_daily, index=dates)
        bench = pd.Series(bench_daily, index=dates)
        result = compute_metrics(port, bench, risk_free_rate=0.0)

        # Beta 應接近 1.2
        assert abs(result["beta"] - 1.2) < 0.05

        # Alpha 方向應為正（投組有正截距）
        assert result["annualized_alpha"] > 0

    def test_known_sharpe_value(self):
        """用已知均值和標準差驗算 Sharpe。
        252 天，日均報酬 0.001，日標準差 0.02，rf=0。
        Sharpe = mean/std * sqrt(252) = 0.001/0.02 * 15.875 ≈ 0.794
        """
        np.random.seed(123)
        n = 50000  # 大樣本讓統計量收斂
        daily_mean = 0.001
        daily_std = 0.02
        rets = pd.Series(
            np.random.normal(daily_mean, daily_std, n),
            index=pd.date_range("2020-01-01", periods=n),
        )
        result = compute_metrics(rets, risk_free_rate=0.0)
        expected_sharpe = daily_mean / daily_std * np.sqrt(252)
        # 大樣本下 Sharpe 應在 ±0.15 內（幾何 vs 算數報酬的差異）
        assert abs(result["sharpe_ratio"] - expected_sharpe) < 0.15

    def test_annualized_volatility_value(self):
        """日標準差 0.01 → 年化波動率 = 0.01 * sqrt(252) ≈ 0.1587"""
        np.random.seed(456)
        n = 10000
        rets = pd.Series(
            np.random.normal(0, 0.01, n),
            index=pd.date_range("2020-01-01", periods=n),
        )
        result = compute_metrics(rets)
        expected_vol = 0.01 * np.sqrt(252)
        assert abs(result["annualized_volatility"] - expected_vol) < 0.005


class TestAdjustSplits:
    """adjust_splits(): 股票分割自動偵測與前復權。"""

    def test_no_split(self):
        """正常價格序列不應被調整。"""
        prices = pd.Series(
            [100, 101, 102, 103, 104],
            index=pd.date_range("2024-01-01", periods=5),
        )
        adjusted = adjust_splits(prices)
        pd.testing.assert_series_equal(adjusted, prices.astype(float))

    def test_1_to_4_split(self):
        """模擬 1:4 分割：180 → 45（-75%）。
        分割前價格應全部乘以 0.25。"""
        prices = pd.Series(
            [160.0, 170.0, 180.0, 45.0, 46.0, 47.0],
            index=pd.date_range("2024-01-01", periods=6),
        )
        adjusted = adjust_splits(prices)
        # 分割後不變
        assert adjusted.iloc[3] == 45.0
        assert adjusted.iloc[4] == 46.0
        assert adjusted.iloc[5] == 47.0
        # 分割前乘以 45/180 = 0.25
        assert abs(adjusted.iloc[0] - 160.0 * 0.25) < 0.01
        assert abs(adjusted.iloc[1] - 170.0 * 0.25) < 0.01
        assert abs(adjusted.iloc[2] - 180.0 * 0.25) < 0.01

    def test_1_to_2_split(self):
        """模擬 1:2 分割：200 → 100（-50%）。"""
        prices = pd.Series(
            [190.0, 200.0, 100.0, 105.0],
            index=pd.date_range("2024-01-01", periods=4),
        )
        adjusted = adjust_splits(prices)
        assert abs(adjusted.iloc[0] - 95.0) < 0.01   # 190 * 0.5
        assert abs(adjusted.iloc[1] - 100.0) < 0.01  # 200 * 0.5
        assert adjusted.iloc[2] == 100.0              # 分割日不變
        assert adjusted.iloc[3] == 105.0

    def test_daily_returns_continuous_after_split(self):
        """調整後的日報酬不應有 >40% 的跳動。"""
        prices = pd.Series(
            [100.0, 102.0, 104.0, 26.0, 27.0, 28.0],  # 1:4 split
            index=pd.date_range("2024-01-01", periods=6),
        )
        adjusted = adjust_splits(prices)
        daily_ret = adjusted.pct_change().dropna()
        assert daily_ret.min() > -0.40  # 不再有 split 造成的大跌

    def test_multiple_splits(self):
        """罕見情境：兩次分割。"""
        prices = pd.Series(
            [400.0, 200.0, 210.0, 52.5, 55.0],  # 1:2 then 1:4
            index=pd.date_range("2024-01-01", periods=5),
        )
        adjusted = adjust_splits(prices)
        # 調整後日報酬應全部 < 40%
        daily_ret = adjusted.pct_change().dropna()
        assert daily_ret.min() > -0.40
        assert daily_ret.max() < 0.40

    def test_empty_series(self):
        """空序列不應報錯。"""
        result = adjust_splits(pd.Series(dtype="float64"))
        assert result.empty

    def test_single_price(self):
        """單一價格不應報錯。"""
        prices = pd.Series([100.0], index=pd.date_range("2024-01-01", periods=1))
        result = adjust_splits(prices)
        assert len(result) == 1

    def test_normal_limit_move_not_detected(self):
        """台股漲跌停 ±10%，不應誤判為 split。"""
        prices = pd.Series(
            [100.0, 90.0, 81.0, 73.0],  # 連續跌停 -10%
            index=pd.date_range("2024-01-01", periods=4),
        )
        adjusted = adjust_splits(prices)
        # 不應有任何調整
        pd.testing.assert_series_equal(adjusted, prices.astype(float))

    def test_reverse_split_10_to_1(self):
        """模擬 10:1 合股：50 → 500（+900%）。
        合股前價格應全部乘以 10。"""
        prices = pd.Series(
            [48.0, 50.0, 500.0, 510.0],  # 10:1 reverse split
            index=pd.date_range("2024-01-01", periods=4),
        )
        adjusted = adjust_splits(prices)
        # 合股後不變
        assert adjusted.iloc[2] == 500.0
        assert adjusted.iloc[3] == 510.0
        # 合股前乘以 500/50 = 10
        assert abs(adjusted.iloc[0] - 480.0) < 0.01  # 48 * 10
        assert abs(adjusted.iloc[1] - 500.0) < 0.01  # 50 * 10

    def test_reverse_split_5_to_1(self):
        """模擬 5:1 合股：20 → 100（+400%）。"""
        prices = pd.Series(
            [18.0, 20.0, 100.0, 105.0],
            index=pd.date_range("2024-01-01", periods=4),
        )
        adjusted = adjust_splits(prices)
        assert abs(adjusted.iloc[0] - 90.0) < 0.01   # 18 * 5
        assert abs(adjusted.iloc[1] - 100.0) < 0.01  # 20 * 5
        assert adjusted.iloc[2] == 100.0
        assert adjusted.iloc[3] == 105.0

    def test_reverse_split_continuous_returns(self):
        """合股調整後日報酬不應有 >100% 的跳動。"""
        prices = pd.Series(
            [10.0, 11.0, 110.0, 115.0],  # 10:1 reverse split
            index=pd.date_range("2024-01-01", periods=4),
        )
        adjusted = adjust_splits(prices)
        daily_ret = adjusted.pct_change().dropna()
        assert daily_ret.max() < 1.00  # 不再有合股造成的大漲

    def test_reverse_split_2_to_1_boundary(self):
        """2:1 合股邊界：50 → 100（剛好 +100%）。
        _REVERSE_SPLIT_THRESHOLD=1.00 用 >= 才能涵蓋此情境。"""
        prices = pd.Series(
            [48.0, 50.0, 100.0, 105.0],  # 2:1 reverse split, exactly +100%
            index=pd.date_range("2024-01-01", periods=4),
        )
        adjusted = adjust_splits(prices)
        # 合股後不變
        assert adjusted.iloc[2] == 100.0
        assert adjusted.iloc[3] == 105.0
        # 合股前乘以 100/50 = 2
        assert abs(adjusted.iloc[0] - 96.0) < 0.01   # 48 * 2
        assert abs(adjusted.iloc[1] - 100.0) < 0.01  # 50 * 2

    def test_normal_limit_up_not_detected(self):
        """台股漲停 +10%，連續漲停也不應誤判為 reverse split。"""
        prices = pd.Series(
            [100.0, 110.0, 121.0, 133.1],  # 連續漲停 +10%
            index=pd.date_range("2024-01-01", periods=4),
        )
        adjusted = adjust_splits(prices)
        pd.testing.assert_series_equal(adjusted, prices.astype(float))


class TestBenchmarkAnnualizationAlignment:
    """M2: benchmark 年化分母用 aligned 期間，不是 portfolio n_years。"""

    def test_aligned_window_shorter_than_portfolio(self):
        """portfolio 252 天，benchmark 只在前 126 天 overlap。
        benchmark_annualized_return 必須用 126/252 當分母，不是 252/252。"""
        n = 252
        dates = pd.date_range("2024-01-01", periods=n)
        port = pd.Series([0.001] * n, index=dates)
        # benchmark 只有前 126 天有資料
        bench = pd.Series([0.0005] * 126, index=dates[:126])

        result = compute_metrics(port, bench, risk_free_rate=0.0)

        # benchmark_total = (1.0005)^126 - 1 ≈ 0.0645
        # aligned_n_years = 126/252 = 0.5
        # bench_ann = (1.0645)^2 - 1 ≈ 0.133
        expected_bench_total = (1.0005) ** 126 - 1
        expected_bench_ann = (1 + expected_bench_total) ** (252 / 126) - 1
        assert abs(result["benchmark_annualized_return"] - expected_bench_ann) < 0.002

        # alpha 也用 aligned 期間
        expected_port_total = (1.001) ** 126 - 1
        expected_port_ann = (1 + expected_port_total) ** (252 / 126) - 1
        expected_alpha = expected_port_ann - expected_bench_ann
        assert abs(result["annualized_alpha"] - expected_alpha) < 0.002


class TestShortBenchmarkOverlapGuard:
    """M2-guard: aligned<21 天不做年化，避免 _ay clamp 放大 100×。"""

    def test_2_day_overlap_skips_relative_metrics(self):
        port_dates = pd.date_range("2024-01-01", periods=252)
        bench_dates = port_dates[-2:]
        port = pd.Series([0.001] * 252, index=port_dates)
        bench = pd.Series([0.005, 0.005], index=bench_dates)
        result = compute_metrics(port, bench, risk_free_rate=0.0)
        assert "annualized_alpha" not in result
        assert "benchmark_annualized_return" not in result
        assert "beta" not in result

    def test_exact_21_day_overlap_computes_metrics(self):
        dates = pd.date_range("2024-01-01", periods=21)
        port = pd.Series([0.001] * 21, index=dates)
        bench = pd.Series([0.0005] * 21, index=dates)
        result = compute_metrics(port, bench, risk_free_rate=0.0)
        assert "annualized_alpha" in result
        assert "beta" in result


class TestStatStabilityOnConstants:
    """M3: 常數/近常數序列不應讓 skew/kurtosis/JB 吐 NaN。"""

    def test_constant_series_stats_are_none(self):
        """全部 +1% 序列 → std=0 → skew/kurt/JB 應為 None，不是 NaN。"""
        rets = pd.Series([0.01] * 10, index=pd.date_range("2024-01-01", periods=10))
        result = compute_metrics(rets)
        assert result["skewness"] is None
        assert result["kurtosis"] is None
        assert result["jarque_bera_stat"] is None
        assert result["jarque_bera_pvalue"] is None

    def test_zero_series_stats_are_none(self):
        rets = pd.Series([0.0] * 20, index=pd.date_range("2024-01-01", periods=20))
        result = compute_metrics(rets)
        assert result["skewness"] is None
        assert result["kurtosis"] is None

    def test_non_constant_series_stats_are_finite(self):
        np.random.seed(0)
        rets = pd.Series(
            np.random.normal(0.0005, 0.01, 500),
            index=pd.date_range("2024-01-01", periods=500),
        )
        result = compute_metrics(rets)
        assert result["skewness"] is not None
        assert result["kurtosis"] is not None
        assert np.isfinite(result["skewness"])
        assert np.isfinite(result["kurtosis"])
