"""Cache Rebuild — 全新重建 cache（TWSE 為主，FinMind 為輔）。

建立全新 data/cache_new/ 目錄，不動現有 cache。
Phase 0: 建立空目錄
Phase 1: stock_info + dividends（TWSE）
Phase 2: 上市股 OHLCV（TWSE STOCK_DAY）
Phase 3: 上櫃股 OHLCV（FinMind）
Phase 4: Revenue（FinMind）
Phase 5: market_value（TWSE 計算）

Usage:
    docker compose run --rm --entrypoint python portfolio-bot scripts/cache_rebuild.py
    docker compose run --rm --entrypoint python portfolio-bot scripts/cache_rebuild.py --status
    docker compose run --rm --entrypoint python portfolio-bot scripts/cache_rebuild.py --phase 2
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import pickle
import sys
import time as _time
from datetime import datetime, timedelta

import pandas as pd

# Load .env for FINMIND_TOKEN* (local execution)
try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # Docker has env vars set directly

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
NEW_CACHE = pathlib.Path(os.environ.get("DATA_CACHE_DIR_NEW",
                                         PROJECT_ROOT / "data" / "cache_new"))
PROGRESS_DIR = PROJECT_ROOT / "data"

TWSE_INTERVAL = 1.5  # seconds between TWSE API calls
START_YEAR = 2019
START_MONTH = 1

PROXY_LIST_URL = ("https://raw.githubusercontent.com/proxifly/"
                  "free-proxy-list/main/proxies/protocols/socks5/data.txt")


# =========================================================================
# ProxyPool for TWSE (shared by Phase 2 and validate_cache.py)
# =========================================================================

class TwseProxyPool:
    """Auto-rotate free SOCKS5 proxies when TWSE blocks our IP (HTTP 307)."""

    def __init__(self, max_per_ip: int = 15):
        self._proxies: list[str] = []
        self._idx = -1  # -1 = direct connection
        self._calls = 0
        self._max = max_per_ip
        self._active = False  # starts with direct, proxy only on 307

    @property
    def active(self) -> bool:
        return self._active

    @property
    def proxies_dict(self) -> dict | None:
        if self._idx < 0 or self._idx >= len(self._proxies):
            return None
        p = self._proxies[self._idx]
        return {"http": p, "https": p}

    @property
    def label(self) -> str:
        if not self._active or self._idx < 0:
            return "Direct"
        if self._idx < len(self._proxies):
            return f"Proxy({self._proxies[self._idx].split('/')[-1][:20]})"
        return "NoProxy"

    def record_call(self):
        self._calls += 1

    def need_rotate(self) -> bool:
        return self._active and self._calls >= self._max

    def activate_on_307(self):
        """Switch from direct to proxy mode after detecting 307."""
        if not self._active:
            logger.warning("TWSE 307 detected — switching to proxy mode")
            self._active = True
            self._rotate()

    def rotate_if_needed(self):
        if self.need_rotate():
            self._rotate()

    def _rotate(self):
        self._idx += 1
        self._calls = 0
        if self._idx >= len(self._proxies):
            new = self._fetch()
            if new:
                self._proxies.extend(new)
            else:
                logger.warning("No proxies available, waiting 5 min...")
                _time.sleep(300)
                new = self._fetch()
                if new:
                    self._proxies.extend(new)
        if self._idx < len(self._proxies):
            logger.info("Switched to %s", self.label)

    def _fetch(self) -> list[str]:
        import requests as _req
        import urllib3
        import random
        urllib3.disable_warnings()
        logger.info("Fetching SOCKS5 proxy list...")
        try:
            resp = _req.get(PROXY_LIST_URL, timeout=15)
            candidates = [l.strip() for l in resp.text.strip().split("\n") if l.strip()]
        except Exception:
            return []
        random.shuffle(candidates)
        working = []
        for p in candidates[:25]:
            try:
                r = _req.get(
                    "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
                    params={"date": "20240101", "stockNo": "2330", "response": "json"},
                    proxies={"http": p, "https": p}, timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"}, verify=False)
                if r.status_code == 200:
                    working.append(p)
                    logger.info("  OK: %s", p)
                    if len(working) >= 5:
                        break
            except Exception:
                pass
        logger.info("Found %d working proxies", len(working))
        return working


# =========================================================================
# FinMind Multi-Token Rotator (Phase 3 & 4)
# =========================================================================

class TokenRotator:
    """Rotate Token + IP together to maximize FinMind quota.

    FinMind free tier: 600 calls/hr per token.
    Strategy:
      Token1 + Direct IP     → 580 calls
      Token2 + Proxy-A (new IP) → 580 calls
      Token3 + Proxy-B (new IP) → 580 calls
      All exhausted → wait 65 min, restart from Token1

    Confirmed 2026-04-09: FinMind tracks quota per-token (not per-IP),
    and tokens work from any IP (JWT ip field is not verified).
    We still switch IP together with token for maximum safety.
    """

    QUOTA_PER_SLOT = 580  # leave 20 call buffer under the 600 limit
    QUOTA_WINDOW_MIN = 60  # FinMind quota window — sliding 60min from first call
    _PROXY_LIST_URL = ("https://raw.githubusercontent.com/proxifly/"
                       "free-proxy-list/main/proxies/protocols/socks5/data.txt")

    # 2026-05-05 V0.15 cache infra improvements:
    # - PROXY_BENCHMARK_MAX: max candidates to latency-benchmark per fetch
    # - PROXY_LATENCY_CUTOFF_SEC: ignore proxies slower than this on connect
    # - BACKUP_POOL_SIZE: keep N backup proxies for hot-swap (without rotating token)
    PROXY_BENCHMARK_MAX = 10
    PROXY_LATENCY_CUTOFF_SEC = 5.0
    BACKUP_POOL_SIZE = 3

    def __init__(self):
        self._tokens: list[str] = []
        for key in ["FINMIND_TOKEN", "FINMIND_TOKEN2", "FINMIND_TOKEN3"]:
            t = os.environ.get(key, "")
            if t and t != "your_bot_token_here":
                self._tokens.append(t)
        if not self._tokens:
            raise RuntimeError("No FINMIND_TOKEN found in environment")

        # Slots: (token, proxy_url_or_None)
        # Slot 0 = Token1 + Direct, Slot 1+ = Token2+ with proxies (fetched on demand)
        self._slots: list[tuple[str, str | None]] = [(self._tokens[0], None)]
        self._current_slot = 0
        self._calls_on_current = 0
        self._loader = None
        self._current_proxy: str | None = None  # for external access
        # V0.15: track when each slot's first call was made → smart_sleep can
        # calculate exact wait for earliest quota window reset.
        self._slot_first_call_at: dict[int, datetime] = {}
        # V0.15: backup proxies (already latency-verified) for hot-swap.
        self._backup_proxies: list[str] = []
        logger.info("TokenRotator: %d tokens loaded, starting with Token1 + Direct",
                     len(self._tokens))

    @property
    def calls_on_current(self) -> int:
        return self._calls_on_current

    @property
    def current_label(self) -> str:
        proxy = self._slots[self._current_slot][1] if self._current_slot < len(self._slots) else None
        token_num = min(self._current_slot + 1, len(self._tokens))
        if proxy:
            return f"Token{token_num}+Proxy({proxy.split('/')[-1][:20]})"
        return f"Token{token_num}+Direct"

    def _verify_proxy_with_latency(self, proxy: str) -> float | None:
        """V0.15: verify proxy connectivity + return connect latency in seconds.

        Returns None if connection fails or status != 200. A successful return
        means the proxy CAN reach api.ipify.org within PROXY_LATENCY_CUTOFF_SEC
        timeout window. The returned latency lets caller pick the fastest.
        """
        import requests as _req
        t0 = _time.monotonic()
        try:
            r = _req.get(
                "https://api.ipify.org?format=json",
                proxies={"http": proxy, "https": proxy},
                timeout=self.PROXY_LATENCY_CUTOFF_SEC,
            )
            if r.status_code == 200:
                _ = r.json()["ip"]
                return _time.monotonic() - t0
        except Exception:
            pass
        return None

    def _fetch_working_proxy(self) -> str | None:
        """V0.15: fetch + benchmark up to PROXY_BENCHMARK_MAX proxies, return fastest.

        Replaces single-proxy "first OK wins" with latency-aware selection:
        1. Fetch Proxifly list (random shuffle)
        2. Try up to PROXY_BENCHMARK_MAX candidates with latency timing
        3. Return fastest; store next BACKUP_POOL_SIZE in self._backup_proxies for hot-swap

        Trade-off: 10 candidates × 5s timeout = up to 50s upfront, but only
        called on rotation/exhaust path so amortized cost is small. Avoids
        Token1 wasting 577 calls on 25s/call slow proxy (our 14:00 incident).
        """
        import requests as _req
        import random
        logger.info("Fetching free SOCKS5 proxy list (V0.15 latency benchmark)...")
        try:
            resp = _req.get(self._PROXY_LIST_URL, timeout=15)
            candidates = [l.strip() for l in resp.text.strip().split("\n") if l.strip()]
        except Exception as exc:
            logger.warning("Failed to fetch proxy list: %s", exc)
            return None

        random.shuffle(candidates)
        verified: list[tuple[float, str]] = []  # (latency_sec, proxy_url)
        for proxy in candidates:
            if len(verified) >= self.PROXY_BENCHMARK_MAX:
                break
            latency = self._verify_proxy_with_latency(proxy)
            if latency is not None and latency < self.PROXY_LATENCY_CUTOFF_SEC:
                verified.append((latency, proxy))
                logger.info("  Proxy OK: %s @ %.2fs", proxy, latency)

        if not verified:
            logger.warning("No working proxy found in %d candidates",
                           min(self.PROXY_BENCHMARK_MAX, len(candidates)))
            return None

        verified.sort(key=lambda x: x[0])
        fastest_latency, fastest_proxy = verified[0]
        # Store next BACKUP_POOL_SIZE as backup for hot-swap (excludes the primary).
        self._backup_proxies = [p for _, p in verified[1:1 + self.BACKUP_POOL_SIZE]]
        logger.info(
            "Proxy benchmark: best %s @ %.2fs (from %d verified, %d backup pool)",
            fastest_proxy, fastest_latency, len(verified), len(self._backup_proxies),
        )
        return fastest_proxy

    def get_backup_proxy(self) -> str | None:
        """V0.15: pop a pre-verified backup proxy for hot-swap (without rotating token).

        Returns None if backup pool is empty. Caller falls back to full rotate.
        """
        if not self._backup_proxies:
            return None
        return self._backup_proxies.pop(0)

    def patch_current_proxy(self, new_proxy: str) -> None:
        """V0.15: hot-swap proxy on current token without consuming the token slot.

        Used when slow proxy detected but token still has quota. Re-inits
        loader with new proxy on same token. _calls_on_current is preserved
        (don't reset — we're continuing the same quota window).
        """
        token, _old_proxy = self._slots[self._current_slot]
        self._slots[self._current_slot] = (token, new_proxy)
        self._current_proxy = new_proxy
        # Force loader rebuild on next get_loader() to pick up the new proxy.
        self._loader = None
        logger.warning(
            "Hot-swap proxy on [Token%d] (calls=%d/%d, quota preserved): %s",
            self._current_slot + 1, self._calls_on_current,
            self.QUOTA_PER_SLOT, new_proxy,
        )

    def _smart_sleep_until_quota_reset(self) -> None:
        """V0.15: replace hardcoded 65 min sleep with quota-window-aware wait.

        FinMind quota window = QUOTA_WINDOW_MIN (60 min) from first call on
        each token. Once all tokens exhausted, the earliest-resetting token is
        max(slot_first_call_at.values()) + 60min (not min — we need the
        earliest reset that's still in the future; if all are in the past
        already, sleep is 0 = no wait).

        Actually we want the earliest token to come back online: that's
        min(first_call + 60min). For our case Token1@14:00, Token2@14:02,
        Token3@14:42 → Token1 resets first at 15:00; if we're at 14:45 we
        sleep ~15 min. If we're at 15:18 (already past Token1 reset) sleep ~0.
        """
        if not self._slot_first_call_at:
            wait_min = self.QUOTA_WINDOW_MIN
            logger.warning(
                "Smart sleep: no first-call timestamps — fallback %d min wait",
                wait_min,
            )
            _time.sleep(wait_min * 60)
            return

        now = datetime.now()
        # Earliest token to come back online = min(first_call + window)
        earliest_reset = min(
            t + timedelta(minutes=self.QUOTA_WINDOW_MIN)
            for t in self._slot_first_call_at.values()
        )
        sleep_sec = max(60.0, (earliest_reset - now).total_seconds() + 30.0)
        sleep_sec = min(sleep_sec, (self.QUOTA_WINDOW_MIN + 5) * 60.0)  # cap at 65 min
        logger.warning(
            "Smart sleep: %.1f min (earliest token quota window resets at %s)",
            sleep_sec / 60.0, earliest_reset.strftime("%H:%M:%S"),
        )
        _time.sleep(sleep_sec)

    def _build_remaining_slots(self):
        """V0.21 (2026-05-05): hybrid Token1+Direct fixed / Token2/3+Proxy with fallback.

        Per user explicit request 2026-05-05 23:50: "Token1 固定用本機 + Token2/3
        使用 proxy + 過了一小時 quota reset 後自己再用本機 IP 打 Token1".

        Design (revert V0.20 back to V0.19 + clarify):
        - Slot 0 = Token1 + Direct (本機 IP, fixed) — already done in __init__
        - Slot 1 = Token2 + Proxy (Proxifly fetch)
        - Slot 2 = Token3 + Proxy (Proxifly fetch)
        - V0.19 fallback: if proxy SSL fail → _make_loader appends Direct as
          last resort (避免 sleep 浪費)
        - All exhausted → V0.15 smart_sleep until earliest reset → restart
          from Slot 0 (Token1 + Direct) automatically

        V0.20 全 Direct 觀察：Token3+Direct 在 6 calls 即觸發 SLOW 偵測，疑似
        FinMind 對同 IP 多 token 確有 throttle（memory warning 部分 confirmed）。
        V0.21 spread load: Token1 用本機 IP (主力), Token2/3 走 proxy 分散 IP，
        proxy 死才 fallback Direct。
        """
        for i in range(1, len(self._tokens)):
            if i < len(self._slots):
                continue  # already built
            proxy = self._fetch_working_proxy()
            if proxy:
                self._slots.append((self._tokens[i], proxy))
                logger.info("V0.21 Prepared slot %d: Token%d + Proxy %s",
                            i, i + 1, proxy)
            else:
                # Proxifly fetch fail → fallback to Direct upfront (V0.19 fallback
                # would still try, but better to skip the SSL handshake waste).
                logger.warning(
                    "V0.21 Cannot find proxy for Token%d, falling back to Direct",
                    i + 1)
                self._slots.append((self._tokens[i], None))

    def _make_loader(self):
        """V0.17 (2026-05-05) connection-resilient loader build.

        Trigger: V0.15 hot-swap pulled `socks5://104.200.152.30:4145` from
        backup pool, but that proxy was TCP-alive yet HTTPS-dead (couldn't
        reach api.web.finmindtrade.com). `loader.login_by_token()` raised
        ConnectionError uncaught → process crash at 16:16:23.

        Fix: wrap login_by_token in try/except. On connection failure, drain
        backup_proxies and retry; if all backups exhausted, raise ConnectionError
        which caller (_rotate / get_loader) handles via record_quota_error path.
        """
        from FinMind.data import DataLoader
        token, original_proxy = self._slots[self._current_slot]

        # V0.17 retry loop: try original proxy, then drain backups on conn failure.
        # V0.19 (2026-05-05): if all proxies SSL-poisoned (Proxifly free pool can
        # serve self-signed cert proxies), fall back to Direct (None = workstation
        # IP) as last resort. User explicit consent: "掛掉時請用本機IP". Trade-off:
        # 3 tokens sharing workstation IP risks IP-based throttling, but Proxifly
        # today (2026-05-05) is largely unusable so fallback Direct beats sleep.
        proxies_to_try: list[str | None] = [original_proxy]
        proxies_to_try.extend(self._backup_proxies)
        if None not in proxies_to_try:
            proxies_to_try.append(None)  # V0.19 Direct fallback
        last_exc: Exception | None = None

        for attempt_idx, proxy in enumerate(proxies_to_try):
            loader = DataLoader()
            try:
                # Attach proxy BEFORE login so login itself goes through proxy.
                if proxy:
                    proxies = {"http": proxy, "https": proxy}
                    loader._FinMindApi__session.proxies.update(proxies)
                loader.login_by_token(api_token=token)
            except (ConnectionError, Exception) as exc:
                # Catch broad: ConnectionError, MaxRetryError, NewConnectionError,
                # SSLError, ReadTimeout, etc. all surface as failed login.
                exc_name = type(exc).__name__
                msg = str(exc).lower()
                is_connection_failure = (
                    "connection" in msg
                    or "timeout" in msg
                    or "max retries" in msg
                    or "ssl" in msg
                    or exc_name in ("ConnectionError", "MaxRetryError",
                                     "NewConnectionError", "SSLError",
                                     "ReadTimeout", "ConnectTimeout")
                )
                if not is_connection_failure:
                    # Real bug — re-raise.
                    raise
                last_exc = exc
                logger.warning(
                    "V0.17 _make_loader attempt %d/%d failed on proxy=%s: %s",
                    attempt_idx + 1, len(proxies_to_try), proxy, exc_name,
                )
                continue

            # Login succeeded — adopt this loader + proxy.
            self._loader = loader
            # If we fell through to a backup, swap _slots[current] to use it.
            if proxy != original_proxy:
                self._slots[self._current_slot] = (token, proxy)
                # Drain the backup we just consumed (already removed from list
                # since we extended into proxies_to_try above; remove from
                # _backup_proxies if it's still there).
                if proxy in self._backup_proxies:
                    self._backup_proxies.remove(proxy)
            self._current_proxy = proxy
            self._calls_on_current = 0
            logger.info("Loader ready [%s] (0/%d calls)",
                         self.current_label, self.QUOTA_PER_SLOT)
            return

        # All attempts failed — raise so caller can rotate to next token slot.
        raise ConnectionError(
            f"V0.17 _make_loader: all {len(proxies_to_try)} proxy attempts failed "
            f"on Token{self._current_slot + 1}. Last error: {last_exc}"
        )

    def get_loader(self):
        """Return a DataLoader, rotating token+proxy if quota is near limit.

        V0.17: if _make_loader raises ConnectionError (all proxy attempts
        failed on current token), force-advance to next slot.
        """
        if self._loader is None:
            try:
                self._make_loader()
            except ConnectionError as exc:
                logger.warning(
                    "V0.17 _make_loader exhausted all proxies on Token%d: %s — "
                    "force-advancing to next slot",
                    self._current_slot + 1, exc,
                )
                # Force-trigger rotation by marking current slot exhausted.
                self._calls_on_current = self.QUOTA_PER_SLOT

        if self._calls_on_current < self.QUOTA_PER_SLOT:
            return self._loader

        # Current slot exhausted — move to next
        next_slot = self._current_slot + 1

        # Build remaining slots if needed (lazy: only fetch proxies when needed)
        if next_slot >= len(self._slots) and next_slot < len(self._tokens):
            self._build_remaining_slots()

        if next_slot < len(self._slots):
            self._current_slot = next_slot
            # V0.18 (2026-05-05): wrap _make_loader rotation path in try/except
            # too. V0.17 only wrapped the first-call path; rotation path was
            # bare, causing crash when rotated-to-slot's all proxies SSL fail
            # (e.g. 20:36:54 incident: Token2 hot-swap → all 4 proxies SSL fail
            # → ConnectionError uncaught → process exit 1).
            try:
                self._make_loader()
                logger.info("Rotated to [%s]", self.current_label)
            except ConnectionError as exc:
                logger.warning(
                    "V0.18 _make_loader exhausted all proxies on rotated slot %d: %s — "
                    "advancing again",
                    self._current_slot + 1, exc,
                )
                self._calls_on_current = self.QUOTA_PER_SLOT
                return self.get_loader()  # recursive: try next slot
        else:
            # All tokens exhausted — V0.15 smart sleep until earliest quota reset.
            logger.warning(
                "All %d tokens exhausted. Calculating smart sleep...",
                len(self._tokens))
            self._smart_sleep_until_quota_reset()
            self._current_slot = 0
            self._slots = [(self._tokens[0], None)]  # reset, re-fetch proxies later
            self._slot_first_call_at = {}  # reset timestamps for fresh quota window
            self._backup_proxies = []
            # V0.18: wrap post-sleep _make_loader too. If reset-Token1 fails
            # (e.g. workstation IP suddenly throttled), advance instead of crash.
            try:
                self._make_loader()
                logger.info("Quota reset. Restarting from Token1 + Direct")
            except ConnectionError as exc:
                logger.warning(
                    "V0.18 _make_loader post-sleep failed on Token1+Direct: %s — "
                    "trying next slot",
                    exc,
                )
                self._calls_on_current = self.QUOTA_PER_SLOT
                return self.get_loader()
        return self._loader

    def record_call(self):
        """Record one API call.

        V0.15: track timestamp of first call per slot for smart_sleep wait calc.
        """
        if self._current_slot not in self._slot_first_call_at:
            self._slot_first_call_at[self._current_slot] = datetime.now()
        self._calls_on_current += 1

    def record_quota_error(self):
        """Force-rotate to next token+proxy on quota error."""
        logger.warning("[%s] quota error at %d calls, force-rotating",
                        self.current_label, self._calls_on_current)
        self._calls_on_current = self.QUOTA_PER_SLOT  # trigger rotation
        self.get_loader()

    def start_with_proxy(self) -> bool:
        """S6.1 Path B (R25-mid 獨立 audit, 2026-05-05): patch Slot 0 to
        start with fresh Proxifly SOCKS5 proxy instead of Direct.

        Per user 提醒 + memory `FinMind Tokens & Quota`: 3 tokens all bound
        to same IP (<isp_ip>). Default starting Direct means Token1
        runs 580 calls on workstation IP before rotation. For large
        datasets (e.g. quarterly_financial_full / balance_sheet ~2492
        symbols × 2), this risks IP-based throttling. Calling this method
        AFTER `__init__` swaps Slot 0 from (Token1, None) to (Token1,
        fresh_proxy).

        Returns True if proxy fetch succeeded; False on fallback to Direct.
        """
        proxy = self._fetch_working_proxy()
        if proxy:
            self._slots[0] = (self._tokens[0], proxy)
            # Invalidate any pre-built loader to force rebuild with proxy
            self._loader = None
            self._current_proxy = proxy
            logger.info("Slot 0 patched: Token1 + %s (V0.14 P-B starting-with-proxy)", proxy)
            return True
        logger.warning("start_with_proxy(): failed to fetch fresh proxy — "
                        "Slot 0 remains Token1 + Direct fallback")
        return False


def _progress_file(phase: int) -> pathlib.Path:
    """Each phase has its own progress file to avoid concurrent write conflicts."""
    return PROGRESS_DIR / f"cache_rebuild_p{phase}.json"


def _load_phase_progress(phase: int) -> list:
    p = _progress_file(phase)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return []


def _save_phase_progress(phase: int, done_list: list):
    with open(_progress_file(phase), "w") as f:
        json.dump(done_list, f)


def _is_phase_done(phase: int) -> bool:
    flag = PROGRESS_DIR / f"cache_rebuild_p{phase}_done.flag"
    return flag.exists()


def _mark_phase_done(phase: int):
    flag = PROGRESS_DIR / f"cache_rebuild_p{phase}_done.flag"
    flag.write_text(datetime.now().isoformat())


# =========================================================================
# Phase 1: stock_info from TWSE + TPEX OpenAPI
# =========================================================================

def phase1_stock_info():
    """Build stock_info from TWSE + TPEX company profile APIs."""
    if _is_phase_done(1):
        logger.info("Phase 1 already done, skipping")
        return

    logger.info("=== Phase 1: stock_info + dividends ===")

    from src.data.twse_scraper import (
        fetch_twse_issued_capital,
        fetch_twse_dividends,
        _parse_company_profile,
    )
    import requests
    import urllib3
    urllib3.disable_warnings()

    records = []

    # --- TWSE (上市) ---
    try:
        resp = requests.get(
            "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
            timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=False,
        )
        if resp.status_code == 200:
            data = resp.json()
            sample = data[0]
            keys = list(sample.keys())
            # Find keys by substring
            code_key = name_key = abbr_key = ind_key = date_key = None
            for k in keys:
                if "公司代號" in k: code_key = k
                elif "公司簡稱" in k: abbr_key = k
                elif "公司名稱" in k and not name_key: name_key = k
                elif "產業" in k or "營業" in k: ind_key = k
                elif "上市日期" in k or "上櫃日期" in k: date_key = k

            for row in data:
                sid = str(row.get(code_key, "")).strip()
                if not sid:
                    continue
                name = str(row.get(abbr_key, row.get(name_key, sid))).strip()
                # Industry code → need mapping (use code for now)
                industry = str(row.get(ind_key, "")).strip()
                date_str = str(row.get(date_key, "")).strip()
                # Parse date: YYYYMMDD → YYYY-MM-DD
                if len(date_str) == 8:
                    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                else:
                    date_fmt = ""
                records.append({
                    "stock_id": sid,
                    "stock_name": name,
                    "industry_category": industry,
                    "type": "twse",
                    "date": date_fmt,
                })
            logger.info("TWSE stock_info: %d companies", len([r for r in records if r["type"] == "twse"]))
    except Exception as exc:
        logger.error("TWSE stock_info failed: %s", exc)

    # --- TPEX (上櫃) ---
    try:
        resp = requests.get(
            "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
            timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=False,
        )
        if resp.status_code == 200:
            data = resp.json()
            sample = data[0]
            code_key = name_key = ind_key = date_key = None
            for k in sample.keys():
                kl = k.lower()
                if "securitiescompanycod" in kl: code_key = k
                elif "companyname" in kl: name_key = k
                elif "securitiesindustryco" in kl: ind_key = k
                elif "dateoflisting" in kl: date_key = k

            for row in data:
                sid = str(row.get(code_key, "")).strip()
                if not sid:
                    continue
                name = str(row.get(name_key, sid)).strip()
                industry = str(row.get(ind_key, "")).strip()
                date_str = str(row.get(date_key, "")).strip()
                if len(date_str) == 8:
                    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                else:
                    date_fmt = ""
                records.append({
                    "stock_id": sid,
                    "stock_name": name,
                    "industry_category": industry,
                    "type": "tpex",
                    "date": date_fmt,
                })
            logger.info("TPEX stock_info: %d companies",
                        len([r for r in records if r["type"] == "tpex"]))
    except Exception as exc:
        logger.error("TPEX stock_info failed: %s", exc)

    if not records:
        logger.error("No stock_info records — cannot continue")
        return

    # Save stock_info
    si_dir = NEW_CACHE / "stock_info"
    si_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(records)
    df.to_pickle(si_dir / "_global.pkl")
    df.to_csv(si_dir / "stock_info_snapshot.csv", index=False)
    (si_dir / "_global.meta").write_text(datetime.now().strftime("%Y-%m-%d"))
    logger.info("stock_info saved: %d records", len(df))

    # --- Dividends (TWSE) ---
    div_dir = NEW_CACHE / "dividends"
    div_dir.mkdir(parents=True, exist_ok=True)
    try:
        now = datetime.now()
        divs = fetch_twse_dividends(START_YEAR, now.year)
        if divs:
            with open(div_dir / "_global.pkl", "wb") as f:
                pickle.dump(divs, f)
            (div_dir / "_global.meta").write_text(now.strftime("%Y-%m-%d"))
            logger.info("Dividends saved: %d records", len(divs))
    except Exception as exc:
        logger.error("Dividends failed: %s", exc)

    _mark_phase_done(1)
    logger.info("Phase 1 complete")


# =========================================================================
# Phase 2: 上市股 OHLCV from TWSE STOCK_DAY
# =========================================================================

def phase2_twse_ohlcv():
    """Fetch OHLCV for all TWSE-listed stocks using STOCK_DAY.

    Fixes applied 2026-04-09 (9 bugs):
    - Ghost stocks: only mark done when pkl saved
    - IPO-aware: skip months before listing date
    - 307 auto-proxy: switch to SOCKS5 proxy on rate limit
    - Validate before save: check row count + constant data
    - Progress saved per stock (not every 10)
    - end_month refreshed per stock
    - consecutive_empty raised to 24
    """
    logger.info("=== Phase 2: 上市股 OHLCV (TWSE) ===")

    import requests as _req
    import urllib3
    urllib3.disable_warnings()
    from src.data.twse_scraper import _TWSE_STOCK_DAY_URL, _parse_roc_date

    si = pd.read_csv(NEW_CACHE / "stock_info" / "stock_info_snapshot.csv")
    si["stock_id"] = si["stock_id"].astype(str).str.strip()
    twse_stocks = sorted(set(
        si[si["type"] == "twse"]["stock_id"]
        [si["stock_id"].str.fullmatch(r"\d{4}")]
    ))

    # Include key ETFs (not in stock_info)
    REQUIRED_ETFS = ["0050", "0051", "0052", "0053", "0055", "0056"]
    for etf in REQUIRED_ETFS:
        if etf not in twse_stocks:
            twse_stocks.append(etf)
    twse_stocks = sorted(twse_stocks)

    # Fix 8: IPO dates from stock_info
    ipo_dates: dict[str, tuple[int, int]] = {}
    for _, row in si.iterrows():
        sid = str(row.get("stock_id", "")).strip()
        date_str = str(row.get("date", "")).strip()
        if sid and len(date_str) >= 7:
            try:
                dt = pd.to_datetime(date_str)
                ipo_dates[sid] = (dt.year, dt.month)
            except Exception:
                pass

    done_set = set(_load_phase_progress(2))
    todo = [s for s in twse_stocks if s not in done_set]
    logger.info("TWSE stocks: %d total, %d done, %d todo", len(twse_stocks), len(done_set), len(todo))

    ohlcv_dir = NEW_CACHE / "ohlcv"
    ohlcv_dir.mkdir(parents=True, exist_ok=True)

    pool = TwseProxyPool(max_per_ip=15)
    global_307_count = 0  # Fix 7: track consecutive 307s

    for i, sym in enumerate(todo, 1):
        if i % 50 == 0 or i <= 3:
            logger.info("[Phase2 %d/%d] %s [%s] ...", i, len(todo), sym, pool.label)

        # Fix 5: refresh end date per stock
        now = datetime.now()
        end_year, end_month = now.year, now.month

        # Fix 8: start from IPO date if known
        ipo = ipo_dates.get(sym)
        if ipo and ipo > (START_YEAR, START_MONTH):
            start_y, start_m = ipo
        else:
            start_y, start_m = START_YEAR, START_MONTH

        all_records = []
        year, month = start_y, start_m
        consecutive_empty = 0

        while (year, month) <= (end_year, end_month):
            # Fix 2+3: use proxy pool, handle 307
            pool.rotate_if_needed()
            date_str = f"{year}{month:02d}01"

            records = []
            was_307 = False
            try:
                resp = _req.get(
                    _TWSE_STOCK_DAY_URL,
                    params={"date": date_str, "stockNo": sym, "response": "json"},
                    timeout=15, headers={"User-Agent": "Mozilla/5.0"},
                    verify=False, proxies=pool.proxies_dict,
                )
                pool.record_call()

                if resp.status_code in (307, 403):
                    was_307 = True
                    global_307_count += 1
                    # Fix 7: auto-activate proxy after repeated 307
                    if global_307_count >= 3:
                        pool.activate_on_307()
                        global_307_count = 0
                        # Retry with proxy
                        pool.rotate_if_needed()
                        _time.sleep(2)
                        resp = _req.get(
                            _TWSE_STOCK_DAY_URL,
                            params={"date": date_str, "stockNo": sym, "response": "json"},
                            timeout=15, headers={"User-Agent": "Mozilla/5.0"},
                            verify=False, proxies=pool.proxies_dict,
                        )
                        pool.record_call()
                        if resp.status_code in (307, 403):
                            was_307 = True

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("stat") == "OK":
                        for row in data.get("data") or []:
                            if len(row) < 7:
                                continue
                            dp = _parse_roc_date(str(row[0]))
                            if not dp:
                                continue
                            try:
                                records.append({
                                    "date": dp,
                                    "open": float(str(row[3]).replace(",", "")),
                                    "high": float(str(row[4]).replace(",", "")),
                                    "low": float(str(row[5]).replace(",", "")),
                                    "close": float(str(row[6]).replace(",", "")),
                                    "volume": int(str(row[1]).replace(",", "")),
                                })
                            except (ValueError, TypeError):
                                continue
                    global_307_count = 0  # successful request resets counter
            except Exception:
                pass

            if records:
                all_records.extend(records)
                consecutive_empty = 0
            else:
                if not was_307:
                    consecutive_empty += 1
                # else: 307 doesn't count toward consecutive_empty (Fix 2)
                # Fix 8: raised from 12 to 24
                if consecutive_empty >= 24:
                    break

            # Next month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1

            _time.sleep(TWSE_INTERVAL)

        # Fix 1 + 6 + 9: validate before save, only mark done if pkl saved
        if all_records:
            df = pd.DataFrame(all_records)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            df.index = df.index.tz_localize("UTC")
            df = df[["open", "high", "low", "close", "volume"]].dropna()

            # Fix 6+9: validation
            if len(df) < 20:
                logger.warning("  %s: only %d rows after cleanup, skipping", sym, len(df))
            elif len(df["close"].unique()) <= 5 and len(df) > 100:
                logger.warning("  %s: constant data detected, skipping", sym)
            else:
                # Atomic write
                tmp = ohlcv_dir / f"{sym}.tmp"
                df.to_pickle(tmp)
                tmp.replace(ohlcv_dir / f"{sym}.pkl")
                done_set.add(sym)
                # Fix 4: save progress every stock
                _save_phase_progress(2, sorted(done_set))
        else:
            logger.warning("  %s: no data, NOT marking done", sym)

    _save_phase_progress(2, sorted(done_set))
    logger.info("Phase 2 complete: %d TWSE stocks processed", len(todo))


# =========================================================================
# Phase 3: 上櫃股 OHLCV from FinMind
# =========================================================================

def phase3_tpex_ohlcv():
    """Fetch OHLCV for TPEX stocks using FinMind with multi-token rotation."""
    logger.info("=== Phase 3: 上櫃股 OHLCV (FinMind) ===")

    si = pd.read_csv(NEW_CACHE / "stock_info" / "stock_info_snapshot.csv")
    si["stock_id"] = si["stock_id"].astype(str).str.strip()
    tpex_stocks = sorted(set(
        si[si["type"] == "tpex"]["stock_id"]
        [si["stock_id"].str.fullmatch(r"\d{4}")]
    ))

    done_set = set(_load_phase_progress(3))
    todo = [s for s in tpex_stocks if s not in done_set]
    logger.info("TPEX stocks: %d total, %d done, %d todo", len(tpex_stocks), len(done_set), len(todo))

    rotator = TokenRotator()
    ohlcv_dir = NEW_CACHE / "ohlcv"
    ohlcv_dir.mkdir(parents=True, exist_ok=True)

    start_date = f"{START_YEAR}-{START_MONTH:02d}-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    failed_count = 0
    data_not_found = []

    for i, sym in enumerate(todo, 1):
        if i % 50 == 0 or i <= 3:
            logger.info("[Phase3 %d/%d] %s [%s] ...",
                        i, len(todo), sym, rotator.current_label)

        try:
            _time.sleep(0.5)
            loader = rotator.get_loader()
            raw = loader.taiwan_stock_daily(
                stock_id=sym, start_date=start_date, end_date=end_date,
            )
            rotator.record_call()
            if raw is not None and not raw.empty:
                df = raw.rename(columns={
                    "date": "timestamp", "max": "high", "min": "low",
                    "Trading_Volume": "volume",
                })
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df = df.set_index("timestamp").sort_index()
                for col in ("open", "high", "low", "close", "volume"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df[["open", "high", "low", "close", "volume"]].dropna()
                if not df.empty:
                    df.to_pickle(ohlcv_dir / f"{sym}.pkl")
                    failed_count = 0
                else:
                    data_not_found.append(sym)
                    failed_count += 1
            else:
                data_not_found.append(sym)
                failed_count += 1
        except KeyError as exc:
            if str(exc) == "'data'":
                # Likely quota exceeded — FinMind returns no 'data' key
                rotator.record_quota_error()
                failed_count = 0  # reset after rotation, retry later
            else:
                failed_count += 1
        except Exception as exc:
            if i <= 5:
                logger.warning("  Error: %s", exc)
            failed_count += 1

        done_set.add(sym)
        if i % 50 == 0:
            _save_phase_progress(3, sorted(done_set))

        if failed_count >= 20:
            logger.warning("20 consecutive non-data failures — stopping")
            _save_phase_progress(3, sorted(done_set))
            break

    _save_phase_progress(3, sorted(done_set))
    if data_not_found:
        failed_path = PROJECT_ROOT / "data" / "cache_rebuild_failed_tpex.json"
        with open(failed_path, "w") as f:
            json.dump(data_not_found, f)
        logger.info("TPEX data-not-found: %d stocks (saved to %s)", len(data_not_found), failed_path)
    logger.info("Phase 3 complete")


# =========================================================================
# Phase 4: Revenue from FinMind
# =========================================================================

def phase4_revenue():
    """Fetch Revenue for all stocks using FinMind with multi-token rotation."""
    logger.info("=== Phase 4: Revenue (FinMind) ===")

    from src.data.finmind import FinMindSource

    si = pd.read_csv(NEW_CACHE / "stock_info" / "stock_info_snapshot.csv")
    si["stock_id"] = si["stock_id"].astype(str).str.strip()
    all_stocks = sorted(set(
        si[si["stock_id"].str.fullmatch(r"\d{4}")]["stock_id"]
    ))

    done_set = set(_load_phase_progress(4))
    todo = [s for s in all_stocks if s not in done_set]
    logger.info("Revenue: %d total, %d done, %d todo", len(all_stocks), len(done_set), len(todo))

    rotator = TokenRotator()

    def _make_source() -> FinMindSource:
        """Create FinMindSource and patch its internal session with current proxy."""
        token = rotator._slots[rotator._current_slot][0]
        src = FinMindSource(token=token, backtest_mode=False,
                            cache_dir=str(NEW_CACHE))
        if rotator._current_proxy:
            proxies = {"http": rotator._current_proxy, "https": rotator._current_proxy}
            src.loader._FinMindApi__session.proxies.update(proxies)
        return src

    source = _make_source()
    _last_proxy = [rotator._current_proxy]
    failed_count = 0

    for i, sym in enumerate(todo, 1):
        if i % 100 == 0 or i <= 3:
            logger.info("[Phase4 %d/%d] %s [%s] ...",
                        i, len(todo), sym, rotator.current_label)

        is_good = False
        try:
            df = source.fetch_month_revenue(sym, months=60)
            rotator.record_call()
            if df is not None and not df.empty and len(df) >= 12:
                is_good = True
                failed_count = 0
            elif df is not None and not df.empty:
                # Got data but < 12 months — likely TWSE fallback (FinMind quota hit)
                failed_count += 1
            else:
                failed_count += 1
        except KeyError:
            rotator.record_quota_error()
            source = _make_source()
            failed_count = 0
        except Exception:
            failed_count += 1

        # Detect quota exhaustion: 10 consecutive short results = FinMind blocked
        if failed_count == 10:
            logger.warning("10 consecutive short results — likely quota exhaustion, rotating proxy")
            rotator.record_quota_error()
            source = _make_source()
            _last_proxy[0] = rotator._current_proxy
            failed_count = 0

        # Rebuild source if proxy changed
        if rotator._current_proxy != _last_proxy[0]:
            source = _make_source()
            _last_proxy[0] = rotator._current_proxy

        if is_good:
            done_set.add(sym)
        if i % 50 == 0:
            _save_phase_progress(4, sorted(done_set))

        if failed_count >= 20:
            logger.warning("20 consecutive failures — stopping")
            _save_phase_progress(4, sorted(done_set))
            break

    _save_phase_progress(4, sorted(done_set))
    logger.info("Phase 4 complete")


# =========================================================================
# Phase 5: market_value from TWSE
# =========================================================================

def phase5_market_value():
    """Compute market_value from TWSE shares × OHLCV cache."""
    if _is_phase_done(5):
        logger.info("Phase 5 already done, skipping")
        return

    logger.info("=== Phase 5: market_value ===")

    from src.data.twse_scraper import fetch_twse_issued_capital

    shares = fetch_twse_issued_capital()
    if not shares:
        logger.error("Cannot fetch issued capital")
        return

    ohlcv_dir = NEW_CACHE / "ohlcv"
    records = []
    for p in ohlcv_dir.glob("*.pkl"):
        sym = p.stem
        if sym not in shares:
            continue
        try:
            df = pd.read_pickle(p)
            if df.empty or "close" not in df.columns:
                continue
            close = df[["close"]].copy()
            close.index = pd.to_datetime(close.index, utc=True)
            monthly = close.resample("ME").last().dropna()
            for ts, row in monthly.iterrows():
                records.append({
                    "stock_id": sym,
                    "date": ts.tz_localize(None),
                    "market_value": float(row["close"]) * shares[sym],
                })
        except Exception as exc:
            logger.warning("  %s: failed to compute market_value: %s", sym, exc)
            continue

    if not records:
        logger.error("No market_value records produced — OHLCV cache may be empty or unreadable. Phase 5 NOT marked done.")
        return

    mv_dir = NEW_CACHE / "market_value"
    mv_dir.mkdir(parents=True, exist_ok=True)
    result = pd.DataFrame(records)
    result["date"] = pd.to_datetime(result["date"])
    result["market_value"] = pd.to_numeric(result["market_value"], errors="coerce")
    result = result.sort_values(["stock_id", "date"]).reset_index(drop=True)
    result.to_pickle(mv_dir / "_global.pkl")
    (mv_dir / "_global.meta").write_text(datetime.now().strftime("%Y-%m-%d"))
    logger.info("market_value saved: %d stocks, %d records", result["stock_id"].nunique(), len(result))

    _mark_phase_done(5)
    logger.info("Phase 5 complete")


# =========================================================================
# Main
# =========================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cache Rebuild — 全新重建")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--phase", type=int, help="只跑指定 phase (1-5)")
    args = parser.parse_args()

    if args.status:
        print(f"Phase 1 (stock_info):   {'done' if _is_phase_done(1) else 'pending'}")
        print(f"Phase 2 (TWSE OHLCV):   {len(_load_phase_progress(2))} stocks done")
        print(f"Phase 3 (TPEX OHLCV):   {len(_load_phase_progress(3))} stocks done")
        print(f"Phase 4 (Revenue):      {len(_load_phase_progress(4))} stocks done")
        print(f"Phase 5 (market_value): {'done' if _is_phase_done(5) else 'pending'}")
        # Token info
        tokens = [os.environ.get(k, "") for k in
                  ["FINMIND_TOKEN", "FINMIND_TOKEN2", "FINMIND_TOKEN3"]
                  if os.environ.get(k, "") and os.environ.get(k, "") != "your_bot_token_here"]
        print(f"FinMind tokens:         {len(tokens)} available")
        print(f"SOCKS5 proxy:           {os.environ.get('SOCKS5_PROXY', 'none')}")
        return

    NEW_CACHE.mkdir(parents=True, exist_ok=True)

    if args.phase:
        phases = [args.phase]
    else:
        phases = [1, 2, 3, 4, 5]

    for p in phases:
        if p == 1:
            phase1_stock_info()
        elif p == 2:
            phase2_twse_ohlcv()
        elif p == 3:
            phase3_tpex_ohlcv()
        elif p == 4:
            phase4_revenue()
        elif p == 5:
            phase5_market_value()

    logger.info("=" * 50)
    logger.info("Cache rebuild finished. New cache at: %s", NEW_CACHE)
    logger.info("To switch: mv data/cache data/cache_old && mv data/cache_new data/cache")


if __name__ == "__main__":
    main()
