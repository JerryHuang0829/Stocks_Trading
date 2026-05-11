"""Shared fixtures for Quantitative-Trading test suite."""

from __future__ import annotations

import pytest


@pytest.fixture
def portfolio_config():
    """Production-equivalent portfolio config (matches settings.yaml).

    R19 audit external audit P1 fix (2026-05-02): slippage_bps 從 5 (drift) 改 10
    對齊 config/settings.yaml:72. 之前 fixture / production drift 會讓
    成本敏感 tests silently 低估成本 → backtest assertion 假通過.
    """
    return {
        "top_n": 8,
        "hold_buffer": 3,
        "hold_score_floor": 60,
        "max_position_weight": 0.12,
        "min_holdings": 3,
        "max_same_industry": 3,
        "turnover_score_threshold": 6.0,
        "turnover_cost": 0.0047,
        "slippage_bps": 10,  # 對齊 config/settings.yaml:72 (R19 external audit fix)
        "weight_mode": "score_weighted",
        "min_factor_coverage_per_symbol": 0.6,  # R19 audit A.1 (settings.yaml)
        "exposure": {
            "risk_on": 0.96,
            "caution": 0.70,
            "risk_off": 0.35,
        },
        "score_weights": {
            "price_momentum": 0.55,
            "trend_quality": 0.20,
            "revenue_momentum": 0.25,
            "institutional_flow": 0.00,
        },
    }


@pytest.fixture
def market_view_risk_on():
    return {"symbol": "0050", "regime": "trending_up", "regime_display": "上升趨勢", "signal": "risk_on"}


@pytest.fixture
def market_view_caution():
    return {"symbol": "0050", "regime": "ranging", "regime_display": "盤整", "signal": "caution"}


@pytest.fixture
def market_view_risk_off():
    return {"symbol": "0050", "regime": "trending_down", "regime_display": "下降趨勢", "signal": "risk_off"}


def make_analysis(symbol, name="Test", industry="電子工業", eligible=True,
                  pm=50.0, tq=0.6, rev=0.1, inst=0.0, vol=0.02):
    """Helper to build a single analysis dict for testing."""
    return {
        "symbol": symbol,
        "name": name,
        "industry": industry,
        "eligible": eligible,
        "filters": [] if eligible else ["turnover_too_low"],
        "price_momentum_raw": pm,
        "trend_quality_raw": tq,
        "revenue_raw": rev,
        "institutional_raw": inst,
        "close": 100.0,
        "regime": "trending_up",
        "regime_display": "上升趨勢",
        "momentum_12_1": pm / 100.0 if pm is not None else None,
        "revenue_yoy": rev,
        "institutional_detail": "",
        "volatility_20d": vol,
    }


@pytest.fixture
def ten_eligible_analyses():
    """10 eligible stocks across 4 industries, with varied factor scores."""
    return [
        make_analysis("2330", "台積電", "半導體業", pm=80, tq=0.9, rev=0.15, vol=0.018),
        make_analysis("2317", "鴻海", "電子工業", pm=70, tq=0.7, rev=0.12, vol=0.025),
        make_analysis("2454", "聯發科", "半導體業", pm=75, tq=0.8, rev=0.20, vol=0.030),
        make_analysis("2308", "台達電", "電子工業", pm=65, tq=0.6, rev=0.10, vol=0.022),
        make_analysis("1301", "台塑", "塑膠工業", pm=40, tq=0.4, rev=0.05, vol=0.015),
        make_analysis("2881", "富邦金", "金融保險業", pm=45, tq=0.5, rev=0.08, vol=0.012),
        make_analysis("2882", "國泰金", "金融保險業", pm=42, tq=0.45, rev=0.07, vol=0.013),
        make_analysis("2303", "聯電", "半導體業", pm=60, tq=0.65, rev=0.11, vol=0.028),
        make_analysis("2886", "兆豐金", "金融保險業", pm=38, tq=0.35, rev=0.06, vol=0.011),
        make_analysis("1303", "南亞", "塑膠工業", pm=35, tq=0.3, rev=0.04, vol=0.016),
    ]
