"""Point-in-time (PIT) data helpers — single source of truth across 4 data paths.

2026-05-11 R30 architecture cleanup（Codex R29 抓的 4-path PIT 違規 root cause）：
    Stock-Trading 專案有 4 條獨立 data loading path：
      1. IC pipeline       (`scripts/run_factor_ic.py` + `scripts/_factor_ic_helpers.py`)
      2. Portfolio         (`src/portfolio/tw_stock.py`)
      3. Phase D v7 sweep  (`scripts/d_cell_sweep_v7_real.py`)
      4. Cache fetch       (`src/data/finmind.py`)

    R26 抓 path 1 / R27 抓 path 2 / R28-2 修 path 2 局部 / R29 抓 path 3 + path 2 殘留。
    每輪 audit 抓另一條 path 的同款 PIT 違規（每 path 自寫 `_load_<X>` 走 latest）。

    本模組是 single source of truth。4 條 path 全部 import from here。
    舊 helper functions（`scripts/_factor_ic_helpers.py::_load_market_value_panel`
    等）改為 re-export shim（backward compat）。

Functions exported:
    _load_market_value_panel      — load full PIT-able mv panel
    _market_value_asof            — as-of mv lookup at target_date
    _load_issued_capital_panel    — load issued_shares panel (with cache fallback)
    _issued_capital_asof          — as-of issued_shares lookup at target_date

Static-snapshot caveat（Codex R28-1 / R29 finding 4）：
    `data/cache/issued_capital/_global.pkl` 缺 date column 時，fallback 用
    `pd.Timestamp("1970-01-01")` 讓所有歷史 query 都 hit latest snapshot。
    這是 form-correct 但 substance-equivalent（仍是 latest_shares 對所有 date）。
    完整 PIT 需另寫 TWSE OpenAPI scraper 抓歷史 issued_shares (P1 backlog 4-8 hr).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# market_value
# ---------------------------------------------------------------------------
def _load_market_value_panel(cache_dir: Path) -> pd.DataFrame:
    """PIT-able market_value panel.

    Returns DataFrame with columns ``stock_id``, ``date``, ``market_value``.
    Use with :func:`_market_value_asof` for as-of lookup at each rebalance
    date (replaces legacy ``_load_market_value`` which returned latest only).

    Returns empty DataFrame if cache missing / malformed (caller should
    detect via ``.empty``).
    """
    path = cache_dir / "market_value" / "_global.pkl"
    if not path.exists():
        return pd.DataFrame(columns=["stock_id", "date", "market_value"])
    df = pd.read_pickle(path)
    if df is None or df.empty:
        return pd.DataFrame(columns=["stock_id", "date", "market_value"])
    required = {"stock_id", "date", "market_value"}
    if not required.issubset(df.columns):
        return pd.DataFrame(columns=["stock_id", "date", "market_value"])
    df = df[["stock_id", "date", "market_value"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["stock_id"] = df["stock_id"].astype(str)
    df["market_value"] = df["market_value"].astype(float)
    return df.sort_values(["stock_id", "date"]).reset_index(drop=True)


def _market_value_asof(panel: pd.DataFrame, target_date: pd.Timestamp) -> dict[str, float]:
    """As-of market_value lookup (PIT-correct).

    Returns ``{stock_id: market_value}`` taking each symbol's last record
    where ``date <= target_date``. Symbols with no record at or before the
    target are silently dropped — caller's downstream NaN guard handles those.
    """
    if panel is None or panel.empty:
        return {}
    target_ts = pd.Timestamp(target_date)
    if target_ts.tz is not None:
        target_ts = target_ts.tz_convert(None)
    sub = panel[panel["date"] <= target_ts]
    if sub.empty:
        return {}
    latest = sub.drop_duplicates("stock_id", keep="last")
    return dict(zip(latest["stock_id"], latest["market_value"]))


# ---------------------------------------------------------------------------
# issued_capital
# ---------------------------------------------------------------------------
def _load_issued_capital_panel(cache_dir: Path) -> pd.DataFrame:
    """PIT-able issued_capital panel.

    Returns DataFrame with columns ``stock_id``, ``date``, ``issued_shares``.
    Use with :func:`_issued_capital_asof` for as-of lookup at each rebalance
    date (replaces legacy ``_load_issued_capital`` which returned latest only).

    Falls back to ``issued_capital/_global.pkl`` if ``market_value/_global.pkl``
    lacks the ``issued_shares`` column. Returns empty DataFrame if neither
    cache populated (caller should detect via ``.empty``).

    ⚠️ Static-snapshot caveat (Codex R28-1 / R29-4):
        當 ``issued_capital/_global.pkl`` 缺 ``date`` column 時，fallback 用
        ``pd.Timestamp("1970-01-01")`` 讓所有歷史 query 都 hit。這只是
        form-correct（cache 結構帶 date column），實質仍是 latest snapshot。
        完整 PIT 需新 TWSE OpenAPI scraper 抓歷史 issued_shares (P1 backlog).
    """
    path = cache_dir / "market_value" / "_global.pkl"
    if path.exists():
        df = pd.read_pickle(path)
        if (
            df is not None
            and not df.empty
            and "stock_id" in df.columns
            and "date" in df.columns
            and "issued_shares" in df.columns
        ):
            df = df[["stock_id", "date", "issued_shares"]].copy()
            df["date"] = pd.to_datetime(df["date"])
            df["stock_id"] = df["stock_id"].astype(str)
            df["issued_shares"] = df["issued_shares"].astype(float)
            return df.sort_values(["stock_id", "date"]).reset_index(drop=True)

    capital_path = cache_dir / "issued_capital" / "_global.pkl"
    if capital_path.exists():
        cap = pd.read_pickle(capital_path)
        if cap is not None and not cap.empty and "stock_id" in cap.columns:
            col = "issued_shares" if "issued_shares" in cap.columns else None
            if col is None:
                for candidate in ("shares_issued", "capital_shares", "shares"):
                    if candidate in cap.columns:
                        col = candidate
                        break
            if col is None:
                return pd.DataFrame(columns=["stock_id", "date", "issued_shares"])
            if "date" not in cap.columns:
                # 2026-05-10 R27 P0-2 fallback：cache lacks date column → treat
                # as static snapshot valid for ALL dates (Timestamp.min equivalent).
                # Form-correct but substance-equivalent to latest (Codex R28-1).
                warnings.warn(
                    f"issued_capital cache {capital_path} lacks 'date' column. "
                    "Treating as static snapshot (PIT approximation). To get "
                    "true PIT, run cache_fill_new_factors.py with date-bearing "
                    "issued_shares history.",
                    UserWarning,
                    stacklevel=3,
                )
                date = pd.Timestamp("1970-01-01")
                out = cap[["stock_id", col]].rename(columns={col: "issued_shares"}).copy()
                out["date"] = date
                out = out[["stock_id", "date", "issued_shares"]]
                out["date"] = pd.to_datetime(out["date"])
                out["stock_id"] = out["stock_id"].astype(str)
                out["issued_shares"] = out["issued_shares"].astype(float)
                return out.sort_values(["stock_id", "date"]).reset_index(drop=True)
            cap = cap[["stock_id", "date", col]].rename(columns={col: "issued_shares"}).copy()
            cap["date"] = pd.to_datetime(cap["date"])
            cap["stock_id"] = cap["stock_id"].astype(str)
            cap["issued_shares"] = cap["issued_shares"].astype(float)
            return cap.sort_values(["stock_id", "date"]).reset_index(drop=True)

    return pd.DataFrame(columns=["stock_id", "date", "issued_shares"])


def _issued_capital_asof(panel: pd.DataFrame, target_date: pd.Timestamp) -> dict[str, float]:
    """As-of issued_shares lookup (PIT-correct).

    Returns ``{stock_id: issued_shares}`` taking each symbol's last record
    where ``date <= target_date``. Symbols with no record at or before the
    target are silently dropped — caller's downstream NaN guard handles those.
    """
    if panel is None or panel.empty:
        return {}
    target_ts = pd.Timestamp(target_date)
    if target_ts.tz is not None:
        target_ts = target_ts.tz_convert(None)
    sub = panel[panel["date"] <= target_ts]
    if sub.empty:
        return {}
    latest = sub.drop_duplicates("stock_id", keep="last")
    return dict(zip(latest["stock_id"], latest["issued_shares"]))
