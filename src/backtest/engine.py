"""Backtest replay engine — point-in-time monthly rebalance with daily return series."""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timedelta

import pandas as pd

from ..portfolio.tw_stock import (
    _analyze_symbol,
    _rank_analyses,
    _select_positions,
    _analyze_market_proxy,
    get_portfolio_config,
    _batch_precompute_and_analyze,
)
from ..storage.database import compute_config_hash
from ..utils.constants import TECH_SUPPLY_CHAIN_KEYWORDS, TW_ROUND_TRIP_COST, to_utc_ts
from .metrics import adjust_dividends, adjust_splits, compute_metrics, format_report
from .universe import HistoricalUniverse

logger = logging.getLogger(__name__)

DEFAULT_SLIPPAGE_BPS = 10  # 對齊 config/settings.yaml:72 (R19 external audit P1 fix 2026-05-02 + Pro sprint 2026-05-04 補 src/scripts 層)


def _compute_theme_concentration(
    positions: list[dict], ranked: list[dict],
) -> dict:
    """計算持股的主題集中度（監控用，不影響選股邏輯）。

    Returns dict with:
      tech_weight: 廣義科技供應鏈的總權重
      tech_count: 科技相關持股數
      top_industry: 權重最高的產業
      top_industry_weight: 該產業的總權重
      industries: {產業: 權重} 完整分佈
    """
    # 建立 symbol → industry 映射（從 ranked 取得，因為 positions 可能不含 industry）
    ind_map = {item["symbol"]: item.get("industry", "") for item in ranked}

    industry_weights: dict[str, float] = {}
    tech_weight = 0.0
    tech_count = 0
    for p in positions:
        sym = p.get("symbol", "")
        w = float(p.get("target_weight", p.get("weight", 0)))
        ind = p.get("industry") or ind_map.get(sym, "")
        industry_weights[ind] = industry_weights.get(ind, 0) + w
        if any(kw in ind for kw in TECH_SUPPLY_CHAIN_KEYWORDS):
            tech_weight += w
            tech_count += 1

    top_ind = max(industry_weights, key=industry_weights.get) if industry_weights else ""
    return {
        "tech_weight": round(tech_weight, 4),
        "tech_count": tech_count,
        "total_positions": len(positions),
        "top_industry": top_ind,
        "top_industry_weight": round(industry_weights.get(top_ind, 0), 4),
        "industries": {k: round(v, 4) for k, v in sorted(
            industry_weights.items(), key=lambda x: -x[1]
        )},
    }


def _compute_score_dispersion(ranked: list[dict]) -> dict | None:
    """計算橫截面分數分散度（監控用）。

    低分散度 = 股票間分數差異小 = 排名效果差 = 動能策略信心應降低。
    歷史 25th percentile 以下視為低分散度。
    """
    scores = [
        item["portfolio_score"]
        for item in ranked
        if item.get("eligible") and item.get("portfolio_score") is not None
    ]
    if len(scores) < 5:
        return None
    import numpy as np
    arr = np.array(scores)
    return {
        "std": round(float(arr.std()), 4),
        "iqr": round(float(np.percentile(arr, 75) - np.percentile(arr, 25)), 4),
        "n_eligible": len(scores),
    }


