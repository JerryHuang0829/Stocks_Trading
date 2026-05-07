"""Base interfaces for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataSource(ABC):
    """Common interface used by the portfolio workflows."""

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame | None:
        """Return OHLCV data indexed by UTC timestamps."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Return whether the underlying market is currently open."""

    def fetch_institutional(self, symbol: str, days: int = 30) -> pd.DataFrame | None:
        """Optional institutional flow dataset."""
        return None

    def fetch_month_revenue(self, symbol: str, months: int = 15) -> pd.DataFrame | None:
        """Optional monthly revenue dataset for Taiwan stocks."""
        return None

    def fetch_stock_info(self) -> pd.DataFrame | None:
        """Optional stock master dataset."""
        return None

    def fetch_market_value(self, days: int = 10) -> pd.DataFrame | None:
        """Optional market value dataset used for universe prefiltering."""
        return None

    def fetch_dividends(self, start_year: int, end_year: int) -> list[dict] | None:
        """Optional ex-dividend records for total-return price adjustment.

        Returns list of dicts with keys: stock_id, ex_date, cash_dividend.
        """
        return None
