"""Institutional flow scoring for Taiwan stocks.

Extracted from src/strategy/signals.py so that both the portfolio
engine and the legacy signal path can share the same implementation
without private-function coupling.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def score_institutional(institutional_df: pd.DataFrame | None, days: int = 5) -> dict:
    """Score institutional investor activity over the last *days* trading days.

    Returns a **continuous** net-flow composite (NTD) suitable for
    cross-sectional percentile ranking.  The composite weights
    foreign investors at 70 % and investment trust at 30 %.

    FinMind format:
        date | stock_id | buy | name | sell
        name: Foreign_Investor, Investment_Trust, Dealer_self, ...
    """
    if institutional_df is None or institutional_df.empty:
        return {"score": 0, "detail": "no_data", "icon": "➖"}

    if not {"name", "buy", "sell", "date"}.issubset(institutional_df.columns):
        return {"score": 0, "detail": "bad_columns", "icon": "⚠️"}

    try:
        df = institutional_df.copy()
        df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0)
        df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0)
        df["net"] = df["buy"] - df["sell"]

        dates = sorted(df["date"].unique())
        if len(dates) < days:
            return {"score": 0, "detail": "insufficient_days", "icon": "➖"}
        recent_dates = dates[-days:]
        df = df[df["date"].isin(recent_dates)]

        # Foreign investor net flow (primary signal, 70% weight)
        foreign = df[df["name"] == "Foreign_Investor"]
        foreign_net = float(foreign["net"].sum()) if not foreign.empty else 0.0

        # Investment trust net flow (secondary signal, 30% weight)
        trust = df[df["name"] == "Investment_Trust"]
        trust_net = float(trust["net"].sum()) if not trust.empty else 0.0

        # Weighted composite — raw NTD amount for percentile ranking
        composite = 0.7 * foreign_net + 0.3 * trust_net

        # Descriptive detail for logging
        if composite > 0:
            icon = "✅"
        elif composite < 0:
            icon = "🔻"
        else:
            icon = "➖"

        return {
            "score": composite,
            "detail": f"net_flow_{days}d",
            "icon": icon,
            "foreign_net": foreign_net,
            "trust_net": trust_net,
        }

    except Exception as exc:
        logger.warning("Institutional scoring error: %s", exc)
        return {"score": 0, "detail": "error", "icon": "⚠️"}
