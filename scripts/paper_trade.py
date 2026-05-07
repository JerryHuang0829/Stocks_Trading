"""Paper trading recorder.

Reads the latest rebalance result from DB and saves it as a paper trading record.
No real orders are placed. Records are append-only and never overwritten.

Usage:
    # Inside Docker (recommended):
    docker compose run --rm --entrypoint python portfolio-bot scripts/paper_trade.py

    # Or directly (with venv):
    python scripts/paper_trade.py

    # Force re-run even if this month already has a record:
    python scripts/paper_trade.py --allow-rerun

    # Fallback: run live rebalance if DB has no record (old behavior):
    python scripts/paper_trade.py --live
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.storage.database import Database
from src.utils.constants import TECH_SUPPLY_CHAIN_KEYWORDS, TW_TZ

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Windows 上直接跑會因為 pickle cache encoding 導致中文亂碼，
# 必須在 Docker 裡執行（Linux UTF-8 環境）。
if sys.platform == "win32":
    logger.warning(
        "⚠️  Running on Windows — FinMind cache pickles may have encoding issues.\n"
        "    建議改用 Docker 執行：\n"
        "    docker compose run --rm --entrypoint python portfolio-bot scripts/paper_trade.py"
    )

OUTPUT_DIR = Path("reports/paper_trading")


def _load_history() -> list[dict]:
    """讀取 history.json（如果存在）。"""
    perf_path = OUTPUT_DIR / "history.json"
    if perf_path.exists():
        with open(perf_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_history(history: list[dict]) -> None:
    """寫入 history.json（保持按 month_key 排序）。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    perf_path = OUTPUT_DIR / "history.json"
    history.sort(key=lambda x: (x["month_key"], x.get("run_timestamp", "")))
    with open(perf_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _has_record_for_month(history: list[dict], month_key: str) -> bool:
    """檢查某月是否已有正式紀錄（非 rerun）。"""
    return any(
        h["month_key"] == month_key and not h.get("is_rerun", False)
        for h in history
    )


def _build_record_from_db(rebalance: dict, run_ts: str) -> dict:
    """從 DB rebalance 紀錄建立 paper trading record。"""
    positions_data = rebalance.get("positions_json", [])
    ranking_data = rebalance.get("ranking_json", [])

    positions = [
        {
            "symbol": p["symbol"],
            "name": p.get("name", ""),
            "weight": p.get("target_weight", p.get("weight", 0)),
            "score": p.get("score", p.get("rank_score", 0)),
            "industry": p.get("industry", ""),
        }
        for p in positions_data
    ]

    top10 = [
        {
            "rank": r.get("rank", i + 1),
            "symbol": r["symbol"],
            "name": r.get("name", ""),
            "score": r.get("score", r.get("portfolio_score", 0)),
        }
        for i, r in enumerate(ranking_data[:10])
    ]

    month_key = rebalance["month_key"]

    return {
        "date": rebalance["rebalance_date"],
        "month_key": month_key,
        "run_timestamp": run_ts,
        "source": "db",
        "db_rebalance_id": rebalance.get("id"),
        "market_signal": rebalance.get("market_signal", ""),
        "market_regime": rebalance.get("market_regime", ""),
        "gross_exposure": rebalance.get("gross_exposure", 0),
        "total_candidates": rebalance.get("total_candidates", 0),
        "eligible_candidates": rebalance.get("eligible_candidates", 0),
        "selected_count": rebalance.get("selected_count", 0),
        "positions": positions,
        "top10_ranking": top10,
        "config_hash": rebalance.get("config_hash", ""),
    }


def _build_record_live(config_path: str, run_ts: str) -> dict:
    """Fallback：直接跑策略產出紀錄（舊行為，僅在 --live 時使用）。"""
    from src.data.finmind import FinMindSource
    from src.portfolio.tw_stock import (
        get_portfolio_config,
        run_tw_stock_portfolio_rebalance,
    )
    from src.utils.config import load_config

    config = load_config(config_path)
    portfolio_config = get_portfolio_config(config)

    token = os.getenv("FINMIND_TOKEN")
    source = FinMindSource(token=token)
    db = Database(config.get("database", {}).get("path", "data/signals.db"))

    logger.info("Running LIVE rebalance (--live mode)...")
    snapshot = run_tw_stock_portfolio_rebalance(config, source, db, portfolio_config)

    if snapshot is None:
        logger.error("Rebalance returned None — check logs for errors")
        sys.exit(1)

    now = datetime.now(TW_TZ)
    month_key = now.strftime("%Y-%m")

    positions = [
        {
            "symbol": p["symbol"],
            "name": p.get("name", ""),
            "weight": p.get("target_weight", p.get("weight", 0)),
            "score": p.get("portfolio_score", p.get("score", 0)),
            "industry": p.get("industry", ""),
        }
        for p in snapshot["positions"]
    ]

    top10 = [
        {
            "rank": r["rank"],
            "symbol": r["symbol"],
            "name": r["name"],
            "score": r.get("score", r.get("portfolio_score", 0)),
        }
        for r in snapshot.get("ranking", [])[:10]
    ]

    return {
        "date": now.strftime("%Y-%m-%d"),
        "month_key": month_key,
        "run_timestamp": run_ts,
        "source": "live",
        "market_signal": snapshot["market_signal"],
        "market_regime": snapshot["market_regime"],
        "gross_exposure": snapshot["gross_exposure"],
        "total_candidates": snapshot["total_candidates"],
        "eligible_candidates": snapshot["eligible_candidates"],
        "selected_count": snapshot["selected_count"],
        "positions": positions,
        "top10_ranking": top10,
        "config_hash": snapshot.get("config_hash", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Paper trading recorder (append-only)")
    parser.add_argument("--config", default="config/settings.yaml", help="Config file path")
    parser.add_argument(
        "--live", action="store_true",
        help="Run live rebalance instead of reading from DB (fallback mode)",
    )
    parser.add_argument(
        "--allow-rerun", action="store_true",
        help="Allow recording even if this month already has a record (marked as rerun)",
    )
    args = parser.parse_args()

    load_dotenv()

    now = datetime.now(TW_TZ)
    run_ts = now.strftime("%Y%m%d-%H%M%S")

    # --- 載入歷史紀錄 ---
    history = _load_history()

    # --- 建立本月紀錄 ---
    if args.live:
        record = _build_record_live(args.config, run_ts)
    else:
        # 預設：從 DB 讀取最新 rebalance
        from src.utils.config import load_config
        config = load_config(args.config)
        db = Database(config.get("database", {}).get("path", "data/signals.db"))

        rebalance = db.get_latest_rebalance("tw_stock")
        if rebalance is None:
            logger.error(
                "DB 中沒有 rebalance 紀錄。請先執行 live rebalance，或使用 --live 模式。"
            )
            sys.exit(1)

        record = _build_record_from_db(rebalance, run_ts)
        logger.info(
            "從 DB 讀取 rebalance（id=%s, date=%s, %d 檔持股）",
            rebalance.get("id"), rebalance.get("rebalance_date"),
            rebalance.get("selected_count", 0),
        )

    month_key = record["month_key"]

    # --- Append-only 保護 ---
    if _has_record_for_month(history, month_key):
        if not args.allow_rerun:
            logger.warning(
                "本月（%s）已有正式紀錄，跳過。若要強制記錄，請加 --allow-rerun",
                month_key,
            )
            sys.exit(0)
        else:
            record["is_rerun"] = True
            logger.info("本月已有紀錄，以 rerun 身份追加")

    # 正式紀錄加上 actual_return 佔位
    if not record.get("is_rerun"):
        record["actual_return"] = None

    # --- 計算集中度（必須在寫入 JSON 之前）---
    industry_weights: dict[str, float] = {}
    tech_weight = 0.0
    tech_count = 0
    for p in record["positions"]:
        w = float(p.get("weight", 0))
        ind = p.get("industry", "未知")
        industry_weights[ind] = industry_weights.get(ind, 0) + w
        if any(kw in ind for kw in TECH_SUPPLY_CHAIN_KEYWORDS):
            tech_weight += w
            tech_count += 1
    top_ind = max(industry_weights, key=industry_weights.get) if industry_weights else ""
    top_ind_w = industry_weights.get(top_ind, 0)

    record["theme_concentration"] = {
        "tech_weight": round(tech_weight, 4),
        "tech_count": tech_count,
        "top_industry": top_ind,
        "top_industry_weight": round(top_ind_w, 4),
    }

    # --- 儲存月度 JSON（帶時間戳，不覆寫）---
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{month_key}_{run_ts}.json"
    out_path = OUTPUT_DIR / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    # --- Append 到 history.json ---
    history.append(record)
    _save_history(history)

    # --- Print summary ---
    rerun_tag = " [RERUN]" if record.get("is_rerun") else ""
    source_tag = f" (from {record.get('source', 'unknown')})"
    print("\n" + "=" * 50)
    print(f"  Paper Trading Record{rerun_tag}{source_tag}")
    print("=" * 50)
    print(f"\n  日期:       {record['date']}")
    print(f"  月份:       {record['month_key']}")
    print(f"  時間戳:     {record['run_timestamp']}")
    print(f"  市場訊號:   {record['market_signal']}")
    print(f"  總曝險:     {record['gross_exposure']:.0%}")
    print(f"  持股數:     {record['selected_count']}")
    print(f"\n  --- 集中度監控 ---")
    print(f"    科技供應鏈：{tech_weight:.0%}（{tech_count} 檔）")
    print(f"    最大產業：{top_ind} {top_ind_w:.0%}")
    print(f"\n  --- 建議持股 ---")
    for p in record["positions"]:
        print(f"    {p['symbol']} {p['name']:　<6} 權重 {p['weight']:.1%}  分數 {p['score']:.1f}  [{p.get('industry', '')}]")
    print(f"\n  --- Top 10 排名 ---")
    for r in record["top10_ranking"]:
        print(f"    #{r['rank']} {r['symbol']} {r['name']:　<6} {r['score']:.1f}")
    print(f"\n  已儲存: {out_path}")
    print(f"  歷史紀錄: {OUTPUT_DIR / 'history.json'}")

    # history 中正式紀錄數量
    official_count = sum(1 for h in history if not h.get("is_rerun", False))
    print(f"  正式紀錄:  {official_count} 個月")
    print("=" * 50)


if __name__ == "__main__":
    main()
