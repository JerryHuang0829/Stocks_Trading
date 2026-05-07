"""Tests for TWSE/TPEX margin + institutional fetchers and backfill logic.

Coverage (Phase A1 R11):
  - TWSE MI_MARGN parser: field mapping 16 → FinMind cols
  - TPEX margin parser:   field-order difference (券賣/券買 swap)
  - TWSE T86 parser:      wide → FinMind long format (5 rows/sym)
  - TPEX insti parser:    wide → long with different column order vs TWSE
  - Fallback: TWSE T86 selectType=ALL empty → ALLBUT0999 retry
  - Backfill: insert-if-missing, idempotent skip, schema drift detection
  - Preload date-cache speed-up
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from src.data.twse_scraper import (
    FINMIND_INSTITUTIONAL_COLS,
    FINMIND_MARGIN_SHORT_COLS,
    _parse_int,
    _is_four_digit_stock,
    fetch_twse_margin_daily_all,
    fetch_tpex_margin_daily_all,
    fetch_twse_institutional_daily_all,
    fetch_tpex_institutional_daily_all,
)


# ---------- _parse_int / _is_four_digit_stock ----------

def test_parse_int_handles_commas_and_dashes():
    assert _parse_int("1,234,567") == 1234567
    assert _parse_int("--") == 0
    assert _parse_int("") == 0
    assert _parse_int(None) == 0
    assert _parse_int("\u3000") == 0  # full-width space
    assert _parse_int("123.0") == 123


def test_is_four_digit_stock_accepts_only_4_numeric():
    assert _is_four_digit_stock("2330")
    assert _is_four_digit_stock("0050")
    assert not _is_four_digit_stock("　")  # 合計 row
    assert not _is_four_digit_stock("23301")  # 5 digit
    assert not _is_four_digit_stock("233")    # 3 digit
    assert not _is_four_digit_stock("233A")   # alpha
    assert not _is_four_digit_stock("")


# ---------- TWSE MI_MARGN fetcher ----------

def _mock_twse_margin_response() -> dict:
    """Minimal 4/17 response skeleton for 2330 + header/summary."""
    return {
        "stat": "OK",
        "date": "20260417",
        "tables": [
            {"title": "summary"},  # tables[0] summary
            {
                "title": "per_stock",
                "fields": ["代號", "名稱", "買進", "賣出", "現金償還",
                           "前日餘額", "今日餘額", "次一營業日限額",
                           "買進", "賣出", "現券償還",
                           "前日餘額", "今日餘額", "次一營業日限額",
                           "資券互抵", "註記"],
                "data": [
                    ["　", "合計", "100", "200", "0", "1000", "900",
                     "1000000", "50", "60", "0", "500", "490",
                     "1000000", "10", "　"],
                    ["2330", "台積電", "1692", "2500", "0",
                     "26478", "25670", "1880795",
                     "20", "25", "0", "120", "115",
                     "1880795", "2", " "],
                ],
            },
        ],
    }


def test_twse_margin_fetcher_parses_correctly():
    with mock.patch("src.data.twse_scraper.requests.get") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = _mock_twse_margin_response()
        result = fetch_twse_margin_daily_all(datetime(2026, 4, 17))
    assert "2330" in result
    assert "合計" not in result  # summary row filtered
    r = result["2330"]
    # Spot-check key fields
    assert r["MarginPurchaseBuy"] == 1692
    assert r["MarginPurchaseSell"] == 2500
    assert r["MarginPurchaseYesterdayBalance"] == 26478
    assert r["MarginPurchaseTodayBalance"] == 25670
    assert r["ShortSaleBuy"] == 20
    assert r["ShortSaleSell"] == 25
    assert r["ShortSaleTodayBalance"] == 115
    assert r["OffsetLoanAndShort"] == 2


def test_twse_margin_non_trading_day_returns_empty():
    with mock.patch("src.data.twse_scraper.requests.get") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = {"stat": "很抱歉，沒有符合條件的資料!"}
        result = fetch_twse_margin_daily_all(datetime(2026, 4, 18))  # 週六
    assert result == {}


# ---------- TPEX margin fetcher ----------

def _mock_tpex_margin_response() -> dict:
    """TPEX 20-col schema with 券賣/券買 reversed vs TWSE."""
    return {
        "tables": [{
            "fields": ["代號", "名稱", "前資餘額", "資買", "資賣", "現償", "資餘額",
                       "資屬證金", "資使用率", "資限額",
                       "前券餘額", "券賣", "券買", "券償", "券餘額",
                       "券屬證金", "券使用率", "券限額", "資券相抵", "備註"],
            "data": [[
                "6488", "環球晶",
                "5000", "100", "200", "0", "4900",
                "21", "0.27", "1880795",
                "50", "30", "20", "0", "40",  # 券賣=30, 券買=20（TPEX 順序）
                "0", "0.0", "1880795", "5", "",
            ]],
        }],
    }


def test_tpex_margin_fetcher_handles_column_swap():
    with mock.patch("src.data.twse_scraper.requests.get") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = _mock_tpex_margin_response()
        result = fetch_tpex_margin_daily_all(datetime(2026, 4, 17))
    r = result["6488"]
    # TPEX 的 column 11=券賣, 12=券買 (與 TWSE 相反). fetcher 必須 swap 回 FinMind 標準
    assert r["ShortSaleSell"] == 30  # row[11]
    assert r["ShortSaleBuy"] == 20   # row[12]
    # MarginPurchase 的 YesterdayBalance 來自 col[2], TodayBalance 來自 col[6]
    assert r["MarginPurchaseYesterdayBalance"] == 5000
    assert r["MarginPurchaseTodayBalance"] == 4900


# ---------- TWSE T86 institutional fetcher ----------

def _mock_twse_t86_response_with_data() -> dict:
    """19-col wide format for 2330."""
    return {
        "stat": "OK",
        "fields": ["代號", "名稱"] + ["c%d" % i for i in range(17)],
        "data": [[
            "2330", "台積電",
            "15452539", "24835310", "-9382771",   # [2][3][4] 外陸資(不含外資自營商)
            "0", "0", "0",                          # [5][6][7] 外資自營商
            "652645", "2838308", "-2185663",        # [8][9][10] 投信
            "-269860",                              # [11] 自營商買賣超合計
            "453000", "812000", "-359000",          # [12][13][14] 自行買賣
            "350687", "261547", "89140",            # [15][16][17] 避險
            "-11838294",                            # [18] 三大法人合計
        ]],
    }


def test_twse_t86_fetcher_produces_finmind_long_format():
    with mock.patch("src.data.twse_scraper.requests.get") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = _mock_twse_t86_response_with_data()
        result = fetch_twse_institutional_daily_all(datetime(2026, 4, 17))
    assert "2330" in result
    rows = result["2330"]
    assert len(rows) == 5  # 5 institution categories
    names = {r["name"] for r in rows}
    assert names == {
        "Foreign_Investor", "Foreign_Dealer_Self",
        "Investment_Trust", "Dealer_self", "Dealer_Hedging",
    }
    # Map to dict for spot check
    by_name = {r["name"]: r for r in rows}
    assert by_name["Foreign_Investor"]["buy"] == 15452539
    assert by_name["Foreign_Investor"]["sell"] == 24835310
    assert by_name["Dealer_self"]["buy"] == 453000   # 自行買賣
    assert by_name["Dealer_Hedging"]["sell"] == 261547
    assert by_name["Foreign_Dealer_Self"]["buy"] == 0


def test_twse_t86_fallback_to_allbut0999_when_all_returns_empty():
    """Regression: 2026-04-17 observed: selectType=ALL returns empty
    but ALLBUT0999 returns valid data. Fetcher must try ALLBUT0999 first."""
    first_call = {"stat": "OK", "data": [["2330", "台積電"] + ["0"] * 17]}
    calls = []

    def mock_get(url, params=None, **kwargs):
        calls.append(params.get("selectType"))
        r = mock.MagicMock()
        r.status_code = 200
        r.json.return_value = first_call
        return r

    with mock.patch("src.data.twse_scraper.requests.get", side_effect=mock_get):
        fetch_twse_institutional_daily_all(datetime(2026, 4, 17))
    # ALLBUT0999 is tried first per fetcher design
    assert calls[0] == "ALLBUT0999"


# ---------- TPEX insti fetcher ----------

def _mock_tpex_insti_response() -> dict:
    """TPEX 24-col wide: outer[2-4] + dealer_self[5-7] + foreign_investor[8-10] +
    investment_trust[11-13] + dealer_self_trade[14-16] + dealer_hedge[17-19] +
    dealer_total[20-22] + grand_total[23]."""
    return {
        "tables": [{
            "data": [[
                "6488", "環球晶",
                "842739", "2336067", "-1493328",     # 外陸資合計（not used）
                "0", "0", "0",                         # 外資自營商 → Foreign_Dealer_Self
                "842739", "2336067", "-1493328",      # 外陸資不含外資自營商 → Foreign_Investor
                "250766", "661592", "-410826",        # 投信 → Investment_Trust
                "6000", "126200", "-120200",          # 自營商自行買賣 → Dealer_self
                "82279", "203647", "-121368",         # 自營商避險 → Dealer_Hedging
                "88279", "329847", "-241568",         # 自營商總計 (not stored)
                "-2145722",                            # 三大法人合計
            ]],
        }],
    }


def test_tpex_insti_fetcher_uses_correct_column_indices():
    with mock.patch("src.data.twse_scraper.requests.get") as m:
        m.return_value.status_code = 200
        m.return_value.json.return_value = _mock_tpex_insti_response()
        result = fetch_tpex_institutional_daily_all(datetime(2026, 4, 17))
    rows = result["6488"]
    by_name = {r["name"]: r for r in rows}
    # Foreign_Investor 應來自 [8][9] (外陸資不含外資自營商)
    assert by_name["Foreign_Investor"]["buy"] == 842739
    assert by_name["Foreign_Investor"]["sell"] == 2336067
    # Dealer_self 應來自 [14][15] (自行買賣)
    assert by_name["Dealer_self"]["buy"] == 6000
    assert by_name["Dealer_self"]["sell"] == 126200
    # Dealer_Hedging 應來自 [17][18] (避險)
    assert by_name["Dealer_Hedging"]["buy"] == 82279
    assert by_name["Dealer_Hedging"]["sell"] == 203647


# ---------- Backfill script helpers ----------

def test_backfill_idempotent_skip(tmp_path: Path):
    """Inserting same (sym, day) twice should only write the first time."""
    from scripts.backfill_tw_factors import _append_margin_day

    ms_dir = tmp_path / "margin_short"
    ms_dir.mkdir()
    day = datetime(2026, 4, 17)
    snap = {
        "9999": {c: 1 for c in FINMIND_MARGIN_SHORT_COLS if c not in ("date", "stock_id")}
    }

    # First call: creates new pickle.
    ins1, skp1, new1, drf1 = _append_margin_day(tmp_path, day, snap, dry_run=False)
    assert new1 == 1

    # Second call: idempotent skip (day already in pickle).
    ins2, skp2, new2, drf2 = _append_margin_day(tmp_path, day, snap, dry_run=False)
    assert new2 == 0
    assert skp2 == 1
    assert ins2 == 0

    # Pickle should have exactly 1 row.
    pkl = ms_dir / "9999.pkl"
    df = pd.read_pickle(pkl)
    assert len(df) == 1


def test_backfill_schema_drift_raises_and_skips(tmp_path: Path):
    """Existing pickle with wrong cols → _append_margin_day records drift, no write."""
    from scripts.backfill_tw_factors import _append_margin_day

    ms_dir = tmp_path / "margin_short"
    ms_dir.mkdir()
    # Write an existing pickle with MISSING 'OffsetLoanAndShort' column.
    bad_df = pd.DataFrame([{"date": pd.Timestamp("2026-04-16"), "stock_id": "9998",
                            "MarginPurchaseBuy": 1}])
    bad_df.to_pickle(ms_dir / "9998.pkl")

    day = datetime(2026, 4, 17)
    snap = {
        "9998": {c: 1 for c in FINMIND_MARGIN_SHORT_COLS if c not in ("date", "stock_id")}
    }
    ins, skp, new, drf = _append_margin_day(tmp_path, day, snap, dry_run=False)
    assert drf == 1
    assert ins == 0
    # Original bad pickle must NOT be modified.
    df = pd.read_pickle(ms_dir / "9998.pkl")
    assert len(df) == 1


def test_backfill_preload_date_cache_accelerates(tmp_path: Path):
    """Date-cache preload should correctly index existing pickle dates."""
    from scripts.backfill_tw_factors import _preload_date_cache

    ms_dir = tmp_path / "margin_short"
    ms_dir.mkdir()
    # Seed one pickle with 3 dates.
    df = pd.DataFrame([
        {"date": pd.Timestamp("2026-04-14"), "stock_id": "2330",
         **{c: 0 for c in FINMIND_MARGIN_SHORT_COLS if c not in ("date", "stock_id")}},
        {"date": pd.Timestamp("2026-04-15"), "stock_id": "2330",
         **{c: 0 for c in FINMIND_MARGIN_SHORT_COLS if c not in ("date", "stock_id")}},
        {"date": pd.Timestamp("2026-04-16"), "stock_id": "2330",
         **{c: 0 for c in FINMIND_MARGIN_SHORT_COLS if c not in ("date", "stock_id")}},
    ])
    df.to_pickle(ms_dir / "2330.pkl")

    cache = _preload_date_cache(ms_dir)
    assert "2330" in cache
    assert len(cache["2330"]) == 3
    assert pd.Timestamp("2026-04-15") in cache["2330"]


def test_backfill_insert_uses_date_cache_for_skip(tmp_path: Path):
    """When date_cache indicates day exists, _append_margin_day should skip
    without reading/writing pickle."""
    from scripts.backfill_tw_factors import _append_margin_day

    ms_dir = tmp_path / "margin_short"
    ms_dir.mkdir()
    # Write pickle with 4/17 already.
    existing = pd.DataFrame([{
        "date": pd.Timestamp("2026-04-17"), "stock_id": "2330",
        **{c: 999 for c in FINMIND_MARGIN_SHORT_COLS if c not in ("date", "stock_id")}
    }])
    existing.to_pickle(ms_dir / "2330.pkl")
    cache = {"2330": {pd.Timestamp("2026-04-17")}}

    day = datetime(2026, 4, 17)
    # Different values in snapshot — must NOT overwrite existing row.
    snap = {"2330": {c: 1 for c in FINMIND_MARGIN_SHORT_COLS if c not in ("date", "stock_id")}}
    ins, skp, new, drf = _append_margin_day(tmp_path, day, snap, dry_run=False, date_cache=cache)
    assert skp == 1
    assert ins == 0
    # Pickle must be untouched.
    df = pd.read_pickle(ms_dir / "2330.pkl")
    assert df.iloc[0]["MarginPurchaseBuy"] == 999  # original value preserved
