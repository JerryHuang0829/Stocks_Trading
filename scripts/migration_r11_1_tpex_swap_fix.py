"""Migration: Phase A1 R11.1 v2 — TPEX margin SS_Buy/Sell swap fix + 2022-06-22 anomaly.

Problem (discovered Codex Round 10-11):
  FinMind historical API stored TPEX margin rows with ShortSaleBuy/ShortSaleSell
  swapped (TPEX endpoint returns 券賣→券買 order; FinMind didn't swap back).
  Initial R11.1 v1 fix used `stock_info.type=='tpex'` filter which missed
  transferred stocks (historical TPEX → current TWSE; e.g., 1597/1795/3092/4736).
  Codex caught this via 2019-06-17 live audit. v2 re-scans ALL margin pkls.

Additionally, 2022-06-22 has 593 TWSE-listed symbols where SS_Buy/SS_Sell
pkl values differ from TWSE live (non-swap pattern, other 12 cols match) —
appears to be TWSE official post-publication correction that FinMind didn't
refresh. This script optionally overrides those 593 rows' SS values with
live TWSE data.

Usage:
  # Default — applies the shipped plan (migrations/r11_1/plan_v2.json) idempotently.
  python scripts/migration_r11_1_tpex_swap_fix.py

  # Dry-run against shipped plan
  python scripts/migration_r11_1_tpex_swap_fix.py --dry-run

  # Explicit plan path
  python scripts/migration_r11_1_tpex_swap_fix.py --plan <json>

  # Re-scan cache live (~30-50 min) — use when plan not trusted / cross-machine fresh
  python scripts/migration_r11_1_tpex_swap_fix.py --scan

Idempotent:
  Re-running this script after success writes 0 rows. Row is only modified if
  current SS values still match the swap pattern (or 2022-06-22 anomaly pattern).

Plan schema compat:
  Accepts both native format (from --scan output) and legacy v2 format
  (Stage 2b output). Auto-normalised via _normalize_plan().

This script is a one-shot migration. After running on a fresh machine once,
the cache should reach the same final state as the originally-patched data.
Commit this script so future machines can reproduce the patch state.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time
from datetime import datetime

import pandas as pd

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.twse_scraper import (  # noqa: E402
    FINMIND_MARGIN_SHORT_COLS,
    fetch_tpex_margin_daily_all,
    fetch_twse_margin_daily_all,
)
from src.utils.paths import resolve_cache_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _write_atomic(pkl: pathlib.Path, df: pd.DataFrame) -> None:
    tmp = pkl.with_suffix(".tmp")
    df.to_pickle(tmp)
    tmp.replace(pkl)


def scan_and_build_plan(cache_dir: pathlib.Path, sleep: float = 0.5) -> dict:
    """Scan ALL margin_short pkls × all trading days × both TWSE+TPEX snapshots.
    Returns plan dict with swap_fixes + 20220622_overrides."""
    ms_dir = cache_dir / "margin_short"
    pkl_data = {}
    for p in ms_dir.glob("*.pkl"):
        try:
            pkl_data[p.stem] = pd.read_pickle(p)
        except Exception as exc:
            logger.warning("skip %s: %s", p.stem, exc)
    logger.info("Loaded %d margin pkls", len(pkl_data))

    all_dates = set()
    for df in pkl_data.values():
        all_dates.update(pd.to_datetime(df["date"]).dt.normalize())
    all_dates = sorted(all_dates)
    logger.info("Unique trading dates across all pkls: %d", len(all_dates))

    swap_fixes: dict[str, list] = {}
    override_20220622: dict[str, dict] = {}  # sym → {'idx':int, 'ssb':int, 'sss':int}
    non_swap_others: list[dict] = []  # truly unknown mismatches
    fetch_fails = 0
    t0 = time.time()
    target_22 = pd.Timestamp("2022-06-22")

    for i, day in enumerate(all_dates, 1):
        tpex = fetch_tpex_margin_daily_all(day.to_pydatetime())
        twse = fetch_twse_margin_daily_all(day.to_pydatetime())
        combined = {**tpex, **twse}
        if len(combined) < 500:
            fetch_fails += 1
            continue
        day_ts = pd.Timestamp(day).normalize()
        for sid, live in combined.items():
            df = pkl_data.get(sid)
            if df is None:
                continue
            mask = df["date"].dt.normalize() == day_ts
            if not mask.any():
                continue
            idx = df.index[mask][0]
            pb = int(df.at[idx, "ShortSaleBuy"])
            ps = int(df.at[idx, "ShortSaleSell"])
            eb = int(live["ShortSaleBuy"])
            es = int(live["ShortSaleSell"])
            if pb == eb and ps == es:
                continue
            if pb == es and ps == eb:
                swap_fixes.setdefault(sid, []).append((int(idx), eb, es))
            elif day_ts == target_22:
                # 2022-06-22 anomaly: verify other 12 cols match; if yes, override SS only
                others_match = all(
                    int(df.at[idx, k]) == int(live[k])
                    for k in live
                    if k not in ("Note", "ShortSaleBuy", "ShortSaleSell")
                )
                if others_match:
                    override_20220622[sid] = {"idx": int(idx), "ssb": eb, "sss": es,
                                               "pkl_ssb": pb, "pkl_sss": ps}
                else:
                    non_swap_others.append({"sym": sid, "date": str(day_ts.date()),
                                             "pkl_ssb": pb, "pkl_sss": ps,
                                             "live_ssb": eb, "live_sss": es})
            else:
                non_swap_others.append({"sym": sid, "date": str(day_ts.date()),
                                         "pkl_ssb": pb, "pkl_sss": ps,
                                         "live_ssb": eb, "live_sss": es})
        if i % 200 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(all_dates) - i)
            logger.info("[%d/%d] %s | swap_rows=%d override_22=%d non_swap_other=%d | elapsed=%.0fs eta=%.0fs",
                        i, len(all_dates), day.date(),
                        sum(len(v) for v in swap_fixes.values()),
                        len(override_20220622), len(non_swap_others), elapsed, eta)
        time.sleep(sleep)

    return {
        "swap_fixes": {s: [(int(i), int(b), int(ss)) for i, b, ss in lst]
                       for s, lst in swap_fixes.items()},
        "override_20220622": override_20220622,
        "non_swap_others": non_swap_others,
        "stats": {
            "swap_rows": sum(len(v) for v in swap_fixes.values()),
            "swap_syms": len(swap_fixes),
            "override_20220622_count": len(override_20220622),
            "non_swap_others": len(non_swap_others),
            "fetch_fails": fetch_fails,
        },
    }


def _normalize_plan(plan: dict) -> dict:
    """Accept either native format (this script's --scan output) or the
    legacy Stage 2b v2 format (data/tpex_swap_fix_plan_v2.json). Return
    internal canonical shape.

    Native (native keys already present):
        {swap_fixes, override_20220622, non_swap_others, stats:{swap_rows,...}}

    Legacy v2 (Stage 2b output, produced before this script existed):
        {fixes_per_sym, non_swap, stats:{swap_fixes, non_swap, fetch_fails, ...}}

    Legacy v1 (R11.1 v1 Stage 2 output):
        {fixes_per_sym, non_swap_mismatches, stats:{swap_fixes, non_swap}}
    """
    if "swap_fixes" in plan:
        # Already native or close; fill optional fields
        plan.setdefault("override_20220622", {})
        plan.setdefault("non_swap_others", [])
        s = plan.setdefault("stats", {})
        s.setdefault("swap_rows", sum(len(v) for v in plan["swap_fixes"].values()))
        s.setdefault("swap_syms", len(plan["swap_fixes"]))
        s.setdefault("override_20220622_count", len(plan["override_20220622"]))
        s.setdefault("non_swap_others", len(plan["non_swap_others"]))
        s.setdefault("fetch_fails", 0)
        return plan

    # Legacy formats — convert to native
    out = {}
    if "fixes_per_sym" in plan:
        out["swap_fixes"] = plan["fixes_per_sym"]
    else:
        raise ValueError("Plan missing both 'swap_fixes' and 'fixes_per_sym' — unrecognised schema")

    non_swap_list = plan.get("non_swap") or plan.get("non_swap_mismatches") or []
    # Build override_20220622 from non_swap entries dated 2022-06-22 that have
    # pkl_ssb/pkl_sss/live_ssb/live_sss fields.
    override = {}
    others = []
    for m in non_swap_list:
        if (m.get("date") == "2022-06-22"
                and all(k in m for k in ("pkl_ssb", "pkl_sss", "live_ssb", "live_sss"))):
            override[m["sym"]] = {
                "idx": int(m.get("idx", -1)) if "idx" in m else -1,
                "ssb": int(m["live_ssb"]),
                "sss": int(m["live_sss"]),
                "pkl_ssb": int(m["pkl_ssb"]),
                "pkl_sss": int(m["pkl_sss"]),
            }
        else:
            others.append(m)
    # Legacy v2 may lack idx; resolve by scanning pickle at apply time. Leave idx=-1 marker.
    out["override_20220622"] = override
    out["non_swap_others"] = others

    legacy_stats = plan.get("stats", {})
    out["stats"] = {
        "swap_rows": sum(len(v) for v in out["swap_fixes"].values()),
        "swap_syms": len(out["swap_fixes"]),
        "override_20220622_count": len(override),
        "non_swap_others": len(others),
        "fetch_fails": legacy_stats.get("fetch_fails", 0),
    }
    return out


def apply_plan(cache_dir: pathlib.Path, plan: dict, include_20220622: bool,
               dry_run: bool) -> dict:
    """Apply swap_fixes + optional 20220622 override to pickles.
    Idempotent: only modifies row if current state still matches expected pattern."""
    ms_dir = cache_dir / "margin_short"
    swap_applied = swap_skipped_clean = swap_failed = 0
    override_applied = override_skipped_clean = override_failed = 0

    # Swap fixes (groupable by sym for batch write)
    for sid, fix_list in plan["swap_fixes"].items():
        p = ms_dir / f"{sid}.pkl"
        if not p.exists():
            swap_failed += len(fix_list)
            continue
        df = pd.read_pickle(p)
        # Schema drift check
        if set(df.columns) != FINMIND_MARGIN_SHORT_COLS:
            logger.warning("%s schema drift, skipping", sid)
            swap_failed += len(fix_list)
            continue
        mod = 0
        for idx, cb, cs in fix_list:
            if idx not in df.index:
                swap_failed += 1
                continue
            pb = int(df.at[idx, "ShortSaleBuy"])
            ps = int(df.at[idx, "ShortSaleSell"])
            if pb == cb and ps == cs:
                swap_skipped_clean += 1  # already correct
                continue
            if pb == cs and ps == cb:
                # still in swap state — apply
                if not dry_run:
                    df.at[idx, "ShortSaleBuy"] = cb
                    df.at[idx, "ShortSaleSell"] = cs
                mod += 1
                swap_applied += 1
            else:
                # unexpected state — skip
                swap_failed += 1
        if mod > 0 and not dry_run:
            _write_atomic(p, df)

    # 2022-06-22 override (per-sym, single row each)
    if include_20220622:
        target_22 = pd.Timestamp("2022-06-22")
        for sid, info in plan.get("override_20220622", {}).items():
            p = ms_dir / f"{sid}.pkl"
            if not p.exists():
                override_failed += 1
                continue
            df = pd.read_pickle(p)
            if set(df.columns) != FINMIND_MARGIN_SHORT_COLS:
                override_failed += 1
                continue
            # Legacy v2 JSON lacks idx field; resolve by date scan.
            idx = info.get("idx", -1)
            if idx < 0 or idx not in df.index:
                mask = df["date"].dt.normalize() == target_22
                if not mask.any():
                    override_failed += 1
                    continue
                idx = df.index[mask][0]
            cur_b = int(df.at[idx, "ShortSaleBuy"])
            cur_s = int(df.at[idx, "ShortSaleSell"])
            if cur_b == info["ssb"] and cur_s == info["sss"]:
                override_skipped_clean += 1  # already correct
                continue
            if cur_b == info["pkl_ssb"] and cur_s == info["pkl_sss"]:
                # still in original FinMind state → apply override
                if not dry_run:
                    df.at[idx, "ShortSaleBuy"] = info["ssb"]
                    df.at[idx, "ShortSaleSell"] = info["sss"]
                    _write_atomic(p, df)
                override_applied += 1
            else:
                override_failed += 1  # unexpected state

    return {
        "swap_applied": swap_applied,
        "swap_skipped_clean": swap_skipped_clean,
        "swap_failed": swap_failed,
        "override_applied": override_applied,
        "override_skipped_clean": override_skipped_clean,
        "override_failed": override_failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="R11.1 v2 TPEX swap + 2022-06-22 migration")
    default_plan = PROJECT_ROOT / "scripts" / "migration_r11_1_plan_v2.json"
    parser.add_argument("--plan", default=str(default_plan) if default_plan.exists() else None,
                        help=f"Plan JSON path (default: {default_plan} if exists)")
    parser.add_argument("--scan", action="store_true",
                        help="Re-scan cache live (~30-50 min); writes plan to data/ then applies")
    parser.add_argument("--include-20220622", action="store_true",
                        help="Also override 593 SS values for 2022-06-22 anomaly")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()

    cache_dir = pathlib.Path(args.cache_dir) if args.cache_dir else resolve_cache_dir()
    logger.info("Cache dir: %s", cache_dir)

    if args.plan:
        raw = json.load(open(args.plan, "r", encoding="utf-8"))
        plan = _normalize_plan(raw)
        logger.info("Loaded plan (%s schema): swap=%d syms %d rows, override_22=%d",
                    "native" if "swap_fixes" in raw else "legacy",
                    plan["stats"]["swap_syms"], plan["stats"]["swap_rows"],
                    plan["stats"]["override_20220622_count"])
    elif args.scan:
        logger.info("Scanning (this takes ~30-50 min)...")
        plan = scan_and_build_plan(cache_dir, sleep=args.sleep)
        plan_path = cache_dir.parent / "migration_r11_1_plan.json"
        with plan_path.open("w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        logger.info("Plan written to %s", plan_path)
        logger.info("Stats: swap_rows=%d swap_syms=%d override_22=%d non_swap_others=%d fetch_fails=%d",
                    plan["stats"]["swap_rows"], plan["stats"]["swap_syms"],
                    plan["stats"]["override_20220622_count"],
                    plan["stats"]["non_swap_others"], plan["stats"]["fetch_fails"])
    else:
        parser.error("Must specify --scan or --plan <path> (default plan not found at "
                     f"{default_plan})")

    result = apply_plan(cache_dir, plan, args.include_20220622, args.dry_run)
    logger.info("=== APPLY RESULT (dry_run=%s include_20220622=%s) ===",
                args.dry_run, args.include_20220622)
    for k, v in result.items():
        logger.info("  %s: %d", k, v)


if __name__ == "__main__":
    main()
