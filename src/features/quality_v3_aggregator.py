"""quality_v3 history aggregator — TTM rolling + Δassets YoY (Path B S6.1).

R25-mid Codex audit P-B fix (2026-05-05): D-E quality_v3 needs full income
statement + balance sheet history (NOT EPS-only existing cache + NOT
single-snapshot fetch_financial_quality). This module aggregates raw FinMind
quarterly data into the schema expected by `src.features.quality_v3.compute_quality_v3_panel`:
    - 'symbol' / 'period_end' / 'quarter' / 'roe_ttm' / 'gross_margin_ttm' / 'assets_yoy_pct'

TTM (Trailing Twelve Months) rolling: sum of last 4 quarterly values
    - roe_ttm = sum(net_income_4Q) / equity_latest_quarter
    - gross_margin_ttm = sum(gross_profit_4Q) / sum(revenue_4Q)
    - assets_yoy_pct = (total_assets_current_quarter - total_assets_4Q_ago) /
                      total_assets_4Q_ago

Caller (S6.1 cell sweep) typically:
    fs_full = finmind.fetch_quarterly_financial_full(symbol)
    bs_history = finmind.fetch_balance_sheet_history(symbol)
    history_df = aggregate_quality_v3_history(symbol, fs_full, bs_history)
    panel = compute_quality_v3_panel(history_df, as_of=rebalance_date)
"""
from __future__ import annotations

import pandas as pd


def _pivot_long_to_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot FinMind long-format (date / type / value) to wide (one column per type).

    Input: DataFrame with columns ['date', 'stock_id', 'type', 'value']
    Output: DataFrame indexed by date with columns Revenue / GrossProfit / etc.
    """
    if long_df is None or long_df.empty:
        return pd.DataFrame()
    if not {"date", "type", "value"}.issubset(long_df.columns):
        return pd.DataFrame()
    wide = long_df.pivot_table(
        index="date", columns="type", values="value", aggfunc="last",
    )
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def _quarter_from_date(date: pd.Timestamp) -> int:
    """Map period_end date to quarter number (1-4)."""
    month = date.month
    if month <= 3:
        return 1
    if month <= 6:
        return 2
    if month <= 9:
        return 3
    return 4


def aggregate_quality_v3_history(
    symbol: str,
    fs_full: pd.DataFrame | None,
    bs_history: pd.DataFrame | None,
) -> pd.DataFrame:
    """Aggregate raw FinMind data into quality_v3 schema with TTM + YoY.

    Args:
        symbol: stock symbol str
        fs_full: long-format DataFrame from `fetch_quarterly_financial_full()`;
                 must contain columns date / type / value with type values
                 including 'Revenue' / 'GrossProfit' / 'IncomeAfterTaxes'
        bs_history: long-format DataFrame from `fetch_balance_sheet_history()`;
                    must contain type values including 'Equity' / 'TotalAssets'

    Returns:
        DataFrame with columns 'symbol' / 'period_end' / 'quarter' /
        'roe_ttm' / 'gross_margin_ttm' / 'assets_yoy_pct'.
        One row per (symbol, quarter) where TTM (4Q rolling) + YoY (5Q ago)
        computable. Empty DataFrame if data insufficient.
    """
    fs_wide = _pivot_long_to_wide(fs_full) if fs_full is not None else pd.DataFrame()
    bs_wide = _pivot_long_to_wide(bs_history) if bs_history is not None else pd.DataFrame()
    if fs_wide.empty or bs_wide.empty:
        return pd.DataFrame()

    # Required income statement columns
    required_fs = {"Revenue", "GrossProfit", "IncomeAfterTaxes"}
    if not required_fs.issubset(fs_wide.columns):
        return pd.DataFrame()
    # Required balance sheet columns
    required_bs = {"Equity", "TotalAssets"}
    if not required_bs.issubset(bs_wide.columns):
        return pd.DataFrame()

    # Rolling 4Q sums for TTM (income statement)
    revenue_ttm = fs_wide["Revenue"].rolling(window=4, min_periods=4).sum()
    gross_profit_ttm = fs_wide["GrossProfit"].rolling(window=4, min_periods=4).sum()
    net_income_ttm = fs_wide["IncomeAfterTaxes"].rolling(window=4, min_periods=4).sum()

    # gross_margin_ttm = sum(GrossProfit_4Q) / sum(Revenue_4Q)
    gross_margin_ttm = gross_profit_ttm / revenue_ttm.replace(0, pd.NA)

    # roe_ttm = sum(net_income_4Q) / equity_latest (need date alignment)
    # Align bs_wide["Equity"] to fs_wide quarterly dates
    aligned_equity = bs_wide["Equity"].reindex(fs_wide.index, method="nearest")
    roe_ttm = net_income_ttm / aligned_equity.replace(0, pd.NA)

    # Δassets YoY: (TotalAssets_current - TotalAssets_4Q_ago) / TotalAssets_4Q_ago
    aligned_assets = bs_wide["TotalAssets"].reindex(fs_wide.index, method="nearest")
    assets_yoy_pct = aligned_assets.pct_change(periods=4)

    # Build output DataFrame; drop rows where any TTM/YoY missing
    out = pd.DataFrame({
        "period_end": fs_wide.index,
        "quarter": [_quarter_from_date(d) for d in fs_wide.index],
        "roe_ttm": roe_ttm.values,
        "gross_margin_ttm": gross_margin_ttm.values,
        "assets_yoy_pct": assets_yoy_pct.values,
    })
    out["symbol"] = symbol
    out = out[["symbol", "period_end", "quarter", "roe_ttm", "gross_margin_ttm", "assets_yoy_pct"]]
    out = out.dropna(subset=["roe_ttm", "gross_margin_ttm", "assets_yoy_pct"])
    return out.reset_index(drop=True)
