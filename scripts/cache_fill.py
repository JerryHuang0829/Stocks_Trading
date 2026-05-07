"""Cache Fill — 補齊 + 更新 OHLCV + Revenue 資料。

可中斷恢復：進度記錄在 data/cache_fill_progress.json。

Usage:
    # 每天 15:00 後執行（TWSE STOCK_DAY_ALL，只用 2 次 request，不消耗 FinMind）
    python scripts/cache_fill.py --daily

    # 每天 15:00 後執行（TPEX 上櫃股，用 FinMind 抓當月最新，~7 min，881 次 API）
    python scripts/cache_fill.py --daily-tpex

    # 每月 1-15 號加跑（更新月營收，~3hr FinMind）
    python scripts/cache_fill.py --revenue-only

    # 只補缺失的（預設，用 FinMind）
    python scripts/cache_fill.py

    # 全面更新（補缺失 + 更新過時的到最新，用 FinMind）
    python scripts/cache_fill.py --refresh-all

    # 只補 top-80
    python scripts/cache_fill.py --top-80-only

    # 查看進度
    python scripts/cache_fill.py --status
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_OHLCV_REQUIRED_COLS = {"open", "high", "low", "close", "volume"}


def _validate_ohlcv(df: pd.DataFrame, label: str) -> bool:
    """Return True if df has all required OHLCV columns."""
    missing = _OHLCV_REQUIRED_COLS - set(df.columns)
    if missing:
        logger.warning("%s: missing OHLCV columns %s — skipping write", label, missing)
        return False
    return True
CACHE_DIR = pathlib.Path(os.environ.get("DATA_CACHE_DIR", PROJECT_ROOT / "data" / "cache"))
PROGRESS_FILE = PROJECT_ROOT / "data" / "cache_fill_progress.json"


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"ohlcv_done": [], "revenue_done": []}


def _save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)


def _get_tradeable_stocks() -> set[str]:
    """Return deduplicated set of tradeable stock_ids (excl ETF, emerging, delisted)."""
    csv_path = CACHE_DIR / "stock_info" / "stock_info_snapshot.csv"
    pkl_path = CACHE_DIR / "stock_info" / "_global.pkl"

    if csv_path.exists():
        si = pd.read_csv(csv_path)
    elif pkl_path.exists():
        import pickle
        with open(pkl_path, "rb") as f:
            si = pd.DataFrame(pickle.load(f))
        logger.info("stock_info: loaded from _global.pkl (no CSV snapshot)")
    else:
        raise FileNotFoundError(f"No stock_info found in {CACHE_DIR / 'stock_info'}")
    si["stock_id"] = si["stock_id"].astype(str).str.strip()

    if "date" in si.columns:
        si["date"] = pd.to_datetime(si["date"], errors="coerce")
        si = si.sort_values(["stock_id", "date"]).drop_duplicates("stock_id", keep="last")

    mask = (
        si["stock_id"].str.fullmatch(r"\d{4}")
        & ~si["stock_id"].str.startswith("00")
        & ~si["type"].str.contains("emerging", case=False, na=False)
    )
    tradeable = set(si[mask]["stock_id"])

    delist_path = CACHE_DIR / "delisting" / "_global.pkl"
    if delist_path.exists():
        try:
            dl = pd.read_pickle(delist_path)
            if "stock_id" in dl.columns:
                delisted = set(dl["stock_id"].astype(str))
                removed = tradeable & delisted
                tradeable -= delisted
                if removed:
                    logger.info("Excluded %d delisted stocks", len(removed))
        except Exception:
            pass

    return tradeable


def _get_top80() -> list[str]:
    """Return top-80 stocks by close×volume from OHLCV cache."""
    ohlcv_dir = CACHE_DIR / "ohlcv"
    size_proxy = {}
    for p in ohlcv_dir.glob("*.pkl"):
        try:
            df = pd.read_pickle(p)
            if len(df) >= 5:
                tv = (df["close"] * df["volume"]).tail(20).mean()
                size_proxy[p.stem] = float(tv) if pd.notna(tv) else 0.0
        except Exception:
            continue
    ranked = sorted(size_proxy.items(), key=lambda x: -x[1])
    return [r[0] for r in ranked[:80]]


def _daily_ohlcv_update(
    ohlcv_dir: pathlib.Path, as_of: datetime | None = None
) -> tuple[int, int]:
    """用 STOCK_DAY_ALL（2 requests）更新所有股票今天的 OHLCV。

    比 FinMind 逐支抓省約 1,000 次 API 呼叫。
    TWSE 上市股取得完整 OHLCV，TPEX 上櫃股只有 close/volume（dailySummary 端點限制）。

    Args:
        ohlcv_dir: pickle cache 目錄
        as_of: 目標日期；None 時用 datetime.now()。用於 backfill 漏天。

    Returns: (updated_count, skipped_count)
    """
    from src.data.twse_scraper import fetch_twse_daily_all

    target = as_of or datetime.now()
    today_ts = pd.Timestamp(target.date(), tz="UTC")

    # 抓目標日全市場快照（TWSE + TPEX），2 requests
    snapshot = fetch_twse_daily_all(target)

    # 非交易日保護：< 500 支代表空盤或假日
    if len(snapshot) < 500:
        logger.warning(
            "STOCK_DAY_ALL returned only %d stocks — likely non-trading day, skip",
            len(snapshot),
        )
        return 0, 0

    logger.info("STOCK_DAY_ALL: %d stocks fetched for %s", len(snapshot), today_ts.date())

    updated, skipped = 0, 0
    for sym, data in snapshot.items():
        pkl_path = ohlcv_dir / f"{sym}.pkl"
        if not pkl_path.exists():
            skipped += 1
            continue

        # 需要完整 OHLCV（TPEX 只有 close/volume，跳過讓 FinMind 補）
        if not all(k in data for k in ("open", "high", "low", "close", "volume")):
            skipped += 1
            continue

        try:
            df = pd.read_pickle(pkl_path)

            # 今天已有資料則跳過（idempotent）
            if today_ts in df.index:
                skipped += 1
                continue

            # 建新一行，格式與現有 pkl 一致
            new_row = pd.DataFrame(
                [{k: data[k] for k in ("open", "high", "low", "close", "volume")}],
                index=pd.DatetimeIndex([today_ts], name="date"),
            )
            new_row["volume"] = new_row["volume"].astype("int64")

            # Append + 原子寫入（防中途崩潰損毀）
            df = pd.concat([df, new_row]).sort_index()
            df = df[~df.index.duplicated(keep="last")]
            df = df[df["close"] > 0]  # 過濾 close=0 行（與 TPEX 一致）
            if not _validate_ohlcv(df, sym):
                skipped += 1
                continue
            tmp = pkl_path.with_suffix(".tmp")
            df.to_pickle(tmp)
            tmp.replace(pkl_path)
            updated += 1
        except Exception as exc:
            logger.warning("  %s: %s", sym, exc)
            skipped += 1

    logger.info("STOCK_DAY_ALL update: %d updated, %d skipped", updated, skipped)
    return updated, skipped


def _daily_tpex_update(
    ohlcv_dir: pathlib.Path, as_of: datetime | None = None
) -> tuple[int, int]:
    """用 FinMind 抓當月最新資料，更新所有 TPEX 上櫃股 OHLCV。

    每支股票一次 API call，抓「本月 1 日 ~ 目標日」，只補缺失的行。
    881 支股票 × 0.5s ≈ 7-10 分鐘，消耗 ~881 次 FinMind 額度。

    Args:
        ohlcv_dir: pickle cache 目錄
        as_of: 目標日期；None 時用 datetime.now()。用於 backfill 漏天。

    Returns: (updated_count, failed_count)
    """
    import time as _time
    from scripts.validate_cache import FinMindRotator, _finmind_raw_to_df

    today = as_of or datetime.now()
    start_str = today.strftime("%Y-%m-01")
    end_str = today.strftime("%Y-%m-%d")
    today_ts = pd.Timestamp(today.date(), tz="UTC")

    # 找出所有 TPEX 股票（從 stock_info CSV 的 type 欄位判斷，比讀 pkl index_name 更快更可靠）
    tradeable = _get_tradeable_stocks()
    try:
        csv_path = CACHE_DIR / "stock_info" / "stock_info_snapshot.csv"
        si = pd.read_csv(csv_path)
        si["stock_id"] = si["stock_id"].astype(str).str.strip()
        tpex_ids = set(si[si["type"] == "tpex"]["stock_id"]) & tradeable
    except Exception:
        tpex_ids = set()

    tpex_pkls = [
        (pkl_path.stem, pkl_path)
        for pkl_path in sorted(ohlcv_dir.glob("*.pkl"))
        if pkl_path.stem in tpex_ids
    ]

    if not tpex_pkls:
        logger.warning("No TPEX pkls found in %s", ohlcv_dir)
        return 0, 0

    logger.info("TPEX daily update: %d stocks, %s ~ %s", len(tpex_pkls), start_str, end_str)
    rotator = FinMindRotator()
    updated = failed = 0

    for sym, pkl_path in tpex_pkls:
        try:
            df = pd.read_pickle(pkl_path)
            # 今天已有資料則跳過
            if today_ts in df.index:
                continue
            raw = rotator.fetch(sym, start_str, end_str)
            ndf = _finmind_raw_to_df(raw)
            if ndf is None:
                logger.warning("  %s: no data from FinMind", sym)
                failed += 1
                continue
            df = pd.concat([df, ndf])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df = df[df["close"] > 0]
            df.index.name = "date"  # normalize: TPEX 原為 "timestamp"
            if not _validate_ohlcv(df, sym):
                failed += 1
                continue
            tmp = pkl_path.with_suffix(".tmp")
            df.to_pickle(tmp)
            tmp.replace(pkl_path)
            updated += 1
        except Exception as exc:
            logger.warning("  %s: %s", sym, exc)
            failed += 1

    logger.info("TPEX daily update: %d updated, %d failed", updated, failed)
    return updated, failed


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cache Fill — 補齊 + 更新 OHLCV + Revenue")
    parser.add_argument("--status", action="store_true", help="只顯示進度")
    parser.add_argument("--top-80-only", action="store_true", help="只補 top-80 缺失")
    parser.add_argument("--refresh-all", action="store_true",
                        help="全面更新：補缺失 + 更新所有過時資料到最新（用 FinMind）")
    parser.add_argument("--daily", action="store_true",
                        help="每日模式：用 STOCK_DAY_ALL 更新今天 OHLCV（2 requests，不消耗 FinMind）")
    parser.add_argument("--daily-tpex", action="store_true",
                        help="每日模式（TPEX）：用 FinMind 抓當月最新，更新上櫃股 OHLCV（~881 requests，~7 min）")
    parser.add_argument("--revenue-only", action="store_true",
                        help="只更新月營收（用 FinMind，建議每月 1-15 號執行）")
    parser.add_argument("--date", default=None,
                        help="YYYY-MM-DD；backfill 指定日（預設 today）。僅對 --daily / --daily-tpex 生效")
    args = parser.parse_args()

    target_date: datetime | None = None
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")

    progress = _load_progress()

    if args.status:
        print(f"OHLCV done: {len(progress['ohlcv_done'])}")
        print(f"Revenue done: {len(progress['revenue_done'])}")
        return

    ohlcv_dir = CACHE_DIR / "ohlcv"

    # ====== --daily 模式：STOCK_DAY_ALL 更新今天 OHLCV（TWSE only）======
    if args.daily:
        logger.info("=== Daily OHLCV Update (STOCK_DAY_ALL, TWSE) ===")
        if not ohlcv_dir.exists():
            logger.error("OHLCV dir not found: %s", ohlcv_dir)
            sys.exit(1)
        _daily_ohlcv_update(ohlcv_dir, as_of=target_date)
        logger.info("Done. Also run --daily-tpex to update TPEX stocks via FinMind.")
        return

    # ====== --daily-tpex 模式：FinMind 更新當月 TPEX OHLCV ======
    if args.daily_tpex:
        logger.info("=== Daily TPEX OHLCV Update (FinMind) ===")
        if not ohlcv_dir.exists():
            logger.error("OHLCV dir not found: %s", ohlcv_dir)
            sys.exit(1)
        _daily_tpex_update(ohlcv_dir, as_of=target_date)
        return

    tradeable = _get_tradeable_stocks()
    logger.info("Tradeable stocks (excl ETF/emerging/delisted): %d", len(tradeable))

    from src.data.finmind import FinMindSource

    token = os.environ.get("FINMIND_TOKEN")
    source = FinMindSource(token=token, backtest_mode=False)

    top80 = _get_top80()

    # ====== Phase 1: OHLCV（FinMind，用於補洞或 --refresh-all）======
    if not args.revenue_only:
        ohlcv_cached = {p.stem for p in ohlcv_dir.glob("*.pkl")} if ohlcv_dir.exists() else set()
        ohlcv_done_set = set(progress["ohlcv_done"])

        if args.refresh_all:
            # --refresh-all: 不用 progress file，每次都全量（修復：避免 Day 2 空跑）
            ohlcv_todo = sorted(tradeable)
            progress["ohlcv_done"] = []  # 重置，避免累積失效
        else:
            # 只補真正缺失的
            ohlcv_missing = tradeable - ohlcv_cached
            ohlcv_todo = sorted(ohlcv_missing - ohlcv_done_set)

        if args.top_80_only:
            ohlcv_todo = [s for s in ohlcv_todo if s in set(top80)]

        logger.info("=== Phase 1: OHLCV (FinMind) ===")
        logger.info("Mode: %s", "refresh-all" if args.refresh_all else "missing-only")
        logger.info("Todo: %d stocks", len(ohlcv_todo))

        ohlcv_updated = 0
        ohlcv_skipped = 0
        failed_count = 0
        stale_cutoff = pd.Timestamp(datetime.now().date()) - pd.Timedelta(days=3)

        for i, sym in enumerate(ohlcv_todo, 1):
            if i % 100 == 0 or i <= 5:
                logger.info("[OHLCV %d/%d] %s ...", i, len(ohlcv_todo), sym)
            try:
                df = source.fetch_ohlcv(sym, "D", 2000)
                if df is not None and not df.empty:
                    if args.refresh_all:
                        max_date = df.index.max()
                        if pd.Timestamp(max_date).tz_localize(None) < stale_cutoff:
                            ohlcv_skipped += 1
                            failed_count += 1
                            continue
                    ohlcv_updated += 1
                    failed_count = 0
                else:
                    ohlcv_skipped += 1
                    failed_count += 1
            except Exception as exc:
                if i <= 10:
                    logger.warning("  Error: %s", exc)
                ohlcv_skipped += 1
                failed_count += 1

            if failed_count == 0:
                progress["ohlcv_done"].append(sym)
            if i % 50 == 0:
                _save_progress(progress)

            if failed_count >= 20:
                logger.warning("20 consecutive failures — API quota likely exhausted. Progress saved.")
                _save_progress(progress)
                break

        _save_progress(progress)
        logger.info("OHLCV done: %d updated, %d skipped/failed", ohlcv_updated, ohlcv_skipped)
    else:
        ohlcv_updated, ohlcv_skipped = 0, 0

    # ====== Phase 2: Revenue（FinMind）======
    today = datetime.now()
    if args.revenue_only or args.refresh_all or (not args.top_80_only and today.day <= 15):
        # Revenue 每月 1-15 號發布，只在此區間更新（節省 FinMind 額度）
        rev_done_set = set(progress["revenue_done"])

        if args.refresh_all or args.revenue_only:
            rev_todo_set = tradeable - rev_done_set
        else:
            rev_dir = CACHE_DIR / "revenue"
            rev_has_data = set()
            if rev_dir.exists():
                for p in rev_dir.glob("*.pkl"):
                    try:
                        df = pd.read_pickle(p)
                        if not df.empty:
                            rev_has_data.add(p.stem)
                    except Exception:
                        pass
            rev_todo_set = tradeable - rev_has_data - rev_done_set

        top80_set = set(top80)
        top80_rev = sorted(rev_todo_set & top80_set)
        others_rev = sorted(rev_todo_set - top80_set)

        if args.top_80_only:
            rev_ordered = top80_rev
        else:
            rev_ordered = top80_rev + others_rev

        logger.info("=== Phase 2: Revenue (FinMind) ===")
        logger.info("Mode: %s", "revenue-only/refresh-all" if (args.revenue_only or args.refresh_all) else f"monthly window (day={today.day})")
        logger.info("Todo: %d stocks (top-80 first: %d)", len(rev_ordered), len(top80_rev))

        rev_updated = 0
        rev_skipped = 0
        failed_count = 0
        min_months = 12

        for i, sym in enumerate(rev_ordered, 1):
            if i % 100 == 0 or i <= 5:
                logger.info("[Revenue %d/%d] %s ...", i, len(rev_ordered), sym)
            is_good = False
            try:
                df = source.fetch_month_revenue(sym, months=60)
                if df is not None and not df.empty and len(df) >= min_months:
                    rev_updated += 1
                    failed_count = 0
                    is_good = True
                else:
                    rev_skipped += 1
                    failed_count += 1
            except Exception as exc:
                if i <= 10:
                    logger.warning("  Error: %s", exc)
                rev_skipped += 1
                failed_count += 1

            if is_good:
                progress["revenue_done"].append(sym)
            if i % 50 == 0:
                _save_progress(progress)

            if failed_count >= 20:
                logger.warning("20 consecutive failures — API quota likely exhausted. Progress saved.")
                _save_progress(progress)
                break

        _save_progress(progress)
        logger.info("Revenue done: %d updated, %d skipped/failed", rev_updated, rev_skipped)
    else:
        rev_updated, rev_skipped = 0, 0
        logger.info("=== Phase 2: Revenue skipped (day=%d, outside 1-15 window) ===", today.day)
        logger.info("Run with --revenue-only to force update.")

    # ====== Summary ======
    logger.info("=" * 50)
    logger.info("  OHLCV:   %d updated, %d skipped", ohlcv_updated, ohlcv_skipped)
    logger.info("  Revenue: %d updated, %d skipped", rev_updated, rev_skipped)
    logger.info("=" * 50)
    logger.info("Run cache_health.py to verify coverage.")


if __name__ == "__main__":
    main()
