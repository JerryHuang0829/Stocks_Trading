"""Phase A1 R11: Backfill margin_short + institutional_v2 caches via TWSE/TPEX
public endpoints (replaces per-symbol FinMind fetch pattern).

Uses 4 fetcher endpoints (each returns a full-market snapshot in 1 call):
    * TWSE MI_MARGN  + TPEX margin/balance   → margin_short
    * TWSE T86        + TPEX insti/dailyTrade → institutional_v2

Algorithm: for each target date, fetch all-market snapshot(s) and
**insert-if-missing** into per-symbol pickle. Existing FinMind rows are
NEVER overwritten. Simultaneously handles:
    - Gap A  (latest-day not yet ingested)
    - Gap B  (symbols missing entirely from cache, including 140 margin gaps)
    - Gap C  (isolated missing days mid-pickle)

Usage:
    # Single day (daily_update.sh)
    python scripts/backfill_tw_factors.py --dataset both --date 2026-04-17

    # Historical range
    python scripts/backfill_tw_factors.py --dataset both \\
        --start 2019-01-02 --end 2026-04-17

    # Dry-run (report gaps without writing)
    python scripts/backfill_tw_factors.py --dataset both --date 2026-04-17 --dry-run

    # Resume from checkpoint
    python scripts/backfill_tw_factors.py --dataset both \\
        --start 2019-01-02 --end 2026-04-17 --resume
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time
from datetime import datetime
from typing import Iterable

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.twse_scraper import (  # noqa: E402
    fetch_margin_daily_combined,
    fetch_institutional_daily_combined,
    FINMIND_MARGIN_SHORT_COLS,
    FINMIND_INSTITUTIONAL_COLS,
)
from src.utils.paths import resolve_cache_dir  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


class SchemaDriftError(RuntimeError):
    """Raised when an existing pickle's columns disagree with FinMind canonical set."""


def _progress_path(cache_dir: pathlib.Path) -> pathlib.Path:
    return cache_dir.parent / "cache_sweep_progress.json"


def _load_progress(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return set(raw.get("done_days", []))
    except Exception as exc:
        logger.warning("Progress load failed (%s); starting fresh", exc)
        return set()


def _save_progress(path: pathlib.Path, done_days: set[str]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"done_days": sorted(done_days)}, f, ensure_ascii=False)
    tmp.replace(path)


def _trading_days(start: datetime, end: datetime) -> list[datetime]:
    """Business days inclusive. Non-trading holidays handled by fetcher
    returning empty snapshot (<100 stocks) and skipped silently."""
    rng = pd.date_range(start=start.date(), end=end.date(), freq="B")
    return [d.to_pydatetime() for d in rng]


def _check_schema(df: pd.DataFrame, expected: frozenset[str], sym: str) -> None:
    existing = set(df.columns)
    if existing != expected:
        raise SchemaDriftError(
            f"{sym}: expected cols {sorted(expected)}, got {sorted(existing)}"
        )


def _preload_date_cache(cache_subdir: pathlib.Path) -> dict[str, set]:
    """Pre-scan every pickle in a dir, record its `date` column as a set
    of pd.Timestamp (normalised to day). Avoids re-reading each pickle
    in every loop iteration during full-history sweep.

    Returns {stock_id: set of normalised pd.Timestamp}.
    """
    cache: dict[str, set] = {}
    pkls = list(cache_subdir.glob("*.pkl"))
    logger.info("Preloading date index from %d pkls in %s ...",
                len(pkls), cache_subdir.name)
    t0 = time.time()
    for p in pkls:
        try:
            df = pd.read_pickle(p)
            if "date" in df.columns:
                cache[p.stem] = set(pd.to_datetime(df["date"]).dt.normalize())
        except Exception as exc:
            logger.debug("preload skip %s: %s", p.stem, exc)
    logger.info("Preload done for %s: %d pkls indexed in %.1fs",
                cache_subdir.name, len(cache), time.time() - t0)
    return cache


