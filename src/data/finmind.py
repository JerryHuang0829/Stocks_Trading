"""FinMind data source wrapper for Taiwan stocks with persistent disk caching.

Historical market data is immutable — yesterday's close never changes.
A disk-based cache (pickle files under ``data/cache/``) stores all fetched
DataFrames so that repeated backtests incur zero API calls after the first
successful run.  Only truly new data (the gap between the cached max-date
and today) is fetched from the API.
"""

from __future__ import annotations

import logging
import os
import pathlib
import time as _time
from datetime import datetime, timedelta

import pandas as pd

from .base import DataSource
from ..utils.constants import TW_TZ, to_utc_ts

logger = logging.getLogger(__name__)

MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 0
MARKET_CLOSE_HOUR = 13
MARKET_CLOSE_MIN = 30

# First fetch covers ~11 years — the slicer requests limit=2000 (×1.8 = 3600 days).
_WIDE_LOOKBACK_DAYS = 4000


# ---------------------------------------------------------------------------
# Persistent disk cache
# ---------------------------------------------------------------------------

class _DiskCacheCorruptedError(RuntimeError):
    """Raised when a cache file exists but cannot be deserialized.

    In backtest mode this is fatal: silently falling through to live API
    breaks PIT / reproducibility. Callers in backtest mode must not catch.
    """


class _BacktestCacheMissError(RuntimeError):
    """Raised when a cache miss occurs under backtest_mode=True.

    Backtest runs must be fully reproducible from the disk cache — any
    fall-through to a live API/scraper would make results depend on
    network state at replay time. Callers MUST NOT catch this; seed the
    cache first (run once in live mode) then replay.
    """


class FinMindTransientError(RuntimeError):
    """V0.22 (2026-05-06): raised on TRANSIENT FinMind API errors that should
    NOT be confused with "real empty data" (which legitimately marks done in
    V0.16 negative cache logic).

    Transient errors include:
    - "ip banned" / "ip blocked" — IP-based throttle, usually 1-24 hr
    - "unexpected response" — FinMind API anomaly, retry next run
    - "rate limit" — retry after quota window
    - "service unavailable" / 503 / 502

    Caller (cache_fill_new_factors.py) MUST NOT mark these stocks as done in
    progress JSON — V0.16 negative cache only applies to legitimate empty
    DataFrame returns (small cap / preferred / delisted with no quarterly
    statements).

    Trigger: 2026-05-06 audit found 1421 stocks (incl. TSMC 2330, 鴻海 2317,
    聯發科 2454, 台達 2308) falsely negative-cached in balance_sheet because
    FinMind returned "ip banned" during 00:38-00:57 IP ban window — V0.16
    `if df is None: api_call_succeeded_no_data = True` mistakenly classified
    transient error as legitimate empty data.
    """


# V0.22: keywords that indicate transient FinMind API failure (not real empty)
_TRANSIENT_KEYWORDS: tuple[str, ...] = (
    "ip banned",
    "ip blocked",
    "unexpected response",
    "rate limit",
    "rate-limit",
    "service unavailable",
    "too many requests",
    "503",
    "502",
    "504",
)


def _maybe_raise_transient(exc: Exception, symbol: str, dataset: str) -> None:
    """V0.22: classify exception → raise FinMindTransientError if transient.

    Caller must invoke after logger.warning(...) and before return None.
    Side effect: if msg matches transient keyword, raise FinMindTransientError;
    else no-op (caller continues to return None for legitimate empty).
    """
    msg = str(exc).lower()
    for kw in _TRANSIENT_KEYWORDS:
        if kw in msg:
            raise FinMindTransientError(
                f"Transient FinMind API error on {dataset} for {symbol}: {exc}"
            ) from exc


class _DiskCache:
    """Append-only persistent cache using pickle files.

    Layout::

        cache_dir/
          ohlcv/2330.pkl          # per-symbol time-series
          institutional/2330.pkl
          revenue/2330.pkl
          stock_info/_global.pkl  # snapshot datasets
          stock_info/_global.meta # date string for TTL expiry
    """

    def __init__(self, cache_dir: str | pathlib.Path):
        self._dir = pathlib.Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, pd.DataFrame] = {}

    def _path(self, dataset: str, symbol: str = "_global") -> pathlib.Path:
        subdir = self._dir / dataset
        subdir.mkdir(exist_ok=True)
        safe = symbol.replace("/", "_").replace("\\", "_")
        return subdir / f"{safe}.pkl"

    def load(
        self, dataset: str, symbol: str = "_global", *, strict: bool = False
    ) -> pd.DataFrame | None:
        """Load cached DataFrame; returns None if file missing.

        strict=True (use in backtest mode): corrupted file → raise
        _DiskCacheCorruptedError. Prevents silent fallback to live API.
        strict=False (default, live mode): corrupted file → warn + None,
        caller will refetch from API.
        """
        key = f"{dataset}:{symbol}"
        if key in self._mem:
            return self._mem[key]
        path = self._path(dataset, symbol)
        if not path.exists():
            return None
        try:
            df = pd.read_pickle(path)
            self._mem[key] = df
            return df
        except Exception as exc:
            logger.warning("Cache read failed for %s (keeping file): %s", path, exc)
            if strict:
                raise _DiskCacheCorruptedError(
                    f"Cache file {path} exists but cannot be read: {exc}"
                ) from exc
            return None

    def save(self, dataset: str, df: pd.DataFrame, symbol: str = "_global") -> None:
        key = f"{dataset}:{symbol}"
        self._mem[key] = df
        try:
            df.to_pickle(self._path(dataset, symbol))
        except Exception as exc:
            logger.warning("Disk cache write failed: %s", exc)

    # Lightweight metadata sidecar for TTL-based datasets.
    def meta(self, dataset: str, symbol: str = "_global") -> str | None:
        p = self._path(dataset, symbol).with_suffix(".meta")
        return p.read_text().strip() if p.exists() else None

    def save_meta(self, dataset: str, date_str: str, symbol: str = "_global") -> None:
        try:
            self._path(dataset, symbol).with_suffix(".meta").write_text(date_str)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# In-memory cache for ephemeral non-DataFrame values
# ---------------------------------------------------------------------------

class _SimpleCache:
    """Same-day in-memory cache (booleans, scalars)."""

    def __init__(self):
        self._store: dict[str, tuple[str, object]] = {}

    def get(self, key: str) -> object | None:
        today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        e = self._store.get(key)
        return e[1] if e and e[0] == today else None

    def set(self, key: str, value: object) -> None:
        self._store[key] = (datetime.now(TW_TZ).strftime("%Y-%m-%d"), value)


