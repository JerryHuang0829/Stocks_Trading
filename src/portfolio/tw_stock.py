"""Taiwan stock portfolio construction and monthly rebalance logic."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

from ..storage.database import compute_config_hash

import pandas as pd

from ..backtest.metrics import adjust_splits
from ..data.finmind import _BacktestCacheMissError
from ..features.foreign_investor_v2 import compute_foreign_investor_v2_universe
from ..features.high_proximity import compute_high_proximity_universe
from ..features.institutional import score_institutional
from ..features.margin_short_ratio import compute_margin_short_ratio_universe
from ..features.pead_eps import compute_pead_eps_universe
from ..features.revenue_momentum_v2 import compute_revenue_momentum_v2_universe
from ..strategy.indicators import calculate_indicators
from ..strategy.regime import detect_regime, get_regime_display
from ..utils.constants import (
    MIN_OHLCV_BARS,
    MOMENTUM_PERIOD_3M,
    MOMENTUM_PERIOD_6M,
    MOMENTUM_PERIOD_12M,
    MOMENTUM_SKIP_DAYS,
    REVENUE_LAG_DAYS,
    TW_ROUND_TRIP_COST,
    TW_TZ,
)
from ..utils.paths import resolve_cache_dir

logger = logging.getLogger(__name__)

DEFAULT_PORTFOLIO_CONFIG = {
    "profile": None,
    "profile_label": "custom",
    "target_holding_months": None,
    "enabled": True,
    "rebalance_frequency": "monthly",
    "rebalance_day": 5,
    "rebalance_after_close_hour": 14,
    "top_n": 5,
    "hold_buffer": 2,
    "hold_score_floor": 55.0,
    "max_position_weight": 0.20,
    "market_proxy_symbol": "0050",
    "history_limit": 320,
    "monthly_revenue_months": 15,
    "min_price": 20.0,
    "min_avg_turnover": 50_000_000.0,
    "exclude_etf": True,
    "use_monthly_revenue": True,
    "use_auto_universe": True,
    "auto_universe_size": 80,
    "auto_universe_markets": ["twse", "tpex"],
    "auto_universe_exclude_industries": [
        "ETF",
        "ETN",
        "受益證券",
        "存託憑證",
        "權證",
    ],
    "auto_universe_include_symbols": [],
    "auto_universe_exclude_symbols": [],
    "exposure": {
        "risk_on": 1.0,
        "caution": 0.7,
        "risk_off": 0.35,
    },
    "score_weights": {
        # 2026-05-11 R30-6 fix (R30): institutional_flow 0.10 → 0.0
        # 對齊 active config/settings.yaml（legacy 因子，profile 切換時不該被
        # 意外帶回；外資 v1 IC=-0.053 已被 R26-R29 確認 fail，v2 R28 DROP）.
        # 舊 default 0.45/0.25/0.20/0.10 重分配為 0.55/0.20/0.25/0.00.
        "price_momentum": 0.55,
        "trend_quality": 0.20,
        "revenue_momentum": 0.25,
        "institutional_flow": 0.00,
        # Phase A2 Step 2: new factor slots (default 0.0; enable via settings.yaml)
        "high_proximity": 0.0,
        "pead_eps": 0.0,
        "margin_short_ratio": 0.0,
        "revenue_momentum_v2": 0.0,
        "foreign_investor_v2": 0.0,
    },
    # 交易成本相關
    "turnover_cost": TW_ROUND_TRIP_COST,
    "turnover_score_threshold": 5.0,  # 新股 score 需超過被替換股至少 N 分才換倉
    # 產業分散
    "max_same_industry": 2,  # 同產業最多持有 N 檔
    # 權重分配模式: "equal" | "score_weighted"
    "weight_mode": "score_weighted",
    # 安全閥
    "min_eligible_ratio": 0.3,  # 至少 30% 候選股分析成功才執行再平衡
}

PORTFOLIO_PROFILES = {
    "tw_3m_stable": {
        "label": "Taiwan 3M Stable",
        "target_holding_months": 3,
        "rebalance_day": 12,
        "top_n": 8,
        "hold_buffer": 3,
        "hold_score_floor": 60.0,
        "max_position_weight": 0.12,
        "turnover_score_threshold": 6.0,
        "max_same_industry": 3,  # P1 驗證：2→3（settings.yaml 為準）
        "exposure": {
            "risk_on": 0.96,
            "caution": 0.70,
            "risk_off": 0.35,
        },
        "score_weights": {
            "price_momentum": 0.55,     # P1-P3 grid search 最佳
            "trend_quality": 0.20,
            "revenue_momentum": 0.25,
            "institutional_flow": 0.00,  # 已停用（P2 測試績效下降）
            # Phase A2 Step 2: new factor slots (default 0.0 for this profile too)
            "high_proximity": 0.0,
            "pead_eps": 0.0,
            "margin_short_ratio": 0.0,
            "revenue_momentum_v2": 0.0,
            "foreign_investor_v2": 0.0,
        },
    },
    "tw_6m_defensive": {
        "label": "Taiwan 6M Defensive",
        "target_holding_months": 6,
        "rebalance_day": 12,
        "top_n": 10,
        "hold_buffer": 4,
        "hold_score_floor": 62.0,
        "max_position_weight": 0.10,
        "turnover_score_threshold": 8.0,
        "max_same_industry": 2,
        "exposure": {
            "risk_on": 1.0,
            "caution": 0.70,
            "risk_off": 0.35,
        },
        "score_weights": {
            # 2026-05-11 R30-6 fix (R30): institutional_flow 0.10 → 0.0
            # 同主 profile（legacy 因子已 R26-R29 確認 fail；redistribute 至
            # price_momentum + revenue_momentum 保持 sum=1）.
            "price_momentum": 0.50,
            "trend_quality": 0.20,
            "revenue_momentum": 0.30,
            "institutional_flow": 0.00,
            # Phase A2 Step 2: new factor slots (default 0.0 for this profile too)
            "high_proximity": 0.0,
            "pead_eps": 0.0,
            "margin_short_ratio": 0.0,
            "revenue_momentum_v2": 0.0,
            "foreign_investor_v2": 0.0,
        },
    },
}


def get_portfolio_config(config: dict) -> dict:
    """Merge defaults with user config."""
    user_config = config.get("portfolio", {})
    profile_name = user_config.get("profile")
    profile = PORTFOLIO_PROFILES.get(profile_name, {})
    if profile_name and not profile:
        logger.warning("Unknown portfolio profile '%s'; using custom overrides only", profile_name)

    merged = DEFAULT_PORTFOLIO_CONFIG.copy()
    merged.update({k: v for k, v in profile.items() if k not in {"label", "exposure", "score_weights"}})
    merged.update({k: v for k, v in user_config.items() if k not in {"exposure", "score_weights"}})
    merged["profile"] = profile_name
    merged["profile_label"] = profile.get("label", "custom")
    merged["exposure"] = {
        **DEFAULT_PORTFOLIO_CONFIG["exposure"],
        **profile.get("exposure", {}),
        **user_config.get("exposure", {}),
    }
    merged["score_weights"] = {
        **DEFAULT_PORTFOLIO_CONFIG["score_weights"],
        **profile.get("score_weights", {}),
        **user_config.get("score_weights", {}),
    }
    weight_sum = sum(merged["score_weights"].values())
    if abs(weight_sum - 1.0) > 0.01:
        logger.warning(
            "score_weights sum to %.2f (expected ~1.0); normalization will adjust",
            weight_sum,
        )
    return merged


def should_rebalance_now(portfolio_config: dict, db, source) -> tuple[bool, str]:
    """Return whether the monthly rebalance should run now."""
    now = datetime.now(TW_TZ)

    if not portfolio_config.get("enabled", True):
        return False, "portfolio disabled"

    if portfolio_config.get("rebalance_frequency", "monthly") != "monthly":
        return False, "only monthly rebalance is supported"

    if now.weekday() >= 5:
        return False, "weekend"

    # 使用 FinMindSource 的假日判斷（如果可用）
    if hasattr(source, "is_trading_day") and not source.is_trading_day():
        return False, "holiday"

    if now.day < int(portfolio_config.get("rebalance_day", 5)):
        return False, "before rebalance day"

    if now.hour < int(portfolio_config.get("rebalance_after_close_hour", 14)):
        return False, "before market close window"

    if source.is_market_open():
        return False, "market still open"

    month_key = now.strftime("%Y-%m")
    if db.has_portfolio_rebalance("tw_stock", month_key):
        return False, f"already rebalanced for {month_key}"

    return True, "rebalance window open"


def build_tw_stock_universe(config: dict, source, portfolio_config: dict) -> list[dict]:
    """Build the Taiwan stock universe from auto-universe + manual overrides."""
    manual_entries = {
        item["symbol"]: item
        for item in config.get("symbols", [])
        if item.get("market") == "tw_stock" and item.get("source") == "finmind"
    }
    manual_enabled = [
        item
        for item in manual_entries.values()
        if item.get("enabled", False)
    ]

    if not portfolio_config.get("use_auto_universe", True):
        return manual_enabled

    stock_info = source.fetch_stock_info() if hasattr(source, "fetch_stock_info") else None
    if stock_info is None or stock_info.empty:
        logger.warning("Auto universe unavailable; falling back to manual symbols")
        return manual_enabled

    # Universe selection: rank by trading activity (close × volume).
    # This is the official strategy spec — not a fallback.
    # Momentum strategies perform better in actively-traded stocks.
    # market_value is kept for monitoring/dashboard only, not for universe selection.
    candidates = _prepare_auto_universe_by_size_proxy(
        stock_info, source, portfolio_config
    )
    if not candidates:
        logger.warning("Auto universe returned no candidates; falling back to manual symbols")
        return manual_enabled

    universe: list[dict] = []
    seen: set[str] = set()
    for row in candidates:
        symbol = row["symbol"]
        if symbol in seen:
            continue
        override = manual_entries.get(symbol, {})
        if symbol in manual_entries and override.get("enabled") is False:
            continue
        universe.append(
            {
                "name": override.get("name", row["name"]),
                "market": "tw_stock",
                "source": "finmind",
                "symbol": symbol,
                "timeframe": override.get("timeframe", "D"),
                "enabled": True,
                "strategy": override.get("strategy", {}),
                "industry": row.get("industry", ""),
            }
        )
        seen.add(symbol)

    for item in manual_enabled:
        if item["symbol"] in seen:
            continue
        universe.append(item)

    return universe


def run_tw_stock_portfolio_rebalance(
    config: dict,
    source,
    db,
    portfolio_config: dict,
) -> dict | None:
    """Build a Taiwan stock portfolio snapshot and target weights."""
    as_of = datetime.now(TW_TZ)
    universe = build_tw_stock_universe(config, source, portfolio_config)
    if not universe:
        logger.warning("No enabled Taiwan stock symbols configured")
        return None

    default_strategy = config.get("default_strategy", {})

    # 先算 market_view，讓 eligibility filter 可依市場狀態調整門檻
    market_view = _analyze_market_proxy(source, default_strategy, portfolio_config)
    market_signal = market_view["signal"]

    analyses = _batch_precompute_and_analyze(
        universe, source, default_strategy, portfolio_config, as_of, market_signal,
    )

    # 安全閥：分析成功率太低時不執行再平衡
    min_eligible_ratio = float(portfolio_config.get("min_eligible_ratio", 0.3))
    success_count = sum(1 for a in analyses if "analysis_error" not in str(a.get("filters", [])))
    if len(analyses) > 0 and success_count / len(analyses) < min_eligible_ratio:
        logger.error(
            "Only %d/%d symbols analyzed successfully (%.0f%%), below threshold %.0f%%. Skipping rebalance.",
            success_count, len(analyses),
            success_count / len(analyses) * 100,
            min_eligible_ratio * 100,
        )
        return None

    ranked = _rank_analyses(analyses, portfolio_config, market_view=market_view)
    current_positions = {
        row["symbol"]: row
        for row in db.get_portfolio_positions("tw_stock")
    }
    selection = _select_positions(ranked, current_positions, portfolio_config, market_view)

    top_preview = [
        {
            "rank": item["rank"],
            "symbol": item["symbol"],
            "name": item["name"],
            "score": item["portfolio_score"],
            "regime": item.get("regime", ""),
            "momentum_12_1": item.get("momentum_12_1"),
            "revenue_yoy": item.get("revenue_yoy"),
        }
        for item in ranked[: min(10, len(ranked))]
    ]

    # P0-4: 完整 ranked universe（每檔因子原始值 + percentile + 篩選結果）
    full_ranked = [
        {
            "rank": item.get("rank"),
            "symbol": item["symbol"],
            "name": item.get("name", ""),
            "industry": item.get("industry", ""),
            "eligible": item.get("eligible", False),
            "filters": item.get("filters", []),
            "portfolio_score": item.get("portfolio_score", 0),
            "rank_components": item.get("rank_components", {}),
            "price_momentum_raw": item.get("price_momentum_raw"),
            "trend_quality_raw": item.get("trend_quality_raw"),
            "revenue_raw": item.get("revenue_raw"),
            "institutional_raw": item.get("institutional_raw"),
            "momentum_12_1": item.get("momentum_12_1"),
            "revenue_yoy": item.get("revenue_yoy"),
            "close": item.get("close"),
        }
        for item in ranked
    ]

    # P0-4: universe snapshot（記錄本次分析的 universe 組成）
    universe_snapshot = [
        {"symbol": sym["symbol"], "name": sym.get("name", ""), "industry": sym.get("industry", "")}
        for sym in universe
    ]

    # P0-4: config hash
    config_hash = compute_config_hash(portfolio_config)

    snapshot = {
        "market": "tw_stock",
        "strategy_mode": "tw_stock_portfolio",
        "portfolio_profile": portfolio_config.get("profile"),
        "portfolio_profile_label": portfolio_config.get("profile_label", "custom"),
        "target_holding_months": portfolio_config.get("target_holding_months"),
        "rebalance_date": as_of.strftime("%Y-%m-%d"),
        "month_key": as_of.strftime("%Y-%m"),
        "market_regime": market_view["regime"],
        "market_regime_display": market_view["regime_display"],
        "market_signal": market_view["signal"],
        "market_proxy_symbol": market_view["symbol"],
        "gross_exposure": selection["gross_exposure"],
        "cash_weight": selection["cash_weight"],
        "total_candidates": len(analyses),
        "eligible_candidates": len([item for item in ranked if item.get("eligible")]),
        "selected_count": len(selection["positions"]),
        "positions": selection["positions"],
        "entries": selection["entries"],
        "holds": selection["holds"],
        "exits": selection["exits"],
        "ranking": top_preview,
        "notes": selection["notes"]
        + [
            f"profile={portfolio_config.get('profile') or 'custom'}",
            f"profile_label={portfolio_config.get('profile_label', 'custom')}",
            f"target_holding_months={portfolio_config.get('target_holding_months')}",
            f"universe_mode={'auto' if portfolio_config.get('use_auto_universe', True) else 'manual'}",
            f"universe_size={len(universe)}",
            f"analysis_success_rate={success_count}/{len(analyses)}",
        ],
        # P0-4: 研究可重現性欄位
        "config_hash": config_hash,
        "strategy_version": f"{portfolio_config.get('profile', 'custom')}@{config_hash}",
        "full_ranked": full_ranked,
        "universe_snapshot": universe_snapshot,
        "data_as_of": as_of.isoformat(),
        "fallback_notes": [],
    }
    return snapshot


def _analyze_market_proxy(source, default_strategy: dict, portfolio_config: dict) -> dict:
    """Estimate Taiwan market risk state using a proxy ETF."""
    symbol = portfolio_config.get("market_proxy_symbol", "0050")
    limit = max(int(portfolio_config.get("history_limit", 320)), 120)
    strategy = {**default_strategy}
    df = source.fetch_ohlcv(symbol, "D", limit)
    if df is None or len(df) < max(strategy.get("sma_slow", 60), 60):
        return {
            "symbol": symbol,
            "regime": "ranging",
            "regime_display": get_regime_display("ranging"),
            "signal": "caution",
        }

    # Forward-adjust stock splits so SMA/ADX calculations are not corrupted
    # by price discontinuities (e.g. 0050 1:4 split in 2025-06).
    df = df.copy()
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = adjust_splits(df[col])

    df = calculate_indicators(df, strategy)
    latest = df.iloc[-1]
    regime = detect_regime(df)
    close = float(latest["close"])
    sma_fast = float(latest.get("sma_fast", close))
    sma_slow = float(latest.get("sma_slow", close))

    if close < sma_slow or regime == "trending_down":
        signal = "risk_off"
    elif close < sma_fast or regime == "ranging":
        signal = "caution"
    else:
        signal = "risk_on"

    return {
        "symbol": symbol,
        "regime": regime,
        "regime_display": get_regime_display(regime),
        "signal": signal,
    }


def _prepare_auto_universe_by_size_proxy(
    stock_info: pd.DataFrame, source, portfolio_config: dict
) -> list[dict]:
    """Fallback: 用 close×volume 20日均值取代付費 market_value API 做 size 排序。"""
    working = stock_info.copy()
    if "stock_id" not in working.columns:
        return []

    working["stock_id"] = working["stock_id"].astype(str).str.strip()
    working = working[working["stock_id"].str.fullmatch(r"(?:\d{4}|00\d{4})")]

    if portfolio_config.get("exclude_etf", True):
        working = working[~working["stock_id"].str.startswith("00")]

    allowed_markets = {
        str(item).lower()
        for item in portfolio_config.get("auto_universe_markets", ["twse", "tpex"])
    }
    if allowed_markets and "type" in working.columns:
        working = working[
            working["type"].astype(str).str.lower().apply(
                lambda value: any(token in value for token in allowed_markets)
            )
        ]

    excluded_industries = [
        str(item).lower()
        for item in portfolio_config.get("auto_universe_exclude_industries", [])
    ]
    if excluded_industries and "industry_category" in working.columns:
        working = working[
            ~working["industry_category"].astype(str).str.lower().apply(
                lambda value: any(keyword in value for keyword in excluded_industries)
            )
        ]

    include_symbols = {
        str(s) for s in portfolio_config.get("auto_universe_include_symbols", [])
    }
    exclude_symbols = {
        str(s) for s in portfolio_config.get("auto_universe_exclude_symbols", [])
    }
    if include_symbols:
        working = working[working["stock_id"].isin(include_symbols)]
    if exclude_symbols:
        working = working[~working["stock_id"].isin(exclude_symbols)]

    # --- TWSE 成交金額預篩（與 backtest universe.py 同規格，live/backtest parity）---
    # 先把 ~1900 支縮到流動性前 N 名（預設 400），再算 size_proxy top 80。
    # 這一層原本只在 backtest 有，live 少了會造成實盤選股與回測模擬不一致。
    pre_filter_size = int(portfolio_config.get("auto_universe_pre_filter_size", 0) or 0)
    if pre_filter_size > 0 and len(working) > pre_filter_size:
        try:
            from src.data.twse_scraper import fetch_combined_turnover
            from datetime import datetime as _dt
            ohlcv_src = source.fetch_ohlcv if hasattr(source, "fetch_ohlcv") else None
            candidate_ids = working["stock_id"].astype(str).tolist()
            _turnover = fetch_combined_turnover(
                _dt.now(),
                ohlcv_source=ohlcv_src,
                stock_ids=candidate_ids,
            )
        except Exception as _exc:
            logger.warning("Live pre-filter: TWSE scraper failed: %s", _exc)
            _turnover = {}

        if _turnover:
            min_coverage = float(
                portfolio_config.get("auto_universe_pre_filter_min_coverage", 0.80)
            )
            coverage = len(_turnover) / max(len(working), 1)
            if coverage < min_coverage:
                raise RuntimeError(
                    f"Live pre-filter coverage too low: "
                    f"{len(_turnover)}/{len(working)} ({coverage:.1%}) "
                    f"< min {min_coverage:.0%}. Cache likely incomplete."
                )
            working["_pre_turnover"] = working["stock_id"].map(_turnover).fillna(0.0)
            working = (
                working
                .sort_values(["_pre_turnover", "stock_id"], ascending=[False, True])
                .head(pre_filter_size)
                .drop(columns=["_pre_turnover"])
                .reset_index(drop=True)
            )
            logger.info(
                "Live pre-filter: → %d stocks (coverage %.1f%%)",
                len(working), coverage * 100,
            )
        else:
            raise RuntimeError(
                "Live pre-filter: TWSE turnover unavailable — "
                "refusing to run live selection on full market without pre-filter."
            )

    # 計算 size proxy: close × volume 的 20 日均值
    # Hard-fail guard: source 必須可用，否則 live 會退化成字典序 top-N。
    if source is None:
        raise RuntimeError(
            "Live universe selection requires a data source; got source=None. "
            "Refusing to run size-proxy ranking on stock_id lexicographic order."
        )
    if not hasattr(source, "fetch_ohlcv"):
        raise RuntimeError(
            f"Live universe selection requires source.fetch_ohlcv; "
            f"got {type(source).__name__} without it."
        )

    size_proxy: dict[str, float] = {}
    n_success = 0
    failed_samples: list[str] = []
    for _, row in working.iterrows():
        sym = str(row["stock_id"])
        try:
            df = source.fetch_ohlcv(sym, "D", 30)
            if df is not None and len(df) >= 5:
                turnover = (df["close"] * df["volume"]).tail(20).mean()
                if pd.notna(turnover):
                    size_proxy[sym] = float(turnover)
                    n_success += 1
                    continue
            size_proxy[sym] = 0.0
            if len(failed_samples) < 10:
                failed_samples.append(sym)
        except Exception as _exc:
            size_proxy[sym] = 0.0
            if len(failed_samples) < 10:
                failed_samples.append(f"{sym}:{type(_exc).__name__}")

    total = max(len(working), 1)
    success_rate = n_success / total
    min_success = float(
        portfolio_config.get("auto_universe_size_proxy_min_success", 0.60)
    )
    if success_rate < min_success:
        raise RuntimeError(
            f"Live size-proxy success rate too low: "
            f"{n_success}/{total} ({success_rate:.1%}) < min {min_success:.0%}. "
            f"Sample failures: {failed_samples[:5]}. "
            f"Refusing to fall back to stock_id lexicographic order."
        )

    working["_size_proxy"] = working["stock_id"].map(size_proxy).fillna(0.0)
    working = working.sort_values(["_size_proxy", "stock_id"], ascending=[False, True])

    limit = int(portfolio_config.get("auto_universe_size", 80) or 0)
    if limit > 0:
        working = working.head(limit)

    result = []
    for _, row in working.iterrows():
        result.append(
            {
                "symbol": str(row["stock_id"]),
                "name": str(row.get("stock_name", row["stock_id"])),
                "market_value": row["_size_proxy"],
                "industry": str(row.get("industry_category", "")),
                "type": str(row.get("type", "")),
            }
        )
    logger.info(
        "Built universe via size proxy (close×volume): %d candidates", len(result)
    )
    return result


def _analyze_symbol(
    sym_config: dict,
    source,
    default_strategy: dict,
    portfolio_config: dict,
    as_of: datetime,
    *,
    market_signal: str = "caution",
) -> dict:
    """Analyze one stock and return raw factors used by the portfolio ranker."""
    symbol = sym_config["symbol"]
    name = sym_config.get("name", symbol)
    industry = sym_config.get("industry", "")
    history_limit = max(
        int(portfolio_config.get("history_limit", 320)),
        int(default_strategy.get("sma_slow", 60)) + 30,
    )
    strategy = {**default_strategy, **sym_config.get("strategy", {})}

    df = source.fetch_ohlcv(symbol, "D", history_limit)
    if df is None or len(df) < MIN_OHLCV_BARS:
        return {
            "symbol": symbol,
            "name": name,
            "industry": industry,
            "eligible": False,
            "filters": ["insufficient_history"],
        }

    df = calculate_indicators(df, strategy)
    regime = detect_regime(df)
    latest = df.iloc[-1]

    close = float(latest["close"])
    sma_fast = _float_or_none(latest.get("sma_fast"))
    sma_slow = _float_or_none(latest.get("sma_slow"))
    structure = int(latest.get("structure", 0) or 0)

    avg_turnover_20 = float((df["close"] * df["volume"]).tail(20).mean())
    volatility_20d = float(df["close"].pct_change().tail(20).std()) if len(df) >= 21 else None
    momentum_3m = _period_return(df["close"], MOMENTUM_PERIOD_3M)
    momentum_6m = _period_return(df["close"], MOMENTUM_PERIOD_6M)
    momentum_12m = _period_return(df["close"], MOMENTUM_PERIOD_12M)
    momentum_12_1 = _skip_period_return(df["close"], MOMENTUM_PERIOD_12M, MOMENTUM_SKIP_DAYS)

    price_momentum_raw = _weighted_average(
        [
            (momentum_3m, 0.20),
            (momentum_6m, 0.35),
            (momentum_12_1, 0.45),
        ]
    )
    trend_quality_raw = _trend_quality(close, sma_fast, sma_slow, structure, regime)

    institutional_result = {"score": 0, "detail": "disabled"}
    inst_weight = float(portfolio_config.get("score_weights", {}).get("institutional_flow", 0))
    if inst_weight > 0 and strategy.get("use_institutional", True) and hasattr(source, "fetch_institutional"):
        # PIT cover note (audit 2026-05-02 A.2): when `source` is a
        # `_DataSlicer` (the backtest engine wires it that way at engine.py:421),
        # `fetch_institutional` is the slicer's PIT-correct override
        # (engine.py:196) — so the call below is already truncated by
        # `_truncate_by_date_col` against the slicer's `set_as_of`. Live
        # callers pass the raw `FinMindSource` and there is no `as_of` to
        # enforce. The pre-audit comment claiming "未傳遞 as_of 截斷" was stale.
        institutional_df = source.fetch_institutional(symbol)
        institutional_result = score_institutional(institutional_df)
    institutional_raw = float(institutional_result.get("score", 0))

    # 品質因子（ROE × 毛利率 → 0-1 分數）
    # 只有權重 > 0 時才呼叫 API，避免無效的資料成本
    quality_raw = None
    score_weights = portfolio_config.get("score_weights", {})
    if float(score_weights.get("quality", 0)) > 0 and hasattr(source, "fetch_financial_quality"):
        # Audit 2026-05-02 A.2: `fetch_financial_quality` returns a single
        # latest-quarter snapshot dict (not a time-series), so it has no PIT
        # entry-point. In backtest the snapshot reflects whatever quarter
        # the cache was filled at — typically newer than `as_of` → look-ahead.
        # We refuse to silently use it; re-enabling the quality factor in
        # backtest requires implementing a `fetch_financial_quality_history`
        # equivalent that returns a per-quarter time series PIT-truncated by
        # the slicer.
        if portfolio_config.get("_backtest_context", False):
            raise NotImplementedError(
                "quality factor weight>0 in backtest_context but "
                "fetch_financial_quality is a single-snapshot dict (no PIT "
                "entry-point); implement fetch_financial_quality_history "
                "before re-enabling. Live mode is unaffected."
            )
        fq = source.fetch_financial_quality(symbol)
        if fq is not None:
            roe = fq.get("roe")
            gm = fq.get("gross_margin")
            if roe is not None and gm is not None:
                # ROE clamp 到 0-0.5（0%~50%），毛利率 clamp 到 0-1
                roe_score = max(0.0, min(roe, 0.5)) / 0.5
                gm_score = max(0.0, min(gm, 1.0))
                quality_raw = roe_score * 0.6 + gm_score * 0.4

    revenue_yoy = None
    revenue_accel = None
    revenue_raw = None
    rm_weight = float(portfolio_config.get("score_weights", {}).get("revenue_momentum", 0))
    if rm_weight > 0 and portfolio_config.get("use_monthly_revenue", True) and hasattr(source, "fetch_month_revenue"):
        revenue_df = source.fetch_month_revenue(
            symbol,
            months=int(portfolio_config.get("monthly_revenue_months", 15)),
        )
        revenue_yoy, revenue_accel, revenue_raw = _monthly_revenue_momentum(revenue_df, as_of)

    filters: list[str] = []
    if portfolio_config.get("exclude_etf", True) and str(symbol).startswith("00"):
        filters.append("etf_excluded")
    if close < float(portfolio_config.get("min_price", 20.0)):
        filters.append("price_below_floor")
    if avg_turnover_20 < float(portfolio_config.get("min_avg_turnover", 50_000_000.0)):
        filters.append("turnover_too_low")

    # 趨勢 / 動能門檻依市場狀態分級：
    # risk_on / caution：嚴格（原邏輯）
    # risk_off：放寬，避免空頭市場 eligible 歸零
    if market_signal == "risk_off":
        # 空頭放寬：只要求 close > SMA60 × 0.85（容許跌破 15%）
        if sma_slow is not None and close <= sma_slow * 0.85:
            filters.append("below_sma_slow_relaxed")
        # 不檢查 SMA20 > SMA60（空頭幾乎不可能）
        # momentum_6m 容許至 -15%
        if momentum_6m is None or momentum_6m <= -0.15:
            filters.append("momentum_6m_deep_negative")
    else:
        if sma_slow is None or close <= sma_slow:
            filters.append("below_sma_slow")
        if sma_fast is None or sma_slow is None or sma_fast <= sma_slow:
            filters.append("trend_not_aligned")
        if momentum_6m is None or momentum_6m <= 0:
            filters.append("momentum_6m_non_positive")

    return {
        "symbol": symbol,
        "name": name,
        "industry": industry,
        "close": close,
        "regime": regime,
        "regime_display": get_regime_display(regime),
        "avg_turnover_20": avg_turnover_20,
        "momentum_3m": momentum_3m,
        "momentum_6m": momentum_6m,
        "momentum_12m": momentum_12m,
        "momentum_12_1": momentum_12_1,
        "price_momentum_raw": price_momentum_raw,
        "trend_quality_raw": trend_quality_raw,
        "revenue_yoy": revenue_yoy,
        "revenue_accel": revenue_accel,
        "revenue_raw": revenue_raw,
        "institutional_raw": institutional_raw,
        "institutional_detail": institutional_result.get("detail", ""),
        "quality_raw": quality_raw,
        "volatility_20d": volatility_20d,
        "eligible": len(filters) == 0,
        "filters": filters,
    }


def _resolve_regime_score_weights(
    portfolio_config: dict,
    market_view: dict | None,
) -> dict:
    """Phase A3.1.2 helper: pick factor weight schedule based on regime.

    Returns the weight dict that `_rank_analyses` should use. Precedence:
    1. If market_view is None OR `regime_score_weights` config missing/empty,
       return flat `portfolio_config['score_weights']` (Phase A2 behavior).
    2. If regime_score_weights present AND market_view["regime"] matches a key,
       return that regime-specific weight dict (log the active regime).
    3. If regime doesn't match any key (unknown state), fall back to flat
       score_weights and log a warning.
    """
    base_weights = portfolio_config.get("score_weights", {}) or {}
    regime_weights_map = portfolio_config.get("regime_score_weights") or {}

    if not regime_weights_map or not market_view:
        return base_weights

    regime = market_view.get("regime")
    if regime and regime in regime_weights_map:
        selected = regime_weights_map[regime]
        logger.info(
            "Regime-aware weighting active: regime=%s, weights=%s",
            regime, selected,
        )
        return selected

    logger.warning(
        "regime_score_weights configured but regime '%s' not in keys %s — "
        "falling back to flat score_weights",
        regime, list(regime_weights_map.keys()),
    )
    return base_weights


def _rank_analyses(
    analyses: list[dict],
    portfolio_config: dict,
    market_view: dict | None = None,
) -> list[dict]:
    """Attach percentile-ranked factor scores and total portfolio score.

    Phase A3.1.2 (2026-04-22): optional regime-aware weighting. If
    portfolio_config has `regime_score_weights` dict AND `market_view` is
    provided with a valid regime, the factor weights used for ranking are
    looked up from `regime_score_weights[regime]` instead of the flat
    `score_weights`. Falls back to flat `score_weights` for:
    - market_view=None (legacy callers)
    - regime_score_weights missing/empty
    - regime key not present in regime_score_weights (unknown regime)

    This gives strategies per-regime factor emphasis without changing the
    default / backward-compat behavior for existing callers and tests.
    """
    if not analyses:
        return []

    score_weights = _resolve_regime_score_weights(portfolio_config, market_view)
    ranked = [dict(item) for item in analyses]

    # 只在可交易 universe 內做百分位排名，避免 ineligible 股票稀釋分數
    eligible_items = [item for item in ranked if item.get("eligible", False)]
    ineligible_items = [item for item in ranked if not item.get("eligible", False)]
    logger.info(
        "Ranking %d eligible stocks (excluded %d ineligible from ranking pool)",
        len(eligible_items), len(ineligible_items),
    )

    available_metrics = {
        "price_momentum": "price_momentum_raw",
        "trend_quality": "trend_quality_raw",
        "revenue_momentum": "revenue_raw",
        "institutional_flow": "institutional_raw",
        "quality": "quality_raw",
        # Phase A2 Step 2: 5 new factors (batch-computed in _batch_precompute_and_analyze)
        "high_proximity": "high_proximity_raw",
        "pead_eps": "pead_eps_raw",
        "margin_short_ratio": "margin_short_ratio_raw",
        "revenue_momentum_v2": "revenue_momentum_v2_raw",
        "foreign_investor_v2": "foreign_investor_v2_raw",
    }

    # Phase A3.1.1: opt-in sector-neutral ranking per factor.
    # Config:  portfolio_config["sector_neutral_metrics"] = ["high_proximity", ...]
    # Default: [] (pure cross-sectional rank, Phase A2 behavior preserved)
    sector_neutral_metrics = set(
        portfolio_config.get("sector_neutral_metrics", []) or []
    )

    metric_ranks: dict[str, dict[str, float]] = {}
    active_weights: dict[str, float] = {}
    silent_dropped: list[str] = []
    for score_name, metric_key in available_metrics.items():
        weight = float(score_weights.get(score_name, 0))
        if weight <= 0:
            continue
        use_sector_neutral = score_name in sector_neutral_metrics
        ranks, has_real_data = _metric_ranks(
            eligible_items, metric_key, sector_neutral=use_sector_neutral,
        )
        metric_ranks[score_name] = ranks
        if has_real_data:
            active_weights[score_name] = weight
        else:
            silent_dropped.append(score_name)

    # Phase A2 Step 1.5.4 silent renormalize guard: factors with weight>0 but
    # no real data (>50% NaN/Inf) would be quietly excluded from active_weights,
    # then weight_sum renormalizes — producing backtest outputs that look clean
    # but didn't use the intended factor set. Backtest context raises; live path
    # only warns (won't block real rebalance on transient data quality issues).
    if silent_dropped:
        msg = (
            f"Factors with weight>0 but no real data (>50% NaN/Inf): {silent_dropped}. "
            f"Silent renormalization would produce false-positive results."
        )
        if portfolio_config.get("_backtest_context", False):
            raise RuntimeError(
                msg + " Seed cache or adjust weights before rerunning backtest."
            )
        else:
            logger.warning(msg)

    weight_sum = sum(active_weights.values()) or 1.0
    logger.info(
        "Active factors: %s (weight_sum=%.2f)",
        {k: f"{v/weight_sum:.0%}" for k, v in active_weights.items()},
        weight_sum,
    )

    # Audit 2026-05-02 A.1 fix: per-symbol weight_sum re-normalization.
    # `_metric_ranks` returns None for symbols missing factor data (was 0.5 median
    # imputation pre-fix). Each symbol now gets normalized over only the factors
    # it actually has, with `min_factor_coverage_per_symbol` floor below which
    # the symbol's score collapses to 0 (forced ineligible by ranking).
    min_coverage = float(
        portfolio_config.get("min_factor_coverage_per_symbol", 0.6)
    )
    if not active_weights:
        # Nothing to score against; preserve legacy behavior of zero score.
        for item in eligible_items:
            item["rank_components"] = {}
            item["portfolio_score"] = 0.0
            item["score_dropped_factors"] = []
    else:
        total_active_weight = sum(active_weights.values())
        for item in eligible_items:
            symbol = item["symbol"]
            score_total = 0.0
            per_symbol_weight = 0.0
            components: dict[str, float] = {}
            dropped: list[str] = []
            for score_name, weight in active_weights.items():
                rank_value = metric_ranks[score_name].get(symbol)
                if rank_value is None:
                    dropped.append(score_name)
                    continue
                components[score_name] = round(rank_value * 100.0, 2)
                score_total += rank_value * weight
                per_symbol_weight += weight
            item["rank_components"] = components
            item["score_dropped_factors"] = dropped
            coverage_ratio = (
                per_symbol_weight / total_active_weight
                if total_active_weight > 0 else 0.0
            )
            if per_symbol_weight <= 0 or coverage_ratio < min_coverage:
                # Below coverage floor: force score to 0 so the symbol cannot
                # win `top_n` solely on the factors that happened to load.
                item["portfolio_score"] = 0.0
                item["score_below_coverage"] = True
            else:
                item["portfolio_score"] = round(
                    (score_total / per_symbol_weight) * 100.0, 2
                )
                item["score_below_coverage"] = False

    for item in ineligible_items:
        item["rank_components"] = {}
        item["portfolio_score"] = 0.0

    all_ranked = eligible_items + ineligible_items
    all_ranked.sort(
        key=lambda item: (
            not item.get("eligible", False),
            -(item.get("portfolio_score") or 0.0),
            -(item.get("trend_quality_raw") or 0.0),
        )
    )

    for index, item in enumerate(all_ranked, start=1):
        item["rank"] = index

    top_display = min(10, len(all_ranked))
    if top_display > 0:
        logger.info(
            "Top %d ranked: %s",
            top_display,
            [(r["symbol"], r.get("portfolio_score", 0)) for r in all_ranked[:top_display]],
        )

    return all_ranked


def _safe_fetch(fetch_func, symbol: str, *extra_args, **kwargs):
    """Per-symbol fetch with exception isolation for universe-batch factor precompute.

    CRITICAL (external audit Round 14 P1-1): must NOT catch _BacktestCacheMissError —
    that exception signals backtest cache-miss that must propagate so the user
    seeds cache first; catching would produce silent-wrong results.
    Other exceptions (network / transient API errors) are logged and return
    None so the batch factor can drop this symbol gracefully.

    extra_args / kwargs let callers pass additional positional/keyword args
    (e.g. ``_safe_fetch(source.fetch_ohlcv, sym, "D")`` because
    ``_DataSlicer.fetch_ohlcv(symbol, timeframe)`` requires timeframe).
    """
    try:
        return fetch_func(symbol, *extra_args, **kwargs)
    except _BacktestCacheMissError:
        raise  # MUST NOT catch in backtest mode
    except Exception as exc:
        logger.warning(
            "batch fetch failed for %s (%s): %s",
            symbol, getattr(fetch_func, "__name__", "fetch"), exc,
        )
        return None


def _bulk_fetch_latest_market_value(
    source,
    as_of: pd.Timestamp | None = None,
) -> dict[str, float]:
    """Bulk fetch market_value (PIT-aware after R30 architecture cleanup).

    2026-05-11 R30 cleanup (R29 finding 2): aligned with IC pipeline +
    portfolio path issued_capital helper to use single source-of-truth PIT
    helpers from ``src.data.pit_helpers``. Adds ``as_of`` keyword (default
    None = today/live mode).

    Behavior:
        - ``as_of=None`` (live mode): falls through to legacy
          ``source.fetch_market_value(days=10)`` for fast latest snapshot.
        - ``as_of=<historical>``: uses disk cache PIT panel +
          ``_market_value_asof()`` for PIT-correct lookup. Caller (backtest
          replay) needs cache populated for the target date.

    external audit Round 14 P0-1 FIX context: ``fetch_market_value(days=10)`` takes
    ``days`` (int) — does NOT accept a symbol argument. Returns full-market
    DataFrame (stock_id, date, market_value).
    """
    # Live mode: as_of None or today → fast latest snapshot via source
    if as_of is None or pd.Timestamp(as_of).date() >= pd.Timestamp.today().date():
        try:
            mv_panel = source.fetch_market_value()  # default days=10
        except _BacktestCacheMissError:
            raise
        except Exception as exc:
            logger.warning("bulk market_value fetch failed: %s", exc)
            return {}
        if mv_panel is None or mv_panel.empty:
            return {}
        latest = mv_panel.sort_values("date").groupby("stock_id").tail(1)
        return {
            str(row["stock_id"]): float(row["market_value"])
            for _, row in latest.iterrows()
            if pd.notna(row.get("market_value"))
        }

    # Backtest mode: PIT-asof from disk cache panel
    from src.data.pit_helpers import _load_market_value_panel, _market_value_asof
    cache_dir = Path(resolve_cache_dir())
    panel = _load_market_value_panel(cache_dir)
    if panel.empty:
        logger.warning(
            "market_value PIT panel empty — backtest as_of=%s lookup will return {}",
            as_of,
        )
        return {}
    return _market_value_asof(panel, pd.Timestamp(as_of))


def _load_issued_capital_dict(
    universe_symbols: list[str],
    as_of: pd.Timestamp | None = None,
) -> dict[str, float]:
    """Load issued_shares for universe symbols (R28-2 PIT-aligned).

    2026-05-10 R28-2 修法: was reading global latest snapshot (one-shot
    dict). Now imports the same panel + asof helper as IC pipeline
    (`scripts._factor_ic_helpers`), so portfolio/backtest path gets the same
    PIT discipline (or fallback static-snapshot warning when cache lacks date).

    external audit Round 14 P0-2 history: explicit dtype cast (stock_id->str,
    issued_shares->float) + coverage warning when universe symbols missing
    from cache (e.g. ETFs like 0050/0056, or newly listed stocks).

    Args:
        universe_symbols: list of stock_ids to look up
        as_of: target date for PIT lookup. None = today (live mode).
    """
    from scripts._factor_ic_helpers import (
        _load_issued_capital_panel,
        _issued_capital_asof,
    )

    cache_dir = Path(resolve_cache_dir())
    panel = _load_issued_capital_panel(cache_dir)
    if panel.empty:
        logger.warning(
            "issued_capital panel empty — margin_short_ratio batch will skip",
        )
        return {}

    target = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()
    if target.tz is not None:
        target = target.tz_convert(None)

    asof_dict = _issued_capital_asof(panel, target)
    out: dict[str, float] = {
        sym: float(v)
        for sym, v in asof_dict.items()
        if v is not None and v > 0
    }

    missing = [s for s in universe_symbols if s not in out]
    if missing:
        logger.warning(
            "margin_short_ratio: %d/%d universe symbols missing issued_shares "
            "at as_of=%s (first 5: %s) — those symbols will be dropped from the batch factor",
            len(missing), len(universe_symbols), target.date(), missing[:5],
        )
    return out


def _compute_universe_batch_factors(
    universe_symbols: list[str],
    source,
    portfolio_config: dict,
    as_of_ts: pd.Timestamp,
) -> dict[str, dict[str, float]]:
    """Pre-compute 5 Phase A2 universe-batch factor scores.

    Each factor is computed only when its weight > 0 (cost optimization — we
    never fetch data for disabled factors). Returns a mapping::

        {factor_name: {symbol: score, ...}}

    Missing factor keys mean weight was 0 and the factor was skipped.
    Downstream caller injects these scores as ``<factor>_raw`` onto each
    per-symbol analysis dict.
    """
    sw = portfolio_config.get("score_weights", {})
    out: dict[str, dict[str, float]] = {}

    if float(sw.get("high_proximity", 0)) > 0:
        # fetch_ohlcv(symbol, timeframe, limit) — _DataSlicer.fetch_ohlcv defaults
        # to limit=100 (tail of full cache), which is too short for 52W High
        # Proximity (requires 252 rolling_max + min_history=126). Pass 500 for
        # comfortable margin (~2 trading years of history post-as_of slice).
        ohlcv_by_sym = {s: _safe_fetch(source.fetch_ohlcv, s, "D", 500) for s in universe_symbols}
        series = compute_high_proximity_universe(ohlcv_by_sym, as_of=as_of_ts)
        out["high_proximity"] = series.to_dict()

    if float(sw.get("pead_eps", 0)) > 0:
        eps_by_sym = {s: _safe_fetch(source.fetch_quarterly_eps, s) for s in universe_symbols}
        series = compute_pead_eps_universe(eps_by_sym, as_of=as_of_ts)
        out["pead_eps"] = series.to_dict()

    if float(sw.get("margin_short_ratio", 0)) > 0:
        margin_by_sym = {s: _safe_fetch(source.fetch_margin_short, s) for s in universe_symbols}
        # 2026-05-10 R28-2: pass as_of so issued_shares is PIT-aligned with
        # IC pipeline (still falls back to static snapshot when cache lacks date
        # column — same approximation as IC pipeline, consistent across paths).
        issued_by_sym = _load_issued_capital_dict(universe_symbols, as_of=as_of_ts)
        series = compute_margin_short_ratio_universe(
            margin_by_sym, issued_by_symbol=issued_by_sym, as_of=as_of_ts,
        )
        out["margin_short_ratio"] = series.to_dict()

    if float(sw.get("revenue_momentum_v2", 0)) > 0:
        rev_by_sym = {s: _safe_fetch(source.fetch_month_revenue, s) for s in universe_symbols}
        series = compute_revenue_momentum_v2_universe(rev_by_sym, as_of=as_of_ts)
        out["revenue_momentum_v2"] = series.to_dict()

    if float(sw.get("foreign_investor_v2", 0)) > 0:
        # 2026-05-10 P1-3 (R27): foreign_investor_v2 v2 API requires
        # close_by_symbol for dollar-denominated cum_ratio + rank_stability
        # (P0-B 修法). Without it both sub-signals are skipped and
        # covered_weight drops below 0.5 threshold → universe goes empty.
        # 2026-05-11 R30 4-path PIT cleanup (R29 finding 2):
        # _bulk_fetch_latest_market_value now PIT-aware with as_of kwarg.
        # Live mode (as_of=today): fast source.fetch_market_value path.
        # Backtest mode (as_of=historical): disk cache panel + asof lookup.
        inst_by_sym = {s: _safe_fetch(source.fetch_three_institutional, s) for s in universe_symbols}
        mv_by_sym = _bulk_fetch_latest_market_value(source, as_of=as_of_ts)
        # Build close panel: reuse fetched ohlcv if high_proximity already loaded
        # else fetch fresh.
        if "high_proximity" in out and "ohlcv_by_sym" in dir():
            close_by_sym = {s: df["close"].copy() for s, df in ohlcv_by_sym.items() if df is not None and "close" in df.columns}
        else:
            ohlcv_for_fb = {s: _safe_fetch(source.fetch_ohlcv, s, "D", 500) for s in universe_symbols}
            close_by_sym = {s: df["close"].copy() for s, df in ohlcv_for_fb.items() if df is not None and "close" in df.columns}
        series = compute_foreign_investor_v2_universe(
            inst_by_sym,
            market_value_by_symbol=mv_by_sym,
            as_of=as_of_ts,
            close_by_symbol=close_by_sym,
        )
        out["foreign_investor_v2"] = series.to_dict()

    return out


def _batch_precompute_and_analyze(
    universe: list[dict],
    source,
    default_strategy: dict,
    portfolio_config: dict,
    as_of: datetime,
    market_signal: str,
) -> list[dict]:
    """Shared analyze loop for both live and backtest callers.

    Phase A2 Step 1.5: Pure extraction of current per-symbol analyze behavior.
    Phase A2 Step 2: Universe-batch factor precompute runs BEFORE the symbol
    loop; batch scores are injected as ``<factor>_raw`` onto each per-symbol
    analysis dict. Both live and backtest paths pick this up automatically
    (external audit Round 14 P0-1 fix).

    Error handling: per-symbol Exception is caught and logged; the offending
    symbol gets a 5-key stub dict (symbol/name/eligible/filters/industry) with
    eligible=False and a filter containing analysis_error:<exc>. Callers should
    still apply their own min_eligible_ratio guard (live returns None, backtest
    raises — intentionally divergent downstream policy).
    """
    as_of_ts = pd.Timestamp(as_of)
    universe_symbols = [s["symbol"] for s in universe]
    batch_scores = _compute_universe_batch_factors(
        universe_symbols, source, portfolio_config, as_of_ts,
    )

    analyses: list[dict] = []
    for sym_config in universe:
        sym = sym_config["symbol"]
        try:
            analysis = _analyze_symbol(
                sym_config, source, default_strategy, portfolio_config,
                as_of, market_signal=market_signal,
            )
        except Exception as exc:
            logger.warning("Failed to analyze %s: %s", sym, exc)
            analysis = {
                "symbol": sym,
                "name": sym_config.get("name", sym),
                "eligible": False,
                "filters": [f"analysis_error:{exc}"],
                "industry": sym_config.get("industry", ""),
            }
        # Inject batch factor scores as <factor>_raw keys.
        # Missing scores (symbol dropped by factor module due to insufficient
        # data) become None — _rank_analyses treats None as NaN and the silent
        # renormalize guard will surface this (raise in backtest, warn in live).
        for factor_name, scores in batch_scores.items():
            analysis[f"{factor_name}_raw"] = scores.get(sym)
        analyses.append(analysis)
    return analyses


def _select_positions(
    ranked: list[dict],
    current_positions: dict[str, dict],
    portfolio_config: dict,
    market_view: dict,
) -> dict:
    """Convert ranked candidates into target positions with turnover penalty and industry limits."""
    top_n = int(portfolio_config.get("top_n", 5))
    hold_buffer = int(portfolio_config.get("hold_buffer", 2))
    hold_score_floor = float(portfolio_config.get("hold_score_floor", 55.0))
    max_same_industry = int(portfolio_config.get("max_same_industry", 2))
    turnover_score_threshold = float(portfolio_config.get("turnover_score_threshold", 5.0))
    to_remove: set[str] = set()  # 追蹤被產業限制移除的股票

    eligible = [item for item in ranked if item.get("eligible")]
    min_holdings = int(portfolio_config.get("min_holdings", 3))
    if len(eligible) < min_holdings:
        logger.warning(
            "Only %d eligible stocks (min_holdings=%d) — going full cash to avoid concentration risk",
            len(eligible), min_holdings,
        )
        return {
            "positions": [],
            "entries": [],
            "holds": [],
            "exits": [
                {
                    "symbol": sym,
                    "name": prev.get("name", sym),
                    "previous_weight": float(prev.get("target_weight", 0) or 0),
                }
                for sym, prev in current_positions.items()
            ],
            "gross_exposure": 0.0,
            "cash_weight": 1.0,
            "notes": [f"min_holdings_not_met:{len(eligible)}<{min_holdings}"],
        }
    rank_by_symbol = {item["symbol"]: item["rank"] for item in ranked}
    score_by_symbol = {item["symbol"]: item.get("portfolio_score", 0) for item in ranked}
    industry_by_symbol = {item["symbol"]: item.get("industry", "") for item in ranked}

    selected: list[dict] = []
    selected_symbols: set[str] = set()

    # Step 1: 保留現有持倉（hold buffer 內且達標）
    for symbol, previous in current_positions.items():
        candidate = next((item for item in eligible if item["symbol"] == symbol), None)
        if candidate is None:
            continue
        if rank_by_symbol[symbol] <= top_n + hold_buffer and candidate["portfolio_score"] >= hold_score_floor:
            selected.append(candidate)
            selected_symbols.add(symbol)

    selected.sort(key=lambda item: item["rank"])
    if len(selected) > top_n:
        selected = selected[:top_n]
        selected_symbols = {item["symbol"] for item in selected}

    # Log if top-ranked new candidates were excluded by hold buffer
    if len(selected) >= top_n:
        skipped_top = [
            c for c in eligible
            if c["symbol"] not in selected_symbols and c["rank"] <= top_n
        ]
        if skipped_top:
            logger.info(
                "Hold buffer: %d top-ranked new candidates excluded: %s",
                len(skipped_top),
                [c["symbol"] for c in skipped_top],
            )

    # Step 1.5: 產業硬限制 — 超額產業中分數最低者移除
    if max_same_industry > 0:
        industry_groups: dict[str, list[dict]] = {}
        for item in selected:
            ind = item.get("industry", "") or "_unknown"
            industry_groups.setdefault(ind, []).append(item)

        to_remove: set[str] = set()
        for industry, members in industry_groups.items():
            if len(members) <= max_same_industry:
                continue
            members.sort(key=lambda x: x.get("portfolio_score", 0))
            excess = len(members) - max_same_industry
            for i in range(excess):
                to_remove.add(members[i]["symbol"])
                logger.info(
                    "Industry limit: removing %s (%s, score=%.1f) — %s exceeds limit of %d",
                    members[i]["symbol"], members[i].get("name", ""),
                    members[i].get("portfolio_score", 0),
                    industry, max_same_industry,
                )

        if to_remove:
            selected = [item for item in selected if item["symbol"] not in to_remove]
            selected_symbols -= to_remove

    # Step 2: 填入新候選，考慮交易成本門檻 + 產業分散
    _used_replaceable: set[str] = set()  # P0-7: 追蹤已配對的 replaceable 持股
    for candidate in eligible:
        if len(selected) >= top_n:
            break
        if candidate["symbol"] in selected_symbols:
            continue

        # 產業分散檢查
        candidate_industry = candidate.get("industry", "") or "_unknown"
        if max_same_industry > 0:
            same_industry_count = sum(
                1 for s in selected
                if (s.get("industry", "") or "_unknown") == candidate_industry
            )
            if same_industry_count >= max_same_industry:
                continue

        # P0-7 fix: 交易成本門檻 — 逐一配對比較，每次選入後移除已配對的 replaceable
        if current_positions:
            replaceable = [
                (sym, score_by_symbol.get(sym, 0))
                for sym in current_positions
                if sym not in selected_symbols and sym not in _used_replaceable
            ]
            if replaceable:
                # 取剩餘 replaceable 中分數最低者做配對
                weakest_sym, weakest_score = min(replaceable, key=lambda x: x[1])
                if candidate["portfolio_score"] < weakest_score + turnover_score_threshold:
                    continue  # 優勢不夠大，不值得換倉
                _used_replaceable.add(weakest_sym)

        selected.append(candidate)
        selected_symbols.add(candidate["symbol"])

    selected.sort(key=lambda item: item["rank"])

    # 計算曝險
    exposure = float(
        portfolio_config.get("exposure", {}).get(
            market_view["signal"],
            DEFAULT_PORTFOLIO_CONFIG["exposure"]["caution"],
        )
    )

    # 權重分配
    weight_mode = portfolio_config.get("weight_mode", "score_weighted")
    position_weights = _calculate_position_weights(
        selected, exposure,
        float(portfolio_config.get("max_position_weight", 0.20)),
        weight_mode,
    )

    positions = []
    entries = []
    holds = []
    exits = []
    total_weight = 0.0

    for candidate in selected:
        symbol = candidate["symbol"]
        previous = current_positions.get(symbol)
        target_weight = position_weights.get(symbol, 0.0)
        total_weight += target_weight

        action = "ENTER"
        if previous is not None:
            previous_weight = float(previous.get("target_weight", 0) or 0)
            if target_weight < previous_weight - 0.005:
                action = "REDUCE"
            elif target_weight > previous_weight + 0.005:
                action = "ADD"
            else:
                action = "HOLD"

        position = {
            "symbol": symbol,
            "name": candidate["name"],
            "industry": candidate.get("industry", ""),
            "target_weight": target_weight,
            "rank": candidate["rank"],
            "score": candidate["portfolio_score"],
            "action": action,
            "close": candidate.get("close"),
            "regime": candidate.get("regime", ""),
            "regime_display": candidate.get("regime_display", ""),
            "momentum_12_1": candidate.get("momentum_12_1"),
            "revenue_yoy": candidate.get("revenue_yoy"),
            "institutional_detail": candidate.get("institutional_detail", ""),
        }
        positions.append(position)

        if action == "ENTER":
            entries.append(position)
        else:
            holds.append(position)

    for symbol, previous in current_positions.items():
        if symbol in selected_symbols:
            continue
        exits.append(
            {
                "symbol": symbol,
                "name": previous.get("name", symbol),
                "previous_weight": float(previous.get("target_weight", 0) or 0),
            }
        )

    # 估算換倉成本，納入 ENTER / EXIT / ADD / REDUCE 的實際權重變化
    turnover_cost = float(portfolio_config.get("turnover_cost", TW_ROUND_TRIP_COST))
    slippage_bps = float(portfolio_config.get("slippage_bps", 10))  # 對齊 settings.yaml (R19 fix 補)
    estimated_turnover = _estimate_rebalance_turnover(current_positions, positions)
    estimated_cost = estimated_turnover * turnover_cost + estimated_turnover * 2 * (slippage_bps / 10000.0)

    cash_weight = round(max(0.0, 1.0 - total_weight), 4)
    logger.info(
        "Selection result: %d entries, %d holds, %d exits, exposure=%.1f%%, cash=%.1f%%",
        len(entries), len(holds), len(exits), total_weight * 100, cash_weight * 100,
    )
    notes = [
        f"market_state={market_view['signal']}",
        f"gross_exposure_target={exposure:.0%}",
        f"hold_buffer={hold_buffer}",
        f"weight_mode={weight_mode}",
        f"estimated_turnover={estimated_turnover:.1%}",
        f"estimated_cost={estimated_cost:.3%}",
    ]
    if not positions:
        notes.append("no_eligible_positions")

    return {
        "positions": positions,
        "entries": entries,
        "holds": holds,
        "exits": exits,
        "gross_exposure": round(total_weight, 4),
        "cash_weight": cash_weight,
        "notes": notes,
        "rejected_by_industry": sorted(to_remove) if to_remove else [],
    }


def _cap_and_redistribute(
    weights: dict[str, float], max_weight: float, max_iterations: int = 10,
) -> dict[str, float]:
    """迭代式 redistribution — 確保超過 cap 的 excess 被重新分配給未超標的部位。"""
    for _ in range(max_iterations):
        excess = 0.0
        capped_symbols: set[str] = set()
        for sym, w in weights.items():
            if w > max_weight:
                excess += w - max_weight
                weights[sym] = max_weight
                capped_symbols.add(sym)
        if excess < 1e-6:
            break
        uncapped = [sym for sym in weights if sym not in capped_symbols and weights[sym] < max_weight]
        if not uncapped:
            logger.warning(
                "Weight capping: %.4f excess could not be redistributed (all positions at cap %.2f)",
                excess, max_weight,
            )
            break
        per_share = excess / len(uncapped)
        for sym in uncapped:
            weights[sym] += per_share
    return weights


def _calculate_position_weights(
    selected: list[dict],
    exposure: float,
    max_position_weight: float,
    weight_mode: str,
) -> dict[str, float]:
    """根據 weight_mode 計算每檔個股的目標權重。

    支援三種模式：
    - score_weighted: 權重 ∝ portfolio_score（高分股多拿）
    - vol_weighted: 權重 ∝ 1/volatility（低波動股多拿，risk parity lite）
    - equal: 等權
    """
    if not selected:
        return {}

    if weight_mode == "score_weighted":
        scores = {s["symbol"]: max(s.get("portfolio_score", 0), 1.0) for s in selected}
        score_sum = sum(scores.values())
        weights = {sym: (score / score_sum) * exposure for sym, score in scores.items()}
        weights = _cap_and_redistribute(weights, max_position_weight)
        return {sym: round(w, 4) for sym, w in weights.items()}

    if weight_mode == "vol_weighted":
        # Risk parity lite: 權重 ∝ 1 / volatility_20d
        # 波動率越低的股票拿越多權重 → 組合整體波動更平穩
        inv_vols: dict[str, float] = {}
        for s in selected:
            vol = s.get("volatility_20d")
            if vol is not None and vol > 0:
                inv_vols[s["symbol"]] = 1.0 / vol
            else:
                # 缺波動率資料 → 用中位數波動率的倒數（不偏不倚）
                inv_vols[s["symbol"]] = None  # type: ignore[assignment]
        # 用已知的中位數填補 None
        known = [v for v in inv_vols.values() if v is not None]
        median_inv_vol = sorted(known)[len(known) // 2] if known else 1.0
        for sym in inv_vols:
            if inv_vols[sym] is None:
                inv_vols[sym] = median_inv_vol
        inv_sum = sum(inv_vols.values()) or 1.0
        weights = {sym: (iv / inv_sum) * exposure for sym, iv in inv_vols.items()}
        weights = _cap_and_redistribute(weights, max_position_weight)
        return {sym: round(w, 4) for sym, w in weights.items()}

    # equal weight (default fallback)
    equal_weight = min(max_position_weight, exposure / len(selected))
    return {s["symbol"]: round(equal_weight, 4) for s in selected}


def _estimate_rebalance_turnover(
    current_positions: dict[str, dict],
    target_positions: list[dict],
) -> float:
    """Estimate one-way portfolio turnover across the rebalance.

    Uses 0.5 * sum(abs(target_weight - current_weight)) so that a full
    portfolio replacement equals 100% one-way turnover instead of 200%
    gross traded notional.
    """
    current_weights = {
        symbol: float(position.get("target_weight", 0.0) or 0.0)
        for symbol, position in current_positions.items()
    }
    target_weights = {
        position["symbol"]: float(position.get("target_weight", 0.0) or 0.0)
        for position in target_positions
    }

    all_symbols = set(current_weights) | set(target_weights)
    traded_notional = 0.0
    for symbol in all_symbols:
        traded_notional += abs(target_weights.get(symbol, 0.0) - current_weights.get(symbol, 0.0))
    return round(traded_notional / 2.0, 4)


_SECTOR_NEUTRAL_MIN_SIZE = 3     # 產業內人數 < 此值 pool 進 _OTHER
_SECTOR_NEUTRAL_OTHER_LABEL = "_OTHER"
_SECTOR_NEUTRAL_UNKNOWN_LABEL = "_UNKNOWN"


def _group_items_by_industry(
    items: list[dict],
    *,
    min_size: int = _SECTOR_NEUTRAL_MIN_SIZE,
) -> dict[str, list[dict]]:
    """Phase A3.1.1: Group items by industry, pool small groups into _OTHER.

    Small-industry pooling avoids statistical noise from ranking < 3 stocks
    within a sector (rank=0.5 fallback would otherwise dominate).
    Missing/empty industry strings map to _UNKNOWN, also pool eligible.
    """
    by_industry: dict[str, list[dict]] = {}
    for item in items:
        ind = item.get("industry") or _SECTOR_NEUTRAL_UNKNOWN_LABEL
        ind = str(ind).strip() or _SECTOR_NEUTRAL_UNKNOWN_LABEL
        by_industry.setdefault(ind, []).append(item)

    pooled: dict[str, list[dict]] = {}
    other_group: list[dict] = []
    for ind, group in by_industry.items():
        if len(group) >= min_size:
            pooled[ind] = group
        else:
            other_group.extend(group)
    if other_group:
        pooled[_SECTOR_NEUTRAL_OTHER_LABEL] = other_group
    return pooled


def _metric_ranks(
    items: list[dict],
    key: str,
    *,
    sector_neutral: bool = False,
) -> tuple[dict[str, float | None], bool]:
    """Compute percentile rank of a factor across the eligible universe.

    Phase A3.1.1 (2026-04-22): `sector_neutral=True` performs rank within
    each industry bucket instead of cross-sectional. Small industries
    (< 3 members) are pooled into _OTHER to avoid noise. Backward
    compatible — default `sector_neutral=False` preserves Phase A2 behavior.

    Audit 2026-05-02 A.1 fix (silent imputation removal):
        Symbols whose factor value is NaN/Inf used to be filled with 0.5
        (median percentile) so they competed in `top_n` with a "neutral"
        score. This masked data-quality issues. Now they receive `None`
        sentinel; the caller (`_rank_analyses`) per-symbol re-normalizes
        the weight_sum so the symbol is judged only on the factors it
        actually has, with a `min_factor_coverage_per_symbol` floor below
        which the symbol's portfolio_score is forced to 0.
    """
    if sector_neutral:
        return _metric_ranks_sector_neutral(items, key)

    values = {
        item["symbol"]: item.get(key)
        for item in items
        if item.get(key) is not None and isfinite(item.get(key))
    }
    if not values:
        return ({item["symbol"]: None for item in items}, False)

    nan_count = len(items) - len(values)
    if nan_count > len(items) * 0.5:
        logger.warning(
            "Factor '%s': %d/%d stocks have NaN/Inf (>50%%) — marking as unreliable",
            key, nan_count, len(items),
        )
        return ({item["symbol"]: None for item in items}, False)

    series = pd.Series(values, dtype="float64")
    ranks = series.rank(pct=True, ascending=True, method="average")
    output: dict[str, float | None] = {item["symbol"]: None for item in items}
    for symbol, value in ranks.items():
        output[symbol] = float(value)
    return output, True


def _metric_ranks_sector_neutral(
    items: list[dict],
    key: str,
) -> tuple[dict[str, float | None], bool]:
    """Rank factor within each industry bucket; pool small industries into _OTHER.

    Returns {symbol: within-industry percentile rank or None} + has_real_data bool.
    has_real_data mirrors cross-sectional >50% NaN threshold globally.

    Phase A3.1.4 (2026-04-23): groups whose valid-value count < 2 are deferred
    to a second-pass pool (rather than silently skipped with items left at
    0.5). Semantics: items whose sector cannot rank meaningfully get pooled
    together and ranked in a single cross-sectional bucket, so has_real_data
    no longer collapses below 50% just because the universe is sliced thin
    across many thin sectors.

    Audit 2026-05-02 A.1 fix (silent imputation removal):
        Symbols missing factor data — even after second-pass pooling — used
        to receive 0.5 (median percentile) and silently competed in `top_n`.
        Now they get `None`; caller per-symbol re-normalizes weight_sum.
    """
    groups = _group_items_by_industry(items)
    output: dict[str, float | None] = {item["symbol"]: None for item in items}

    total_valid = 0
    total_items = len(items)
    pool_items: list[dict] = []
    for _industry, group_items in groups.items():
        group_values = {
            item["symbol"]: item.get(key)
            for item in group_items
            if item.get(key) is not None and isfinite(item.get(key))
        }
        if len(group_values) < 2:
            pool_items.extend(group_items)
            continue

        total_valid += len(group_values)
        series = pd.Series(group_values, dtype="float64")
        ranks = series.rank(pct=True, ascending=True, method="average")
        for symbol, value in ranks.items():
            output[symbol] = float(value)

    if pool_items:
        pool_values = {
            item["symbol"]: item.get(key)
            for item in pool_items
            if item.get(key) is not None and isfinite(item.get(key))
        }
        if len(pool_values) >= 2:
            total_valid += len(pool_values)
            series = pd.Series(pool_values, dtype="float64")
            ranks = series.rank(pct=True, ascending=True, method="average")
            for symbol, value in ranks.items():
                output[symbol] = float(value)

    has_real_data = total_valid > total_items * 0.5
    if not has_real_data:
        logger.warning(
            "Factor '%s' sector-neutral: only %d/%d items got valid rank (<50%%) — unreliable",
            key, total_valid, total_items,
        )
    return output, has_real_data


def _period_return(series: pd.Series, periods: int) -> float | None:
    if len(series) <= periods:
        return None
    start = float(series.iloc[-periods - 1])
    end = float(series.iloc[-1])
    if start == 0:
        return None
    return (end / start) - 1.0


def _skip_period_return(series: pd.Series, total_periods: int, skip_recent: int) -> float | None:
    if len(series) <= total_periods + skip_recent:
        return None
    start = float(series.iloc[-(total_periods + skip_recent + 1)])
    end = float(series.iloc[-(skip_recent + 1)])
    if start == 0:
        return None
    return (end / start) - 1.0


def _trend_quality(
    close: float,
    sma_fast: float | None,
    sma_slow: float | None,
    structure: int,
    regime: str,
) -> float:
    """Continuous trend quality score (0–1).

    Each component uses linear interpolation instead of binary 0/1,
    so percentile ranking across stocks has real discriminating power.
    """
    score = 0.0

    # 35%: Price vs SMA_slow — linear 0→1 over [-5%, +20%]
    if sma_slow is not None and sma_slow > 0:
        pct = close / sma_slow - 1.0
        score += 0.35 * _clamp01((pct + 0.05) / 0.25)

    # 15%: Price vs SMA_fast — linear 0→1 over [-3%, +10%]
    if sma_fast is not None and sma_fast > 0:
        pct = close / sma_fast - 1.0
        score += 0.15 * _clamp01((pct + 0.03) / 0.13)

    # 25%: MA alignment (spread) — linear 0→1 over [-2%, +10%]
    if sma_fast is not None and sma_slow is not None and sma_slow > 0:
        spread = sma_fast / sma_slow - 1.0
        score += 0.25 * _clamp01((spread + 0.02) / 0.12)

    # 15%: Structure (higher highs / higher lows — binary)
    if structure == 1:
        score += 0.15

    # 10%: Regime (categorical → ordinal)
    _regime_score = {"trending_up": 1.0, "ranging": 0.5, "trending_down": 0.0}
    score += 0.10 * _regime_score.get(regime, 0.25)

    return round(min(score, 1.0), 4)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _monthly_revenue_momentum(
    df: pd.DataFrame | None,
    as_of: datetime,
) -> tuple[float | None, float | None, float | None]:
    if df is None or df.empty:
        return None, None, None

    working = df.copy()
    if "date" not in working.columns:
        return None, None, None

    working["date"] = pd.to_datetime(working["date"])
    working = working.sort_values("date")

    # 過濾尚未公開的營收資料（避免 look-ahead bias）
    # 台股月營收法定公告期限為次月 10 日前
    # FinMind date 欄位為營收月份（如 2026-01-01 表示一月營收）
    # P0-8: 使用 REVENUE_LAG_DAYS 天延遲（次月底 + 5 天緩衝），原 40 天偏保守會浪費可用資料
    as_of_ts = pd.Timestamp(as_of).tz_localize(None) if pd.Timestamp(as_of).tzinfo else pd.Timestamp(as_of)
    cutoff = as_of_ts - pd.Timedelta(days=REVENUE_LAG_DAYS)
    working = working[working["date"] <= cutoff]
    if working.empty:
        return None, None, None

    revenue_col = next(
        (col for col in ["revenue", "Revenue", "monthly_revenue"] if col in working.columns),
        None,
    )
    if revenue_col is None:
        return None, None, None

    working["_revenue"] = pd.to_numeric(working[revenue_col], errors="coerce")
    working = working.dropna(subset=["_revenue"])
    if working.empty:
        return None, None, None

    # 用日期比對找去年同月，而非 index 偏移
    latest_row = working.iloc[-1]
    latest_date = latest_row["date"]
    latest_revenue = float(latest_row["_revenue"])

    yoy = None
    target_year_ago = latest_date - pd.DateOffset(months=12)
    # 找最接近 12 個月前的那筆（容許 ±45 天）
    working["_date_diff"] = (working["date"] - target_year_ago).abs()
    candidates = working[working["_date_diff"] <= pd.Timedelta(days=45)]
    if not candidates.empty:
        yoy_row = candidates.loc[candidates["_date_diff"].idxmin()]
        yoy_revenue = float(yoy_row["_revenue"])
        if yoy_revenue != 0:
            yoy = (latest_revenue / yoy_revenue) - 1.0

    # 加速度：近 3 個月平均 vs 前 3 個月平均
    accel = None
    if len(working) >= 6:
        recent_3m = working["_revenue"].iloc[-3:].mean()
        prev_3m = working["_revenue"].iloc[-6:-3].mean()
        if prev_3m != 0:
            accel = (float(recent_3m) / float(prev_3m)) - 1.0

    raw = _weighted_average(
        [
            (yoy, 0.70),
            (accel, 0.30),
        ]
    )
    return yoy, accel, raw


def _weighted_average(values: list[tuple[float | None, float]]) -> float | None:
    valid = [(value, weight) for value, weight in values if value is not None]
    if not valid:
        return None
    weight_sum = sum(weight for _, weight in valid)
    if weight_sum == 0:
        return None
    return sum(value * weight for value, weight in valid) / weight_sum


def _float_or_none(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
