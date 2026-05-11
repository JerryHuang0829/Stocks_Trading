"""PIT mutation tests — Sprint Phase C deliverable.

Throwaway probes that inject FUTURE-dated rows into a fake data source and verify
`_DataSlicer` rejects them via the `<=` cutoff. Per Pro Research Standard, smoke
checks that walked the code path without injecting violations are NOT enough; we
must demonstrate the cutoff actually drops bad data.

Pattern: each test injects a row whose date > as_of, calls the slicer's fetch
method, and asserts the future row is NOT in the returned DataFrame. Mutation
test logic: if someone replaced `<=` with `<` or removed the filter, these tests
would FAIL (the future row would leak through).

After sprint these tests can be deleted unless user wants them permanent.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.backtest.engine import _DataSlicer


class FakeSource:
    """Test double — returns pre-canned DataFrames with mix of past + future rows."""

    def __init__(self):
        # OHLCV: index is UTC-tz timestamp, will inject future row at 2025-12-31
        self.ohlcv_df = pd.DataFrame(
            {"open": [100, 110, 120, 999], "close": [101, 111, 121, 1000]},
            index=pd.DatetimeIndex(
                ["2024-01-15", "2024-06-15", "2024-12-15", "2025-12-31"],
                tz="UTC",
            ),
        )
        # Institutional: date column, naive datetime
        self.inst_df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-10", "2024-06-10", "2024-12-10", "2025-12-25"]),
            "buy_sell": [100, 200, 300, 9999],
        })
        # Monthly revenue: date column
        self.rev_df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01", "2024-06-01", "2024-12-01", "2025-12-01"]),
            "revenue": [1.0, 2.0, 3.0, 99.0],
        })
        # Market value: full-market panel (stock_id, date, market_value); date column
        self.mv_df = pd.DataFrame({
            "stock_id": ["1101", "1101", "1101", "1101"],
            "date": pd.to_datetime(["2024-01-05", "2024-06-05", "2024-12-05", "2025-12-05"]),
            "market_value": [1.0e12, 2.0e12, 3.0e12, 9.9e12],
        })

    def fetch_ohlcv(self, symbol, timeframe, days):
        return self.ohlcv_df.copy()

    def fetch_institutional(self, symbol, days):
        return self.inst_df.copy()

    def fetch_month_revenue(self, symbol, months):
        return self.rev_df.copy()

    def fetch_market_value(self, days=10):
        return self.mv_df.copy()


def test_pit_mutation_ohlcv_rejects_forward_leak():
    """如果 _DataSlicer 把 cutoff 改成 `<` 或拿掉，這個測試會 FAIL（999/1000 那筆會漏進來）。"""
    source = FakeSource()
    slicer = _DataSlicer(source, as_of=datetime(2024, 6, 30))
    result = slicer.fetch_ohlcv("TEST", "D", limit=100)

    assert result is not None
    # Past rows in (≤ 2024-06-30): 2024-01-15, 2024-06-15 → 2 rows
    # Future rows excluded: 2024-12-15, 2025-12-31 → must NOT appear
    assert len(result) == 2, f"Expected 2 past rows, got {len(result)}: {result.index.tolist()}"
    assert 999 not in result["open"].values, "FORWARD LEAK: as_of=2024-06-30 returned future row open=999"
    assert 1000 not in result["close"].values, "FORWARD LEAK: as_of=2024-06-30 returned future row close=1000"
    assert all(idx <= pd.Timestamp("2024-06-30", tz="UTC") for idx in result.index), \
        f"FORWARD LEAK: index contains date > as_of: {result.index.tolist()}"


def test_pit_mutation_institutional_rejects_forward_leak():
    """date 欄 truncate 走 `_truncate_by_date_col`：cutoff 改 `<` 或拿掉，9999 那筆會漏進來。"""
    source = FakeSource()
    slicer = _DataSlicer(source, as_of=datetime(2024, 6, 30))
    result = slicer.fetch_institutional("TEST", days=365)

    assert result is not None
    assert 9999 not in result["buy_sell"].values, \
        "FORWARD LEAK: as_of=2024-06-30 returned future institutional row buy_sell=9999"
    max_date = pd.to_datetime(result["date"]).max()
    assert max_date <= pd.Timestamp("2024-06-30"), \
        f"FORWARD LEAK: institutional max date {max_date} > as_of 2024-06-30"


def test_pit_mutation_revenue_rejects_forward_leak():
    """月營收 same pattern：99.0 future revenue 不能出現在 ≤ 2024-06-30 的 slice 裡。"""
    source = FakeSource()
    slicer = _DataSlicer(source, as_of=datetime(2024, 6, 30))
    result = slicer.fetch_month_revenue("TEST", months=24)

    assert result is not None
    assert 99.0 not in result["revenue"].values, \
        "FORWARD LEAK: as_of=2024-06-30 returned future revenue 99.0"
    max_date = pd.to_datetime(result["date"]).max()
    assert max_date <= pd.Timestamp("2024-06-30"), \
        f"FORWARD LEAK: revenue max date {max_date} > as_of 2024-06-30"


def test_pit_mutation_market_value_rejects_forward_leak():
    """市值 panel（date 欄，走 ``_truncate_by_date_col``）：未來市值 9.9e12 不能出現在
    ≤ 2024-06-30 的切片裡。

    閉合 ``reports/diagnosis/architecture_audit_2026_05_02.md`` §B.2 與
    ``reports/sprint_pro_validation/J_multi_perspective_audit.md`` §P6.1 標的
    「PIT mutation tests 未覆蓋 market_value panel」follow-up gap。
    """
    source = FakeSource()
    slicer = _DataSlicer(source, as_of=datetime(2024, 6, 30))
    result = slicer.fetch_market_value(days=10)

    assert result is not None
    # ≤ 2024-06-30 的列：2024-01-05, 2024-06-05 → 2 列；未來列 2024-12-05 / 2025-12-05 排除
    assert len(result) == 2, f"Expected 2 past rows, got {len(result)}: {result['date'].tolist()}"
    assert 9.9e12 not in result["market_value"].values, \
        "FORWARD LEAK: as_of=2024-06-30 returned future market_value 9.9e12"
    max_date = pd.to_datetime(result["date"]).max()
    assert max_date <= pd.Timestamp("2024-06-30"), \
        f"FORWARD LEAK: market_value max date {max_date} > as_of 2024-06-30"


def test_pit_mutation_boundary_inclusive_at_as_of():
    """Boundary check: `<=` 是 inclusive，as_of 當天的資料 SHOULD be included。

    這驗 cutoff 不是 `<`（會 over-restrict 漏掉當天）也不是 `<=` (correct)。
    """
    source = FakeSource()
    # Set as_of EXACTLY to one of the data dates
    slicer = _DataSlicer(source, as_of=datetime(2024, 6, 15))
    ohlcv = slicer.fetch_ohlcv("TEST", "D", limit=100)

    assert ohlcv is not None
    assert pd.Timestamp("2024-06-15", tz="UTC") in ohlcv.index, \
        "BOUNDARY BUG: as_of=2024-06-15 excluded same-day row (cutoff might be `<` instead of `<=`)"