# ---------------------------------------------------------------------------
# FinMind data source
# ---------------------------------------------------------------------------

class FinMindSource(DataSource):
    """Fetch Taiwan stock datasets from FinMind with rate limiting and
    persistent disk caching.

    Parameters
    ----------
    cache_dir : str, optional
        Override the cache directory.  Defaults to ``DATA_CACHE_DIR`` env var
        or ``/app/data/cache`` (the Docker volume mount).
    """

    def __init__(
        self,
        token: str | None = None,
        request_interval: float = 0.5,
        use_adjusted: bool = True,
        cache_dir: str | None = None,
        backtest_mode: bool = False,
    ):
        from FinMind.data import DataLoader

        self.loader = DataLoader()
        self._request_interval = request_interval
        self._last_request_time: float = 0.0
        self._simple_cache = _SimpleCache()
        self._use_adjusted = use_adjusted

        self._backtest_mode = backtest_mode

        if cache_dir is None:
            # Shared resolver matches twse_scraper / backtest.universe so a
            # workstation without DATA_CACHE_DIR still finds <repo>/data/cache
            # instead of creating an empty /app/data/cache that silently
            # looks like "0 cached stocks".
            from ..utils.paths import resolve_cache_dir
            cache_dir = str(resolve_cache_dir())
        self._disk = _DiskCache(cache_dir)

        if token:
            self.loader.login_by_token(api_token=token)
            logger.info("FinMind token login completed")
        else:
            logger.info("FinMind token not provided; continuing with default client state")

    # ---------------------------------------------------------------- helpers

    def _rate_limit(self) -> None:
        """Ensure at least ``request_interval`` seconds between API calls."""
        elapsed = _time.monotonic() - self._last_request_time
        if elapsed < self._request_interval:
            _time.sleep(self._request_interval - elapsed)
        self._last_request_time = _time.monotonic()

    @staticmethod
    def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        """Standardize FinMind daily DataFrame into OHLCV format."""
        df = df.rename(columns={
            "date": "timestamp", "max": "high", "min": "low",
            "Trading_Volume": "volume",
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp").sort_index()
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    @staticmethod
    def _ts_naive(ts: pd.Timestamp) -> pd.Timestamp:
        return ts.tz_localize(None) if ts.tzinfo else ts

    # ----------------------------------------------------------------- OHLCV

    def _fetch_ohlcv_from_twse(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame | None:
        """Fallback: fetch OHLCV from TWSE per-stock monthly endpoint (TWSE only, not TPEX)."""
        from .twse_scraper import fetch_twse_stock_day

        all_records: list[dict] = []
        # Iterate month by month from start to end
        current = start.replace(day=1)
        while current <= end:
            records = fetch_twse_stock_day(symbol, current.year, current.month)
            all_records.extend(records)
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
            # Rate limit between TWSE calls (avoid being blocked)
            _time.sleep(0.5)

        if not all_records:
            return None

        df = pd.DataFrame(all_records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df.index = df.index.tz_localize("UTC")
        # Rename to match FinMind normalized format
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        if df.empty:
            return None
        logger.info("TWSE fallback OHLCV for %s: %d days (%s ~ %s)",
                     symbol, len(df), df.index.min().date(), df.index.max().date())
        return df

    def _api_fetch_ohlcv(self, symbol: str, start: str, end: str) -> pd.DataFrame | None:
        """Raw API call — tries adjusted price then falls back to unadjusted."""
        df = None
        if self._use_adjusted:
            df = self._fetch_adjusted_daily(symbol, start, end)
        if df is None or df.empty:
            self._rate_limit()
            try:
                df = self.loader.taiwan_stock_daily(
                    stock_id=symbol, start_date=start, end_date=end,
                )
            except Exception as exc:
                logger.error("Failed to fetch daily data for %s: %s", symbol, exc)
                return None
        if df is None or df.empty:
            return None
        return self._normalize_ohlcv(df)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame | None:
        if timeframe != "D":
            logger.warning("FinMind only supports daily data; requested timeframe=%s", timeframe)

        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")
        want_start = now - timedelta(days=int(limit * 1.8))

        cached = self._disk.load("ohlcv", symbol, strict=self._backtest_mode)

        # Backtest mode: historical data is immutable — use cache as-is, skip all refresh.
        # Cache miss (or empty) MUST raise, not fall through to live API: a silent
        # refetch breaks PIT reproducibility and makes replay network-dependent.
        if self._backtest_mode:
            if cached is None or cached.empty:
                raise _BacktestCacheMissError(
                    f"ohlcv cache miss for {symbol} in backtest_mode "
                    f"(want_start={want_start.date()}, limit={limit}); "
                    f"seed the cache in live mode before replay."
                )
            start_ts = to_utc_ts(want_start)
            result = cached[cached.index >= start_ts]
            result = result[["open", "high", "low", "close", "volume"]].dropna().tail(limit)
            return result if not result.empty else None

        # 容忍 3 天間隔（週五快取 → 週日不觸發向前延伸）
        stale_boundary = pd.Timestamp(now.date()) - pd.Timedelta(days=3)
        changed = False

        if cached is not None and not cached.empty:
            c_min = self._ts_naive(cached.index.min())
            c_max = self._ts_naive(cached.index.max())
            req_start = pd.Timestamp(want_start.date())

            # Extend backward only if cache is very sparse (< 252 rows ≈ 1 year of trading days).
            # With _WIDE_LOOKBACK_DAYS=4000, any first fetch already covers 11 years.
            # For existing symbols cached before this setting, 1000+ rows is sufficient
            # for any 3Y+ backtest — the slicer truncates to the actual backtest window.
            cached_in_range = cached[cached.index >= to_utc_ts(want_start)]
            if len(cached) < 252 and c_min > req_start + pd.Timedelta(days=1):
                old = self._api_fetch_ohlcv(
                    symbol,
                    want_start.strftime("%Y-%m-%d"),
                    (c_min - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                )
                if old is not None and not old.empty:
                    cached = pd.concat([old, cached]).sort_index()
                    cached = cached[~cached.index.duplicated(keep="last")]
                    changed = True

            # Extend forward if cache is stale (3-day tolerance for weekends/holidays).
            # Skip if data is > 1 year old — stock is likely delisted or API unavailable.
            one_year_ago = pd.Timestamp(now.date()) - pd.Timedelta(days=365)
            if c_max < stale_boundary and c_max >= one_year_ago:
                new = self._api_fetch_ohlcv(
                    symbol,
                    (c_max + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                    end_str,
                )
                if new is not None and not new.empty:
                    cached = pd.concat([cached, new]).sort_index()
                    cached = cached[~cached.index.duplicated(keep="last")]
                    changed = True
        else:
            # First fetch — use wide lookback to cover any backtest period
            wide_start = now - timedelta(days=max(int(limit * 1.8), _WIDE_LOOKBACK_DAYS))
            cached = self._api_fetch_ohlcv(symbol, wide_start.strftime("%Y-%m-%d"), end_str)
            # TWSE fallback: if FinMind fails, try TWSE per-stock monthly OHLCV.
            # Do NOT save to disk — TWSE data may differ from FinMind (no ex-dividend adjustment).
            # Return for this session only; next run retries FinMind.
            if (cached is None or cached.empty) and not self._backtest_mode:
                twse_result = self._fetch_ohlcv_from_twse(symbol, wide_start, now)
                if twse_result is not None and not twse_result.empty:
                    return twse_result  # Session-only, not persisted
            if cached is None or cached.empty:
                return None
            changed = True

        if changed:
            self._disk.save("ohlcv", cached, symbol)

        # Slice to the requested range
        start_ts = to_utc_ts(want_start)
        result = cached[cached.index >= start_ts]
        result = result[["open", "high", "low", "close", "volume"]].dropna().tail(limit)
        return result if not result.empty else None

    def _fetch_adjusted_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame | None:
        """Try to fetch adjusted prices (handles ex-dividend, capital reduction)."""
        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_daily_adj(
                stock_id=symbol, start_date=start_date, end_date=end_date,
            )
            if df is not None and not df.empty:
                logger.debug("Using adjusted price for %s", symbol)
                return df
        except (AttributeError, TypeError):
            logger.info("FinMind does not support taiwan_stock_daily_adj; adjusted prices disabled")
            self._use_adjusted = False
        except Exception as exc:
            logger.warning("Failed to fetch adjusted daily for %s: %s", symbol, exc)
            if isinstance(exc, KeyError) and str(exc) == "'data'":
                logger.info("TaiwanStockPriceAdj requires paid access; adjusted prices disabled")
                self._use_adjusted = False
        return None

    # ---------------------------------------------------------- Institutional

    def fetch_institutional(self, symbol: str, days: int = 30) -> pd.DataFrame | None:
        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")

        cached = self._disk.load("institutional", symbol, strict=self._backtest_mode)

        # Backtest mode: use cache as-is, skip all refresh. Empty cache is a
        # valid sentinel ("no inst data for this symbol") seeded in a prior
        # live run. Truly missing (None) is a replay error — raise.
        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"institutional cache miss for {symbol} in backtest_mode "
                    f"(days={days}); seed the cache in live mode before replay."
                )
            if cached.empty:
                return None
            return cached.sort_values("date") if "date" in cached.columns else cached

        changed = False

        if cached is not None:
            # 空 DataFrame = 哨兵（此 symbol 無法人資料），不重複呼叫 API
            if cached.empty:
                return None
            if "date" in cached.columns:
                c_max = pd.Timestamp(cached["date"].max())
                # 7-day tolerance (handles weekends + institutional report lag)
                inst_stale_boundary = pd.Timestamp(now.date()) - pd.Timedelta(days=7)
                one_year_ago = pd.Timestamp(now.date()) - pd.Timedelta(days=365)
                if c_max < inst_stale_boundary and c_max >= one_year_ago:
                    new_df = self._api_fetch_institutional(
                        symbol,
                        (c_max + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                        end_str,
                    )
                    if new_df is not None and not new_df.empty:
                        cached = self._merge_institutional(cached, new_df)
                        changed = True
        else:
            wide_start = now - timedelta(days=max(int(days * 1.5), _WIDE_LOOKBACK_DAYS))
            cached = self._api_fetch_institutional(
                symbol, wide_start.strftime("%Y-%m-%d"), end_str,
            )
            if cached is None or cached.empty:
                # Don't persist empty sentinel to disk — a transient API failure
                # (rate limit, network) would permanently block retries.
                # _DataSlicer's in-memory cache handles per-run dedup.
                return None
            changed = True

        if changed:
            self._disk.save("institutional", cached, symbol)

        return cached.sort_values("date") if (cached is not None and "date" in cached.columns) else cached

    def _api_fetch_institutional(self, symbol: str, start: str, end: str) -> pd.DataFrame | None:
        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_institutional_investors(
                stock_id=symbol, start_date=start, end_date=end,
            )
        except KeyError as exc:
            if str(exc) == "'data'":
                logger.warning(
                    "Institutional dataset unavailable for %s with current FinMind access; "
                    "factor will be empty for this symbol",
                    symbol,
                )
                return None
            logger.warning("Failed to fetch institutional data for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to fetch institutional data for %s: %s", symbol, exc)
            return None

        if df is None or df.empty:
            return None

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        for col in ("buy", "sell"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df.sort_values("date")

    @staticmethod
    def _merge_institutional(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
        merged = pd.concat([old, new])
        dedup_cols = ["date", "name"] if "name" in merged.columns else ["date"]
        return merged.drop_duplicates(subset=dedup_cols, keep="last").sort_values("date")

    # ---------------------------------------------------- Margin / Short Sale

    def fetch_margin_short(
        self, symbol: str, start_date: str = "2019-01-01",
    ) -> pd.DataFrame | None:
        """Fetch margin purchase + short sale history.

        FinMind dataset: TaiwanStockMarginPurchaseShortSale.
        Columns: date, stock_id, MarginPurchaseBuy / Sell / CashRepayment /
                 TodayBalance / YesterdayBalance / Limit,
                 ShortSaleBuy / Sell / CashRepayment / TodayBalance /
                 YesterdayBalance / Limit, OffsetLoanAndShort, Note.

        Cached at data/cache/margin_short/<symbol>.pkl.
        Does NOT share cache with fetch_institutional (different dataset).
        """
        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")

        cached = self._disk.load("margin_short", symbol, strict=self._backtest_mode)

        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"margin_short cache miss for {symbol} in backtest_mode; "
                    f"seed the cache in live mode before replay."
                )
            if cached.empty:
                return None
            return cached.sort_values("date") if "date" in cached.columns else cached

        changed = False
        if cached is not None:
            if cached.empty:
                return None
            if "date" in cached.columns:
                c_max = pd.Timestamp(cached["date"].max())
                stale_boundary = pd.Timestamp(now.date()) - pd.Timedelta(days=3)
                one_year_ago = pd.Timestamp(now.date()) - pd.Timedelta(days=365)
                if c_max < stale_boundary and c_max >= one_year_ago:
                    new_df = self._api_fetch_margin_short(
                        symbol,
                        (c_max + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                        end_str,
                    )
                    if new_df is not None and not new_df.empty:
                        cached = pd.concat([cached, new_df]).drop_duplicates(
                            subset=["date"], keep="last",
                        ).sort_values("date")
                        changed = True
        else:
            cached = self._api_fetch_margin_short(symbol, start_date, end_str)
            if cached is None or cached.empty:
                return None
            changed = True

        if changed:
            self._disk.save("margin_short", cached, symbol)

        return cached.sort_values("date") if (cached is not None and "date" in cached.columns) else cached

    def _api_fetch_margin_short(
        self, symbol: str, start: str, end: str,
    ) -> pd.DataFrame | None:
        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_margin_purchase_short_sale(
                stock_id=symbol, start_date=start, end_date=end,
            )
        except KeyError as exc:
            # FinMind 402 quota response lacks "data" key. Genuine "no data"
            # returns status 200 with data=[] (no KeyError). So KeyError("'data'")
            # is always quota — re-raise so caller can rotate tokens.
            if str(exc) == "'data'":
                raise
            logger.warning("Failed to fetch margin/short for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to fetch margin/short for %s: %s", symbol, exc)
            return None

        if df is None or df.empty:
            return None

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        numeric_cols = [
            "MarginPurchaseBuy", "MarginPurchaseSell", "MarginPurchaseCashRepayment",
            "MarginPurchaseTodayBalance", "MarginPurchaseYesterdayBalance", "MarginPurchaseLimit",
            "ShortSaleBuy", "ShortSaleSell", "ShortSaleCashRepayment",
            "ShortSaleTodayBalance", "ShortSaleYesterdayBalance", "ShortSaleLimit",
            "OffsetLoanAndShort",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values("date")

    # --------------------------------------- Three Institutional (v2, buy/sell)

    def fetch_three_institutional(
        self, symbol: str, start_date: str = "2019-01-01",
    ) -> pd.DataFrame | None:
        """Fetch three major institutional investors buy/sell history (new version).

        FinMind dataset: TaiwanStockInstitutionalInvestorsBuySell.
        Row-per-(date, stock_id, name) where name ∈ {Foreign_Investor,
        Investment_Trust, Dealer_self, Dealer_Hedging}. Columns: buy, sell.

        Cached at data/cache/institutional_v2/<symbol>.pkl.
        Legacy fetch_institutional (data/cache/institutional/) retained for
        backward-compatibility with old factor; do NOT overwrite.
        """
        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")

        cached = self._disk.load("institutional_v2", symbol, strict=self._backtest_mode)

        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"institutional_v2 cache miss for {symbol} in backtest_mode; "
                    f"seed the cache in live mode before replay."
                )
            if cached.empty:
                return None
            return cached.sort_values("date") if "date" in cached.columns else cached

        changed = False
        if cached is not None:
            if cached.empty:
                return None
            if "date" in cached.columns:
                c_max = pd.Timestamp(cached["date"].max())
                stale_boundary = pd.Timestamp(now.date()) - pd.Timedelta(days=3)
                one_year_ago = pd.Timestamp(now.date()) - pd.Timedelta(days=365)
                if c_max < stale_boundary and c_max >= one_year_ago:
                    new_df = self._api_fetch_three_institutional(
                        symbol,
                        (c_max + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                        end_str,
                    )
                    if new_df is not None and not new_df.empty:
                        cached = self._merge_institutional(cached, new_df)
                        changed = True
        else:
            cached = self._api_fetch_three_institutional(symbol, start_date, end_str)
            if cached is None or cached.empty:
                return None
            changed = True

        if changed:
            self._disk.save("institutional_v2", cached, symbol)

        return cached.sort_values("date") if (cached is not None and "date" in cached.columns) else cached

    def _api_fetch_three_institutional(
        self, symbol: str, start: str, end: str,
    ) -> pd.DataFrame | None:
        self._rate_limit()
        try:
            # FinMind DataLoader's taiwan_stock_institutional_investors now
            # maps to Dataset.TaiwanStockInstitutionalInvestorsBuySell (returns
            # buy/sell/name columns). Shared method with legacy fetch_institutional
            # but stored in a different cache directory.
            df = self.loader.taiwan_stock_institutional_investors(
                stock_id=symbol, start_date=start, end_date=end,
            )
        except KeyError as exc:
            # KeyError("'data'") is always a 402 quota response — re-raise.
            if str(exc) == "'data'":
                raise
            logger.warning("Failed to fetch institutional_v2 for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to fetch institutional_v2 for %s: %s", symbol, exc)
            return None

        if df is None or df.empty:
            return None

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        for col in ("buy", "sell"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df.sort_values("date")

    # ---------------------------------------------------- Quarterly EPS

    def fetch_quarterly_eps(
        self, symbol: str, start_date: str = "2016-01-01",
    ) -> pd.DataFrame | None:
        """Fetch quarterly EPS history.

        FinMind dataset: TaiwanStockFinancialStatements (filter type="EPS").
        Returns DataFrame with columns: date, stock_id, type, value (= EPS in NTD).

        Cached at data/cache/quarterly_eps/<symbol>.pkl (EPS-only subset to
        keep cache compact; other fin-statement types fetched separately via
        fetch_financial_quality).
        """
        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")

        cached = self._disk.load("quarterly_eps", symbol, strict=self._backtest_mode)

        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"quarterly_eps cache miss for {symbol} in backtest_mode; "
                    f"seed the cache in live mode before replay."
                )
            if cached.empty:
                return None
            return cached.sort_values("date") if "date" in cached.columns else cached

        changed = False
        if cached is not None:
            if cached.empty:
                return None
            if "date" in cached.columns:
                c_max = pd.Timestamp(cached["date"].max())
                # Quarterly: stale if older than 100 days (one quarter + buffer)
                stale_boundary = pd.Timestamp(now.date()) - pd.Timedelta(days=100)
                two_years_ago = pd.Timestamp(now.date()) - pd.Timedelta(days=730)
                if c_max < stale_boundary and c_max >= two_years_ago:
                    new_df = self._api_fetch_quarterly_eps(
                        symbol,
                        (c_max + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                        end_str,
                    )
                    if new_df is not None and not new_df.empty:
                        cached = pd.concat([cached, new_df]).drop_duplicates(
                            subset=["date"], keep="last",
                        ).sort_values("date")
                        changed = True
        else:
            cached = self._api_fetch_quarterly_eps(symbol, start_date, end_str)
            if cached is None or cached.empty:
                return None
            changed = True

        if changed:
            self._disk.save("quarterly_eps", cached, symbol)

        return cached.sort_values("date") if (cached is not None and "date" in cached.columns) else cached

    def _api_fetch_quarterly_eps(
        self, symbol: str, start: str, end: str,
    ) -> pd.DataFrame | None:
        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_financial_statement(
                stock_id=symbol, start_date=start, end_date=end,
            )
        except KeyError as exc:
            # KeyError("'data'") is always a 402 quota response — re-raise.
            if str(exc) == "'data'":
                raise
            logger.warning("Failed to fetch EPS for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to fetch EPS for %s: %s", symbol, exc)
            return None

        if df is None or df.empty:
            return None

        # Keep only EPS rows (drop Revenue / GrossProfit / IncomeAfterTaxes / ...)
        if "type" in df.columns:
            df = df[df["type"] == "EPS"].copy()
        if df.empty:
            return None

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        if "value" in df.columns:
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.sort_values("date")

    # --------------------------------------------------------- Month Revenue

    def fetch_month_revenue(self, symbol: str, months: int = 15) -> pd.DataFrame | None:
        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")

        cached = self._disk.load("revenue", symbol, strict=self._backtest_mode)

        # Backtest mode: use cache as-is, skip all refresh / TWSE fallback.
        # Missing cache (None) → raise; empty sentinel is valid.
        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"month_revenue cache miss for {symbol} in backtest_mode "
                    f"(months={months}); seed the cache in live mode before replay."
                )
            if cached.empty:
                return None
            return cached

        changed = False

        if cached is not None:
            # 空 DataFrame = 哨兵（FinMind 曾失敗）。嘗試 TWSE fallback 但不存 disk。
            if cached.empty:
                if not self._backtest_mode:
                    twse_result = self._fetch_revenue_from_twse(symbol)
                    if twse_result is not None and not twse_result.empty:
                        return twse_result  # 回傳但不存 disk，讓下次重試 FinMind
                return None
            if "date" in cached.columns:
                c_max = pd.Timestamp(cached["date"].max())
                # Revenue is monthly; stale if older than 45 days.
                # Skip if > 1 year old — stock likely delisted or API unavailable.
                stale_threshold = pd.Timestamp(now.date()) - pd.Timedelta(days=45)
                one_year_ago = pd.Timestamp(now.date()) - pd.Timedelta(days=365)
                if c_max < stale_threshold and c_max >= one_year_ago:
                    fetch_start = (c_max - pd.Timedelta(days=35)).strftime("%Y-%m-%d")
                    new_df = self._api_fetch_revenue(symbol, fetch_start, end_str)
                    if new_df is not None and not new_df.empty:
                        cached = pd.concat([cached, new_df]).drop_duplicates(
                            subset=["date"], keep="last",
                        ).sort_values("date")
                        changed = True
        else:
            wide_start = now - timedelta(days=max(int(months * 35), _WIDE_LOOKBACK_DAYS))
            cached = self._api_fetch_revenue(
                symbol, wide_start.strftime("%Y-%m-%d"), end_str,
            )
            # TWSE fallback: if FinMind fails, try TWSE OpenData for latest month.
            # Do NOT save to disk — TWSE only has 1 month, insufficient for YoY.
            # Next run will retry FinMind for full history.
            if (cached is None or cached.empty) and not self._backtest_mode:
                twse_result = self._fetch_revenue_from_twse(symbol)
                if twse_result is not None and not twse_result.empty:
                    return twse_result  # Return for this session only, don't persist
            if cached is None or cached.empty:
                # 不存哨兵 — 讓下次還能重試 FinMind
                return None
            changed = True

        if changed:
            self._disk.save("revenue", cached, symbol)

        return cached

    # In-memory cache for TWSE monthly revenue (one API call covers all stocks)
    _twse_revenue_cache: dict[str, dict] | None = None

    def _fetch_revenue_from_twse(self, symbol: str) -> pd.DataFrame | None:
        """Fallback: get latest month revenue from TWSE/TPEX OpenData."""
        # Cache the full-market fetch (one API call per session)
        if FinMindSource._twse_revenue_cache is None:
            from .twse_scraper import fetch_twse_monthly_revenue
            FinMindSource._twse_revenue_cache = fetch_twse_monthly_revenue()

        entry = FinMindSource._twse_revenue_cache.get(symbol)
        if not entry or not entry.get("date") or not entry.get("revenue"):
            return None

        df = pd.DataFrame([{"date": entry["date"], "revenue": entry["revenue"]}])
        df["date"] = pd.to_datetime(df["date"])
        df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
        logger.info("TWSE revenue fallback for %s: %s = %.0f",
                     symbol, entry["date"], entry["revenue"])
        return df

    def _api_fetch_revenue(self, symbol: str, start: str, end: str) -> pd.DataFrame | None:
        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_month_revenue(
                stock_id=symbol, start_date=start, end_date=end,
            )
        except Exception as exc:
            logger.warning("Failed to fetch month revenue for %s: %s", symbol, exc)
            return None

        if df is None or df.empty:
            return None

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
        if "revenue" in df.columns:
            df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
        return df

    # ----------------------------------------------------------- Stock Info

    def fetch_stock_info(self) -> pd.DataFrame | None:
        # Snapshot dataset — cache with 7-day TTL
        cached = self._disk.load("stock_info", strict=self._backtest_mode)

        # Backtest mode: use cache as-is, skip TTL check. Missing cache must
        # raise — falling through to loader.taiwan_stock_info() would yield
        # today's snapshot instead of the as-of snapshot.
        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    "stock_info cache miss in backtest_mode; "
                    "seed the cache in live mode before replay."
                )
            self._ensure_stock_info_csv(cached)
            return cached

        meta = self._disk.meta("stock_info")
        if cached is not None and meta:
            if (datetime.now(TW_TZ).replace(tzinfo=None) - datetime.strptime(meta, "%Y-%m-%d")).days < 7:
                # pickle cache 有效 — 確保 CSV 備援也存在
                self._ensure_stock_info_csv(cached)
                return cached

        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_info()
        except KeyError as exc:
            if str(exc) == "'data'":
                logger.warning("Stock info dataset unavailable with current FinMind access")
            else:
                logger.warning("Failed to fetch stock info: %s", exc)
            return cached if (cached is not None and not cached.empty) else self._load_stock_info_csv_fallback()
        except Exception as exc:
            logger.warning("Failed to fetch stock info: %s", exc)
            return cached if (cached is not None and not cached.empty) else self._load_stock_info_csv_fallback()

        if df is None or df.empty:
            return cached if (cached is not None and not cached.empty) else self._load_stock_info_csv_fallback()

        result = df.copy()
        self._disk.save("stock_info", result)
        self._disk.save_meta("stock_info", datetime.now(TW_TZ).strftime("%Y-%m-%d"))
        # 每次 API 成功後同步更新 CSV 快照，供 pickle 損壞時作為最終備援
        self._save_stock_info_csv_snapshot(result)
        return result

    def _load_stock_info_csv_fallback(self) -> pd.DataFrame | None:
        """最終備援：從本地 CSV 快照讀取 stock_info。"""
        csv_path = self._disk._dir / "stock_info" / "stock_info_snapshot.csv"
        if not csv_path.exists():
            logger.warning("No CSV fallback for stock_info at %s", csv_path)
            return None
        try:
            df = pd.read_csv(csv_path, dtype=str)
            logger.info("Loaded stock_info from CSV fallback (%d rows)", len(df))
            return df
        except Exception as exc:
            logger.warning("Failed to read stock_info CSV fallback: %s", exc)
            return None

    def _save_stock_info_csv_snapshot(self, df: pd.DataFrame) -> None:
        """將 stock_info 存為 CSV 快照（UTF-8），供 pickle 備援。"""
        try:
            csv_path = self._disk._dir / "stock_info" / "stock_info_snapshot.csv"
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(csv_path, index=False, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save stock_info CSV snapshot: %s", exc)

    def _ensure_stock_info_csv(self, df: pd.DataFrame) -> None:
        """確保 CSV 備援存在；已存在則跳過，避免每次 cache hit 都寫磁碟。"""
        csv_path = self._disk._dir / "stock_info" / "stock_info_snapshot.csv"
        if not csv_path.exists():
            logger.info("CSV snapshot missing — creating from pickle cache")
            self._save_stock_info_csv_snapshot(df)

    # --------------------------------------------------------- Market Value

    def fetch_market_value(self, days: int = 10) -> pd.DataFrame | None:
        # Snapshot dataset — cache with 3-day TTL
        cached = self._disk.load("market_value", strict=self._backtest_mode)

        # Backtest mode: use cache as-is, skip TTL check / TWSE recompute.
        # Missing cache must raise — `_compute_market_value_from_twse` would
        # otherwise hit the live TWSE OpenAPI for today's shares-outstanding.
        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"market_value cache miss in backtest_mode (days={days}); "
                    f"seed the cache in live mode before replay."
                )
            return cached

        meta = self._disk.meta("market_value")
        if cached is not None and meta:
            if (datetime.now(TW_TZ).replace(tzinfo=None) - datetime.strptime(meta, "%Y-%m-%d")).days < 7:
                return cached

        # --- Primary: compute from TWSE shares × OHLCV cache ---
        result = self._compute_market_value_from_twse()

        # --- Fallback: FinMind API (for paid accounts) ---
        if result is None:
            result = self._fetch_market_value_finmind(days)

        if result is not None and not result.empty:
            self._disk.save("market_value", result)
            self._disk.save_meta("market_value", datetime.now(TW_TZ).strftime("%Y-%m-%d"))
            return result

        return cached

    def _compute_market_value_from_twse(self) -> pd.DataFrame | None:
        """Compute market_value = TWSE shares_outstanding × OHLCV close prices.

        Reads shares outstanding from TWSE OpenAPI (one API call), then
        multiplies by historical close prices from the OHLCV disk cache to
        produce a full historical market_value DataFrame.

        ⚠️ **NOT FULLY PIT** (Codex R30 finding 2, 2026-05-11)：
            shares 是 cache build 時 fetch_twse_issued_capital() 的 latest
            snapshot；historical close 是真歷史。所以每個 (stock_id, date) row：
              - close: PIT-correct ✅
              - shares: latest snapshot 對所有 date 一致 ❌
            market_value = latest_shares × historical_close ≠ fully PIT mv.

            台股 shares 變動少（除權息 / 減增資）→ ratio approximation 仍可用，
            但嚴格 pro 標準 (factor 用 market_value 當分母) 仍是 substance-level
            caveat。完整修法（P1 backlog）：寫新 TWSE OpenAPI scraper 抓歷史
            shares snapshots 取代 latest fetch_twse_issued_capital().

            詳見 `reports/factor_ic/_closeout/old_vs_new_comparison_2026-05-10.md`
            section 9.2.
        """
        from .twse_scraper import fetch_twse_issued_capital

        shares = fetch_twse_issued_capital()
        if not shares:
            return None

        ohlcv_dir = self._disk._dir / "ohlcv"
        if not ohlcv_dir.exists():
            logger.warning("OHLCV cache directory not found; cannot compute historical market value")
            return None

        records: list[dict] = []
        read_count = 0
        for pkl_path in ohlcv_dir.glob("*.pkl"):
            symbol = pkl_path.stem
            if symbol not in shares:
                continue
            # Read directly instead of _DiskCache.load() to avoid
            # file deletion on pickle version incompatibility (Windows).
            try:
                df = pd.read_pickle(pkl_path)
            except Exception as exc:
                # Backtest mode: corrupt cache must not silently skip —
                # that would make market-value ranking depend on which
                # files happen to be readable, breaking PIT reproducibility.
                if self._backtest_mode:
                    raise _DiskCacheCorruptedError(
                        f"OHLCV cache {pkl_path} corrupted in backtest mode: {exc}"
                    ) from exc
                continue
            if df is None or df.empty or "close" not in df.columns:
                continue
            read_count += 1
            # Sample monthly (last trading day of each month) to keep cache compact
            close = df[["close"]].copy()
            close.index = pd.to_datetime(close.index, utc=True)
            monthly = close.resample("ME").last().dropna()
            for ts, row in monthly.iterrows():
                # Strip timezone to produce naive dates (consistent with _DataSlicer)
                records.append({
                    "stock_id": symbol,
                    "date": ts.tz_localize(None),
                    "market_value": float(row["close"]) * shares[symbol],
                })

        if not records:
            logger.warning("No OHLCV cache could be read for market value computation")
            return None

        result = pd.DataFrame(records)
        result["date"] = pd.to_datetime(result["date"])
        result["market_value"] = pd.to_numeric(result["market_value"], errors="coerce")
        result = result.sort_values(["stock_id", "date"]).reset_index(drop=True)
        logger.info(
            "Market value computed from TWSE shares × OHLCV cache: "
            "%d stocks, %d monthly records",
            read_count, len(result),
        )
        return result

    def _fetch_market_value_finmind(self, days: int = 10) -> pd.DataFrame | None:
        """Fallback: fetch market_value from FinMind (requires paid account)."""
        end_date = datetime.now(TW_TZ)
        start_date = end_date - timedelta(days=max(days, 5))

        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_market_value(
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
            )
        except KeyError as exc:
            if str(exc) == "'data'":
                logger.info("FinMind market_value unavailable (free tier); using TWSE computation")
            else:
                logger.warning("Failed to fetch market value from FinMind: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Failed to fetch market value from FinMind: %s", exc)
            return None

        if df is None or df.empty:
            return None

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        if "market_value" in df.columns:
            df["market_value"] = pd.to_numeric(df["market_value"], errors="coerce")

        return df.sort_values(["stock_id", "date"]).dropna(subset=["stock_id"])

    # -------------------------------------------------------------- Delisting

    def fetch_delisting(self) -> pd.DataFrame | None:
        cached = self._disk.load("delisting", strict=self._backtest_mode)

        # Backtest mode: use cache as-is, skip TTL check. Missing cache must
        # raise — the live FinMind delisting endpoint reflects today's state,
        # which leaks post-as-of delistings into historical replay.
        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    "delisting cache miss in backtest_mode; "
                    "seed the cache in live mode before replay."
                )
            return cached

        meta = self._disk.meta("delisting")
        if cached is not None and meta:
            if (datetime.now(TW_TZ).replace(tzinfo=None) - datetime.strptime(meta, "%Y-%m-%d")).days < 7:
                return cached

        self._rate_limit()
        try:
            df = self.loader.taiwan_stock_delisting()
            if df is not None and not df.empty:
                self._disk.save("delisting", df)
                self._disk.save_meta("delisting", datetime.now(TW_TZ).strftime("%Y-%m-%d"))
                return df
        except (AttributeError, TypeError):
            logger.info("FinMind does not support taiwan_stock_delisting")
        except Exception as exc:
            logger.warning("Failed to fetch delisting data: %s", exc)
        return cached

    # -------------------------------------------------------- Financial quality

    def fetch_financial_quality(self, symbol: str) -> dict | None:
        """取得最新一季的品質指標（ROE、毛利率）。

        使用 FinMind TaiwanStockFinancialStatements + TaiwanStockBalanceSheet。
        快取 90 天（季報每季才更新）。
        """
        cache_key = f"quality:{symbol}"
        cached = self._disk.load("quality", symbol, strict=self._backtest_mode)

        # Backtest mode: quality snapshots must come from cache only — the
        # live FinMind statement/balance-sheet endpoints return whatever
        # quarter is most recent at replay time, not the as-of quarter.
        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"financial_quality cache miss for {symbol} in backtest_mode; "
                    f"seed the cache in live mode before replay."
                )
            return cached.to_dict("records")[0] if hasattr(cached, "to_dict") else cached

        meta = self._disk.meta("quality", symbol)
        if cached is not None and meta:
            try:
                if (datetime.now(TW_TZ).replace(tzinfo=None) - datetime.strptime(meta, "%Y-%m-%d")).days < 90:
                    # cached is a pickle of dict
                    return cached.to_dict("records")[0] if hasattr(cached, "to_dict") else cached
            except Exception:
                pass

        self._rate_limit()
        try:
            start = (datetime.now(TW_TZ) - timedelta(days=400)).strftime("%Y-%m-%d")
            fs = self.loader.taiwan_stock_financial_statement(stock_id=symbol, start_date=start)
            if fs is None or fs.empty:
                return None

            self._rate_limit()
            bs = self.loader.taiwan_stock_balance_sheet(stock_id=symbol, start_date=start)
            if bs is None or bs.empty:
                return None

            # 取最新一季
            latest_date = fs["date"].max()
            fs_latest = fs[fs["date"] == latest_date]
            bs_latest = bs[bs["date"] == latest_date]

            def _get(df, type_key):
                rows = df[df["type"] == type_key]
                if rows.empty:
                    return None
                return float(rows.iloc[-1]["value"])

            revenue = _get(fs_latest, "Revenue")
            gross_profit = _get(fs_latest, "GrossProfit")
            net_income = _get(fs_latest, "IncomeAfterTaxes")
            equity = _get(bs_latest, "Equity")

            result = {
                "date": str(latest_date),
                "roe": (net_income / equity * 4) if equity and net_income and equity > 0 else None,
                "gross_margin": (gross_profit / revenue) if revenue and gross_profit and revenue > 0 else None,
            }

            # 存快取（用 DataFrame 包裝以相容 _DiskCache）
            import pandas as _pd
            self._disk.save("quality", _pd.DataFrame([result]), symbol)
            self._disk.save_meta("quality", datetime.now(TW_TZ).strftime("%Y-%m-%d"), symbol)
            return result

        except Exception as exc:
            logger.debug("Failed to fetch financial quality for %s: %s", symbol, exc)
            return None

    # ----------------------------------------- quarterly_financial_full (V0.14 P-B)
    # S6.1 Path B (R25-mid Codex audit P-B fix, 2026-05-05): full quarterly
    # income statement history (Revenue / GrossProfit / IncomeAfterTaxes / EPS
    # / etc) for D-E quality_v3 PIT-correct TTM ROE + gross_margin computation.
    # Existing fetch_quarterly_eps stores EPS-only subset (per finmind.py:674
    # "EPS-only subset to keep cache compact"); this method preserves all rows.
    def fetch_quarterly_financial_full(
        self, symbol: str, start_date: str = "2016-01-01",
    ) -> pd.DataFrame | None:
        """Fetch full quarterly income statement (no type filter).

        Cache: data/cache/quarterly_financial_full/<symbol>.pkl
        FinMind dataset: TaiwanStockFinancialStatements (full table; no EPS filter).
        Returns DataFrame with columns: date, stock_id, type, value covering
        Revenue / GrossProfit / IncomeAfterTaxes / EPS / OperatingIncome / 等.
        Caller (quality_v3_aggregator) pivots to wide format for TTM rolling.
        """
        cached = self._disk.load("quarterly_financial_full", symbol, strict=self._backtest_mode)

        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"quarterly_financial_full cache miss for {symbol} in backtest_mode; "
                    f"seed the cache in live mode before replay."
                )
            if cached.empty:
                return None
            return cached.sort_values("date") if "date" in cached.columns else cached

        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")

        if cached is None or cached.empty:
            self._rate_limit()
            try:
                df = self.loader.taiwan_stock_financial_statement(
                    stock_id=symbol, start_date=start_date, end_date=end_str,
                )
            except KeyError as exc:
                if str(exc) == "'data'":
                    raise
                logger.warning("Failed to fetch financial_full for %s: %s", symbol, exc)
                _maybe_raise_transient(exc, symbol, "financial_full")
                return None
            except Exception as exc:
                logger.warning("Failed to fetch financial_full for %s: %s", symbol, exc)
                _maybe_raise_transient(exc, symbol, "financial_full")
                return None
            if df is None or df.empty:
                return None
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            if "value" in df.columns:
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.sort_values("date")
            self._disk.save("quarterly_financial_full", df, symbol)
            return df

        return cached.sort_values("date") if "date" in cached.columns else cached

    # ----------------------------------------- balance_sheet (V0.14 P-B)
    # S6.1 Path B (R25-mid Codex audit P-B fix): quarterly balance sheet
    # history (Equity / TotalAssets / TotalLiabilities / etc) for D-E
    # quality_v3 Δassets YoY + ROE Equity 分母. Existing fetch_financial_quality
    # cached single-snapshot dict (line 1112-1183); this method stores full
    # history per symbol for PIT-correct quarterly rolling.
    def fetch_balance_sheet_history(
        self, symbol: str, start_date: str = "2016-01-01",
    ) -> pd.DataFrame | None:
        """Fetch quarterly balance sheet history.

        Cache: data/cache/balance_sheet/<symbol>.pkl
        FinMind dataset: TaiwanStockBalanceSheet (full history).
        Returns DataFrame with columns: date, stock_id, type, value covering
        Equity / TotalAssets / TotalLiabilities / etc per quarter.
        """
        cached = self._disk.load("balance_sheet", symbol, strict=self._backtest_mode)

        if self._backtest_mode:
            if cached is None:
                raise _BacktestCacheMissError(
                    f"balance_sheet cache miss for {symbol} in backtest_mode; "
                    f"seed the cache in live mode before replay."
                )
            if cached.empty:
                return None
            return cached.sort_values("date") if "date" in cached.columns else cached

        now = datetime.now(TW_TZ)
        end_str = now.strftime("%Y-%m-%d")

        if cached is None or cached.empty:
            self._rate_limit()
            try:
                df = self.loader.taiwan_stock_balance_sheet(
                    stock_id=symbol, start_date=start_date, end_date=end_str,
                )
            except KeyError as exc:
                if str(exc) == "'data'":
                    raise
                logger.warning("Failed to fetch balance_sheet for %s: %s", symbol, exc)
                _maybe_raise_transient(exc, symbol, "balance_sheet")
                return None
            except Exception as exc:
                logger.warning("Failed to fetch balance_sheet for %s: %s", symbol, exc)
                _maybe_raise_transient(exc, symbol, "balance_sheet")
                return None
            if df is None or df.empty:
                return None
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            if "value" in df.columns:
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.sort_values("date")
            self._disk.save("balance_sheet", df, symbol)
            return df

        return cached.sort_values("date") if "date" in cached.columns else cached

    # --------------------------------------------------------- Market status

    def is_market_open(self) -> bool:
        now = datetime.now(TW_TZ)
        if now.weekday() >= 5:
            return False
        if not self.is_trading_day():
            return False
        market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
        market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)
        return market_open <= now <= market_close

    def is_trading_day(self) -> bool:
        """Check if today is a trading day by querying 0050 recent data.

        Only caches True; False is retried each cycle in case data hasn't
        been published yet.
        """
        now = datetime.now(TW_TZ)
        if now.weekday() >= 5:
            return False

        cache_key = "is_trading_day"
        cached = self._simple_cache.get(cache_key)
        if cached is not None:
            return cached

        today_str = now.strftime("%Y-%m-%d")
        start_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")
        try:
            self._rate_limit()
            df = self.loader.taiwan_stock_daily(
                stock_id="0050",
                start_date=start_date,
                end_date=today_str,
            )
            if df is not None and not df.empty and "date" in df.columns:
                trading_dates = set(str(d)[:10] for d in df["date"])
                is_today_trading = today_str in trading_dates
                if is_today_trading:
                    self._simple_cache.set(cache_key, True)
                return is_today_trading
        except Exception as exc:
            logger.warning("Failed to check trading day via market data: %s", exc)

        return False

    # -------------------------------------------------------- Dividends (P4.5)

    def fetch_dividends(self, start_year: int, end_year: int) -> list[dict] | None:
        """Fetch ex-dividend records for total-return price adjustment.

        Uses TWSE scraping with disk cache (dataset='dividends', TTL=7 days).
        Returns list of dicts: {stock_id, ex_date, cash_dividend, ...}.

        Note: dividend records are a list (not DataFrame), so we use pickle
        directly instead of the _DiskCache helper which expects DataFrames.
        """
        import pickle as _pkl

        cache_path = self._disk._path("dividends")

        # Try loading from disk cache.
        # Parity with _DiskCache.load(strict=backtest_mode): in backtest mode a
        # corrupt pickle must raise (not silently fall through to live scrape),
        # otherwise dividend-adjusted returns silently degrade to price-only
        # for whatever years fail to deserialize.
        cached: list[dict] | None = None
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    cached = _pkl.load(f)
            except Exception as exc:
                logger.warning("Dividend cache read failed: %s", exc)
                if self._backtest_mode:
                    raise _DiskCacheCorruptedError(
                        f"Dividend cache {cache_path} corrupted in backtest mode: {exc}"
                    ) from exc

        if self._backtest_mode:
            # Backtest: dividends must come from cache only. Returning None on
            # miss is valid (callers treat as "no dividend data available for
            # this window"); the key requirement is that we never reach the
            # live TWSE scraper, which would make total-return backtests
            # depend on scrape-time network state.
            return cached

        meta = self._disk.meta("dividends")
        if cached is not None and meta:
            if (datetime.now(TW_TZ).replace(tzinfo=None) - datetime.strptime(meta, "%Y-%m-%d")).days < 7:
                return cached

        from .twse_scraper import fetch_twse_dividends

        try:
            records = fetch_twse_dividends(start_year, end_year)
            if records:
                try:
                    with open(cache_path, "wb") as f:
                        _pkl.dump(records, f)
                except Exception as exc:
                    logger.warning("Dividend cache write failed: %s", exc)
                self._disk.save_meta("dividends", datetime.now(TW_TZ).strftime("%Y-%m-%d"))
                return records
        except Exception as exc:
            logger.warning("Failed to fetch TWSE dividends: %s", exc)

        return cached
