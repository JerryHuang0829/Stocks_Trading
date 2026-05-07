"""
Smart Beta 對照組 NAV 追蹤腳本（支援 /smart-beta-paper skill）
2026-04-17 新增。

用途：
  追蹤三個被動對照組的每週 NAV：
  - 主 baseline：100% 0050（贏大盤目標）
  - 次要對照：100% 0056（高息參考）
  - 參考對照：0050 60% + 0056 40%（退休金型）

  若 factor portfolio 的 paper trading history 存在，計算 rolling Sharpe 對比。

執行：
  MSYS_NO_PATHCONV=1 docker compose run --rm --entrypoint python portfolio-bot \\
    scripts/smart_beta_tracker.py
  # 或
  conda run -n quant python scripts/smart_beta_tracker.py

輸出：
  - data/cache/smart_beta_nav.csv（每週追加一列）
  - reports/smart_beta/update_<date>.md（本週更新報告）
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    # scripts/smart_beta_tracker.py -> 專案根目錄
    return Path(__file__).resolve().parent.parent


def _load_nav_history(csv_path: Path) -> pd.DataFrame:
    if csv_path.exists():
        return pd.read_csv(csv_path, parse_dates=["date"])
    # 初始化：空 DataFrame，首次寫入時以當天價格為 NAV=100 基準
    return pd.DataFrame(columns=["date", "price_0050", "price_0056",
                                 "nav_0050", "nav_0056", "nav_60_40"])


def _fetch_latest_close(symbol: str, source: str = "finmind") -> float | None:
    """
    取得 symbol 最新 close。預設 finmind，fallback yfinance。
    Phase A1 期間用既有 src/data/finmind.py 的 fetch_ohlcv。
    """
    try:
        from src.data.finmind import fetch_ohlcv
    except Exception as exc:  # pragma: no cover
        logger.warning("Cannot import finmind fetcher: %s", exc)
        return None
    try:
        df = fetch_ohlcv(symbol=symbol, days=5)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])
    except Exception as exc:  # pragma: no cover
        logger.warning("fetch_ohlcv failed for %s: %s", symbol, exc)
        return None


def _append_today(history: pd.DataFrame, today: pd.Timestamp,
                  price_0050: float, price_0056: float) -> pd.DataFrame:
    if history.empty:
        nav_0050 = 100.0
        nav_0056 = 100.0
        base_0050, base_0056 = price_0050, price_0056
    else:
        first = history.iloc[0]
        base_0050, base_0056 = float(first["price_0050"]), float(first["price_0056"])
        nav_0050 = 100.0 * price_0050 / base_0050
        nav_0056 = 100.0 * price_0056 / base_0056
    nav_60_40 = 0.6 * nav_0050 + 0.4 * nav_0056
    row = pd.DataFrame([{
        "date": today,
        "price_0050": price_0050,
        "price_0056": price_0056,
        "nav_0050": round(nav_0050, 4),
        "nav_0056": round(nav_0056, 4),
        "nav_60_40": round(nav_60_40, 4),
    }])
    return pd.concat([history, row], ignore_index=True)


def _rolling_sharpe(nav: pd.Series, window_days: int = 63) -> float | None:
    """window_days=63 約 3 個月；簡化計算，年化 √252。"""
    if len(nav) < window_days + 1:
        return None
    rets = nav.pct_change().dropna().tail(window_days)
    if rets.std(ddof=1) == 0 or rets.empty:
        return None
    return float((rets.mean() / rets.std(ddof=1)) * (252 ** 0.5))


def _write_update_report(report_path: Path, history: pd.DataFrame,
                         today: pd.Timestamp) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    latest = history.iloc[-1]
    lines: list[str] = []
    lines.append(f"# Smart Beta 對照組更新（{today.date()}）")
    lines.append("")
    lines.append("## NAV 現況（基期=100）")
    lines.append("")
    lines.append("| Benchmark | NAV | Price |")
    lines.append("|-----------|-----|-------|")
    lines.append(f"| 100% 0050（主） | {latest['nav_0050']:.2f} | {latest['price_0050']:.2f} |")
    lines.append(f"| 100% 0056 | {latest['nav_0056']:.2f} | {latest['price_0056']:.2f} |")
    lines.append(f"| 60/40 混合 | {latest['nav_60_40']:.2f} | - |")
    lines.append("")
    sr_0050 = _rolling_sharpe(history["nav_0050"])
    sr_0056 = _rolling_sharpe(history["nav_0056"])
    sr_60_40 = _rolling_sharpe(history["nav_60_40"])
    lines.append("## Rolling Sharpe（近 3 個月，若樣本足）")
    lines.append("")
    lines.append("| Benchmark | 3M Sharpe |")
    lines.append("|-----------|-----------|")
    lines.append(f"| 100% 0050（主） | {sr_0050:.2f}" if sr_0050 is not None else "| 100% 0050（主） | 樣本不足 |")
    lines.append(f"| 100% 0056 | {sr_0056:.2f}" if sr_0056 is not None else "| 100% 0056 | 樣本不足 |")
    lines.append(f"| 60/40 | {sr_60_40:.2f}" if sr_60_40 is not None else "| 60/40 | 樣本不足 |")
    lines.append("")
    lines.append("## 決策提示")
    lines.append("")
    lines.append("- 主 benchmark = **100% 0050**（贏大盤）")
    lines.append("- factor portfolio 需勝 0050 Sharpe 差 > 0.3 連 3 月才值得實盤")
    lines.append("- 若連 3 月輸 0050 → 考慮切換 100% 0050 月投定期定額")
    lines.append("")
    lines.append(f"累積資料：{len(history)} 筆")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = _project_root()
    csv_path = root / "data" / "cache" / "smart_beta_nav.csv"
    reports_dir = root / "reports" / "smart_beta"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    history = _load_nav_history(csv_path)
    today = pd.Timestamp.now(tz="Asia/Taipei").normalize().tz_localize(None)

    # 防重複寫入（同日跑兩次）
    if not history.empty and pd.Timestamp(history["date"].iloc[-1]) == today:
        logger.info("Already recorded for %s, skipping.", today.date())
        report_path = reports_dir / f"update_{today.date()}.md"
        _write_update_report(report_path, history, today)
        return 0

    price_0050 = _fetch_latest_close("0050")
    price_0056 = _fetch_latest_close("0056")
    if price_0050 is None or price_0056 is None:
        logger.error("Failed to fetch 0050 or 0056 latest close.")
        return 1

    history = _append_today(history, today, price_0050, price_0056)
    history.to_csv(csv_path, index=False)
    logger.info("Appended %s: 0050=%.2f, 0056=%.2f", today.date(), price_0050, price_0056)

    report_path = reports_dir / f"update_{today.date()}.md"
    _write_update_report(report_path, history, today)
    logger.info("Report written: %s", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
