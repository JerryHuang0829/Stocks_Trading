"""Audit 2026-05-02 A.2 fix tests — PIT cover for legacy factors.

Background:
    Audit A.2 finding identified two callsites in `_analyze_symbol` that
    fetch factor data without obvious `as_of` enforcement:

      tw_stock.py:681  source.fetch_institutional(symbol)        # legacy IF
      tw_stock.py:692  source.fetch_financial_quality(symbol)    # quality factor

    Trace finding (post-audit): when `source` is a `_DataSlicer` (the standard
    backtest path: engine.py:421 wires it that way), `fetch_institutional` IS
    overridden by the slicer (engine.py:196) → already PIT-correct via
    `_truncate_by_date_col`. The pre-audit comment "未傳遞 as_of 截斷" was stale.

    `fetch_financial_quality`, however, returns a single latest-quarter
    snapshot dict — fundamentally not a time-series — so the slicer cannot
    PIT-truncate it. Re-enabling weight>0 in backtest would silently use
    a future quarter's metrics. We refuse it via NotImplementedError until a
    `fetch_financial_quality_history` equivalent exists.

Each test corresponds to one Pattern 0 attacker:
    1. weight=0 path: no fetch happens, no raise — preserves cost-isolation.
    2. weight>0 + slicer PIT: fetch_institutional truncates future-dated rows.
    3. weight>0 + as_of in past: only rows ≤ as_of returned.
    4. quality weight>0 in backtest_context: NotImplementedError raised.
    5. quality weight>0 in live context (no _backtest_context): NO raise.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import _DataSlicer
from src.portfolio.tw_stock import _analyze_symbol


class _StubSource:
    """Minimal source stub: tracks call count + returns canned data."""

    def __init__(
        self,
        ohlcv: pd.DataFrame | None = None,
        institutional: pd.DataFrame | None = None,
        quality: dict | None = None,
        revenue: pd.DataFrame | None = None,
    ):
        self.ohlcv = ohlcv
        self.institutional = institutional
        self.quality = quality
        self.revenue = revenue
        self.calls: dict[str, int] = {}

    def _bump(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
        self._bump("fetch_ohlcv")
        return self.ohlcv

    def fetch_institutional(self, symbol: str, days: int = 30):
        self._bump("fetch_institutional")
        return self.institutional

    def fetch_financial_quality(self, symbol: str):
        self._bump("fetch_financial_quality")
        return self.quality

    def fetch_month_revenue(self, symbol: str, months: int = 15):
        self._bump("fetch_month_revenue")
        return self.revenue


def _make_ohlcv(end_date: str = "2026-04-30", n: int = 1500) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame indexed by UTC trading day.

    Default 1500 business days ≈ 6 years — long enough for backtests at
    as_of=2022-06-01 to still have ≥ MIN_OHLCV_BARS (274) rows after slicer
    truncation. (Earlier 400-row fixture left 0 rows post-truncate at
    as_of=2022-06-01 because the dataset only spanned 2024-10 → 2026-04.)
    """
    end = pd.Timestamp(end_date, tz="UTC")
    idx = pd.date_range(end=end, periods=n, freq="B")
    base = 100.0 + (idx.dayofyear * 0.1)
    return pd.DataFrame(
        {
            "open": base,
            "high": base * 1.01,
            "low": base * 0.99,
            "close": base,
            "volume": [1_000_000] * n,
        },
        index=idx,
    )


def _make_institutional(end_date: str, n: int = 200) -> pd.DataFrame:
    """Build institutional buy/sell DataFrame with a 'date' column."""
    dates = pd.date_range(end=end_date, periods=n, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "buy": [10_000.0] * n,
            "sell": [8_000.0] * n,
            "name": ["Foreign_Investor"] * n,
        }
    )


# ---------------------------------------------------------------------------
# Attacker #1: weight=0 path preserves cost-isolation
# ---------------------------------------------------------------------------