def _append_margin_day(
    cache_dir: pathlib.Path,
    day: datetime,
    snapshot: dict[str, dict],
    dry_run: bool,
    date_cache: dict[str, set] | None = None,
) -> tuple[int, int, int, int]:
    """Returns (inserted, skipped_existing, created_new, schema_drift_skipped).

    When `date_cache` is provided, we use in-memory date-set lookup (O(1))
    instead of re-reading each pickle (slow). Writes still read+rewrite pkl.
    """
    ms_dir = cache_dir / "margin_short"
    ms_dir.mkdir(parents=True, exist_ok=True)
    day_ts = pd.Timestamp(day.date())
    inserted = skipped = created = drift = 0
    for sid, fields in snapshot.items():
        pkl = ms_dir / f"{sid}.pkl"
        row = {"date": day_ts, "stock_id": sid, **fields}
        # Fast path: cached date-set lookup.
        if date_cache is not None and sid in date_cache:
            if day_ts in date_cache[sid]:
                skipped += 1
                continue
            # Missing — proceed to read+insert below.
        if not pkl.exists():
            if dry_run:
                created += 1
                continue
            new_df = pd.DataFrame([row])
            _write_atomic(pkl, new_df)
            if date_cache is not None:
                date_cache[sid] = {day_ts}
            created += 1
            continue
        try:
            df = pd.read_pickle(pkl)
            _check_schema(df, FINMIND_MARGIN_SHORT_COLS, sid)
        except SchemaDriftError as exc:
            logger.warning("Schema drift skip: %s", exc)
            drift += 1
            continue
        except Exception as exc:
            logger.warning("%s read failed: %s", sid, exc)
            drift += 1
            continue
        if date_cache is None:
            existing_dates = pd.to_datetime(df["date"]).dt.normalize()
            if (existing_dates == day_ts).any():
                skipped += 1
                continue
        if dry_run:
            inserted += 1
            continue
        new_df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        new_df["date"] = pd.to_datetime(new_df["date"]).dt.normalize()
        new_df = new_df.drop_duplicates(subset=["date"], keep="first").sort_values("date")
        _write_atomic(pkl, new_df)
        if date_cache is not None:
            date_cache.setdefault(sid, set()).add(day_ts)
        inserted += 1
    return inserted, skipped, created, drift


def _append_insti_day(
    cache_dir: pathlib.Path,
    day: datetime,
    snapshot: dict[str, list[dict]],
    dry_run: bool,
    date_cache: dict[str, set] | None = None,
) -> tuple[int, int, int, int]:
    """Returns (inserted_rows, skipped_existing_days, created_new, schema_drift_skipped).
    Each symbol gets 5 rows per day (long format); 'inserted_rows' counts 5 per successful day.
    `date_cache` provides O(1) date-set lookup (avoids per-pickle read)."""
    iv_dir = cache_dir / "institutional_v2"
    iv_dir.mkdir(parents=True, exist_ok=True)
    day_ts = pd.Timestamp(day.date())
    inserted = skipped = created = drift = 0
    for sid, five_rows in snapshot.items():
        pkl = iv_dir / f"{sid}.pkl"
        rows_with_meta = [
            {"date": day_ts, "stock_id": sid, **r} for r in five_rows
        ]
        if date_cache is not None and sid in date_cache:
            if day_ts in date_cache[sid]:
                skipped += 1
                continue
        if not pkl.exists():
            if dry_run:
                created += 1
                continue
            _write_atomic(pkl, pd.DataFrame(rows_with_meta))
            if date_cache is not None:
                date_cache[sid] = {day_ts}
            created += 1
            continue
        try:
            df = pd.read_pickle(pkl)
            _check_schema(df, FINMIND_INSTITUTIONAL_COLS, sid)
        except SchemaDriftError as exc:
            logger.warning("Schema drift skip: %s", exc)
            drift += 1
            continue
        except Exception as exc:
            logger.warning("%s read failed: %s", sid, exc)
            drift += 1
            continue
        if date_cache is None:
            existing_dates = pd.to_datetime(df["date"]).dt.normalize()
            if (existing_dates == day_ts).any():
                skipped += 1
                continue
        if dry_run:
            inserted += len(five_rows)
            continue
        new_df = pd.concat([df, pd.DataFrame(rows_with_meta)], ignore_index=True)
        new_df["date"] = pd.to_datetime(new_df["date"]).dt.normalize()
        new_df = new_df.drop_duplicates(subset=["date", "name"], keep="first").sort_values(
            ["date", "name"]
        )
        _write_atomic(pkl, new_df)
        if date_cache is not None:
            date_cache.setdefault(sid, set()).add(day_ts)
        inserted += len(five_rows)
    return inserted, skipped, created, drift


def _write_atomic(pkl: pathlib.Path, df: pd.DataFrame) -> None:
    tmp = pkl.with_suffix(".tmp")
    df.to_pickle(tmp)
    tmp.replace(pkl)


