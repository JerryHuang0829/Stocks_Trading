"""SQLite persistence for alerts and portfolio snapshots."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_config_hash(config: dict) -> str:
    """產生 config 的 SHA-256 hash，用於重現性追蹤。"""
    serialized = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


class Database:
    """Persistence layer shared by the legacy alert path and the new portfolio path."""

    def __init__(self, db_path: str = "data/signals.db"):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_tables(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    direction TEXT NOT NULL,
                    score INTEGER,
                    regime TEXT,
                    components TEXT,
                    reason TEXT
                );

                CREATE TABLE IF NOT EXISTS signal_cooldown (
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    last_sent TEXT NOT NULL,
                    PRIMARY KEY (symbol, direction)
                );

                CREATE TABLE IF NOT EXISTS portfolio_rebalances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    market TEXT NOT NULL,
                    strategy_mode TEXT NOT NULL,
                    rebalance_date TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    market_regime TEXT,
                    market_signal TEXT,
                    market_proxy_symbol TEXT,
                    gross_exposure REAL,
                    cash_weight REAL,
                    total_candidates INTEGER,
                    eligible_candidates INTEGER,
                    selected_count INTEGER,
                    positions_json TEXT,
                    entries_json TEXT,
                    holds_json TEXT,
                    exits_json TEXT,
                    ranking_json TEXT,
                    notes_json TEXT
                );

                CREATE TABLE IF NOT EXISTS portfolio_positions (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    name TEXT,
                    target_weight REAL,
                    rank_score REAL,
                    rank_no INTEGER,
                    action TEXT,
                    rebalance_date TEXT NOT NULL,
                    PRIMARY KEY (symbol, market)
                );

                CREATE INDEX IF NOT EXISTS idx_history_symbol_time
                    ON signal_history(symbol, timestamp);
                CREATE INDEX IF NOT EXISTS idx_portfolio_rebalances_market_month
                    ON portfolio_rebalances(market, month_key);
                """
            )
            conn.commit()

            # Enforce one rebalance per (market, month_key)
            try:
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_rebalances_market_month
                        ON portfolio_rebalances(market, month_key)
                    """
                )
                conn.commit()
            except sqlite3.OperationalError as exc:
                logger.warning(
                    "Could not create unique index on portfolio_rebalances "
                    "(possibly duplicate rows exist): %s", exc,
                )

            legacy_columns = {
                "setup": "TEXT",
                "triggers": "TEXT",
                "htf_regime": "TEXT",
                "risk_mode": "TEXT",
            }
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(signal_history)").fetchall()
            }
            for name, column_type in legacy_columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE signal_history ADD COLUMN {name} {column_type}")
            conn.commit()

            # P0-4: 新增研究可重現性欄位
            rebalance_extra_columns = {
                "config_hash": "TEXT",
                "strategy_version": "TEXT",
                "full_ranked_json": "TEXT",
                "universe_snapshot_json": "TEXT",
                "data_as_of": "TEXT",
                "fallback_notes": "TEXT",
            }
            rebalance_existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(portfolio_rebalances)").fetchall()
            }
            for name, column_type in rebalance_extra_columns.items():
                if name not in rebalance_existing:
                    conn.execute(f"ALTER TABLE portfolio_rebalances ADD COLUMN {name} {column_type}")
            conn.commit()
        finally:
            conn.close()

    def record_signal(self, sym_config: dict, result: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            triggers = result.get("triggers", [])
            conn.execute(
                """
                INSERT INTO signal_history
                    (timestamp, symbol, name, market, direction, score, regime,
                     components, reason, setup, triggers, htf_regime, risk_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    sym_config["symbol"],
                    sym_config.get("name", ""),
                    sym_config.get("market", ""),
                    result["direction"],
                    result["score"],
                    result["regime"],
                    json.dumps(result["components"], ensure_ascii=False, default=str),
                    result["reason"],
                    result.get("setup", ""),
                    json.dumps(triggers, ensure_ascii=False) if triggers else "",
                    result.get("htf_regime", ""),
                    result.get("risk_mode", "normal"),
                ),
            )
            conn.execute(
                """
                INSERT INTO signal_cooldown (symbol, direction, last_sent)
                VALUES (?, ?, ?)
                ON CONFLICT (symbol, direction) DO UPDATE SET last_sent = excluded.last_sent
                """,
                (sym_config["symbol"], result["direction"], now),
            )
            conn.commit()
        finally:
            conn.close()

    def check_cooldown(self, symbol: str, direction: str, cooldown_hours: float) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT last_sent FROM signal_cooldown WHERE symbol = ? AND direction = ?",
                (symbol, direction),
            ).fetchone()
            if row is None:
                return True

            last_sent = datetime.fromisoformat(row[0])
            now = datetime.now(timezone.utc)
            hours_passed = (now - last_sent).total_seconds() / 3600
            return hours_passed >= cooldown_hours
        finally:
            conn.close()

    def get_recent_signals(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM signal_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                    (symbol, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM signal_history ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def has_portfolio_rebalance(self, market: str, month_key: str) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM portfolio_rebalances WHERE market = ? AND month_key = ? LIMIT 1",
                (market, month_key),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_portfolio_positions(self, market: str = "tw_stock") -> list[dict]:
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT symbol, market, name, target_weight, rank_score, rank_no, action, rebalance_date
                FROM portfolio_positions
                WHERE market = ?
                ORDER BY rank_no ASC, symbol ASC
                """,
                (market,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_latest_rebalance(self, market: str = "tw_stock") -> dict | None:
        """取得最新一筆 rebalance 紀錄（含 positions_json / ranking_json）。"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT *
                FROM portfolio_rebalances
                WHERE market = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (market,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            # 解析 JSON 欄位
            for key in (
                "positions_json", "entries_json", "holds_json", "exits_json",
                "ranking_json", "notes_json", "full_ranked_json",
                "universe_snapshot_json", "fallback_notes",
            ):
                if key in result and result[key]:
                    try:
                        result[key] = json.loads(result[key])
                    except (json.JSONDecodeError, TypeError):
                        pass
            return result
        finally:
            conn.close()

    def record_portfolio_rebalance(self, snapshot: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        market = snapshot.get("market", "tw_stock")
        positions = snapshot.get("positions", [])

        conn = self._get_conn()
        try:
            # 使用 BEGIN IMMEDIATE 確保整個操作是原子性的
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT id FROM portfolio_rebalances
                    WHERE market = ? AND month_key = ?
                    LIMIT 1
                    """,
                    (market, snapshot["month_key"]),
                ).fetchone()
                if existing is not None:
                    raise sqlite3.IntegrityError(
                        f"portfolio rebalance already exists for market={market}, month_key={snapshot['month_key']}"
                    )

                conn.execute(
                    """
                    INSERT INTO portfolio_rebalances (
                        timestamp, market, strategy_mode, rebalance_date, month_key,
                        market_regime, market_signal, market_proxy_symbol,
                        gross_exposure, cash_weight,
                        total_candidates, eligible_candidates, selected_count,
                        positions_json, entries_json, holds_json, exits_json,
                        ranking_json, notes_json,
                        config_hash, strategy_version,
                        full_ranked_json, universe_snapshot_json,
                        data_as_of, fallback_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        market,
                        snapshot.get("strategy_mode", "tw_stock_portfolio"),
                        snapshot["rebalance_date"],
                        snapshot["month_key"],
                        snapshot.get("market_regime", ""),
                        snapshot.get("market_signal", ""),
                        snapshot.get("market_proxy_symbol", ""),
                        snapshot.get("gross_exposure", 0.0),
                        snapshot.get("cash_weight", 1.0),
                        snapshot.get("total_candidates", 0),
                        snapshot.get("eligible_candidates", 0),
                        snapshot.get("selected_count", 0),
                        json.dumps(positions, ensure_ascii=False, default=str),
                        json.dumps(snapshot.get("entries", []), ensure_ascii=False, default=str),
                        json.dumps(snapshot.get("holds", []), ensure_ascii=False, default=str),
                        json.dumps(snapshot.get("exits", []), ensure_ascii=False, default=str),
                        json.dumps(snapshot.get("ranking", []), ensure_ascii=False, default=str),
                        json.dumps(snapshot.get("notes", []), ensure_ascii=False, default=str),
                        snapshot.get("config_hash", ""),
                        snapshot.get("strategy_version", ""),
                        json.dumps(snapshot.get("full_ranked", []), ensure_ascii=False, default=str),
                        json.dumps(snapshot.get("universe_snapshot", []), ensure_ascii=False, default=str),
                        snapshot.get("data_as_of", ""),
                        json.dumps(snapshot.get("fallback_notes", []), ensure_ascii=False, default=str),
                    ),
                )

                conn.execute("DELETE FROM portfolio_positions WHERE market = ?", (market,))
                if positions:
                    conn.executemany(
                        """
                        INSERT INTO portfolio_positions (
                            symbol, market, name, target_weight, rank_score, rank_no, action, rebalance_date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                position["symbol"],
                                market,
                                position.get("name", position["symbol"]),
                                position.get("target_weight", 0.0),
                                position.get("score", 0.0),
                                position.get("rank", 0),
                                position.get("action", ""),
                                snapshot["rebalance_date"],
                            )
                            for position in positions
                        ],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()
