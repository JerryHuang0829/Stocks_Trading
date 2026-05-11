"""Overnight cache rebuild for Phase A1 new factors.

Fetches FinMind history for all 4-digit Taiwan stocks:
    margin_short       → data/cache/margin_short/<symbol>.pkl
    institutional_v2   → data/cache/institutional_v2/<symbol>.pkl
    quarterly_eps      → data/cache/quarterly_eps/<symbol>.pkl

Reuses TokenRotator from scripts/cache_rebuild.py for 3 tokens × 3 proxy
slots (fresh Proxifly SOCKS5 per token to keep FinMind quota per-token).

Usage:
    docker compose run --rm --entrypoint python portfolio-bot \\
        scripts/cache_fill_new_factors.py

    docker compose run --rm --entrypoint python portfolio-bot \\
        scripts/cache_fill_new_factors.py --dataset margin_short

Estimated runtime (all 3 datasets, ~1950 symbols each):
    3 tokens × 580 calls/hour = 1740 calls/cycle
    Total ~5850 calls ≈ 3.4 cycles × 65 min = 3-4 hours
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the battle-tested token+proxy rotation from cache_rebuild.py
from scripts.cache_rebuild import TokenRotator
from src.data.finmind import FinMindSource, FinMindTransientError
from src.utils.paths import resolve_cache_dir


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


DATASET_CONFIG = {
    "margin_short": {
        "fetch_method": "fetch_margin_short",
        "default_start": "2019-01-01",
        "min_rows": 60,   # ~3 months of trading days
    },
    "institutional_v2": {
        "fetch_method": "fetch_three_institutional",
        "default_start": "2019-01-01",
        "min_rows": 200,  # 5 names × ~40 days minimum (long-format)
    },
    "quarterly_eps": {
        "fetch_method": "fetch_quarterly_eps",
        "default_start": "2016-01-01",
        "min_rows": 12,   # 12 quarters
    },
    # S6.1 Path B (R25-mid 獨立 audit P-B fix, 2026-05-05): D-E quality_v3
    # full income statement + balance sheet history. Existing quarterly_eps
    # is EPS-only subset (per finmind.py:674); these new datasets store full
    # income statement + balance sheet for TTM ROE / gross_margin / Δassets.
    "quarterly_financial_full": {
        "fetch_method": "fetch_quarterly_financial_full",
        "default_start": "2018-01-01",  # 4Q TTM + 4Q YoY needs 8Q before earliest backtest start (2019-01-01)
        "min_rows": 12,
    },
    "balance_sheet": {
        "fetch_method": "fetch_balance_sheet_history",
        "default_start": "2018-01-01",
        "min_rows": 12,
    },
}


def _progress_path(cache_dir: pathlib.Path, dataset: str) -> pathlib.Path:
    return cache_dir.parent / f"cache_fill_{dataset}_progress.json"


def _load_progress(cache_dir: pathlib.Path, dataset: str) -> set[str]:
    path = _progress_path(cache_dir, dataset)
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_progress(cache_dir: pathlib.Path, dataset: str, done: set[str]) -> None:
    path = _progress_path(cache_dir, dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")


def _load_stock_ids(cache_dir: pathlib.Path, top_n: int | None = None) -> list[str]:
    """Load 4-digit stock IDs from existing stock_info cache snapshot.

    Excludes 00xx ETF prefix (ETFs have no margin/short or three-institutional
    data — including them causes ``failed_count`` false-positives which
    wrongly trigger token/proxy rotation).

    S6.1 Path B (R25-mid 獨立 audit + user 提醒, 2026-05-05): top_n filter
    縮 universe to top-N by 60-day mean(close × volume) per H_d_v6:69 + per
    `config/settings.yaml:auto_universe_size`. Mitigates over-fetch (v7 cell
    sweep only uses top-80; full 1968-stock fill cycles 3 quota slots × 65min
    sleep is wasteful).
    """
    csv_path = cache_dir / "stock_info" / "stock_info_snapshot.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"stock_info snapshot missing: {csv_path}. "
            f"Run cache_rebuild.py phase 1 first to seed it."
        )
    df = pd.read_csv(csv_path, dtype=str)
    four_digit = df[df["stock_id"].astype(str).str.fullmatch(r"\d{4}")]
    non_etf = four_digit[~four_digit["stock_id"].astype(str).str.startswith("00")]
    ids = sorted(set(non_etf["stock_id"].astype(str)))

    if top_n is None or top_n <= 0:
        return ids

    ohlcv_dir = cache_dir / "ohlcv"
    if not ohlcv_dir.is_dir():
        logger.warning("OHLCV cache dir missing — top_n filter skipped, returning all %d", len(ids))
        return ids

    scored: list[tuple[str, float]] = []
    for sid in ids:
        pkl = ohlcv_dir / f"{sid}.pkl"
        if not pkl.exists():
            continue
        try:
            df_ohlcv = pd.read_pickle(pkl)
            if df_ohlcv is None or df_ohlcv.empty or "close" not in df_ohlcv.columns:
                continue
            tail = df_ohlcv.tail(60)
            if "volume" not in tail.columns or len(tail) < 30:
                continue
            score = float((tail["close"] * tail["volume"]).mean())
            if score > 0 and pd.notna(score):
                scored.append((sid, score))
        except Exception:
            continue

    scored.sort(key=lambda x: -x[1])
    top_ids = [sid for sid, _ in scored[:top_n]]
    logger.info(
        "_load_stock_ids: top_n=%d filter applied; selected %d/%d by 60d mean(close × volume)",
        top_n, len(top_ids), len(ids),
    )
    return sorted(top_ids)


def _make_source(rotator: TokenRotator, cache_dir: pathlib.Path) -> FinMindSource:
    token = rotator._slots[rotator._current_slot][0]
    src = FinMindSource(
        token=token, backtest_mode=False, cache_dir=str(cache_dir),
    )
    if rotator._current_proxy:
        proxies = {"http": rotator._current_proxy, "https": rotator._current_proxy}
        src.loader._FinMindApi__session.proxies.update(proxies)
    return src


def rebuild_dataset(
    dataset: str,
    cache_dir: pathlib.Path,
    *,
    starting_with_proxy: bool = False,
    top_n: int | None = None,
) -> None:
    cfg = DATASET_CONFIG[dataset]
    fetch_method_name = cfg["fetch_method"]
    default_start = cfg["default_start"]
    min_rows = cfg["min_rows"]

    all_ids = _load_stock_ids(cache_dir, top_n=top_n)
    done_set = _load_progress(cache_dir, dataset)
    todo = [s for s in all_ids if s not in done_set]
    logger.info(
        "=== Dataset: %s | total %d, done %d, todo %d | start_date=%s ===",
        dataset, len(all_ids), len(done_set), len(todo), default_start,
    )
    if not todo:
        logger.info("%s: nothing to do (all done).", dataset)
        return

    rotator = TokenRotator()
    # S6.1 Path B (R25-mid 獨立 audit + user 提醒, 2026-05-05): if starting_with_proxy,
    # fetch fresh Proxifly SOCKS5 for Slot 0 BEFORE first call. Per memory
    # `FinMind Tokens & Quota`: 3 tokens all bound to same IP (<isp_ip>).
    # Default starting Direct means Token1 runs 580 calls on workstation IP
    # before rotation. For large datasets (e.g. quarterly_financial_full /
    # balance_sheet ~2492 symbols), this risks IP-based throttling.
    if starting_with_proxy:
        proxy_ok = rotator.start_with_proxy()
        if not proxy_ok:
            logger.warning(
                "starting_with_proxy=True requested but proxy fetch failed — "
                "falling back to Direct (per Proxifly free SOCKS5 ~30%% success rate)."
            )
    source = _make_source(rotator, cache_dir)
    last_proxy = [rotator._current_proxy]
    failed_count = 0
    proxy_fail_count = 0
    slow_call_count = 0

    # Signatures a dying SOCKS5 proxy emits (session.get returns None → .json()
    # on None, socket timeouts, refused connections). Distinct from logic bugs.
    PROXY_DEATH_MARKERS = ("'NoneType'", "timed out", "timeout", "connection",
                            "proxy", "socks", "read timed out")
    # V0.15 (2026-05-05): Per-call slow-proxy threshold. Healthy calls take <2s;
    # a degraded SOCKS5 exit can stretch to 30-60s/call without erroring.
    # Tightened from 20s × 3 (= 60s wasted) to 12s × 2 (= 24s wasted) per
    # 14:00 incident where Token1 wasted 577 calls quota on slow proxy.
    SLOW_CALL_THRESHOLD_SEC = 12.0
    SLOW_CALL_COUNT_LIMIT = 2
    # V0.15: hot-swap threshold. If token still has > HOT_SWAP_QUOTA_FLOOR calls
    # remaining, swap proxy without rotating token (preserve quota). If less,
    # full rotate (token nearly used anyway).
    HOT_SWAP_QUOTA_FLOOR = 100  # min remaining calls to justify hot-swap

    def _rotate(reason: str):
        nonlocal source, failed_count, proxy_fail_count, slow_call_count
        logger.warning("%s on [%s] — force-rotating", reason, rotator.current_label)
        rotator.record_quota_error()
        rotator.get_loader()
        source = _make_source(rotator, cache_dir)
        last_proxy[0] = rotator._current_proxy
        failed_count = 0
        proxy_fail_count = 0
        slow_call_count = 0

    def _hot_swap_proxy(reason: str) -> bool:
        """V0.15: try hot-swap proxy without rotating token.

        Returns True if swap succeeded. If no backup proxy available, falls
        back to full rotate (returns False so caller knows token state changed).
        """
        nonlocal source, slow_call_count, proxy_fail_count
        backup = rotator.get_backup_proxy()
        if backup is None:
            logger.warning("Hot-swap requested (%s) but backup pool empty — full rotate",
                           reason)
            _rotate(reason + " + no backup proxy")
            return False
        rotator.patch_current_proxy(backup)
        rotator.get_loader()  # rebuild loader with new proxy on same token
        source = _make_source(rotator, cache_dir)
        last_proxy[0] = rotator._current_proxy
        logger.warning("%s on [%s] — hot-swapped proxy (token quota preserved)",
                       reason, rotator.current_label)
        slow_call_count = 0
        proxy_fail_count = 0
        return True

    for i, sym in enumerate(todo, 1):
        if i % 100 == 0 or i <= 3:
            logger.info(
                "[%s %d/%d] %s [%s]",
                dataset, i, len(todo), sym, rotator.current_label,
            )

        is_good = False
        # V0.16 (2026-05-05) negative cache marker: API call succeeded but
        # FinMind returned no data for this symbol (small cap / preferred /
        # delisted with no quarterly statements). Mark done to skip on
        # restart — saves wasteful re-fetch on next run.
        # V0.22 (2026-05-06): transient errors (ip banned / unexpected response /
        # rate limit) raise FinMindTransientError separately and MUST NOT be
        # neg-cached. Trigger: 2026-05-06 audit found TSMC/鴻海/聯發科 etc. were
        # falsely neg-cached during 00:38-00:57 IP ban window.
        api_call_succeeded_no_data = False
        call_start = time.monotonic()
        try:
            fetch_fn = getattr(source, fetch_method_name)
            df = fetch_fn(sym, start_date=default_start)
            rotator.record_call()
            proxy_fail_count = 0  # successful call — proxy healthy
            elapsed = time.monotonic() - call_start
            if elapsed > SLOW_CALL_THRESHOLD_SEC:
                slow_call_count += 1
            else:
                slow_call_count = 0
            if df is not None and not df.empty and len(df) >= min_rows:
                is_good = True
                failed_count = 0
            else:
                # API call succeeded but no usable data — mark for skip on restart.
                # NB: this includes BOTH None return (per finmind.py loader empty
                # branch) AND empty/short df. Both indicate FinMind has no data.
                api_call_succeeded_no_data = True
            # Empty / short responses are NORMAL for small caps, warrants, and
            # newly-listed OTC symbols — do NOT count toward failed_count.
            # Real quota exhaustion raises KeyError (handled below).
        except FinMindTransientError as exc:
            # V0.22: transient API error (ip banned / unexpected response / rate
            # limit) — must NOT mark done; will retry next run. Treat like
            # proxy failure for rotation logic.
            logger.warning(
                "V0.22 transient FinMind error for %s / %s: %s — will retry next run",
                sym, dataset, exc,
            )
            proxy_fail_count += 1
            if proxy_fail_count >= 5:
                _rotate("5 consecutive transient errors (likely IP banned)")
                continue
            failed_count += 1
        except KeyError:
            _rotate("quota exhaustion (KeyError 'data')")
            continue
        except Exception as exc:
            logger.debug("Fetch error for %s / %s: %s", sym, dataset, exc)
            msg = str(exc).lower()
            if any(m in msg for m in PROXY_DEATH_MARKERS):
                proxy_fail_count += 1
                if proxy_fail_count >= 5:
                    _rotate("5 consecutive proxy/network errors")
                    continue
            else:
                failed_count += 1

        # V0.15 slow-proxy rotation: SLOW_CALL_COUNT_LIMIT consecutive calls >
        # SLOW_CALL_THRESHOLD_SEC → proxy is degrading. Try hot-swap first if
        # token still has quota; only full-rotate if quota nearly used.
        if slow_call_count >= SLOW_CALL_COUNT_LIMIT:
            reason = (f"{SLOW_CALL_COUNT_LIMIT} consecutive slow calls "
                      f"(>{SLOW_CALL_THRESHOLD_SEC}s each)")
            remaining = rotator.QUOTA_PER_SLOT - rotator.calls_on_current
            if remaining > HOT_SWAP_QUOTA_FLOOR:
                _hot_swap_proxy(reason)
            else:
                _rotate(reason + f" + token nearly used ({remaining} remaining)")
            continue

        # Proactive rotation at the soft 580-call limit. record_quota_error only
        # primes the counter; get_loader() is the call that actually advances
        # _current_slot to the next token+proxy.
        if rotator._calls_on_current >= rotator.QUOTA_PER_SLOT:
            rotator.record_quota_error()
            rotator.get_loader()
            source = _make_source(rotator, cache_dir)
            last_proxy[0] = rotator._current_proxy

        if rotator._current_proxy != last_proxy[0]:
            source = _make_source(rotator, cache_dir)
            last_proxy[0] = rotator._current_proxy

        # V0.16 negative cache: add to done_set if EITHER (a) data fetched OK,
        # OR (b) API confirmed no data exists. Both states are "verified, no
        # need to re-attempt on restart". Excludes proxy/quota errors which
        # are transient and should retry next run.
        if is_good or api_call_succeeded_no_data:
            done_set.add(sym)
        if i % 50 == 0:
            _save_progress(cache_dir, dataset, done_set)

        # Hard stop: 60 consecutive failures means either all tokens are dead
        # OR we've reached a legitimate trailing-empty section near end of list.
        if failed_count >= 60:
            logger.warning("60 consecutive failures — stopping early")
            _save_progress(cache_dir, dataset, done_set)
            break

    _save_progress(cache_dir, dataset, done_set)
    logger.info("%s done: %d cached", dataset, len(done_set))


def seed_issued_capital(cache_dir: pathlib.Path) -> pathlib.Path:
    """P1-2: derive ``issued_shares`` from market_value / close and persist.

    Writes ``issued_capital/_global.pkl`` with columns (stock_id, date,
    issued_shares). Consumed by ``run_factor_ic.py::_load_issued_capital``.

    ⚠️ **NOT TRUE PIT HISTORICAL ISSUED_SHARES** (R29 finding 4)：
    market_value cache 本身是 ``latest_shares × historical_close`` (per
    ``src/data/finmind.py:_compute_market_value_from_twse``). 故 derive shares
    = ``market_value / close`` = ``latest × historical_close / historical_close``
    = **latest_shares 對所有 date 不變**. 這個 seed 只是 form-correct（cache
    結構帶 date column），不是真 PIT historical issued_shares.

    Empirical verification (R28-1 follow-up 2026-05-10): margin_short_ratio
    fresh rerun 跟 fallback ``Timestamp.min`` 比對 ΔIC = +0.0001（noise
    level）→ 證實 form-correct 但 substance-equivalent.

    要拿真正 PIT historical issued_shares 需另寫 TWSE OpenAPI scraper（如
    ``t187ap03_L`` 或同款 monthly issued_capital snapshot endpoint），抓 5+
    年歷史 + 取代當前 derive method (P1 backlog 4-8 hr).

    Original docstring: The ratio is computed pair-wise per (stock_id, date),
    taking the OHLCV close at or immediately before the market_value snapshot
    date. Missing or non-positive closes are skipped. This is a best-effort
    seed — it does not distinguish split-adjusted vs raw shares, which is
    tolerable because downstream use (margin_short_ratio) only needs the
    order of magnitude.
    """
    mv_path = cache_dir / "market_value" / "_global.pkl"
    if not mv_path.exists():
        raise FileNotFoundError(f"market_value cache missing: {mv_path}")
    mv = pd.read_pickle(mv_path)
    required = {"stock_id", "date", "market_value"}
    if mv is None or mv.empty or not required.issubset(mv.columns):
        raise ValueError(
            f"market_value/_global.pkl must contain columns {required}"
        )
    mv = mv.copy()
    mv["date"] = pd.to_datetime(mv["date"], errors="coerce")
    mv = mv.dropna(subset=["date", "market_value"]).sort_values(["stock_id", "date"])

    ohlcv_dir = cache_dir / "ohlcv"
    if not ohlcv_dir.is_dir():
        raise FileNotFoundError(f"OHLCV cache missing: {ohlcv_dir}")

    rows: list[dict] = []
    skipped = 0
    for stock_id, group in mv.groupby("stock_id"):
        path = ohlcv_dir / f"{stock_id}.pkl"
        if not path.exists():
            skipped += len(group)
            continue
        try:
            ohlcv = pd.read_pickle(path)
        except Exception:
            skipped += len(group)
            continue
        if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
            skipped += len(group)
            continue
        idx = pd.to_datetime(ohlcv.index)
        tz = getattr(idx, "tz", None)
        if tz is not None:
            idx = idx.tz_convert(None)
        close = pd.Series(ohlcv["close"].values, index=idx).sort_index().dropna()
        if close.empty:
            skipped += len(group)
            continue
        for _, row in group.iterrows():
            snap_date = row["date"]
            view = close[close.index <= snap_date]
            if view.empty:
                skipped += 1
                continue
            close_px = float(view.iloc[-1])
            if close_px <= 0:
                skipped += 1
                continue
            issued_shares = float(row["market_value"]) / close_px
            if issued_shares <= 0 or not pd.notna(issued_shares):
                skipped += 1
                continue
            rows.append({
                "stock_id": str(stock_id),
                "date": snap_date,
                "issued_shares": issued_shares,
            })

    if not rows:
        raise RuntimeError(
            "seed-issued-capital: no valid (stock_id, date) pairs derived — "
            "market_value / OHLCV caches may be empty."
        )
    out = pd.DataFrame(rows).sort_values(["stock_id", "date"]).reset_index(drop=True)
    target = cache_dir / "issued_capital" / "_global.pkl"
    target.parent.mkdir(parents=True, exist_ok=True)
    out.to_pickle(target)
    logger.info(
        "seed-issued-capital: wrote %s  rows=%d  unique_symbols=%d  skipped=%d",
        target, len(out), out["stock_id"].nunique(), skipped,
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild caches for Phase A1 new factors")
    parser.add_argument(
        "--dataset",
        choices=list(DATASET_CONFIG.keys()) + ["all"],
        default="all",
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument(
        "--seed-issued-capital",
        action="store_true",
        help=(
            "P1-2: derive issued_shares from market_value / close, write "
            "issued_capital/_global.pkl, then exit. Runs without FinMind calls."
        ),
    )
    parser.add_argument(
        "--starting-with-proxy",
        action="store_true",
        help=(
            "S6.1 Path B (R25-mid 獨立 audit, 2026-05-05): start Token1 + "
            "fresh Proxifly SOCKS5 proxy instead of Direct. Per memory "
            "FinMind Tokens & Quota: 3 tokens all bound to same IP "
            "(<isp_ip>); default Direct means Token1 runs 580 calls on "
            "workstation IP before rotation. Recommended for large datasets "
            "(quarterly_financial_full / balance_sheet) to mitigate IP "
            "throttling risk."
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help=(
            "S6.1 Path B (R25-mid 獨立 audit + user 提醒, 2026-05-05): "
            "縮 universe to top-N by 60-day mean(close × volume) per "
            "H_d_v6:69 universe spec + config/settings.yaml:auto_universe_size. "
            "Mitigates over-fetch (v7 cell sweep only uses top-80 stocks). "
            "Recommended: --top-n 80 for v7 cell sweep cache fill (~3 min "
            "wall clock vs ~3.5 hr for full 1968 stocks)."
        ),
    )
    args = parser.parse_args()

    cache_dir = pathlib.Path(args.cache_dir) if args.cache_dir else resolve_cache_dir()
    logger.info("Cache dir: %s", cache_dir)

    if args.seed_issued_capital:
        seed_issued_capital(cache_dir)
        logger.info("seed-issued-capital complete — exiting without dataset rebuild.")
        return

    if args.dataset == "all":
        targets = list(DATASET_CONFIG.keys())
    else:
        targets = [args.dataset]

    for dataset in targets:
        rebuild_dataset(
            dataset, cache_dir,
            starting_with_proxy=args.starting_with_proxy,
            top_n=args.top_n,
        )
        logger.info("---")

    logger.info("All requested datasets processed.")


if __name__ == "__main__":
    main()
