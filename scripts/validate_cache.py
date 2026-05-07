"""Cache Validator — ALL phases, trading-day-level completeness.

Validates stock_info, dividends, OHLCV (TWSE+TPEX), Revenue, market_value.
Can auto-fix by creating/patching pkl files from TWSE (proxy) and FinMind.

Usage:
    PYTHONPATH=. python scripts/validate_cache.py
    PYTHONPATH=. python scripts/validate_cache.py --fix
    PYTHONPATH=. python scripts/validate_cache.py --fix --source twse
    PYTHONPATH=. python scripts/validate_cache.py --fix --source tpex
    PYTHONPATH=. python scripts/validate_cache.py --fix --source revenue
    PYTHONPATH=. python scripts/validate_cache.py --fix --clean-ghosts
    PYTHONPATH=. python scripts/validate_cache.py --fix --dry-run
    PYTHONPATH=. python scripts/validate_cache.py --fix --stock 2330
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import pickle
import random
import time as _time
from collections import Counter
from datetime import datetime

import pandas as pd
import requests as _req

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CACHE = PROJECT_ROOT / "data" / "cache_new"
FIX_LIST_PATH = PROJECT_ROOT / "data" / "validate_fix_list.json"

TWSE_INTERVAL = 1.5
CALENDAR_REFERENCE = ["0050", "0055", "0056", "1101", "1102",
                       "1103", "1108", "1109", "1110", "1201"]
MIN_VOTES = 3
START_DATE = pd.Timestamp("2019-01-01", tz="UTC")
START_YEAR, START_MONTH = 2019, 1
ETF_SET = {"0050", "0051", "0052", "0053", "0055", "0056"}
PROXY_LIST_URL = ("https://raw.githubusercontent.com/proxifly/"
                  "free-proxy-list/main/proxies/protocols/socks5/data.txt")

_TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"


# =========================================================================
# Utilities
# =========================================================================

def _is_phase2_running() -> bool:
    p2 = PROJECT_ROOT / "data" / "cache_rebuild_p2.json"
    if not p2.exists():
        return False
    return (_time.time() - p2.stat().st_mtime) < 300

def _load_si(cache_dir: pathlib.Path):
    si_path = cache_dir / "stock_info" / "stock_info_snapshot.csv"
    if not si_path.exists():
        return pd.DataFrame(), set(), set(), {}
    si = pd.read_csv(si_path)
    si["stock_id"] = si["stock_id"].astype(str).str.strip()
    twse = set(si[si["type"] == "twse"]["stock_id"])
    tpex = set(si[si["type"] == "tpex"]["stock_id"])
    # IPO dates
    ipo = {}
    for _, row in si.iterrows():
        sid = str(row.get("stock_id", "")).strip()
        ds = str(row.get("date", "")).strip()
        if sid and len(ds) >= 7:
            try:
                dt = pd.to_datetime(ds)
                ipo[sid] = (dt.year, dt.month)
            except Exception:
                pass
    return si, twse, tpex, ipo

def _classify(sid: str, twse: set, tpex: set) -> str:
    if sid in tpex: return "tpex"
    if sid in twse: return "twse"
    if sid in ETF_SET: return "etf"
    return "twse"

def _month_range(sy, sm, ey, em):
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12: y += 1; m = 1

def _parse_roc_date(roc_str: str) -> str | None:
    import re
    ma = re.match(r"(\d+)\D+(\d+)\D+(\d+)", roc_str)
    if not ma: return None
    return f"{int(ma.group(1))+1911:04d}-{int(ma.group(2)):02d}-{int(ma.group(3)):02d}"


# =========================================================================
# ProxyPool for TWSE
# =========================================================================

class ProxyPool:
    def __init__(self, max_per_ip: int = 30):
        self._proxies: list[str] = []
        self._idx = -1
        self._calls = 0
        self._max = max_per_ip

    @property
    def proxies_dict(self) -> dict | None:
        if self._idx < 0 or self._idx >= len(self._proxies): return None
        p = self._proxies[self._idx]
        return {"http": p, "https": p}

    @property
    def label(self) -> str:
        if self._idx < 0: return "Direct"
        if self._idx < len(self._proxies):
            return f"Proxy({self._proxies[self._idx].split('/')[-1][:20]})"
        return "NoProxy"

    def record_call(self): self._calls += 1
    def need_rotate(self) -> bool: return self._calls >= self._max

    def force_rotate(self):
        self._calls = self._max

    def rotate(self):
        self._idx += 1
        self._calls = 0
        if self._idx >= len(self._proxies):
            new = self._fetch()
            if new: self._proxies.extend(new)
            else:
                logger.warning("No proxies, waiting 5 min...")
                _time.sleep(300)
                new = self._fetch()
                if new: self._proxies.extend(new)
        if self._idx < len(self._proxies):
            logger.info("Switched to %s", self.label)

    def _fetch(self) -> list[str]:
        import urllib3; urllib3.disable_warnings()
        logger.info("Fetching SOCKS5 proxy list...")
        try:
            resp = _req.get(PROXY_LIST_URL, timeout=15)
            cands = [l.strip() for l in resp.text.strip().split("\n") if l.strip()]
        except Exception: return []
        random.shuffle(cands)
        ok = []
        for p in cands[:100]:  # test more candidates
            try:
                r = _req.get(_TWSE_STOCK_DAY_URL,
                    params={"date":"20240101","stockNo":"2330","response":"json"},
                    proxies={"http":p,"https":p}, timeout=8,
                    headers={"User-Agent":"Mozilla/5.0"}, verify=False)
                if r.status_code == 200:
                    ok.append(p); logger.info("  OK: %s", p)
                    if len(ok) >= 20: break  # keep up to 20 working proxies
            except Exception: pass
        logger.info("Found %d working proxies", len(ok))
        return ok


# =========================================================================
# Trading Calendar
# =========================================================================

class TradingCalendar:
    def __init__(self, days: list[pd.Timestamp]):
        self.all_days = sorted(days)
        self.all_days_set = set(self.all_days)
        self.month_days: dict[tuple[int,int], set[pd.Timestamp]] = {}
        for d in self.all_days:
            self.month_days.setdefault((d.year, d.month), set()).add(d)
        self.first_day = self.all_days[0] if self.all_days else None
        self.last_day = self.all_days[-1] if self.all_days else None

    @property
    def total_days(self): return len(self.all_days)
    @property
    def total_months(self): return len(self.month_days)


def build_calendar(ohlcv_dir: pathlib.Path) -> TradingCalendar:
    # Phase 1: 10 支參考股投票建主日曆
    votes: Counter[pd.Timestamp] = Counter()
    avail = []
    for sym in CALENDAR_REFERENCE:
        pkl = ohlcv_dir / f"{sym}.pkl"
        if not pkl.exists(): continue
        try:
            df = pd.read_pickle(pkl)
            for d in df.index:
                if d >= START_DATE: votes[d] += 1
            avail.append(sym)
        except Exception: continue
    thr = MIN_VOTES if len(avail) >= 5 else max(1, len(avail) // 2)
    days = sorted(d for d, c in votes.items() if c >= thr)

    # Phase 2: 掃所有 pkl 的最後 10 筆，延伸日曆到最新交易日
    # 解決問題：參考股 last_day 落後時，後續日期偵測不到缺漏
    ext_count = 0
    if days:
        ref_last = days[-1]
        extra_votes: Counter[pd.Timestamp] = Counter()
        for pkl in ohlcv_dir.glob("*.pkl"):
            try:
                df = pd.read_pickle(pkl)
                for d in df.index[-10:]:
                    if d > ref_last:
                        extra_votes[d] += 1
            except Exception:
                continue
        ext_days = [d for d, c in extra_votes.items() if c >= MIN_VOTES]
        ext_count = len(ext_days)
        days = sorted(set(days) | set(ext_days))

    if avail:
        logger.info("Calendar: %d days (%d base + %d extended) from %d refs (%s~%s)",
                    len(days), len(days) - ext_count, ext_count, len(avail),
                    days[0].date() if days else "?", days[-1].date() if days else "?")
    return TradingCalendar(days)


# =========================================================================
# Phase 1 Validation
# =========================================================================

def validate_stock_info(cache_dir: pathlib.Path) -> list[str]:
    issues = []
    pkl = cache_dir / "stock_info" / "_global.pkl"
    if not pkl.exists(): return ["stock_info/_global.pkl not found"]
    try: si = pd.read_pickle(pkl)
    except Exception as e: return [f"Cannot load: {e}"]
    if len(si) < 1900: issues.append(f"Too few: {len(si)}")
    si["stock_id"] = si["stock_id"].astype(str).str.strip()
    if si[["stock_id","type"]].isna().sum().sum() > 0: issues.append("Null values")
    if si["stock_id"].duplicated().sum() > 0: issues.append("Duplicate stock_id")
    for s in ["2330","2317","2454","2881","2603"]:
        if s not in set(si["stock_id"]): issues.append(f"{s} missing")
    return issues

def validate_dividends(cache_dir: pathlib.Path) -> list[str]:
    issues = []
    pkl = cache_dir / "dividends" / "_global.pkl"
    if not pkl.exists(): return ["dividends not found"]
    try:
        with open(pkl, "rb") as f: divs = pickle.load(f)
    except Exception as e: return [f"Cannot load: {e}"]
    if not isinstance(divs, list): return [f"Wrong type: {type(divs).__name__}"]
    if len(divs) < 5000: issues.append(f"Too few: {len(divs)}")
    bad_amt = sum(1 for d in divs if d.get("cash_dividend", 0) <= 0)
    bad_close = sum(1 for d in divs if d.get("close_before", 0) <= 0)
    seen = set(); dups = 0
    for d in divs:
        k = (d.get("stock_id",""), d.get("ex_date",""))
        if k in seen: dups += 1
        seen.add(k)
    if bad_amt: issues.append(f"{bad_amt} records cash_dividend<=0")
    if bad_close: issues.append(f"{bad_close} records close_before<=0")
    if dups: issues.append(f"{dups} duplicate (stock_id, ex_date)")
    d0050 = sum(1 for d in divs if d.get("stock_id") == "0050")
    if d0050 < 10: issues.append(f"0050 only {d0050} records")
    return issues


# =========================================================================
# OHLCV Validation
# =========================================================================

def validate_ohlcv(pkl: pathlib.Path, cal: TradingCalendar,
                    twse: set, tpex: set) -> dict:
    sid = pkl.stem
    src = _classify(sid, twse, tpex)
    r = {"stock_id": sid, "source": src, "rows": 0,
         "struct_errors": [], "issues": [], "index_name": None,
         "last_date": None, "vol0_days": 0}
    try: df = pd.read_pickle(pkl)
    except Exception as e:
        r["struct_errors"].append(f"Cannot load: {e}"); return r
    if df.empty:
        r["struct_errors"].append("Empty"); return r

    # Structure
    if set(df.columns) != {"open","high","low","close","volume"}:
        r["struct_errors"].append(f"Bad cols: {list(df.columns)}")
    if df.index.tz is None: r["struct_errors"].append("No UTC")
    if df.index.duplicated().sum() > 0:
        r["struct_errors"].append(f"{df.index.duplicated().sum()} dup dates")
    if df.isna().sum().sum() > 0: r["struct_errors"].append("NaN present")
    if not df.index.is_monotonic_increasing: r["struct_errors"].append("Not sorted")
    r["index_name"] = df.index.name
    r["rows"] = len(df)
    r["last_date"] = str(df.index[-1].date())

    # Values
    if "close" in df.columns:
        zc = (df["close"] <= 0).sum()
        if zc:
            r["struct_errors"].append(f"{zc} close<=0")
            # Add affected months to fix issues (so fix_twse will re-fetch them)
            bad_months = set((d.year, d.month) for d in df.index[df["close"] <= 0])
            existing_issue_months = {(i["year"], i["month"]) for i in r["issues"]}
            for yr, mo in sorted(bad_months):
                if (yr, mo) not in existing_issue_months:
                    r["issues"].append({
                        "stock_id": sid, "source": src, "year": yr, "month": mo,
                        "expected_days": len(cal.month_days.get((yr, mo), set())),
                        "actual_days": 0, "missing_days": [],
                        "issue_type": "close_zero"})
        if len(df["close"].unique()) <= 5 and len(df) > 100:
            r["struct_errors"].append("CONSTANT DATA")
    if "high" in df.columns and "low" in df.columns:
        if (df["high"] < df["low"]).sum() > 0 and src in ("twse","etf"):
            r["struct_errors"].append("high<low")
    if "volume" in df.columns:
        r["vol0_days"] = int((df["volume"] == 0).sum())

    # Future dates
    today = pd.Timestamp(datetime.now().strftime("%Y-%m-%d"), tz="UTC")
    fut = (df.index > today).sum()
    if fut: r["struct_errors"].append(f"LOOK-AHEAD: {fut} future dates")

    # Day-level completeness (vs calendar)
    if cal.total_days == 0: return r
    stock_dates = set(df.index)
    first = df.index[0]
    for (yr, mo), cal_days in cal.month_days.items():
        ms = pd.Timestamp(f"{yr}-{mo:02d}-01", tz="UTC")
        me = ms + pd.offsets.MonthEnd(0)
        if me < first: continue
        exp = {d for d in cal_days if d >= first}
        if not exp: continue
        act = stock_dates & exp
        miss = sorted(exp - act)
        if not miss: continue
        itype = "missing_month" if len(act) == 0 else "partial_month"
        r["issues"].append({
            "stock_id": sid, "source": src, "year": yr, "month": mo,
            "expected_days": len(exp), "actual_days": len(act),
            "missing_days": [str(d.date()) for d in miss], "issue_type": itype})
    return r


# =========================================================================
# Revenue Validation
# =========================================================================

def validate_revenue(cache_dir: pathlib.Path, ohlcv_ids: set, si_4d: set):
    issues = []; short_list = []
    rev_dir = cache_dir / "revenue"
    if not rev_dir.exists(): return ["revenue/ not found"], {}, []
    files = list(rev_dir.glob("*.pkl"))
    stats = {"total": len(files), "good": 0, "short": 0, "load_err": 0,
             "neg_rev": 0, "no_file": 0}
    for f in files:
        try: df = pd.read_pickle(f)
        except Exception:
            stats["load_err"] += 1; issues.append(f"{f.stem}: load error"); continue
        if not isinstance(df, pd.DataFrame) or df.empty:
            stats["short"] += 1; short_list.append((f.stem, 0)); continue
        if "revenue" in df.columns:
            neg = (df["revenue"] < 0).sum()
            if neg: stats["neg_rev"] += 1; issues.append(f"{f.stem}: {neg} negative revenue")
        if len(df) >= 12: stats["good"] += 1
        else: stats["short"] += 1; short_list.append((f.stem, len(df)))

    rev_ids = {f.stem for f in files}
    # Missing: in stock_info but no revenue file (exclude ETFs)
    missing = si_4d - rev_ids - ETF_SET
    stats["no_file"] = len(missing)
    if missing:
        issues.append(f"{len(missing)} stocks in stock_info with no revenue file")
    # Coverage vs OHLCV
    ohlcv_no_rev = ohlcv_ids - rev_ids - ETF_SET
    stats["ohlcv_no_rev"] = len(ohlcv_no_rev)

    return issues, stats, short_list


# =========================================================================
# Phase 5 Validation
# =========================================================================

def validate_market_value(cache_dir: pathlib.Path) -> list[str]:
    mv = cache_dir / "market_value" / "_global.pkl"
    if not mv.exists(): return ["market_value/_global.pkl not found (Phase 5 not run)"]
    try:
        df = pd.read_pickle(mv)
        if df.empty: return ["market_value is empty"]
        for col in ["stock_id", "date", "market_value"]:
            if col not in df.columns: return [f"Missing column: {col}"]
        neg = (df["market_value"] <= 0).sum()
        if neg: return [f"{neg} negative/zero market_value rows"]
    except Exception as e: return [f"Cannot load: {e}"]
    return []


# =========================================================================
# Ghost Detection
# =========================================================================

def detect_ghosts(cache_dir: pathlib.Path):
    ghosts = {}
    ohlcv_dir = cache_dir / "ohlcv"
    for phase, fname in [(2, "cache_rebuild_p2.json"), (3, "cache_rebuild_p3.json")]:
        p = PROJECT_ROOT / "data" / fname
        if not p.exists(): continue
        try:
            done = json.load(open(p))
            g = [s for s in done if not (ohlcv_dir / f"{s}.pkl").exists()]
            ghosts[phase] = g
        except Exception: pass
    return ghosts


# =========================================================================
# Report
# =========================================================================

def generate_report(si_issues, div_issues, ohlcv_results, rev_issues, rev_stats,
                     rev_short, mv_issues, ghosts, cal,
                     twse, tpex, si_4d, ohlcv_ids, rev_ids) -> str:
    L = []
    L.append("=" * 60)
    L.append("Cache Validation Report - ALL PHASES")
    L.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if _is_phase2_running():
        L.append("!! Phase 2 is currently running !!")
    L.append("=" * 60)

    # Phase 1
    L.append("\n--- Phase 1: stock_info ---")
    L.append(f"  {'PASS' if not si_issues else 'FAIL: ' + '; '.join(si_issues)}")
    L.append("\n--- Phase 1: dividends ---")
    L.append(f"  {'PASS' if not div_issues else 'FAIL: ' + '; '.join(div_issues)}")

    # OHLCV
    L.append("\n--- Phase 2+3: OHLCV ---")
    if cal.total_days:
        L.append(f"  Calendar: {cal.total_days} days, {cal.total_months} months")
    twse_r = [r for r in ohlcv_results if r["source"] in ("twse","etf")]
    tpex_r = [r for r in ohlcv_results if r["source"] == "tpex"]
    L.append(f"  TWSE files: {len(twse_r)}/{len(twse)+len(ETF_SET)}" +
             (" (Phase 2 in progress)" if _is_phase2_running() else ""))
    L.append(f"  TPEX files: {len(tpex_r)}/{len(tpex)}")

    # Missing pkl (D1)
    missing_twse = (twse | ETF_SET) - ohlcv_ids
    missing_tpex = tpex - ohlcv_ids
    if missing_twse or missing_tpex:
        L.append(f"  Missing pkl (should exist, no file):")
        if missing_twse: L.append(f"    TWSE: {len(missing_twse)}")
        if missing_tpex: L.append(f"    TPEX: {len(missing_tpex)} {sorted(missing_tpex)[:5]}")

    # Ghosts (D3)
    for phase, glist in ghosts.items():
        if glist: L.append(f"  P{phase} ghost: {len(glist)} (done but no pkl)")

    # Structure errors
    struct = [r for r in ohlcv_results if r["struct_errors"]]
    if struct:
        L.append(f"  Structure errors: {len(struct)} stocks")
        for r in struct[:5]:
            L.append(f"    {r['stock_id']}: {', '.join(r['struct_errors'])}")
        if len(struct) > 5: L.append(f"    ... and {len(struct)-5} more")

    # Completeness
    with_issues = [r for r in ohlcv_results if r["issues"]]
    miss_m = sum(1 for r in ohlcv_results for i in r["issues"] if i["issue_type"]=="missing_month")
    part_m = sum(1 for r in ohlcv_results for i in r["issues"] if i["issue_type"]=="partial_month")
    miss_d = sum(len(i["missing_days"]) for r in ohlcv_results for i in r["issues"])
    L.append(f"  Completeness: {len(with_issues)} stocks, {miss_m} missing months, {part_m} partial, {miss_d} days total")

    # Staleness (D6)
    today = pd.Timestamp(datetime.now().strftime("%Y-%m-%d"), tz="UTC")
    stale = [(r["stock_id"], r["source"], r["last_date"])
             for r in ohlcv_results
             if r["last_date"] and cal.last_day and
             pd.Timestamp(r["last_date"], tz="UTC") < cal.last_day - pd.Timedelta(days=5)]
    if stale:
        L.append(f"  Stale (>5d behind calendar): {len(stale)}")

    # Index name (D7)
    idx_names = Counter(r["index_name"] for r in ohlcv_results if r["index_name"])
    if len(idx_names) > 1:
        L.append(f"  Index name inconsistency: {dict(idx_names)} (warning)")

    # Volume=0 (D8)
    vol0 = [(r["stock_id"], r["vol0_days"]) for r in ohlcv_results if r["vol0_days"] > 50]
    if vol0:
        L.append(f"  Excessive volume=0: {len(vol0)} stocks (>50 days)")

    # Revenue
    L.append("\n--- Phase 4: Revenue ---")
    L.append(f"  Files: {rev_stats.get('total',0)} | Good: {rev_stats.get('good',0)} | Short: {rev_stats.get('short',0)}")
    L.append(f"  No file (in stock_info): {rev_stats.get('no_file',0)}")
    L.append(f"  Load errors: {rev_stats.get('load_err',0)} | Negative revenue: {rev_stats.get('neg_rev',0)}")
    L.append(f"  OHLCV without revenue: {rev_stats.get('ohlcv_no_rev',0)}")
    if rev_short:
        L.append(f"  Short (<12 months): {len(rev_short)}")
        for sid, cnt in rev_short[:5]:
            L.append(f"    {sid}: {cnt} months")
        if len(rev_short) > 5: L.append(f"    ... and {len(rev_short)-5} more")
    if rev_issues:
        for i in rev_issues[:3]: L.append(f"  FAIL: {i}")

    # Phase 5
    L.append("\n--- Phase 5: market_value ---")
    L.append(f"  {'PASS' if not mv_issues else '; '.join(mv_issues)}")

    # Cross-validation
    L.append("\n--- Cross-validation ---")
    L.append(f"  stock_info -> OHLCV missing: {len(missing_twse)+len(missing_tpex)}")
    si_no_rev = si_4d - rev_ids - ETF_SET
    L.append(f"  stock_info -> Revenue missing: {len(si_no_rev)}")
    ohlcv_no_si = ohlcv_ids - (twse | tpex | ETF_SET)
    L.append(f"  OHLCV without stock_info: {len(ohlcv_no_si)}")

    # Summary
    total = (len(si_issues) + len(div_issues) + len(with_issues) + len(struct) +
             len(rev_issues) + len(mv_issues) + len(stale) +
             sum(len(g) for g in ghosts.values()))
    fixable_twse = len([r for r in ohlcv_results if r["issues"] and r["source"] in ("twse","etf")])
    L.append(f"\n--- Summary ---")
    L.append(f"  Total issues: {total}")
    L.append(f"  Missing OHLCV pkl: TWSE {len(missing_twse)}, TPEX {len(missing_tpex)}")
    L.append(f"  Ghosts: P2={len(ghosts.get(2,[]))}, P3={len(ghosts.get(3,[]))}")
    L.append(f"  Stale: {len(stale)}")
    L.append(f"  Overall: {'PASS' if total == 0 else 'ISSUES FOUND'}")

    return "\n".join(L)


# =========================================================================
# Fix: TWSE (patch + create new)
# =========================================================================

def _fetch_twse_month(sym, yr, mo, pool):
    """Fetch one month of OHLCV from TWSE via proxy pool."""
    import urllib3; urllib3.disable_warnings()
    if pool.need_rotate(): pool.rotate()
    date_str = f"{yr}{mo:02d}01"
    try:
        resp = _req.get(_TWSE_STOCK_DAY_URL,
            params={"date": date_str, "stockNo": sym, "response": "json"},
            timeout=15, headers={"User-Agent": "Mozilla/5.0"},
            verify=False, proxies=pool.proxies_dict)
        pool.record_call()
        if resp.status_code in (307, 403):
            pool.force_rotate(); pool.rotate()
            _time.sleep(2)
            resp = _req.get(_TWSE_STOCK_DAY_URL,
                params={"date": date_str, "stockNo": sym, "response": "json"},
                timeout=15, headers={"User-Agent": "Mozilla/5.0"},
                verify=False, proxies=pool.proxies_dict)
            pool.record_call()
        if resp.status_code != 200: return []
        data = resp.json()
        if data.get("stat") != "OK": return []
        records = []
        for row in data.get("data") or []:
            if len(row) < 7: continue
            dp = _parse_roc_date(str(row[0]))
            if not dp: continue
            try:
                records.append({"date": dp,
                    "open": float(str(row[3]).replace(",","")),
                    "high": float(str(row[4]).replace(",","")),
                    "low": float(str(row[5]).replace(",","")),
                    "close": float(str(row[6]).replace(",","")),
                    "volume": int(str(row[1]).replace(",",""))})
            except (ValueError, TypeError): continue
        return records
    except Exception: return []


FIX_PROGRESS_PATH = PROJECT_ROOT / "data" / "fix_twse_progress.json"

def _fetch_with_retry(sym, yr, mo, pool, retries=3):
    """Fetch one month with retry. Returns records list or []."""
    for attempt in range(retries):
        recs = _fetch_twse_month(sym, yr, mo, pool)
        if recs:
            return recs
        if attempt < retries - 1:
            logger.warning("  %s %d-%02d: empty (attempt %d), retrying...", sym, yr, mo, attempt+1)
            pool.force_rotate(); pool.rotate()
            _time.sleep(3)
    logger.warning("  %s %d-%02d: failed after %d attempts", sym, yr, mo, retries)
    return []

def fix_twse(fix_entries, missing_twse, ohlcv_dir, ipo_dates, si_df=None, dry_run=False):
    """Patch existing pkl AND create new ones for missing TWSE stocks."""
    pool = ProxyPool()

    # DR stocks (存託憑證, industry_category=91) have no TWSE STOCK_DAY data — skip
    dr_stocks: set[str] = set()
    if si_df is not None and not si_df.empty and "industry_category" in si_df.columns:
        dr_stocks = set(si_df[si_df["industry_category"].astype(str) == "91"]["stock_id"].astype(str))
        if dr_stocks:
            logger.info("Skipping %d DR stocks (industry_category=91): %s...",
                        len(dr_stocks), sorted(dr_stocks)[:5])

    # Load fix progress (resume support) — 月份級別 key: "sym_yr_mo"
    # 只有成功的月份才寫入，失敗的月份下次自動重試
    progress_done: set[str] = set()
    if FIX_PROGRESS_PATH.exists():
        try:
            progress_done = set(json.load(open(FIX_PROGRESS_PATH)))
            logger.info("Resuming fix: %d months already done", len(progress_done))
        except Exception: pass

    def _save_progress(done_set):
        with open(FIX_PROGRESS_PATH, "w") as f:
            json.dump(sorted(done_set), f)

    # Part 1: Patch existing (缺月補洞 + close_zero 修復)
    # 只把尚未成功的月份放進 by_stock
    by_stock: dict[str, list] = {}
    for e in fix_entries:
        if e["source"] not in ("twse", "etf"): continue
        month_key = f"{e['stock_id']}_{e['year']}_{e['month']:02d}"
        if month_key not in progress_done:
            by_stock.setdefault(e["stock_id"], []).append(e)

    if by_stock:
        todo_months = sum(len(v) for v in by_stock.values())
        logger.info("TWSE patch: %d stocks, %d months%s (%d months already done)",
                    len(by_stock), todo_months,
                    " (DRY)" if dry_run else "", len(progress_done))
        if not dry_run:
            fixed = 0; failed = 0
            for sym, entries in sorted(by_stock.items()):
                pkl_path = ohlcv_dir / f"{sym}.pkl"
                if not pkl_path.exists(): continue
                df = pd.read_pickle(pkl_path); added = 0
                for e in entries:
                    month_key = f"{sym}_{e['year']}_{e['month']:02d}"
                    recs = _fetch_with_retry(sym, e["year"], e["month"], pool)
                    if recs:
                        ndf = pd.DataFrame(recs)
                        ndf["date"] = pd.to_datetime(ndf["date"])
                        ndf = ndf.set_index("date").sort_index()
                        ndf.index = ndf.index.tz_localize("UTC")
                        ndf = ndf[["open","high","low","close","volume"]].dropna()
                        ndf = ndf[ndf["close"] > 0]  # drop invalid rows
                        df = pd.concat([df, ndf])
                        df = df[~df.index.duplicated(keep="last")].sort_index()
                        added += len(ndf); fixed += 1
                        logger.info("  %s %d-%02d: +%d rows [%s]",
                                    sym, e["year"], e["month"], len(ndf), pool.label)
                        progress_done.add(month_key)   # 成功才標 done
                        _save_progress(progress_done)  # 每月 save，精確恢復中斷點
                    else:
                        failed += 1
                        # 不加入 progress_done → 下次 --fix 自動重試
                    _time.sleep(TWSE_INTERVAL)
                # Always write back: also removes any close<=0 rows that weren't re-fetched
                df = df[df["close"] > 0]
                tmp = pkl_path.with_suffix(".tmp"); df.to_pickle(tmp); tmp.replace(pkl_path)
            logger.info("TWSE patch: %d months fixed, %d failed (will retry next run)", fixed, failed)

    # Part 2: Create new pkl for missing stocks
    if not missing_twse: return
    p2_done = set()
    p2_path = PROJECT_ROOT / "data" / "cache_rebuild_p2.json"
    if p2_path.exists():
        try: p2_done = set(json.load(open(p2_path)))
        except Exception: pass
    # Skip DR stocks and already-done stocks
    create_list = sorted(missing_twse - p2_done - dr_stocks - progress_done)
    if not create_list: return

    logger.info("TWSE create: %d new stocks%s", len(create_list), " (DRY)" if dry_run else "")
    if dry_run:
        for s in create_list[:10]: logger.info("  [DRY] %s: would create from scratch", s)
        return

    for sym in create_list:
        ipo = ipo_dates.get(sym)
        sy, sm = ipo if ipo and ipo > (START_YEAR, START_MONTH) else (START_YEAR, START_MONTH)
        now = datetime.now()
        all_recs = []; consec = 0
        for yr, mo in _month_range(sy, sm, now.year, now.month):
            recs = _fetch_with_retry(sym, yr, mo, pool, retries=2)
            if recs: all_recs.extend(recs); consec = 0
            else: consec += 1
            if consec >= 24: break
            _time.sleep(TWSE_INTERVAL)
        if all_recs:
            df = pd.DataFrame(all_recs)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            df.index = df.index.tz_localize("UTC")
            df = df[["open","high","low","close","volume"]].dropna()
            df = df[df["close"] > 0]
            if len(df) >= 20 and len(df["close"].unique()) > 5:
                tmp = ohlcv_dir / f"{sym}.tmp"; df.to_pickle(tmp)
                tmp.replace(ohlcv_dir / f"{sym}.pkl")
                logger.info("  %s: created %d rows [%s]", sym, len(df), pool.label)
                progress_done.add(sym); _save_progress(progress_done)
            else:
                logger.warning("  %s: %d rows, skipping", sym, len(df))
        else:
            logger.warning("  %s: no data (DR or delisted)", sym)


# =========================================================================
# Fix: TPEX (FinMind)
# =========================================================================

class FinMindRotator:
    """Token + IP rotator for FinMind.
    Token1 + Direct → Token2 + Proxy-A → Token3 + Proxy-B
    Each slot: 550 calls before rotating.
    """
    QUOTA = 550

    def __init__(self):
        from FinMind.data import DataLoader
        self._tokens = [t for k in ["FINMIND_TOKEN","FINMIND_TOKEN2","FINMIND_TOKEN3"]
                        if (t := os.environ.get(k,"")) and t != "your_bot_token_here"]
        if not self._tokens:
            raise RuntimeError("No FINMIND_TOKEN found")
        self._slots: list[tuple[str, str|None]] = [(self._tokens[0], None)]
        self._slot = 0; self._calls = 0
        self._loader: object = None
        self._DataLoader = DataLoader
        self._activate_slot()

    def _fetch_proxy(self) -> str | None:
        import urllib3; urllib3.disable_warnings()
        try:
            resp = _req.get(PROXY_LIST_URL, timeout=15)
            cands = [l.strip() for l in resp.text.strip().split("\n") if l.strip()]
        except Exception: return None
        random.shuffle(cands)
        for p in cands[:40]:
            try:
                r = _req.get("https://api.ipify.org?format=json",
                             proxies={"http":p,"https":p}, timeout=8)
                logger.info("  FinMind proxy OK: %s -> %s", p, r.json().get("ip","?"))
                return p
            except Exception: pass
        return None

    def _activate_slot(self):
        token, proxy = self._slots[self._slot]
        loader = self._DataLoader()
        loader.login_by_token(api_token=token)
        if proxy:
            loader._FinMindApi__session.proxies.update({"http":proxy,"https":proxy})
        self._loader = loader; self._calls = 0
        label = f"Token{self._slot+1}+{'Proxy' if proxy else 'Direct'}"
        logger.info("FinMindRotator: activated %s", label)

    def rotate(self):
        next_slot = self._slot + 1
        if next_slot >= len(self._tokens):
            logger.warning("All FinMind tokens exhausted, waiting 65 min...")
            _time.sleep(65 * 60)
            self._slot = 0
        else:
            # Build next slot with proxy
            if next_slot >= len(self._slots):
                proxy = self._fetch_proxy()
                self._slots.append((self._tokens[next_slot], proxy))
            self._slot = next_slot
        self._activate_slot()

    def fetch(self, sym: str, start_str: str, end_str: str):
        if self._calls >= self.QUOTA: self.rotate()
        _time.sleep(0.5); self._calls += 1
        try:
            return self._loader.taiwan_stock_daily(
                stock_id=sym, start_date=start_str, end_date=end_str)
        except Exception as e:
            logger.warning("  FinMind %s: %s", sym, str(e)[:60])
            return None


def _finmind_raw_to_df(raw) -> "pd.DataFrame | None":
    """Convert FinMind taiwan_stock_daily response to standard OHLCV DataFrame."""
    if raw is None or (hasattr(raw, "empty") and raw.empty): return None
    df = raw.rename(columns={"date":"timestamp","max":"high","min":"low","Trading_Volume":"volume"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    for c in ("open","high","low","close","volume"):
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    cols = [c for c in ("open","high","low","close","volume") if c in df.columns]
    df = df[cols].dropna()
    return df if not df.empty else None


def fix_tpex(fix_entries, missing_tpex, ohlcv_dir, dry_run=False):
    """Fix TPEX stocks: patch partial/missing months AND create missing pkls."""
    rotator = FinMindRotator()
    end = datetime.now().strftime("%Y-%m-%d")

    # Part 1: Patch existing TPEX pkls (partial/missing/close_zero months)
    tpex_entries = [e for e in fix_entries if e["source"] == "tpex"]
    by_stock: dict[str, list] = {}
    for e in tpex_entries:
        by_stock.setdefault(e["stock_id"], []).append(e)

    if by_stock:
        logger.info("TPEX patch: %d stocks, %d months%s",
                    len(by_stock), len(tpex_entries), " (DRY)" if dry_run else "")
        if not dry_run:
            for sym, entries in sorted(by_stock.items()):
                pkl_path = ohlcv_dir / f"{sym}.pkl"
                if not pkl_path.exists(): continue
                # One FinMind call covering full range of affected months
                yms = [(e["year"], e["month"]) for e in entries]
                start_str = f"{min(yms)[0]}-{min(yms)[1]:02d}-01"
                # Use today as end to avoid missing last days of month (e.g. Mar 29-31)
                end_str = datetime.now().strftime("%Y-%m-%d")
                raw = rotator.fetch(sym, start_str, end_str)
                ndf = _finmind_raw_to_df(raw)
                if ndf is not None:
                    df = pd.read_pickle(pkl_path)
                    df = pd.concat([df, ndf])
                    df = df[~df.index.duplicated(keep="last")].sort_index()
                    df = df[df["close"] > 0]  # remove any residual close<=0 rows
                    tmp = pkl_path.with_suffix(".tmp"); df.to_pickle(tmp); tmp.replace(pkl_path)
                    logger.info("  %s: patched %d months, +%d rows", sym, len(entries), len(ndf))
                else:
                    logger.warning("  %s: FinMind returned no data", sym)

    # Part 2: Create missing TPEX pkls
    if not missing_tpex: return
    logger.info("TPEX create: %d stocks%s", len(missing_tpex), " (DRY)" if dry_run else "")
    if dry_run:
        for s in sorted(missing_tpex)[:10]: logger.info("  [DRY] %s", s)
        return
    for sym in sorted(missing_tpex):
        raw = rotator.fetch(sym, f"{START_YEAR}-{START_MONTH:02d}-01", end)
        df = _finmind_raw_to_df(raw)
        if df is not None:
            tmp = ohlcv_dir / f"{sym}.tmp"; df.to_pickle(tmp)
            tmp.replace(ohlcv_dir / f"{sym}.pkl")
            logger.info("  %s: %d rows", sym, len(df))
        else:
            logger.warning("  %s: no data", sym)


# =========================================================================
# Fix: Revenue (FinMind)
# =========================================================================

def fix_revenue(missing_rev, cache_dir, dry_run=False):
    if not missing_rev: return
    logger.info("Revenue fix: %d stocks%s", len(missing_rev), " (DRY)" if dry_run else "")
    if dry_run:
        for s in sorted(missing_rev)[:10]: logger.info("  [DRY] %s", s)
        return
    from src.data.finmind import FinMindSource
    token = os.environ.get("FINMIND_TOKEN", "")
    source = FinMindSource(token=token, backtest_mode=False, cache_dir=str(cache_dir))
    rev_dir = cache_dir / "revenue"
    rev_dir.mkdir(parents=True, exist_ok=True)
    for sym in sorted(missing_rev):
        try:
            df = source.fetch_month_revenue(sym, months=120)
            if df is not None and not df.empty and len(df) >= 6:
                logger.info("  %s: %d months", sym, len(df))
        except Exception as e:
            logger.warning("  %s: %s", sym, str(e)[:60])


# =========================================================================
# Fix: Clean ghosts
# =========================================================================

def fix_ghosts(ghosts, dry_run=False):
    for phase, glist in ghosts.items():
        if not glist: continue
        fname = f"cache_rebuild_p{phase}.json"
        p = PROJECT_ROOT / "data" / fname
        if not p.exists(): continue
        logger.info("P%d ghost cleanup: %d%s", phase, len(glist), " (DRY)" if dry_run else "")
        if dry_run: continue
        try:
            done = json.load(open(p))
            cleaned = [s for s in done if s not in set(glist)]
            with open(p, "w") as f: json.dump(sorted(cleaned), f)
            logger.info("  Removed %d ghosts from %s", len(glist), fname)
        except Exception as e:
            logger.warning("  Failed: %s", e)


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(description="Cache Validator - ALL Phases")
    parser.add_argument("--cache-dir", type=str, default=str(DEFAULT_CACHE))
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source", choices=["twse","tpex","revenue"])
    parser.add_argument("--stock", type=str)
    parser.add_argument("--clean-ghosts", action="store_true")
    parser.add_argument("--output", type=str, default=str(FIX_LIST_PATH))
    args = parser.parse_args()

    cache_dir = pathlib.Path(args.cache_dir)
    ohlcv_dir = cache_dir / "ohlcv"
    si, twse, tpex, ipo_dates = _load_si(cache_dir)
    si_4d = set(si[si["stock_id"].str.fullmatch(r"\d{4}")]["stock_id"]) if not si.empty else set()

    # === Validate all phases ===
    logger.info("Validating Phase 1...")
    si_issues = validate_stock_info(cache_dir)
    div_issues = validate_dividends(cache_dir)

    logger.info("Validating Phase 2+3 OHLCV...")
    cal = build_calendar(ohlcv_dir) if ohlcv_dir.exists() else TradingCalendar([])
    ohlcv_results = []
    if ohlcv_dir.exists():
        for f in sorted(ohlcv_dir.glob("*.pkl")):
            ohlcv_results.append(validate_ohlcv(f, cal, twse, tpex))
    ohlcv_ids = {r["stock_id"] for r in ohlcv_results}

    logger.info("Validating Phase 4 Revenue...")
    rev_ids = {f.stem for f in (cache_dir/"revenue").glob("*.pkl")} if (cache_dir/"revenue").exists() else set()
    rev_issues, rev_stats, rev_short = validate_revenue(cache_dir, ohlcv_ids, si_4d)

    logger.info("Validating Phase 5...")
    mv_issues = validate_market_value(cache_dir)

    logger.info("Detecting ghosts...")
    ghosts = detect_ghosts(cache_dir)

    # === Report ===
    report = generate_report(si_issues, div_issues, ohlcv_results,
                              rev_issues, rev_stats, rev_short, mv_issues, ghosts,
                              cal, twse, tpex, si_4d, ohlcv_ids, rev_ids)
    print(report)

    # === Fix list ===
    fix_entries = []
    for r in ohlcv_results:
        for i in r["issues"]:
            fix_entries.append(i)
    with open(args.output, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                    "total": len(fix_entries), "fix_list": fix_entries},
                   f, indent=2, ensure_ascii=False)
    logger.info("Fix list: %s (%d entries)", args.output, len(fix_entries))

    # === Missing sets for fix ===
    missing_twse = (twse | ETF_SET) - ohlcv_ids
    missing_tpex = tpex - ohlcv_ids
    missing_rev = si_4d - rev_ids - ETF_SET

    # === Fix ===
    do_fix = args.fix or args.dry_run
    if not do_fix and not args.clean_ghosts:
        return

    if _is_phase2_running():
        logger.warning("Phase 2 is running — will skip in-progress stocks")

    # Filter by source/stock
    if args.stock:
        fix_entries = [e for e in fix_entries if e["stock_id"] == args.stock]
        missing_twse = {args.stock} & missing_twse
        missing_tpex = {args.stock} & missing_tpex
        missing_rev = {args.stock} & missing_rev

    if args.clean_ghosts or (do_fix and not args.source):
        fix_ghosts(ghosts, dry_run=args.dry_run)

    if do_fix:
        src = args.source
        if src in (None, "twse"):
            fix_twse(fix_entries, missing_twse, ohlcv_dir, ipo_dates,
                     si_df=si, dry_run=args.dry_run)
        if src in (None, "tpex"):
            fix_tpex(fix_entries, missing_tpex, ohlcv_dir, dry_run=args.dry_run)
        if src in (None, "revenue"):
            fix_revenue(missing_rev, cache_dir, dry_run=args.dry_run)

    # Re-validate after fix (rebuild calendar to include newly-patched dates)
    if do_fix and not args.dry_run:
        logger.info("Re-validating with updated calendar...")
        cal2 = build_calendar(ohlcv_dir)
        r2 = [validate_ohlcv(ohlcv_dir / f"{r['stock_id']}.pkl", cal2, twse, tpex)
              for r in ohlcv_results if r["issues"] and (ohlcv_dir / f"{r['stock_id']}.pkl").exists()]
        remaining = sum(len(r["issues"]) for r in r2)
        logger.info("Remaining OHLCV issues after fix: %d", remaining)


if __name__ == "__main__":
    main()
