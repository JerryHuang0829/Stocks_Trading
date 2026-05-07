"""Point-in-time universe reconstruction for survivorship-bias-free backtesting."""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


class HistoricalUniverse:
    """重建歷史可交易 universe，避免 survivorship bias。

    使用 TaiwanStockInfo（含 IPO date）、TaiwanStockDelisting、
    以及截斷至 as_of 的市值資料來決定每個月哪些股票是可交易的。
    """

    def __init__(self, source):
        self._source = source
        self._stock_info: pd.DataFrame | None = None
        self._delisting: pd.DataFrame | None = None

    def load(self) -> None:
        """預載入全部 stock info 與 delisting 資料。"""
        self._stock_info = self._source.fetch_stock_info()
        if self._stock_info is None or self._stock_info.empty:
            raise RuntimeError(
                "Cannot load stock info for historical universe; "
                "FinMind TaiwanStockInfo is unavailable in the current environment/account"
            )

        # 嘗試載入下市資料
        if hasattr(self._source, "fetch_delisting"):
            self._delisting = self._source.fetch_delisting()
        else:
            logger.warning("Source does not support fetch_delisting; delisted stocks will be missing")
            self._delisting = pd.DataFrame()

    def get_universe_at(self, as_of: datetime, portfolio_config: dict, source=None) -> list[dict]:
        """回傳在 as_of 日期時的可交易 universe。

        Parameters
        ----------
        as_of : datetime
            回測日期
        portfolio_config : dict
            投組設定（用於過濾條件）
        source : optional
            資料來源（slicer），用於取得截斷至 as_of 的市值資料。
            若為 None 則不做市值排序。

        Returns
        -------
        list[dict]
            每個元素含 symbol, name, industry, market_value 等欄位
        """
        if self._stock_info is None:
            raise RuntimeError("Must call load() before get_universe_at()")

        working = self._stock_info.copy()

        # --- stock_id 欄位 guard（必須在 dedup 之前，否則 sort_values 會 KeyError）---
        if "stock_id" not in working.columns:
            logger.warning("stock_info has no 'stock_id' column — returning empty universe")
            return []

        # 注意：TaiwanStockInfo.date 是 FinMind 的「記錄更新時間戳記」，
        # 不是 IPO 日期。用 date <= as_of 過濾會把絕大多數合法上市股票排除
        # （實測：2022-01-12 只剩 95 支，2024-12-12 才有 460 支）。
        # 真正的 IPO 日期保護由 _analyze_symbol 的 274-bar OHLCV 要求提供：
        # 一支在 as_of 之前沒有足夠日線歷史的股票，自然會被篩掉。
        #
        # 去重：同一 stock_id 因產業重分類或轉市（上市↔上櫃）會有多筆記錄，
        # 取日期最新那筆（最新的產業/市場分類），避免同一股票被分析兩次。
        if "date" in working.columns:
            working["date"] = pd.to_datetime(working["date"], errors="coerce")
            working = (
                working
                .sort_values(["stock_id", "date"])
                .drop_duplicates("stock_id", keep="last")
            )
        else:
            working = working.drop_duplicates("stock_id", keep="last")
        logger.info(
            "TaiwanStockInfo: %d unique stocks after dedup (total rows before dedup: %d)",
            len(working),
            len(self._stock_info),
        )

        # 過濾已下市的股票
        if self._delisting is not None and not self._delisting.empty:
            if "stock_id" in self._delisting.columns and "date" in self._delisting.columns:
                delisted = self._delisting.copy()
                delisted["date"] = pd.to_datetime(delisted["date"], errors="coerce")
                # Normalize both sides to naive to avoid tz-aware vs naive comparison
                if delisted["date"].dt.tz is not None:
                    delisted["date"] = delisted["date"].dt.tz_localize(None)
                as_of_ts = pd.Timestamp(as_of).tz_localize(None)
                # 在 as_of 之前已下市的股票
                delisted_before = set(
                    delisted[delisted["date"] <= as_of_ts]["stock_id"].astype(str)
                )
                working = working[~working["stock_id"].astype(str).isin(delisted_before)]

        working["stock_id"] = working["stock_id"].astype(str).str.strip()
        # 保留 4 位股票代碼，以及 00xxxx 類 6 位 ETF 家族代碼。
        # 這樣 0050 不受影響，若 exclude_etf=False 時 006208 也可保留；
        # 同時仍可擋掉 71xxxx 等 6 位權證/衍生性商品代碼。
        working = working[working["stock_id"].str.fullmatch(r"(?:\d{4}|00\d{4})")]

        # ETF 過濾
        if portfolio_config.get("exclude_etf", True):
            working = working[~working["stock_id"].str.startswith("00")]

        # 市場類型過濾
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

        # 產業排除
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

        # 強制包含 / 排除
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

        # --- TWSE 成交金額預篩 + 備用排序（auto_universe_pre_filter_size）---
        # 一次抓取 TWSE 公開日交易資料（免費），同時用於：
        #   1. 預篩：把 ~2900 支縮減到流動性前 N 名
        #   2. 排序備援：若付費 market_value API 不可用，直接以成交金額排序
        # 確保台積電等大型股不因 OHLCV 快取缺失而被排除在候選名單之外。
        _twse_turnover: dict[str, float] = {}  # 保留供後續排序使用
        pre_filter_size = int(portfolio_config.get("auto_universe_pre_filter_size", 0) or 0)
        if pre_filter_size > 0:
            try:
                from src.data.twse_scraper import fetch_combined_turnover
                # 歷史日期時，讓 fetch_combined_turnover 直接從 OHLCV cache 計算 turnover
                ohlcv_src = source.fetch_ohlcv if source is not None and hasattr(source, "fetch_ohlcv") else None
                candidate_ids = working["stock_id"].astype(str).tolist()
                _twse_turnover = fetch_combined_turnover(
                    as_of,
                    ohlcv_source=ohlcv_src,
                    stock_ids=candidate_ids,
                )
            except Exception as _exc:
                logger.warning("TWSE scraper import/call failed: %s", _exc)

            if _twse_turnover and len(working) > pre_filter_size:
                _before_pre = len(working)
                # Coverage guard: 若 turnover dict 覆蓋率過低，強制失敗而非靜默
                # 以 0.0 補齊大量股票會讓 top-N 排名變成隨機、靜默扭曲 universe
                # （這是 2026-04-15 抓出的 alpha 幻覺根因）。
                min_coverage = float(
                    portfolio_config.get("auto_universe_pre_filter_min_coverage", 0.80)
                )
                coverage = len(_twse_turnover) / max(_before_pre, 1)
                if coverage < min_coverage:
                    raise RuntimeError(
                        f"Pre-filter turnover coverage too low at {as_of}: "
                        f"{len(_twse_turnover)}/{_before_pre} ({coverage:.1%}) "
                        f"< min {min_coverage:.0%}. Cache likely incomplete for this "
                        f"historical date. Rebuild cache or shorten backtest window."
                    )
                working["_twse_turnover"] = (
                    working["stock_id"].map(_twse_turnover).fillna(0.0)
                )
                working = (
                    working
                    .sort_values(["_twse_turnover", "stock_id"], ascending=[False, True])
                    .head(pre_filter_size)
                    .drop(columns=["_twse_turnover"])
                    .reset_index(drop=True)
                )
                logger.info(
                    "Pre-filter: %d → %d stocks using TWSE turnover at %s (coverage %.1%%)",
                    _before_pre, len(working),
                    as_of.date() if hasattr(as_of, "date") else as_of,
                    coverage * 100,
                )
            elif not _twse_turnover:
                raise RuntimeError(
                    f"TWSE turnover unavailable at {as_of} — pre-filter cannot run. "
                    f"This used to silently fall back to the full market and was the "
                    f"root cause of prior alpha illusions. Fix the cache or data source."
                )

        # --- 流動性排序與 size limit ---
        # Official strategy spec: rank by trading activity, not market cap.
        # Momentum strategies perform better in actively-traded stocks.
        # market_value is kept for monitoring only (stored in snapshot's
        # market_value field), not used for universe selection.
        size_ranked = False

        # 排序：用 close×volume 20日均值（與 tw_stock.py 完全一致的正式規格）
        # P7: 移除 TWSE turnover 排序，統一 live/backtest 路徑，避免微差。
        # TWSE turnover 仍用於 pre_filter 預篩（上方），但不用於最終排序。
        if not size_ranked and source is not None and hasattr(source, "fetch_ohlcv"):
            logger.info(
                "Universe ranking at %s: close×volume size proxy (cache-only)",
                as_of,
            )
            # 僅使用已有磁碟快取的股票：避免對 2000+ 支股票發出 API 呼叫
            # 耗盡每小時 600 次配額，且導致 TSMC 等未快取大型股被排除在外。
            # 未在快取中的股票 size_proxy=0，自然排到尾端不入選 top_n。
            # 後續回測會逐漸填充快取，universe 品質隨時間提升。
            # Use shared resolver: tries $DATA_CACHE_DIR → /app/data/cache →
            # <repo>/data/cache. Previously this only honoured the env var and
            # Docker path, so workstations without DATA_CACHE_DIR saw 0 cached
            # stocks and silently fell back to stock_info order.
            from src.utils.paths import resolve_cache_dir
            _ohlcv_cache_dir = resolve_cache_dir() / "ohlcv"
            cached_syms: set[str] = set()
            if _ohlcv_cache_dir.is_dir():
                cached_syms = {
                    f.stem for f in _ohlcv_cache_dir.iterdir()
                    if f.suffix == ".pkl"
                }
            n_cached = sum(1 for sid in working["stock_id"] if str(sid) in cached_syms)
            logger.info(
                "Size proxy: %d/%d universe stocks have cached OHLCV at %s",
                n_cached, len(working),
                as_of.date() if hasattr(as_of, "date") else as_of,
            )
            size_proxy: dict[str, float] = {}
            cached_success = 0
            cached_total = 0
            failed_samples: list[str] = []
            for _, row in working.iterrows():
                sym = str(row["stock_id"])
                if sym not in cached_syms:
                    size_proxy[sym] = 0.0  # 未快取，不發 API 呼叫（by design）
                    continue
                cached_total += 1
                try:
                    ohlcv = source.fetch_ohlcv(sym, "D", 30)
                    if ohlcv is not None and len(ohlcv) >= 5:
                        turnover = (ohlcv["close"] * ohlcv["volume"]).tail(20).mean()
                        if pd.notna(turnover):
                            size_proxy[sym] = float(turnover)
                            cached_success += 1
                            continue
                    size_proxy[sym] = 0.0
                    if len(failed_samples) < 5:
                        failed_samples.append(sym)
                except Exception as _exc:
                    size_proxy[sym] = 0.0
                    if len(failed_samples) < 5:
                        failed_samples.append(f"{sym}:{type(_exc).__name__}")
            # Guard 設計（三層，避免字典序退化幻覺）：
            #   a) cached_total == 0 → 此日期 cache 完全空，不算 size-ranked，
            #      讓下面的 fallback warning 觸發（不偽稱已排序）。
            #   b) cached_total > 0 but cached_success == 0 → 所有能讀的股票全失敗，
            #      是 active 錯誤（cache 損壞或 fetch 全 raise）→ raise。
            #   c) cached_total >= 10 且 rate < min → 統計顯著的大面積失敗 → raise。
            #   d) 1 <= cached_total < 10 且至少 1 成功 → 接受（小樣本 best effort）。
            if cached_total == 0:
                logger.warning(
                    "No cached OHLCV for any working stock at %s — "
                    "size-proxy unusable, falling back to stock_info order",
                    as_of.date() if hasattr(as_of, "date") else as_of,
                )
                # 不設 size_ranked=True；由下方 fallback 分支處理
            else:
                if cached_success == 0:
                    raise RuntimeError(
                        f"Backtest size-proxy: all {cached_total} cached stocks "
                        f"failed at {as_of} (0 succeeded). Samples: {failed_samples[:5]}. "
                        f"Cache likely corrupted or fetch_ohlcv broken."
                    )
                if cached_total >= 10:
                    _success_rate = cached_success / cached_total
                    _min_success = float(
                        portfolio_config.get("auto_universe_size_proxy_min_success", 0.60)
                    )
                    if _success_rate < _min_success:
                        raise RuntimeError(
                            f"Backtest size-proxy success rate too low at {as_of}: "
                            f"{cached_success}/{cached_total} ({_success_rate:.1%}) "
                            f"< min {_min_success:.0%}. Samples: {failed_samples[:5]}."
                        )
                working["_size_proxy"] = working["stock_id"].map(size_proxy).fillna(0.0)
                working = working.sort_values(
                    ["_size_proxy", "stock_id"], ascending=[False, True]
                )
                working = working.drop(columns=["_size_proxy"])
                size_ranked = True

        if not size_ranked:
            logger.warning(
                "No size data available at %s — using stock_info order as fallback",
                as_of,
            )

        limit = int(portfolio_config.get("auto_universe_size", 80) or 0)
        if limit > 0:
            working = working.head(limit)

        logger.info(
            "Universe at %s: %d stocks (limit=%d, size_ranked=%s)",
            as_of.date() if hasattr(as_of, "date") else as_of,
            len(working), limit, size_ranked,
        )

        result = []
        for _, row in working.iterrows():
            result.append(
                {
                    "symbol": str(row["stock_id"]),
                    "name": str(row.get("stock_name", row["stock_id"])),
                    "market": "tw_stock",
                    "source": "finmind",
                    "timeframe": "D",
                    "enabled": True,
                    "strategy": {},
                    "industry": str(row.get("industry_category", "")),
                    "type": str(row.get("type", "")),
                    "market_value": float(row.get("market_value")) if pd.notna(row.get("market_value")) else None,
                }
            )
        return result