class TestWeightZeroNoFetch:
    """When institutional_flow weight is 0, fetch must NOT be called."""

    def test_weight_zero_skips_institutional_fetch(self):
        ohlcv = _make_ohlcv()
        source = _StubSource(ohlcv=ohlcv, institutional=_make_institutional("2026-04-30"))
        portfolio_config = {
            "min_avg_turnover": 0,  # let it through eligibility
            "min_price": 0,
            "score_weights": {
                "institutional_flow": 0.0,
                "quality": 0.0,
                "revenue_momentum": 0.0,
            },
        }
        # Wrap with a _DataSlicer so production-equivalent behavior is tested
        slicer = _DataSlicer(
            source,
            backtest_start=datetime(2022, 1, 1),
            reference_now=datetime(2026, 4, 30),
        )
        slicer.set_as_of(datetime(2026, 4, 30))
        result = _analyze_symbol(
            {"symbol": "2330", "name": "TSMC", "industry": "半導體業"},
            slicer,
            default_strategy={},
            portfolio_config=portfolio_config,
            as_of=datetime(2026, 4, 30),
            market_signal="risk_on",
        )
        assert source.calls.get("fetch_institutional", 0) == 0, (
            "weight=0 must NOT fetch institutional"
        )
        assert source.calls.get("fetch_financial_quality", 0) == 0, (
            "weight=0 must NOT fetch financial_quality"
        )


# ---------------------------------------------------------------------------
# Attacker #2 + #3: slicer PIT-truncates future-dated institutional rows
# ---------------------------------------------------------------------------


class TestInstitutionalPitCover:
    """slicer.fetch_institutional is the PIT-correct path used in backtest."""

    def test_slicer_truncates_future_institutional_rows(self):
        """Cache contains rows up to 2026-04-30 but as_of is 2022-01-01;
        slicer must drop all rows after 2022-01-01."""
        full_inst = _make_institutional("2026-04-30", n=1500)
        source = _StubSource(institutional=full_inst)
        slicer = _DataSlicer(
            source,
            backtest_start=datetime(2022, 1, 1),
            reference_now=datetime(2026, 4, 30),
        )
        slicer.set_as_of(datetime(2022, 1, 1))
        truncated = slicer.fetch_institutional("2330", days=200)
        assert truncated is not None
        max_date = pd.to_datetime(truncated["date"]).max()
        assert max_date <= pd.Timestamp("2022-01-01"), (
            f"PIT violation: max date {max_date} > as_of 2022-01-01"
        )

    def test_slicer_with_no_as_of_does_not_truncate(self):
        """Live mode (as_of=None) must NOT truncate — full history returned."""
        full_inst = _make_institutional("2026-04-30", n=200)
        source = _StubSource(institutional=full_inst)
        slicer = _DataSlicer(source)  # no as_of, no backtest_start
        result = slicer.fetch_institutional("2330", days=200)
        assert result is not None
        # Live path: max date should still be 2026-04-30 (no truncation)
        max_date = pd.to_datetime(result["date"]).max()
        assert max_date >= pd.Timestamp("2026-04-29"), (
            f"Live mode should not truncate — max date {max_date} suggests it did"
        )


# ---------------------------------------------------------------------------
# Attacker #4: quality weight>0 in backtest_context raises (refusal gate)
# ---------------------------------------------------------------------------


