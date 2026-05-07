"""Dashboard 共用資料讀取函式。"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"
BACKTESTS_DIR = REPORTS_DIR / "backtests"
PAPER_TRADING_DIR = REPORTS_DIR / "paper_trading"
WALK_FORWARD_DIR = REPORTS_DIR / "walk_forward"
OHLCV_DIR = PROJECT_ROOT / "data" / "cache" / "ohlcv"


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=60)
def load_latest_paper_trade() -> dict | None:
    """載入最新一期的 paper trading 紀錄。"""
    files = sorted(PAPER_TRADING_DIR.glob("2???-??.json"), reverse=True)
    if not files:
        return None
    return load_json(files[0])


@st.cache_data(ttl=60)
def load_paper_trading_history() -> list[dict]:
    history_path = PAPER_TRADING_DIR / "history.json"
    data = load_json(history_path)
    return data if isinstance(data, list) else []


@st.cache_data(ttl=300)
def load_walk_forward_summary() -> dict | None:
    return load_json(WALK_FORWARD_DIR / "summary.json")


@st.cache_data(ttl=300)
def load_backtest_metrics(subdir: str, start: str, end: str) -> dict | None:
    base = BACKTESTS_DIR / subdir if subdir else BACKTESTS_DIR
    path = base / f"backtest_{start}_{end}_metrics.json"
    return load_json(path)


@st.cache_data(ttl=300)
def load_backtest_snapshots(subdir: str, start: str, end: str) -> list[dict]:
    base = BACKTESTS_DIR / subdir if subdir else BACKTESTS_DIR
    path = base / f"backtest_{start}_{end}_snapshots.json"
    data = load_json(path)
    return data if isinstance(data, list) else []


@st.cache_data(ttl=300)
def load_daily_returns(subdir: str, start: str, end: str) -> dict | None:
    """載入日頻報酬序列。回傳 {"portfolio": {date: ret}, "benchmark": {date: ret}}。"""
    base = BACKTESTS_DIR / subdir if subdir else BACKTESTS_DIR
    path = base / f"backtest_{start}_{end}_daily_returns.json"
    return load_json(path)


@st.cache_data(ttl=300)
def list_backtest_experiments() -> list[dict]:
    """掃描 reports/backtests/ 所有回測實驗，回傳可供顯示的清單。"""
    if not BACKTESTS_DIR.exists():
        return []
    results = []
    for metrics_file in sorted(BACKTESTS_DIR.rglob("backtest_*_metrics.json")):
        parts = metrics_file.stem.replace("backtest_", "").replace("_metrics", "")
        dates = parts.split("_")
        if len(dates) != 2:
            continue
        start, end = dates
        subdir = str(metrics_file.parent.relative_to(BACKTESTS_DIR))
        if subdir == ".":
            subdir = ""
        # 自動產生友善標籤
        s_yr, s_mo = start[:4], start[4:6]
        e_yr, e_mo = end[:4], end[4:6]
        years = int(e_yr) - int(s_yr)
        duration = f"{years}年" if years >= 1 else f"{int(e_mo) - int(s_mo) + 1}個月"
        subdir_note = f"[{subdir}] " if subdir else ""
        label = f"{subdir_note}{s_yr}-{s_mo} ～ {e_yr}-{e_mo}（{duration}）"
        results.append({
            "label": label,
            "subdir": subdir,
            "start": start,
            "end": end,
            "has_daily": (metrics_file.parent / f"backtest_{start}_{end}_daily_returns.json").exists(),
        })
    return results


@st.cache_data(ttl=300)
def load_latest_close(symbol: str) -> float | None:
    """從 OHLCV cache 讀取最新收盤價。"""
    pkl_path = OHLCV_DIR / f"{symbol}.pkl"
    if not pkl_path.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_pickle(pkl_path)
        if df.empty or "close" not in df.columns:
            return None
        close = df["close"].iloc[-1]
        return float(close) if close > 0 else None
    except Exception:
        return None
