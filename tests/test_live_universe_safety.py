"""Regression tests for live-path universe size-proxy hard-fail.

Prevents live selection from silently degrading to stock_id lexicographic
order when `source` is None, lacks fetch_ohlcv, or fetch_ohlcv mass-fails.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.portfolio.tw_stock import _prepare_auto_universe_by_size_proxy


def _info(n: int = 50) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_id": [f"{2000 + i:04d}" for i in range(n)],
            "stock_name": [f"S{i}" for i in range(n)],
            "type": ["twse"] * n,
            "industry_category": ["電子"] * n,
        }
    )


class _AlwaysRaise:
    def fetch_ohlcv(self, *a, **kw):
        raise RuntimeError("simulated data source failure")


class _NoData:
    def fetch_ohlcv(self, *a, **kw):
        return None


class _GoodSource:
    def __init__(self, good_ids: set[str]):
        self._good = good_ids

    def fetch_ohlcv(self, sym, *a, **kw):
        if sym not in self._good:
            raise RuntimeError("not cached")
        idx = pd.date_range("2024-01-01", periods=20, freq="D", tz="UTC")
        return pd.DataFrame(
            {"close": [100.0] * 20, "volume": [1000.0] * 20}, index=idx
        )


class TestLiveUniverseHardFail:

    def test_source_is_none_raises(self):
        with pytest.raises(RuntimeError, match="source=None"):
            _prepare_auto_universe_by_size_proxy(_info(), None, {})

    def test_source_missing_fetch_ohlcv_raises(self):
        class _Bad:
            pass

        with pytest.raises(RuntimeError, match="fetch_ohlcv"):
            _prepare_auto_universe_by_size_proxy(_info(), _Bad(), {})

    def test_fetch_ohlcv_mass_failure_raises(self):
        with pytest.raises(RuntimeError, match="success rate too low"):
            _prepare_auto_universe_by_size_proxy(
                _info(), _AlwaysRaise(), {"auto_universe_size_proxy_min_success": 0.60}
            )

    def test_fetch_ohlcv_all_none_raises(self):
        with pytest.raises(RuntimeError, match="success rate too low"):
            _prepare_auto_universe_by_size_proxy(
                _info(), _NoData(), {"auto_universe_size_proxy_min_success": 0.60}
            )

    def test_partial_failure_within_threshold_passes(self):
        """正向測：high-proxy ids 故意散佈在非連續位置，避免字典序 fallback 假綠。

        若程式真的退化成 stock_id 字典序 top-10，結果會是 2000-2009 的連續前綴，
        而非我們指定的 [2013, 2019, 2027, ...]。assert 會抓到。
        """
        # 高 size_proxy 分散在尾段，字典序 fallback 會選不到
        high_value_syms = {"2047", "2043", "2039", "2035", "2031"}
        # 再加一些會成功但較低的，共 40 成功 / 50（80% > 60% 門檻）
        mid_value_syms = {f"{2000 + i:04d}" for i in (
            3, 7, 11, 17, 21, 23, 29, 33, 37, 41, 45, 49,
            4, 8, 14, 18, 22, 26, 30, 34, 38, 42, 46,
            5, 9, 15, 19, 25, 28, 32, 36, 40, 44, 48,
        )}

        class _Tiered:
            """回傳兩種 turnover：high_value_syms 給大 turnover，mid 給小。"""

            def fetch_ohlcv(self, sym, *a, **kw):
                if sym in high_value_syms:
                    val = 1_000_000.0
                elif sym in mid_value_syms:
                    val = 1000.0
                else:
                    raise RuntimeError("not in any good set")
                idx = pd.date_range("2024-01-01", periods=20, freq="D", tz="UTC")
                return pd.DataFrame(
                    {"close": [val] * 20, "volume": [1.0] * 20}, index=idx
                )

        result = _prepare_auto_universe_by_size_proxy(
            _info(50),
            _Tiered(),
            {"auto_universe_size_proxy_min_success": 0.60,
             "auto_universe_size": 5},
        )
        result_syms = {r["symbol"] for r in result}
        # Top-5 必須是 high_value_syms（真實 proxy 排序），
        # 而不是字典序 fallback 會給的 {"2003", "2004", "2005", "2007", "2008"} 之類前綴。
        assert result_syms == high_value_syms, (
            f"Expected high-proxy syms {high_value_syms}, got {result_syms}. "
            f"Silent lexicographic fallback detected."
        )