class _DataSlicer:
    """包裝 source，將所有資料截斷到 as_of 日期以避免 look-ahead bias。

    回測啟動時預載入資料，之後每次 fetch 時只回傳截至 as_of 的切片。
    涵蓋：OHLCV、法人買賣超、月營收、市值。
    """

    def __init__(
        self,
        source,
        as_of: datetime | None = None,
        backtest_start: datetime | None = None,
        reference_now: datetime | None = None,
        *,
        ohlcv_min_fetch_days: int = 2000,
        market_value_fetch_days: int = 2500,
        institutional_fallback_days: int = 500,
    ):
        self._source = source
        self._as_of: pd.Timestamp | None = None
        self._backtest_start: datetime | None = backtest_start
        # 固定「現在」時間點，避免同一回測內多次呼叫 datetime.now() 造成跨日漂移
        self._reference_now: datetime = reference_now if reference_now is not None else datetime.now()
        self._ohlcv_cache: dict[str, pd.DataFrame] = {}
        self._df_cache: dict[str, pd.DataFrame] = {}
        self._inst_coverage_warned: bool = False  # 只對同一 slicer 警告一次
        self._ohlcv_min_fetch = ohlcv_min_fetch_days
        self._mv_fetch_days = market_value_fetch_days
        self._inst_fallback_days = institutional_fallback_days
        if as_of is not None:
            self.set_as_of(as_of)

    def set_as_of(self, as_of: datetime) -> None:
        self._as_of = to_utc_ts(as_of)

    @property
    def _as_of_naive(self) -> pd.Timestamp | None:
        """回傳 timezone-naive 版本的 as_of（用於比較 naive date 欄位）。"""
        if self._as_of is None:
            return None
        return self._as_of.tz_localize(None) if self._as_of.tzinfo else self._as_of

    def _truncate_by_date_col(self, df: pd.DataFrame, tail: int | None = None) -> pd.DataFrame | None:
        """依 'date' 欄位截斷 DataFrame 到 as_of，可選取最後 tail 筆。"""
        if df is None or df.empty:
            return None
        if self._as_of_naive is not None and "date" in df.columns:
            df = df[pd.to_datetime(df["date"]) <= self._as_of_naive]
        if df.empty:
            return None
        if not tail:
            return df

        if "date" not in df.columns:
            return df.tail(tail)

        date_values = pd.to_datetime(df["date"], errors="coerce")
        if date_values.isna().all():
            return df.tail(tail)

        unique_dates = pd.Index(date_values.dropna().unique()).sort_values()
        if len(unique_dates) <= tail:
            return df

        cutoff = unique_dates[-tail]
        return df[date_values >= cutoff]

    def preload(self, symbols: list[str], days: int = 3000) -> None:
        """預載入所有標的的歷史資料。"""
        for symbol in symbols:
            if symbol in self._ohlcv_cache:
                continue
            df = self._source.fetch_ohlcv(symbol, "D", days)
            if df is not None and not df.empty:
                self._ohlcv_cache[symbol] = df

    def preload_reference_data(self, backtest_days: int = 2500) -> None:
        """預載入參考資料（回測全期間）。

        P7: market_value 不再用於選股排序（改用成交金額），
        因此不再預載入以避免浪費 TWSE API 呼叫。
        market_value 仍可透過 fetch_market_value() 取得（監控用途）。
        """
        pass

    # --- OHLCV（index 為 UTC timestamp）---

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame | None:
        """回傳截至 as_of 的 OHLCV 切片。"""
        if symbol not in self._ohlcv_cache:
            df = self._source.fetch_ohlcv(symbol, "D", max(limit, self._ohlcv_min_fetch))
            # 無論成功或失敗都 cache（失敗以空 DataFrame 作為哨兵值），
            # 避免下一個再平衡週期對同一標的重複發出 API 請求。
            self._ohlcv_cache[symbol] = df if (df is not None and not df.empty) else pd.DataFrame()

        df = self._ohlcv_cache[symbol]
        if df.empty:
            return None
        if self._as_of is not None:
            df = df[df.index <= self._as_of]
        return df.tail(limit) if not df.empty else None

    # --- 法人買賣超（date 欄位，naive datetime）---

    def fetch_institutional(self, symbol: str, days: int = 30) -> pd.DataFrame | None:
        cache_key = f"inst:{symbol}"
        if cache_key not in self._df_cache:
            # 若知道回測起始日，依此計算需覆蓋的天數；否則退回 config 設定的 fallback 天數
            if self._backtest_start is not None:
                # FinMindSource 內部以 trading_days * 1.5 換算為 calendar days 窗口。
                # 反向計算：ceil(calendar_days / 1.5) 取保守上界，確保起點早於 backtest_start。
                # 使用固定 reference_now 避免同一回測跨日時漂移。
                calendar_days_needed = (self._reference_now - self._backtest_start).days + 60
                fetch_days = math.ceil(calendar_days_needed / 1.5)
            else:
                fetch_days = max(days, self._inst_fallback_days)
            df = self._source.fetch_institutional(symbol, fetch_days)
            if df is not None and not df.empty:
                # Coverage guard：確認抓到的資料起點早於 backtest_start
                if self._backtest_start is not None and not self._inst_coverage_warned:
                    if "date" in df.columns:
                        earliest = pd.to_datetime(df["date"]).min()
                        if earliest > pd.Timestamp(self._backtest_start):
                            logger.warning(
                                "Institutional data coverage insufficient: earliest record %s "
                                "is after backtest_start %s (first seen on symbol %s). "
                                "Factor scores for early rebalance periods will be zero.",
                                earliest.date(), self._backtest_start.date(), symbol,
                            )
                            self._inst_coverage_warned = True
            # 無論成功或失敗都 cache（失敗以空 DataFrame 作為哨兵值），
            # 避免下一個再平衡週期對同一標的重複發出 API 請求。
            self._df_cache[cache_key] = df if (df is not None and not df.empty) else pd.DataFrame()
        df = self._df_cache.get(cache_key)
        if df is None or df.empty:
            return None
        return self._truncate_by_date_col(df.copy(), tail=days)

    # --- 月營收（date 欄位，naive datetime）---

    def fetch_month_revenue(self, symbol: str, months: int = 15) -> pd.DataFrame | None:
        cache_key = f"rev:{symbol}"
        if cache_key not in self._df_cache:
            df = self._source.fetch_month_revenue(symbol, max(months, 60))
            # 無論成功或失敗都 cache（失敗以空 DataFrame 作為哨兵值），
            # 避免下一個再平衡週期對同一標的重複發出 API 請求。
            self._df_cache[cache_key] = df if (df is not None and not df.empty) else pd.DataFrame()
        df = self._df_cache.get(cache_key)
        if df is None or df.empty:
            return None
        return self._truncate_by_date_col(df.copy(), tail=months)

    # --- 市值資料（全市場，date 欄位）---

    def fetch_market_value(self, days: int = 10) -> pd.DataFrame | None:
        if "market_value" not in self._df_cache:
            mv = self._source.fetch_market_value(days=max(days, self._mv_fetch_days))
            self._df_cache["market_value"] = (
                mv if (mv is not None and not mv.empty) else pd.DataFrame()
            )
        df = self._df_cache.get("market_value")
        # 空 DataFrame 為「已查詢但無資料」的哨兵值，與 None 同等回傳 None
        if df is None or df.empty:
            return None
        return self._truncate_by_date_col(df.copy())

    # --- 靜態參考資料（直接透傳）---

    def fetch_stock_info(self) -> pd.DataFrame | None:
        return self._source.fetch_stock_info()

    def fetch_delisting(self) -> pd.DataFrame | None:
        if hasattr(self._source, "fetch_delisting"):
            return self._source.fetch_delisting()
        return None

    # 透傳其他未覆寫的方法
    def __getattr__(self, name):
        return getattr(self._source, name)