def process_day(
    cache_dir: pathlib.Path,
    day: datetime,
    datasets: Iterable[str],
    dry_run: bool,
    margin_cache: dict[str, set] | None = None,
    insti_cache: dict[str, set] | None = None,
) -> dict:
    """Fetch snapshot(s) for day and insert missing rows. Returns summary dict."""
    summary = {"date": day.strftime("%Y-%m-%d"), "skipped_non_trading": False}
    if "margin_short" in datasets:
        snap = fetch_margin_daily_combined(day)
        if len(snap) < 100:
            summary["skipped_non_trading"] = True
            summary["margin_inserted"] = 0
            summary["margin_skipped"] = 0
            summary["margin_created"] = 0
            summary["margin_drift"] = 0
        else:
            ins, skp, new, drf = _append_margin_day(
                cache_dir, day, snap, dry_run, margin_cache,
            )
            summary["margin_inserted"] = ins
            summary["margin_skipped"] = skp
            summary["margin_created"] = new
            summary["margin_drift"] = drf
    if "institutional_v2" in datasets:
        snap = fetch_institutional_daily_combined(day)
        if len(snap) < 100:
            summary["skipped_non_trading"] = True
            summary.setdefault("insti_inserted", 0)
            summary.setdefault("insti_skipped", 0)
            summary.setdefault("insti_created", 0)
            summary.setdefault("insti_drift", 0)
        else:
            ins, skp, new, drf = _append_insti_day(
                cache_dir, day, snap, dry_run, insti_cache,
            )
            summary["insti_inserted"] = ins
            summary["insti_skipped"] = skp
            summary["insti_created"] = new
            summary["insti_drift"] = drf
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill margin_short + institutional_v2 via TWSE/TPEX",
    )
    parser.add_argument(
        "--dataset",
        choices=["margin_short", "institutional_v2", "both"],
        default="both",
    )
    parser.add_argument("--date", default=None, help="Single day YYYY-MM-DD")
    parser.add_argument("--start", default=None, help="Range start YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Range end YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    parser.add_argument("--resume", action="store_true", help="Skip dates in progress file")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="Sleep seconds between days (default 1.0)")
    args = parser.parse_args()

    if args.date and (args.start or args.end):
        parser.error("Use either --date or --start/--end, not both")
    if (args.start and not args.end) or (args.end and not args.start):
        parser.error("--start and --end must be paired")
    if not args.date and not args.start:
        parser.error("Must specify --date or --start/--end")

    cache_dir = resolve_cache_dir()
    logger.info("Cache dir: %s", cache_dir)

    datasets = ["margin_short", "institutional_v2"] if args.dataset == "both" else [args.dataset]
    logger.info("Datasets: %s | dry_run=%s", datasets, args.dry_run)

    if args.date:
        days = [datetime.strptime(args.date, "%Y-%m-%d")]
    else:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d")
        days = _trading_days(start, end)
    logger.info("Days to process: %d (%s .. %s)",
                len(days), days[0].strftime("%Y-%m-%d"), days[-1].strftime("%Y-%m-%d"))

    prog_path = _progress_path(cache_dir)
    done_days = _load_progress(prog_path) if args.resume else set()
    if args.resume:
        logger.info("Resume mode: %d days already done", len(done_days))

    # Preload date-set cache when doing multi-day sweep (>3 days). Avoids
    # re-reading each pickle (~1800 pkl × 10ms = 18s per day without cache).
    margin_cache: dict[str, set] | None = None
    insti_cache: dict[str, set] | None = None
    if len(days) > 3 and not args.dry_run:
        if "margin_short" in datasets:
            margin_cache = _preload_date_cache(cache_dir / "margin_short")
        if "institutional_v2" in datasets:
            insti_cache = _preload_date_cache(cache_dir / "institutional_v2")

    totals = {
        "margin_inserted": 0, "margin_skipped": 0, "margin_created": 0, "margin_drift": 0,
        "insti_inserted": 0, "insti_skipped": 0, "insti_created": 0, "insti_drift": 0,
        "non_trading": 0, "processed": 0,
    }

    for i, day in enumerate(days, 1):
        day_key = day.strftime("%Y-%m-%d")
        if day_key in done_days:
            continue
        try:
            summary = process_day(cache_dir, day, datasets, args.dry_run,
                                  margin_cache=margin_cache, insti_cache=insti_cache)
        except Exception as exc:
            logger.error("Day %s failed: %s", day_key, exc)
            continue
        if summary.get("skipped_non_trading"):
            totals["non_trading"] += 1
        else:
            totals["processed"] += 1
        for k in ("margin_inserted", "margin_skipped", "margin_created", "margin_drift",
                  "insti_inserted", "insti_skipped", "insti_created", "insti_drift"):
            totals[k] += summary.get(k, 0)

        if i % 20 == 0 or i == len(days) or len(days) < 10:
            logger.info(
                "[%d/%d] %s: margin(ins=%d skip=%d new=%d drift=%d) "
                "insti(rows_ins=%d day_skip=%d new=%d drift=%d)%s",
                i, len(days), day_key,
                summary.get("margin_inserted", 0), summary.get("margin_skipped", 0),
                summary.get("margin_created", 0), summary.get("margin_drift", 0),
                summary.get("insti_inserted", 0), summary.get("insti_skipped", 0),
                summary.get("insti_created", 0), summary.get("insti_drift", 0),
                " [NON_TRADING]" if summary.get("skipped_non_trading") else "",
            )
        done_days.add(day_key)
        if not args.dry_run and i % 20 == 0:
            _save_progress(prog_path, done_days)
        if i < len(days):
            time.sleep(args.sleep)

    if not args.dry_run:
        _save_progress(prog_path, done_days)

    logger.info("=== TOTALS ===")
    for k, v in totals.items():
        logger.info("  %s: %d", k, v)


if __name__ == "__main__":
    main()
