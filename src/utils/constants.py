"""Shared constants used across modules."""

from datetime import timedelta, timezone
from typing import Any

import pandas as pd

TW_TZ = timezone(timedelta(hours=8))


def to_utc_ts(value: Any) -> pd.Timestamp:
    """Convert any datetime-like value to a UTC-aware pd.Timestamp.

    Safe for both naive and tz-aware inputs. pandas 2.x raises if you pass
    `tz="UTC"` to a tz-aware value, so callers must branch — this helper
    centralizes that branching.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")

# 台股個股來回交易成本 ≈ 0.47%（手續費 0.1425% x2 + 證交稅 0.3% 賣出）
TW_ROUND_TRIP_COST = 0.0047

# OHLCV 最低歷史長度（交易日）— 252 天動能 + 22 天 SMA buffer
MIN_OHLCV_BARS = 274

# 動能計算期間（交易日）
MOMENTUM_PERIOD_3M = 63
MOMENTUM_PERIOD_6M = 126
MOMENTUM_PERIOD_12M = 252
MOMENTUM_SKIP_DAYS = 21  # 12-1 動能跳過最近 N 天

# 營收資料 look-ahead 延遲（日曆天）
# 台股月營收法定公告期限為次月 10 日前。舊值 35 天對早月再平衡（rebalance_day=5）
# 會把尚未公告的月營收納入因子（例：as_of=2026-03-05, cutoff=2026-01-29
# 會含 2026-02 營收，但該月營收要到 2026-03-10 才公告）。
# 45 天 = 次月 10 日最晚公告 + 5 天 publication buffer，確保 PIT 不洩漏。
REVENUE_LAG_DAYS = 45

# 融資融券 look-ahead 延遲。TWSE 於每交易日盤後結算，次日 15:00 前公告。
# T+1 對應到 as_of 日終 snapshot 來說已安全可用，再加 1 天保守 buffer。
MARGIN_LAG_DAYS = 2

# 三大法人（外資 / 投信 / 自營）買賣超 look-ahead 延遲。
# TWSE 當日盤後 17:00 左右公告，次營業日 T+1 可用。
INSTITUTIONAL_LAG_DAYS = 2

# 季報 EPS look-ahead 延遲（日曆天）。
# 台股季報法定公告期限：Q1/Q2/Q3 為下季結束後 45 天，Q4 年報為次年 3/31（= 90 天）。
# 舊值 60 天統一 blanket 對 Q4 不夠：Q4 date=12-31, as_of=02-15 時 cutoff=12-17
# 會納入尚未公告的 Q4 EPS → look-ahead bias。
# P1-1 修正：per-quarter lag，Q4 使用 90 天、Q1-Q3 使用 45 天。
# QUARTERLY_EPS_LAG_DAYS 保留為向後相容 fallback（若呼叫端不給 quarter-aware lag）。
QUARTERLY_EPS_LAG_DAYS = 60
QUARTERLY_EPS_LAG_DAYS_Q4 = 90
QUARTERLY_EPS_LAG_DAYS_OTHER = 45

# Balance sheet (Δassets) look-ahead 延遲（Phase 2 S2 add per V0.13 lock）。
# 台股 balance sheet 公告通常晚於 income statement 數天到 2 週；保守 60d
# blanket lag 確保 PIT — Q4 balance sheet 90d income lag + 額外 buffer 仍 OK。
# 用法：quality_v3 (D-E) Δassets 計算 + 任何需 balance sheet 數據的 factor。
BALANCE_SHEET_LAG_DAYS = 60

# 廣義科技供應鏈關鍵字 — 用於 theme_concentration 監控
# engine.py 與 paper_trade.py 共用，避免兩處定義不一致
TECH_SUPPLY_CHAIN_KEYWORDS = frozenset([
    "電子", "半導體", "IC", "光電", "通信", "資訊", "電腦", "電機",
])