class BacktestEngine:
    """逐月 replay 引擎，產出日頻報酬序列。

    核心修正：
    - 使用 _DataSlicer 截斷資料到 as_of，避免 look-ahead bias
    - 產出日頻（非月頻）投組報酬序列
    - 每次再平衡時正確扣除 round-trip cost + 滑價
    - Benchmark 與 portfolio 在日頻層級對齊
    """

    def __init__(
        self,
        source,
        config: dict,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    ):
        self._source = source
        self._config = config
        # Phase A2 Step 1.5.4: _backtest_context=True marker enables the
        # silent-renormalize guard in _rank_analyses to raise (instead of warn)
        # when a weight>0 factor has no real data — prevents false-positive
        # backtest numbers. get_portfolio_config returns a new merged dict, so
        # dict-spread here creates one more new dict without mutating any caller
        # input. Live callers do not set this marker → guard only warns.
        self._portfolio_config = {
            **get_portfolio_config(config),
            "_backtest_context": True,
        }
        self._slippage_bps = slippage_bps
        self._round_trip_cost = float(
            self._portfolio_config.get("turnover_cost", TW_ROUND_TRIP_COST)
        )
        # backtest section defaults（向後相容：config 無此 section 時用原有預設值）
        _bt = config.get("backtest", {})
        self._benchmark_lookback = int(_bt.get("benchmark_lookback_days", 3000))
        self._ohlcv_min_fetch = int(_bt.get("ohlcv_min_fetch_days", 2000))
        self._mv_fetch_days = int(_bt.get("market_value_fetch_days", 2500))
        self._inst_fallback_days = int(_bt.get("institutional_fallback_days", 500))
        self._error_rate_threshold = float(_bt.get("error_rate_threshold", 0.2))
        self._factor_coverage_threshold = float(_bt.get("factor_coverage_threshold", 0.3))
        self._dividends: list[dict] | None = None

    def run(
        self,
        start_date: datetime,
        end_date: datetime,
        benchmark_symbol: str = "0050",
    ) -> dict:
        """執行回測，回傳日頻報酬序列與完整 KPI。"""
        logger.info("Backtest: %s to %s", start_date.date(), end_date.date())

        rebalance_day = int(self._portfolio_config.get("rebalance_day", 12))

        # 固定「現在」時間點：整輪回測使用同一個 reference_now，
        # 避免在跨午夜或長時間回測中 datetime.now() 的多次呼叫造成窗口不一致。
        reference_now = datetime.now()

        # 建立截斷資料代理；傳入 backtest_start 與 reference_now 讓各 fetch 方法
        # 計算正確的覆蓋窗口，且整輪保持一致。
        slicer = _DataSlicer(
            self._source,
            backtest_start=start_date,
            reference_now=reference_now,
            ohlcv_min_fetch_days=self._ohlcv_min_fetch,
            market_value_fetch_days=self._mv_fetch_days,
            institutional_fallback_days=self._inst_fallback_days,
        )

        # 取 benchmark 交易日索引，用於對齊 rebalance 日期到真實交易日
        _bench_for_dates = self._source.fetch_ohlcv(benchmark_symbol, "D", self._benchmark_lookback)
        _trading_days = _bench_for_dates.index if _bench_for_dates is not None and not _bench_for_dates.empty else None
        rebalance_dates = self._generate_rebalance_dates(
            start_date, end_date, rebalance_day, trading_days=_trading_days
        )

        if not rebalance_dates:
            logger.error("No rebalance dates in range")
            return {"metrics": {}, "report": "No rebalance dates", "monthly_snapshots": []}

        # 預載入市值等參考資料（覆蓋從回測起點到 reference_now 的完整期間）
        backtest_days = (reference_now - start_date).days + 60
        slicer.preload_reference_data(backtest_days)

        # 建立 point-in-time universe（處理 IPO / 下市 / 市值排序）
        hist_universe = HistoricalUniverse(slicer)
        hist_universe.load()

        # --- 取得除息資料（P4.5 total return adjustment）---
        # Dividend year range must cover the benchmark's full OHLCV lookback
        # (default 3000 days ≈ 8 years), not just backtest start-1.  Otherwise
        # early benchmark prices miss dividend adjustments → benchmark total
        # return is understated → Alpha is overstated.
        _div_start_year = (start_date - timedelta(days=self._benchmark_lookback)).year
        self._dividends: list[dict] | None = None
        try:
            self._dividends = self._source.fetch_dividends(
                _div_start_year, end_date.year,
            )
            if self._dividends:
                # Defensive: drop any ex_date beyond backtest end to prevent
                # look-ahead if price data accidentally extends past end_date.
                cutoff = end_date.strftime("%Y-%m-%d")
                self._dividends = [d for d in self._dividends if d["ex_date"] <= cutoff]
                logger.info("Dividend data loaded: %d records (up to %s)", len(self._dividends), cutoff)
        except Exception as exc:
            logger.warning("Could not fetch dividend data — running without dividend adjustment: %s", exc)

        # --- 取得 benchmark 日線報酬（含 split + dividend 調整）---
        bench_df = self._source.fetch_ohlcv(benchmark_symbol, "D", self._benchmark_lookback)
        benchmark_daily: pd.Series | None = None
        if bench_df is not None and not bench_df.empty:
            adjusted_close = adjust_splits(bench_df["close"])
            if self._dividends:
                adjusted_close = adjust_dividends(adjusted_close, self._dividends, benchmark_symbol)
            benchmark_daily = adjusted_close.pct_change().dropna()

        # --- 逐月 replay ---
        monthly_snapshots: list[dict] = []
        current_holdings: dict[str, float] = {}  # symbol -> weight
        all_daily_returns: list[tuple[pd.Timestamp, float]] = []
        default_strategy = self._config.get("default_strategy", {})

        for i, rebal_date in enumerate(rebalance_dates):
            logger.info("Rebalance #%d: %s", i + 1, rebal_date.date())

            # 截斷資料到再平衡日
            slicer.set_as_of(rebal_date)

            # --- Step A: 計算上一期的日頻報酬 ---
            if i > 0 and current_holdings:
                prev_rebal = rebalance_dates[i - 1]
                daily_rets = self._compute_daily_returns(
                    current_holdings, prev_rebal, rebal_date, slicer
                )
                all_daily_returns.extend(daily_rets)

            # --- Step B: 決定新投組（point-in-time universe）---
            universe = hist_universe.get_universe_at(
                rebal_date, self._portfolio_config, source=slicer
            )
            if not universe:
                logger.warning("Empty universe at %s; keeping positions", rebal_date.date())
                continue

            # 先算 market_view，讓 eligibility filter 依市場狀態調整
            market_view = _analyze_market_proxy(slicer, default_strategy, self._portfolio_config)
            market_signal = market_view["signal"]

            analyses = _batch_precompute_and_analyze(
                universe, slicer, default_strategy, self._portfolio_config,
                rebal_date, market_signal,
            )

            # 資料品質統計
            n_analyzed = len(analyses)
            n_errors = sum(1 for a in analyses if any("analysis_error" in str(f) for f in a.get("filters", [])))
            n_eligible = sum(1 for a in analyses if a.get("eligible", False))

            # Backtest analyze failure-rate guard（與 live tw_stock.py L294-304 對齊）。
            # Live 在失敗率過高時 return None（跳過 rebalance）；backtest 則 raise，
            # 讓研究者明確看到，而不是靜默保留舊持倉 / 空倉影響績效數字。
            min_eligible_ratio = float(
                self._portfolio_config.get("min_eligible_ratio", 0.3)
            )
            analyze_success = n_analyzed - n_errors
            if n_analyzed > 0 and analyze_success / n_analyzed < min_eligible_ratio:
                raise RuntimeError(
                    f"Backtest analyze success rate too low at {rebal_date}: "
                    f"{analyze_success}/{n_analyzed} "
                    f"({analyze_success / n_analyzed:.1%}) < min "
                    f"{min_eligible_ratio:.0%}. Silent degradation would distort "
                    f"performance metrics; fix data before rerunning."
                )

            # Phase A3.1.2: pass market_view so regime-aware weights take effect
            # when portfolio_config has regime_score_weights configured.
            # Falls back to flat score_weights otherwise (Phase A2 behavior).
            ranked = _rank_analyses(
                analyses, self._portfolio_config, market_view=market_view,
            )

            # 將目前持倉轉成 _select_positions 需要的格式
            current_positions_for_select = {
                sym: {"symbol": sym, "target_weight": w}
                for sym, w in current_holdings.items()
            }
            selection = _select_positions(
                ranked, current_positions_for_select, self._portfolio_config, market_view
            )

            # --- Step C: 扣除交易成本 ---
            new_holdings: dict[str, float] = {
                p["symbol"]: p["target_weight"] for p in selection["positions"]
            }
            turnover = self._one_way_turnover(current_holdings, new_holdings)
            # round-trip cost = 買 + 賣各一次，每邊 = round_trip_cost / 2
            # one-way turnover 代表換手的一邊，所以乘完整 round-trip
            rebalance_cost = turnover * self._round_trip_cost
            # 滑價：進出各一次
            slippage_cost = turnover * 2 * (self._slippage_bps / 10000.0)
            total_trade_cost = rebalance_cost + slippage_cost

            if total_trade_cost > 0:
                # 把交易成本記在再平衡當天（含首次建倉）
                all_daily_returns.append(
                    (to_utc_ts(rebal_date), -total_trade_cost)
                )

            current_holdings = new_holdings

            # 識別選股過程中被拒絕的原因
            selected_symbols_set = {p["symbol"] for p in selection["positions"]}
            top_n = int(self._portfolio_config.get("top_n", 8))
            eligible_list = [a["symbol"] for a in ranked if a.get("eligible", False)]
            rejected_by_selection = [
                s for s in eligible_list if s not in selected_symbols_set
            ]
            rejected_by_top_n = [
                s for i, s in enumerate(eligible_list)
                if s not in selected_symbols_set and i >= top_n
            ]

            # 分析階段的拒絕原因細分（從每支股票的 filters 欄位提取）
            rejected_by_turnover = [
                a["symbol"] for a in analyses
                if "turnover_too_low" in a.get("filters", [])
            ]
            rejected_by_price = [
                a["symbol"] for a in analyses
                if "price_below_floor" in a.get("filters", [])
            ]
            rejected_by_history = [
                a["symbol"] for a in analyses
                if "insufficient_history" in a.get("filters", [])
            ]
            rejected_by_trend = [
                a["symbol"] for a in analyses
                if any(f in a.get("filters", []) for f in (
                    "below_sma_slow", "below_sma_slow_relaxed",
                    "trend_not_aligned", "momentum_6m_non_positive",
                    "momentum_6m_deep_negative",
                ))
            ]
            # 產業限制拒絕（由 _select_positions 回傳）
            rejected_by_industry = selection.get("rejected_by_industry", [])

            # factor_coverage：eligible 股票中有有效因子資料的比例
            eligible_analyses = [a for a in analyses if a.get("eligible", False)]
            if eligible_analyses:
                factor_coverage = {
                    "revenue_momentum": round(
                        sum(1 for a in eligible_analyses if a.get("revenue_raw") is not None)
                        / len(eligible_analyses), 3
                    ),
                    # NOTE: 以「非零值」近似 coverage。institutional_raw=0 可能代表
                    # 「真正零流量」或「未 fetch（IF weight=0 時）」，兩者無法區分。
                    # 目前 IF weight=0，此值僅供診斷記錄，不影響 degraded 判定。
                    # 若未來重啟 IF，應改用 None 判斷（missing-aware）。
                    "institutional_flow": round(
                        sum(1 for a in eligible_analyses if (a.get("institutional_raw") or 0) != 0)
                        / len(eligible_analyses), 3
                    ),
                }
            else:
                factor_coverage = {"revenue_momentum": 0.0, "institutional_flow": 0.0}

            # data_degraded 判定：錯誤率超過閾值 或 使用中的因子覆蓋率低於閾值
            # 只檢查權重 > 0 的因子，避免已停用因子（如 IF=0%）觸發 false alarm
            error_degraded = n_errors > n_analyzed * self._error_rate_threshold
            score_weights = self._portfolio_config.get("score_weights", {})
            coverage_factor_map = {
                "revenue_momentum": "revenue_momentum",
                "institutional_flow": "institutional_flow",
            }
            coverage_degraded = False
            if eligible_analyses:
                for factor_name, coverage_key in coverage_factor_map.items():
                    weight = float(score_weights.get(factor_name, 0))
                    if weight > 0 and factor_coverage.get(coverage_key, 0) < self._factor_coverage_threshold:
                        coverage_degraded = True
                        break
            data_degraded = error_degraded or coverage_degraded

            # universe fingerprint：用於偵測不同執行間的資料漂移
            _universe_syms = sorted(s["symbol"] for s in universe)
            _universe_fp = hashlib.md5(",".join(_universe_syms).encode()).hexdigest()[:12]

            # 保存 snapshot
            snapshot = {
                "rebalance_date": rebal_date.isoformat(),
                "market_signal": market_view["signal"],
                "gross_exposure": selection["gross_exposure"],
                "total_analyzed": n_analyzed,
                "analysis_errors": n_errors,
                "eligible_candidates": n_eligible,
                "selected_count": len(selection["positions"]),
                "universe_size": len(universe),
                "universe_fingerprint": _universe_fp,
                "one_way_turnover": round(turnover, 4),
                "trade_cost": round(total_trade_cost, 6),
                "data_degraded": data_degraded,
                "data_degraded_reasons": (
                    (["error_rate_high"] if error_degraded else [])
                    + ([
                        f"factor_coverage_low:{k}"
                        for k, v in coverage_factor_map.items()
                        if float(score_weights.get(k, 0)) > 0
                        and factor_coverage.get(v, 0) < self._factor_coverage_threshold
                    ] if coverage_degraded else [])
                ),
                "factor_coverage": factor_coverage,
                "eligible_list": eligible_list,
                "rejected_not_selected": rejected_by_selection,
                "rejected_by_top_n": rejected_by_top_n,
                "rejected_by_turnover": rejected_by_turnover,
                "rejected_by_price": rejected_by_price,
                "rejected_by_history": rejected_by_history,
                "rejected_by_trend": rejected_by_trend,
                "rejected_by_industry": rejected_by_industry,
                "positions": [
                    {"symbol": p["symbol"], "weight": p["target_weight"], "score": p["score"],
                     "industry": p.get("industry", "")}
                    for p in selection["positions"]
                ],
                "theme_concentration": _compute_theme_concentration(
                    selection["positions"], ranked,
                ),
                "factor_detail": [
                    {
                        "symbol": item["symbol"],
                        "rank": item.get("rank"),
                        "portfolio_score": item.get("portfolio_score"),
                        "rank_components": item.get("rank_components", {}),
                        "price_momentum_raw": item.get("price_momentum_raw"),
                        "trend_quality_raw": item.get("trend_quality_raw"),
                        "revenue_raw": item.get("revenue_raw"),
                        "institutional_raw": item.get("institutional_raw"),
                        "industry": item.get("industry", ""),
                    }
                    for item in ranked[:20]
                    if item.get("eligible", False)
                ],
                "config_hash": compute_config_hash(self._portfolio_config),
                "score_dispersion": _compute_score_dispersion(ranked),
            }
            monthly_snapshots.append(snapshot)

        # --- 最後一期日頻報酬 ---
        if current_holdings and rebalance_dates:
            slicer.set_as_of(end_date)
            final_rets = self._compute_daily_returns(
                current_holdings, rebalance_dates[-1], end_date, slicer
            )
            all_daily_returns.extend(final_rets)

        # --- 組合日頻報酬序列 ---
        if all_daily_returns:
            # 合併同一天的報酬（交易成本日與持倉報酬日可能重疊）
            df_rets = pd.DataFrame(all_daily_returns, columns=["date", "return"])
            df_rets = df_rets.groupby("date")["return"].sum()
            portfolio_daily = df_rets.sort_index()
        else:
            portfolio_daily = pd.Series(dtype="float64")

        # --- 空倉日補 0.0（P4.7）---
        # 原行為只記錄「有持倉」的日子，會讓年化分母 n_years 縮短，
        # 高估年化報酬 / Sharpe。改成 reindex 到交易日曆並 fill 0.0，
        # 讓 cash drag 日明確計入真實持有期間。
        if rebalance_dates:
            window_start = to_utc_ts(rebalance_dates[0])
            window_end = to_utc_ts(end_date)
            if benchmark_daily is not None and not benchmark_daily.empty:
                # benchmark index 是精確的交易日曆（含假日）
                bench_idx = benchmark_daily.index
                in_window = bench_idx[(bench_idx >= window_start) & (bench_idx <= window_end)]
            else:
                # 無 benchmark：退化用 bdate_range（M-F，不含台股假日）— 會略高估
                # 交易日數，但總比 silently drop 空倉日讓 n_years 縮短好。
                logger.warning(
                    "No benchmark available; using bdate_range for empty-holding fill "
                    "(annualization may include non-trading weekdays)"
                )
                in_window = pd.bdate_range(window_start, window_end, tz="UTC")
            if len(in_window) > 0:
                portfolio_daily = portfolio_daily.reindex(in_window).fillna(0.0)

        # --- 計算 KPI ---
        metrics = compute_metrics(portfolio_daily, benchmark_daily)
        # Override benchmark_type if dividends were applied (P4.5)
        if self._dividends and metrics.get("benchmark_type") == "price_only":
            metrics["benchmark_type"] = "total_return"
        report = format_report(metrics, benchmark_symbol)

        # 補充換手率與交易成本統計
        total_turnover = sum(s.get("one_way_turnover", 0) for s in monthly_snapshots)
        total_cost = sum(s.get("trade_cost", 0) for s in monthly_snapshots)
        n_rebalances = len(monthly_snapshots)
        metrics["total_one_way_turnover"] = round(total_turnover, 4)
        metrics["total_trade_cost"] = round(total_cost, 6)
        metrics["avg_turnover_per_rebalance"] = round(
            total_turnover / n_rebalances if n_rebalances else 0, 4
        )
        metrics["n_rebalances"] = n_rebalances

        # 資料品質標記：任一再平衡週期 data_degraded=True 則整輪標記
        degraded_periods = [s for s in monthly_snapshots if s.get("data_degraded")]
        metrics["data_degraded"] = len(degraded_periods) > 0
        metrics["degraded_periods"] = len(degraded_periods)
        if degraded_periods:
            logger.warning(
                "⚠️ 本輪回測有 %d/%d 個再平衡週期資料降級（分析錯誤率 >20%%），"
                "KPI 不應視為乾淨研究基準。",
                len(degraded_periods), n_rebalances,
            )

        logger.info("\n%s", report)

        return {
            "metrics": metrics,
            "report": report,
            "monthly_snapshots": monthly_snapshots,
            "portfolio_returns": portfolio_daily,
            "benchmark_returns": benchmark_daily,
        }

    def _compute_daily_returns(
        self,
        holdings: dict[str, float],
        period_start: datetime,
        period_end: datetime,
        slicer: _DataSlicer,
    ) -> list[tuple[pd.Timestamp, float]]:
        """計算一個持有期間的日頻加權報酬序列。

        假設在 period_start 次一交易日以收盤價成交，
        之後每日以收盤價計算報酬，直到 period_end。
        """
        start_ts = to_utc_ts(period_start)
        end_ts = to_utc_ts(period_end)

        # 收集每個持倉的日報酬
        stock_daily_returns: dict[str, pd.Series] = {}
        for symbol, weight in holdings.items():
            if weight <= 0:
                continue
            df = slicer.fetch_ohlcv(symbol, "D", self._ohlcv_min_fetch)
            if df is None or df.empty:
                continue

            # 取 period_start 之後到 period_end 的資料
            period_df = df[(df.index > start_ts) & (df.index <= end_ts)]
            if len(period_df) < 2:
                continue

            adjusted_close = adjust_splits(period_df["close"])
            if self._dividends:
                adjusted_close = adjust_dividends(adjusted_close, self._dividends, symbol)
            daily_ret = adjusted_close.pct_change().dropna()
            # 過濾 inf：收盤價含 0 或資料錯誤時 pct_change 可能產生 inf，
            # dropna() 不會移除 inf，必須明確替換，否則一個壞點汙染整個 KPI。
            n_inf = daily_ret.isin([float("inf"), float("-inf")]).sum()
            if n_inf > 0:
                logger.warning(
                    "Symbol %s: %d infinite daily return(s) in [%s, %s] — likely bad price data; dropping",
                    symbol, n_inf, period_start.date(), period_end.date(),
                )
                daily_ret = daily_ret[~daily_ret.isin([float("inf"), float("-inf")])]
            if daily_ret.empty:
                continue
            stock_daily_returns[symbol] = daily_ret

        if not stock_daily_returns:
            return []

        # 建立日頻報酬矩陣
        ret_df = pd.DataFrame(stock_daily_returns)
        # 用持倉權重加權
        weights = pd.Series(holdings)
        # 只取有資料的 symbol
        common = weights.index.intersection(ret_df.columns)
        if common.empty:
            return []

        w = weights[common]
        w_sum = w.sum()
        if w_sum <= 0:
            return []

        # --- Drift-aware 日報酬（P4.6）---
        # 以目標權重作為初始 dollar value，每日隨股價漂移更新。
        # 未投資部分（cash = 1 - w_sum）報酬為 0，隱含現金拖累。
        # 等同「buy-and-hold within period」，比固定權重更精確。
        values = w.copy().astype(float)
        cash = 1.0 - w_sum  # 未投資現金部位
        results: list[tuple[pd.Timestamp, float]] = []
        for date in ret_df.index:
            total_before = values.sum() + cash
            if total_before <= 0:
                break
            day_rets = ret_df.loc[date, common].fillna(0.0)
            values = values * (1.0 + day_rets)
            total_after = values.sum() + cash
            port_ret = total_after / total_before - 1.0
            results.append((date, port_ret))

        return results

    @staticmethod
    def _one_way_turnover(
        old: dict[str, float], new: dict[str, float]
    ) -> float:
        """計算 one-way turnover: 0.5 * sum(|new_w - old_w|)。"""
        all_symbols = set(old) | set(new)
        gross = sum(abs(new.get(s, 0) - old.get(s, 0)) for s in all_symbols)
        return gross / 2.0

    @staticmethod
    def _generate_rebalance_dates(
        start: datetime, end: datetime, day: int,
        trading_days: pd.DatetimeIndex | None = None,
    ) -> list[datetime]:
        """產生回測期間內所有再平衡日期。

        若提供 trading_days，會將每個日曆日對齊到**同日或之後**最近的交易日，
        避免在假日/週末做 as_of 截斷造成 look-ahead bias。
        """
        dates = []
        current = start.replace(day=min(day, 28))
        if current < start:
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        while current <= end:
            dates.append(current)
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        if trading_days is not None and len(trading_days) > 0:
            td_naive = trading_days.tz_localize(None) if trading_days.tz else trading_days
            aligned = []
            for d in dates:
                # 找 >= d 的最近交易日
                future = td_naive[td_naive >= pd.Timestamp(d)]
                if len(future) > 0:
                    aligned.append(future[0].to_pydatetime())
                else:
                    # 沒有未來交易日（回測尾端），取 <= d 的最近交易日
                    past = td_naive[td_naive <= pd.Timestamp(d)]
                    if len(past) > 0:
                        aligned.append(past[-1].to_pydatetime())
            dates = aligned

        return dates
