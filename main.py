"""
Taiwan stock quantitative portfolio runner.

The project used to center on per-symbol alerts. The main entrypoint now
defaults to a Taiwan stock portfolio workflow:

1. build the configured stock universe
2. run monthly rebalance after market close
3. rank candidates cross-sectionally
4. persist target positions and rebalance snapshots
5. send one portfolio summary message
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.data.finmind import FinMindSource
from src.notify.telegram import TelegramNotifier
from src.portfolio.tw_stock import (
    build_tw_stock_universe,
    get_portfolio_config,
    run_tw_stock_portfolio_rebalance,
    should_rebalance_now,
)
from src.storage.database import Database
from src.utils.config import load_config
from src.utils.constants import TW_TZ

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "tw_stock_portfolio.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

load_dotenv()

# Graceful shutdown flag
_shutdown_requested = False


def _handle_shutdown(signum, frame):
    """Signal handler for graceful shutdown."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown signal received (signal=%s), will exit after current cycle", signum)


def init_sources(config: dict) -> dict:
    """Initialize only the data sources required by the active mode."""
    mode = config.get("system", {}).get("mode", "tw_stock_portfolio")
    sources: dict[str, object] = {}

    if mode == "tw_stock_portfolio":
        token = os.getenv("FINMIND_TOKEN")
        sources["finmind"] = FinMindSource(token=token)
        logger.info("Initialized FinMind source for Taiwan stock portfolio mode")
        return sources

    raise ValueError(f"Unsupported system.mode: {mode}")


def _log_portfolio_config_warnings(portfolio_config: dict) -> None:
    """Emit warnings for config combinations that deserve explicit review."""
    if portfolio_config.get("use_monthly_revenue", True):
        rebalance_day = int(portfolio_config.get("rebalance_day", 5))
        if rebalance_day < 10:
            logger.warning(
                "Portfolio uses monthly revenue, but rebalance_day=%s is before the typical "
                "monthly revenue release window. Latest revenue data may be intentionally stale; "
                "consider moving rebalance_day to 10-12 or splitting price/fundamental schedules.",
                rebalance_day,
            )

    top_n = int(portfolio_config.get("top_n", 0) or 0)
    max_position_weight = float(portfolio_config.get("max_position_weight", 0.0) or 0.0)
    risk_on_exposure = float(
        portfolio_config.get("exposure", {}).get("risk_on", 1.0)
    )
    theoretical_max_gross = top_n * max_position_weight
    if top_n > 0 and theoretical_max_gross + 1e-9 < risk_on_exposure:
        logger.warning(
            "Profile cannot fully deploy risk_on exposure: top_n=%s and max_position_weight=%.1f%% "
            "cap gross exposure at %.1f%% while risk_on target is %.1f%%.",
            top_n,
            max_position_weight * 100,
            theoretical_max_gross * 100,
            risk_on_exposure * 100,
        )


def run_once(config: dict, sources: dict, db: Database, notifier: TelegramNotifier) -> None:
    """Run one rebalance check for the Taiwan stock portfolio."""
    portfolio_config = get_portfolio_config(config)
    source = sources.get("finmind")
    if source is None:
        raise RuntimeError("FinMind source is required in tw_stock_portfolio mode")

    should_run, reason = should_rebalance_now(portfolio_config, db, source)
    if not should_run:
        logger.info("Portfolio rebalance skipped: %s", reason)
        return

    snapshot = run_tw_stock_portfolio_rebalance(
        config=config,
        source=source,
        db=db,
        portfolio_config=portfolio_config,
    )
    if snapshot is None:
        logger.warning("Portfolio rebalance produced no snapshot")
        notifier.send("Portfolio rebalance attempted but produced no result (safety valve or no universe)")
        return

    db.record_portfolio_rebalance(snapshot)
    notifier.send_portfolio_rebalance(snapshot)
    logger.info(
        "Recorded Taiwan stock rebalance for %s with %s target positions",
        snapshot["rebalance_date"],
        snapshot["selected_count"],
    )


def main() -> None:
    # Register graceful shutdown handlers
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    logger.info("=" * 60)
    logger.info("Taiwan Stock Quant Portfolio System starting")
    logger.info("=" * 60)

    config_path = os.getenv("CONFIG_PATH", "config/settings.yaml")
    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        logger.error("Config file not found: %s", exc)
        sys.exit(1)

    try:
        sources = init_sources(config)
    except Exception as exc:
        logger.error("Failed to initialize data sources: %s", exc, exc_info=True)
        sys.exit(1)

    db = Database(config.get("database", {}).get("path", "data/signals.db"))
    notifier = TelegramNotifier(config.get("telegram", {}))
    portfolio_config = get_portfolio_config(config)
    _log_portfolio_config_warnings(portfolio_config)
    try:
        universe = build_tw_stock_universe(config, sources["finmind"], portfolio_config)
    except Exception as exc:
        logger.warning("Failed to build startup universe via auto builder: %s", exc)
        universe = [
            item
            for item in config.get("symbols", [])
            if item.get("enabled", False) and item.get("market") == "tw_stock"
        ]

    notifier.send_portfolio_startup(config, portfolio_config, universe_count=len(universe))

    check_interval = int(config.get("check_interval", 900))
    heartbeat_interval = int(config.get("heartbeat_interval", 86400))  # 預設每天一次
    logger.info("Check interval: %s minutes", check_interval // 60)
    logger.info("Heartbeat interval: %s hours", heartbeat_interval // 3600)
    logger.info("Taiwan universe size: %s", len(universe))

    last_heartbeat = time.monotonic()
    last_config_mtime = _get_file_mtime(config_path)

    while not _shutdown_requested:
        try:
            # Hot reload config if file changed
            current_mtime = _get_file_mtime(config_path)
            if current_mtime != last_config_mtime:
                logger.info("Config file changed, reloading...")
                try:
                    config = load_config(config_path)
                    portfolio_config = get_portfolio_config(config)
                    check_interval = int(config.get("check_interval", 900))
                    heartbeat_interval = int(config.get("heartbeat_interval", 86400))
                    last_config_mtime = current_mtime
                    _log_portfolio_config_warnings(portfolio_config)
                    logger.info("Config reloaded successfully")
                except Exception as exc:
                    logger.warning("Failed to reload config, keeping current: %s", exc)

            run_once(config, sources, db, notifier)
        except Exception as exc:
            logger.error("Main loop failed: %s", exc, exc_info=True)
            notifier.send(f"Taiwan portfolio loop failed: {exc}")

        # Heartbeat notification
        elapsed_since_heartbeat = time.monotonic() - last_heartbeat
        if elapsed_since_heartbeat >= heartbeat_interval:
            notifier.send_heartbeat(db)
            last_heartbeat = time.monotonic()

        # Interruptible sleep
        sleep_end = time.monotonic() + check_interval
        while not _shutdown_requested and time.monotonic() < sleep_end:
            time.sleep(min(5, sleep_end - time.monotonic()))

    logger.info("Graceful shutdown complete")
    notifier.send("Taiwan Portfolio System shutting down gracefully")


def _get_file_mtime(path: str) -> float:
    """Get file modification time, return 0 if file doesn't exist."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


if __name__ == "__main__":
    main()