class TestQualityFactorBacktestRefusal:
    """`fetch_financial_quality` is a single-snapshot dict; backtest path
    cannot PIT-truncate it → must raise rather than silent look-ahead."""

    def test_quality_weight_positive_in_backtest_raises(self):
        ohlcv = _make_ohlcv()
        source = _StubSource(
            ohlcv=ohlcv,
            quality={"date": "2026-04-30", "roe": 0.20, "gross_margin": 0.45},
        )
        slicer = _DataSlicer(
            source,
            backtest_start=datetime(2022, 1, 1),
            reference_now=datetime(2026, 4, 30),
        )
        slicer.set_as_of(datetime(2022, 6, 1))
        portfolio_config = {
            "min_avg_turnover": 0,
            "min_price": 0,
            "_backtest_context": True,
            "score_weights": {
                "quality": 0.10,  # weight > 0 → triggers refusal gate
                "institutional_flow": 0.0,
                "revenue_momentum": 0.0,
            },
        }
        with pytest.raises(NotImplementedError, match="fetch_financial_quality_history"):
            _analyze_symbol(
                {"symbol": "2330", "name": "TSMC", "industry": "半導體業"},
                slicer,
                default_strategy={},
                portfolio_config=portfolio_config,
                as_of=datetime(2022, 6, 1),
                market_signal="risk_on",
            )

    def test_quality_weight_positive_in_live_does_not_raise(self):
        """Live mode (no `_backtest_context` marker) must NOT raise; the
        snapshot reflects 'now' so look-ahead is non-issue."""
        ohlcv = _make_ohlcv()
        source = _StubSource(
            ohlcv=ohlcv,
            quality={"date": "2026-04-30", "roe": 0.20, "gross_margin": 0.45},
        )
        # No slicer wrap — live path uses raw source directly
        portfolio_config = {
            "min_avg_turnover": 0,
            "min_price": 0,
            # _backtest_context NOT set → live mode
            "score_weights": {
                "quality": 0.10,
                "institutional_flow": 0.0,
                "revenue_momentum": 0.0,
            },
        }
        result = _analyze_symbol(
            {"symbol": "2330", "name": "TSMC", "industry": "半導體業"},
            source,
            default_strategy={},
            portfolio_config=portfolio_config,
            as_of=datetime(2026, 4, 30),
            market_signal="risk_on",
        )
        # No raise; quality_raw should be computed (or None if filters block)
        assert result is not None
        assert source.calls.get("fetch_financial_quality", 0) == 1, (
            "Live mode should call fetch_financial_quality once"
        )

    def test_quality_weight_zero_in_backtest_does_not_raise(self):
        """weight=0 must NOT trigger the refusal gate (cost-isolation
        respected; the gate fires only when quality is actually requested)."""
        ohlcv = _make_ohlcv()
        source = _StubSource(ohlcv=ohlcv)
        slicer = _DataSlicer(
            source,
            backtest_start=datetime(2022, 1, 1),
            reference_now=datetime(2026, 4, 30),
        )
        slicer.set_as_of(datetime(2022, 6, 1))
        portfolio_config = {
            "min_avg_turnover": 0,
            "min_price": 0,
            "_backtest_context": True,
            "score_weights": {
                "quality": 0.0,  # weight=0
                "institutional_flow": 0.0,
                "revenue_momentum": 0.0,
            },
        }
        # Should NOT raise — weight=0 means we never even reach the gate
        result = _analyze_symbol(
            {"symbol": "2330", "name": "TSMC", "industry": "半導體業"},
            slicer,
            default_strategy={},
            portfolio_config=portfolio_config,
            as_of=datetime(2022, 6, 1),
            market_signal="risk_on",
        )
        assert result is not None
        assert source.calls.get("fetch_financial_quality", 0) == 0


# ---------------------------------------------------------------------------
# Attacker #6: __getattr__ passthrough does not silently bypass slicer
# ---------------------------------------------------------------------------


class TestSlicerPassthroughDoesNotMaskFinancialQuality:
    """slicer.__getattr__ (engine.py:269) passthroughs `fetch_financial_quality`
    directly to `_source` — but _analyze_symbol's refusal gate (audit A.2 fix)
    catches the look-ahead BEFORE the passthrough is reached. Verify this with
    a direct slicer call, then verify the gate still fires."""

    def test_slicer_getattr_passes_through_unchanged(self):
        """Sanity: slicer.fetch_financial_quality returns whatever _source
        returns — passthrough is unchanged. The gate lives in _analyze_symbol."""
        source = _StubSource(
            quality={"date": "2026-04-30", "roe": 0.20, "gross_margin": 0.45},
        )
        slicer = _DataSlicer(
            source,
            backtest_start=datetime(2022, 1, 1),
            reference_now=datetime(2026, 4, 30),
        )
        slicer.set_as_of(datetime(2022, 1, 1))
        # __getattr__ passthrough: same dict back, no truncation
        result = slicer.fetch_financial_quality("2330")
        assert result == {
            "date": "2026-04-30",  # future date — passthrough does not detect
            "roe": 0.20,
            "gross_margin": 0.45,
        }
