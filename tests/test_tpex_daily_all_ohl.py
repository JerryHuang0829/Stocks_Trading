"""TPEX OpenAPI OHL 抓取測試（2026-04-16 升級驗證）.

TPEX `tpex_mainboard_daily_close_quotes` 端點現在提供 Open/High/Low 欄位
（過去只有 Close）。此測試確保 `fetch_twse_daily_all` 正確抓取並填入
open/high/low，同時保留 fallback-to-close 邏輯應對舊格式。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.data.twse_scraper import fetch_twse_daily_all


# 共用的 TWSE mock response
# TWSE 有 retry loop（_MAX_RETRY_DAYS=7），若回空會重試 7 次
# 為避免測試複雜化，讓 TWSE 第一次就回「1 筆佔位資料」使其立刻 break
# 這樣 mock side_effect 只需提供 2 個 response（TWSE + TPEX）
_TWSE_MINIMAL_RESPONSE = {
    "stat": "OK",
    "data": [
        # [0]id [1]name [2]volume [3]turnover [4]open [5]high [6]low [7]close [8]change [9]deals
        ["9999", "TWSE 佔位", "100", "5000", "50.0", "51.0", "49.5", "50.5", "+0.5", "10"],
    ],
}


def _make_response(status_code: int, json_data):
    """建立 mock response 物件。"""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data
    return m


def _make_tpex_row(**overrides):
    """建立一筆 TPEX row 的 default template，可覆蓋特定欄位。"""
    defaults = {
        "Date": "1150416",
        "SecuritiesCompanyCode": "006201",
        "CompanyName": "元大富櫃50",
        "Close": "40.34",
        "Open": "38.80",
        "High": "40.34",
        "Low": "38.69",
        "TradingShares": "199499",
        "TransactionAmount": "7834864",
    }
    defaults.update(overrides)
    return defaults


class TestTpexOhlExtraction:
    """驗證 TPEX OpenAPI 的 OHL 欄位正確抓取。"""

    def test_tpex_returns_ohl_when_present(self):
        """TPEX row 含 OHL 時，result 應有 open/high/low 欄位且值正確。"""
        from datetime import datetime

        tpex_rows = [_make_tpex_row(SecuritiesCompanyCode="006201")]

        with patch("src.data.twse_scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(200, _TWSE_MINIMAL_RESPONSE),
                _make_response(200, tpex_rows),
            ]
            result = fetch_twse_daily_all(datetime.now())

        assert "006201" in result
        row = result["006201"]
        assert "open" in row
        assert "high" in row
        assert "low" in row
        assert row["close"] == 40.34
        assert row["open"] == 38.80
        assert row["high"] == 40.34
        assert row["low"] == 38.69
        assert row["volume"] == 199499

    def test_tpex_fallback_to_close_when_ohl_missing(self):
        """OHL 欄位為 '--' 或缺值時，fallback 到 close 值。"""
        from datetime import datetime

        tpex_rows = [
            _make_tpex_row(
                SecuritiesCompanyCode="006202",
                Close="25.00",
                Open="--",      # 舊格式或停牌情境
                High="",
                Low=None,
            )
        ]

        with patch("src.data.twse_scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(200, _TWSE_MINIMAL_RESPONSE),
                _make_response(200, tpex_rows),
            ]
            result = fetch_twse_daily_all(datetime.now())

        assert "006202" in result
        row = result["006202"]
        # 所有 OHL 都該 fallback 到 close=25.00
        assert row["close"] == 25.00
        assert row["open"] == 25.00
        assert row["high"] == 25.00
        assert row["low"] == 25.00

    def test_tpex_skip_zero_close(self):
        """Close=0 的 row 應被跳過（不加入 result）。"""
        from datetime import datetime

        tpex_rows = [
            _make_tpex_row(SecuritiesCompanyCode="006203", Close="0"),
            _make_tpex_row(SecuritiesCompanyCode="006204", Close="15.5"),
        ]

        with patch("src.data.twse_scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(200, _TWSE_MINIMAL_RESPONSE),
                _make_response(200, tpex_rows),
            ]
            result = fetch_twse_daily_all(datetime.now())

        # 006203 因 close=0 被跳過
        assert "006203" not in result
        # 006204 正常
        assert "006204" in result
        assert result["006204"]["close"] == 15.5

    def test_tpex_skips_non_numeric_stock_id(self):
        """非數字 stock_id 應被跳過。"""
        from datetime import datetime

        tpex_rows = [
            _make_tpex_row(SecuritiesCompanyCode="ABC"),       # 非數字
            _make_tpex_row(SecuritiesCompanyCode=""),           # 空
            _make_tpex_row(SecuritiesCompanyCode="006205"),     # 正常
        ]

        with patch("src.data.twse_scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(200, _TWSE_MINIMAL_RESPONSE),
                _make_response(200, tpex_rows),
            ]
            result = fetch_twse_daily_all(datetime.now())

        assert "ABC" not in result
        assert "006205" in result
        # result 含 TWSE 佔位 9999 + TPEX 006205，共 2 筆（ABC 與空字串被跳過）
        assert len(result) == 2

    def test_tpex_does_not_overwrite_twse(self):
        """若 TWSE 已提供某 stock_id，TPEX 不得覆蓋（TWSE 優先）。"""
        from datetime import datetime

        # TWSE 回 006206 with OHL（假設這支在兩邊都有）
        twse_response = {
            "stat": "OK",
            "data": [
                # [0]id [1]name [2]volume [3]turnover [4]open [5]high [6]low [7]close [8]change [9]deals
                ["006206", "TWSE 版", "1000", "50000", "50.0", "52.0", "49.0", "51.0", "+1", "100"],
            ],
        }
        tpex_rows = [
            _make_tpex_row(
                SecuritiesCompanyCode="006206",
                Close="99.99",  # TPEX 不同價，驗證不會覆蓋
                Open="90.0",
                High="100.0",
                Low="89.0",
            )
        ]

        with patch("src.data.twse_scraper.requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(200, twse_response),
                _make_response(200, tpex_rows),
            ]
            result = fetch_twse_daily_all(datetime.now())

        assert "006206" in result
        # 應為 TWSE 版的 51.0，不是 TPEX 版的 99.99
        assert result["006206"]["close"] == 51.0
        assert result["006206"]["open"] == 50.0
