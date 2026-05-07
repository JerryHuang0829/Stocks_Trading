"""Telegram notifications for portfolio summaries and legacy alerts."""

from __future__ import annotations

import logging
import os
from datetime import datetime

import requests

from ..strategy.regime import get_regime_display
from ..utils.constants import TW_TZ

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Thin Telegram client used by the bot."""

    def __init__(self, config: dict):
        self.bot_token = os.getenv(config.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
        self.chat_id = os.getenv(config.get("chat_id_env", "TELEGRAM_CHAT_ID"))
        self.enabled = bool(self.bot_token and self.chat_id)

        if not self.enabled:
            logger.warning("Telegram credentials missing; notifications will be logged only")

    def send(self, message: str) -> bool:
        if not self.enabled:
            logger.info("[Telegram disabled]\n%s", message)
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    def send_portfolio_startup(self, config: dict, portfolio_config: dict, universe_count: int) -> bool:
        target_holding = portfolio_config.get("target_holding_months")
        message = "\n".join(
            [
                "*Taiwan Stock Portfolio System started*",
                f"Mode: `{config.get('system', {}).get('mode', 'tw_stock_portfolio')}`",
                f"Profile: `{portfolio_config.get('profile') or 'custom'}` / {portfolio_config.get('profile_label', 'custom')}",
                f"Universe size: {universe_count}",
                f"Rebalance: monthly after day {portfolio_config.get('rebalance_day', 5)} close",
                f"Target holding: {target_holding} months" if target_holding else "Target holding: custom",
                f"Top N: {portfolio_config.get('top_n', 5)}",
                f"Max position: {portfolio_config.get('max_position_weight', 0.2):.0%}",
                f"Market proxy: {portfolio_config.get('market_proxy_symbol', '0050')}",
            ]
        )
        return self.send(message)

    def send_portfolio_rebalance(self, snapshot: dict) -> bool:
        positions = snapshot.get("positions", [])
        entries = snapshot.get("entries", [])
        exits = snapshot.get("exits", [])
        ranking = snapshot.get("ranking", [])

        lines = [
            "*Taiwan Portfolio Rebalance*",
            f"Date: `{snapshot.get('rebalance_date', '')}`",
            f"Profile: `{snapshot.get('portfolio_profile') or 'custom'}` / {snapshot.get('portfolio_profile_label', 'custom')}",
            f"Market: {snapshot.get('market_regime_display', snapshot.get('market_regime', ''))} / `{snapshot.get('market_signal', '')}`",
            f"Exposure: {snapshot.get('gross_exposure', 0):.0%} | Cash: {snapshot.get('cash_weight', 0):.0%}",
            f"Candidates: {snapshot.get('total_candidates', 0)} | Eligible: {snapshot.get('eligible_candidates', 0)} | Selected: {snapshot.get('selected_count', 0)}",
            "",
            "*Target Positions*",
        ]

        if positions:
            for position in positions:
                lines.append(
                    (
                        f"{position['rank']}. {position['name']} ({position['symbol']}) "
                        f"| {position['target_weight']:.1%} "
                        f"| score {position['score']:.1f} "
                        f"| {position['action']}"
                    )
                )
        else:
            lines.append("No eligible holdings; stay in cash.")

        if entries:
            lines.extend(["", "*New Entries*"])
            for item in entries:
                lines.append(f"- {item['name']} ({item['symbol']}) {item['target_weight']:.1%}")

        if exits:
            lines.extend(["", "*Exits*"])
            for item in exits:
                lines.append(f"- {item['name']} ({item['symbol']}) was {item['previous_weight']:.1%}")

        if ranking:
            lines.extend(["", "*Top Ranking Preview*"])
            for item in ranking[:5]:
                momentum = _format_pct(item.get("momentum_12_1"))
                revenue = _format_pct(item.get("revenue_yoy"))
                lines.append(
                    (
                        f"- #{item['rank']} {item['name']} ({item['symbol']}) "
                        f"| score {item['score']:.1f} "
                        f"| 12-1 {momentum} "
                        f"| revYoY {revenue}"
                    )
                )

        notes = snapshot.get("notes", [])
        if notes:
            lines.extend(["", "*Notes*"])
            for note in notes:
                lines.append(f"- `{note}`")

        return self.send("\n".join(lines))

    def send_heartbeat(self, db=None) -> bool:
        """定期心跳通知，讓用戶確認系統運行中。"""
        now = datetime.now(TW_TZ)
        lines = [
            "*System Heartbeat*",
            f"Time: `{now.strftime('%Y-%m-%d %H:%M TST')}`",
            "Status: Running",
        ]
        if db is not None:
            try:
                positions = db.get_portfolio_positions("tw_stock")
                if positions:
                    lines.append(f"Current holdings: {len(positions)}")
                    for p in positions[:5]:
                        lines.append(f"- {p.get('name', p['symbol'])} ({p['symbol']}) {p.get('target_weight', 0):.1%}")
                else:
                    lines.append("Current holdings: 0 (all cash)")
            except Exception:
                lines.append("Holdings: unable to query")
        return self.send("\n".join(lines))

    def send_signal(self, sym_config: dict, result: dict, df=None) -> bool:
        """Legacy per-symbol alert formatter retained for compatibility."""
        name = sym_config.get("name", sym_config["symbol"])
        symbol = sym_config["symbol"]
        market = sym_config.get("market", "")
        regime_display = get_regime_display(result["regime"])

        if result["direction"] == "BUY":
            action = "BUY"
        elif market == "tw_stock":
            action = "REDUCE"
        else:
            action = "SELL"

        price_section = ""
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            price = latest["close"]
            currency = "NT$" if market == "tw_stock" else "$"
            price_section = f"Price: {currency}{price:,.2f}\n"

        component_lines = []
        for key, comp in result.get("components", {}).items():
            score = comp.get("score", 0)
            detail = comp.get("detail", "")
            component_lines.append(f"- {key}: {detail} ({score:+.0f})")

        time_str = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M TST")
        message = "\n".join(
            [
                f"*{action}* | {name} ({symbol})",
                price_section.rstrip(),
                f"Score: {result['score']}/100",
                f"Regime: {regime_display}",
                "Components:",
                *component_lines,
                f"Reason: {result['reason']}",
                time_str,
            ]
        )
        return self.send(message)

    def send_startup(self, config: dict) -> bool:
        message = "\n".join(
            [
                "*Signal Bot started*",
                f"Mode: `{config.get('system', {}).get('mode', 'signal_bot')}`",
            ]
        )
        return self.send(message)


def _format_pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.1f}%"
