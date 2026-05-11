"""S6.1 wire-up — real BacktestEngine-equivalent 18-cell sweep runner.

Phase 2 Session 6.1 (2026-05-06) — H_d_v6 V0.13 §"Cell sweep adjust pipeline"
+ Plan v7.1 Step 1 sequencing: replace `run_cell_sweep_stub` with real
production-factor-based monthly composite engine.

Architecture decision (per Plan agent design 2026-05-06): lightweight composite
extending `composite_backtest.py` pattern, NOT BacktestEngine wrap. Reuses
composite_backtest.py:81/198/207/212 helpers + production factor modules.

Reused helpers:
- `composite_backtest.py:_load_canonical_round_trip_cost` (V0.13 Assertion 1)
- `composite_backtest.py:_load_universe_ohlcv` (universe filter)
- `composite_backtest.py:_month_end_dates` (rebalance schedule)
- `composite_backtest.py:_next_month_return` (forward return)
- `src/features/{high_proximity,pead_eps,margin_short_ratio,quality_v3,
  industry_momentum,idio_vol_max}.py::compute_*_universe/panel`
- `src/analysis/active_correlation.py::active_corr` (L5(a) per V0.14)
- `src/backtest/metrics.py::adjust_dividends` (0050 total return benchmark)

Output schema (per d_cell_aggregate_v7.aggregate_cell_results expectation):
- ir / mean_alpha_monthly / te / max_dd_diff_vs_0050 / active_corr /
  beta_adj_alpha_t / sharpe_for_dsr (7 keys per cell metrics dict)

13 pre-commit disciplines enforced:
- #1 L1-L7 thresholds frozen (read by aggregator, not this module)
- #2 DSR n_trials=18 (aggregator enforces)
- #3 Sample period 2019-2024 (caller passes)
- #4 Universe top-80 close × volume (caller filters or accepts default)
- #5 6 candidate factor sets locked (CANDIDATE_FACTOR_SETS)
- #6 Monthly frequency only (BME schedule)
- #7 3 top_n {8, 12, 16} (TOP_N_VALUES)
- #8 Foreign_v2 / Revenue_v2 exclusion (yaml configs already exclude)
- #9 Sole_survivor tie-break IR > α (S8 enforces)
- #11 D-A pre-disqualification (Assertion 2 enforced)
- #12 IC canonical n=71 (factor IC step, not this module)
- #13 L6 80% CI lower > 0 (S7 walk_forward enforces)
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.composite_backtest import (  # noqa: E402
    _is_above_min_price_at,
    _load_canonical_round_trip_cost,
    _load_universe_ohlcv,
    _month_end_dates,
    _next_month_return,
)
from scripts.d_cell_sweep_v7 import (  # noqa: E402
    CANDIDATE_FACTOR_SETS,
    TOP_N_VALUES,
    load_candidate_config,
)
from src.analysis.active_correlation import active_corr  # noqa: E402
from src.features.high_proximity import compute_high_proximity_universe  # noqa: E402
from src.features.idio_vol_max import compute_idio_vol_max_panel  # noqa: E402
from src.features.industry_momentum import compute_industry_momentum_panel  # noqa: E402
from src.features.margin_short_ratio import compute_margin_short_ratio_universe  # noqa: E402
from src.features.pead_eps import compute_pead_eps_universe  # noqa: E402
from src.features.quality_v3 import compute_quality_v3_panel  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# V0.13 Assertion 1: cost from settings.yaml (NOT hardcoded)
# ---------------------------------------------------------------------------
TW_ROUND_TRIP_COST, TW_ROUND_TRIP_COST_BPS = _load_canonical_round_trip_cost()


# ---------------------------------------------------------------------------
# Context dataclass-light: bundle all PIT-aware data sources for a sweep run
# ---------------------------------------------------------------------------
class CellSweepContext:
    """Bundles all data sources needed across factors + benchmark.

    Built once at start of run_full_18_cell_sweep — shared across all 18 cells
    for performance (avoid reloading OHLCV / financial history per cell).
    """

    def __init__(
        self,
        cache_dir: pathlib.Path,
        start: datetime,
        end: datetime,
        *,
        require_dividend_adjust: bool = True,
    ) -> None:
        """V0.24: require_dividend_adjust=True (default) → hard fail if 0050
        dividends not present (per H_d_v6 spec total-return benchmark required).
        Set False ONLY for non-formal smoke/dev runs.
        """
        self.cache_dir = cache_dir
        self.start = start
        self.end = end
        self._require_dividend_adjust = require_dividend_adjust
        # Load OHLCV universe with extra lookback for 252d high_proximity window
        lookback_start = start - pd.Timedelta(days=400)
        self.ohlcv_panel: dict[str, pd.DataFrame] = _load_universe_ohlcv(
            cache_dir, lookback_start, end
        )
        # Lazy-loaded cross-cell shared resources
        self._eps_by_symbol: dict[str, pd.DataFrame] | None = None
        self._margin_by_symbol: dict[str, pd.DataFrame] | None = None
        # 2026-05-11 R30 4-path PIT cleanup (R29 finding 1):
        # was `_issued_by_symbol: dict | None`, replaced by panel cache for
        # PIT-asof lookup per rebalance date via `issued_by_symbol_at()`.
        self._issued_capital_panel: pd.DataFrame | None = None
        self._financial_history: pd.DataFrame | None = None
        self._industry_label_map: dict[str, str] | None = None
        self._market_returns: pd.Series | None = None
        self._benchmark_monthly_returns: pd.Series | None = None
        self._month_ends: list[datetime] | None = None

    @property
    def month_ends(self) -> list[datetime]:
        if self._month_ends is None:
            self._month_ends = _month_end_dates(self.start, self.end)
        return self._month_ends

    @property
    def eps_by_symbol(self) -> dict[str, pd.DataFrame]:
        if self._eps_by_symbol is None:
            self._eps_by_symbol = self._load_pkl_panel("quarterly_eps")
        return self._eps_by_symbol

    @property
    def margin_by_symbol(self) -> dict[str, pd.DataFrame]:
        if self._margin_by_symbol is None:
            self._margin_by_symbol = self._load_pkl_panel("margin_short")
        return self._margin_by_symbol

    @property
    def issued_capital_panel(self) -> pd.DataFrame:
        """PIT-able issued_capital panel via single source-of-truth helper.

        2026-05-11 R30 4-path PIT cleanup (R29 finding 1): replaces
        the old ``issued_by_symbol`` dict property which used latest snapshot
        for all rebalance dates (PIT violation). Uses
        ``src.data.pit_helpers._load_issued_capital_panel`` shared with IC
        pipeline + portfolio path.

        Caveat: when cache lacks date column, fallback returns static panel
        dated 1970-01-01 (R28-1 / R29-4 documented limitation; same
        fallback behavior as IC pipeline for cross-path consistency).
        """
        if self._issued_capital_panel is None:
            from src.data.pit_helpers import _load_issued_capital_panel
            self._issued_capital_panel = _load_issued_capital_panel(self.cache_dir)
            if self._issued_capital_panel.empty:
                logger.warning(
                    "issued_capital panel empty — margin_short_ratio may have empty universe"
                )
        return self._issued_capital_panel

    def issued_by_symbol_at(self, as_of: pd.Timestamp) -> dict[str, float]:
        """As-of issued_shares lookup for a specific rebalance date (PIT-correct).

        Per-rebalance lookup via shared ``_issued_capital_asof`` helper.
        Replaces the old ``issued_by_symbol`` property which returned a
        single dict reused across all rebalance dates (PIT violation).
        """
        from src.data.pit_helpers import _issued_capital_asof
        panel = self.issued_capital_panel
        if panel.empty:
            return {}
        return _issued_capital_asof(panel, as_of)

    @property
    def financial_history(self) -> pd.DataFrame:
        """Build quality_v3-compatible financial history from quarterly_financial_full + balance_sheet."""
        if self._financial_history is None:
            self._financial_history = _build_financial_history(self.cache_dir)
        return self._financial_history

    @property
    def industry_label_map(self) -> dict[str, str]:
        """Option B (per V0.14 R14): use current stock_info snapshot industry_category."""
        if self._industry_label_map is None:
            self._industry_label_map = _build_industry_label_map(self.cache_dir)
        return self._industry_label_map

    @property
    def market_returns(self) -> pd.Series:
        """0050 daily returns (price-only, used as IdioVol regression benchmark)."""
        if self._market_returns is None:
            self._market_returns = _build_market_returns(self.cache_dir)
        return self._market_returns

    @property
    def benchmark_monthly_returns(self) -> pd.Series:
        """0050 dividend-adjusted total-return monthly returns aligned to month_ends.

        V0.24: hard-fail if dividends not present unless require_dividend_adjust=False.
        """
        if self._benchmark_monthly_returns is None:
            self._benchmark_monthly_returns = _build_benchmark_monthly_returns(
                self.cache_dir, self.month_ends,
                require_dividend_adjust=self._require_dividend_adjust,
            )
        return self._benchmark_monthly_returns

    def _load_pkl_panel(self, dataset: str) -> dict[str, pd.DataFrame]:
        """Load all .pkl files in a cache subdir; key = stock_id."""
        result: dict[str, pd.DataFrame] = {}
        ds_dir = self.cache_dir / dataset
        if not ds_dir.exists():
            logger.warning("Cache dir %s missing", ds_dir)
            return result
        for p in ds_dir.glob("*.pkl"):
            sid = p.stem
            if not (sid.isdigit() and len(sid) == 4):
                continue
            try:
                df = pd.read_pickle(p)
                if df is not None and not df.empty:
                    result[sid] = df
            except Exception:
                continue
        return result


# ---------------------------------------------------------------------------
# Builders for factor data sources
# ---------------------------------------------------------------------------
def _build_financial_history(cache_dir: pathlib.Path) -> pd.DataFrame:
    """Build quality_v3-compatible long DataFrame from cache/quarterly_financial_full
    + cache/balance_sheet pkls.

    Output schema (per src/features/quality_v3.py:113-144 expectations):
        symbol, period_end, quarter, roe_ttm, gross_margin_ttm, assets_yoy_pct
    """
    qfin_dir = cache_dir / "quarterly_financial_full"
    bs_dir = cache_dir / "balance_sheet"
    if not qfin_dir.exists() or not bs_dir.exists():
        logger.warning("quality_v3 cache panels missing — financial_history empty")
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for qfin_pkl in qfin_dir.glob("*.pkl"):
        sid = qfin_pkl.stem
        if not (sid.isdigit() and len(sid) == 4):
            continue
        bs_pkl = bs_dir / f"{sid}.pkl"
        if not bs_pkl.exists():
            continue
        try:
            qfin = pd.read_pickle(qfin_pkl)
            bs = pd.read_pickle(bs_pkl)
            if qfin.empty or bs.empty:
                continue
            qfin["date"] = pd.to_datetime(qfin["date"])
            bs["date"] = pd.to_datetime(bs["date"])
            # Pivot long → wide per period_end
            qfin_wide = qfin.pivot_table(
                index="date", columns="type", values="value", aggfunc="first"
            )
            bs_wide = bs.pivot_table(
                index="date", columns="type", values="value", aggfunc="first"
            )
            # Compute TTM rolling 4Q for income flows; rolling 4Q mean for balance stocks
            if "Revenue" in qfin_wide.columns:
                qfin_wide["revenue_ttm"] = qfin_wide["Revenue"].rolling(4).sum()
            if "GrossProfit" in qfin_wide.columns:
                qfin_wide["gross_profit_ttm"] = qfin_wide["GrossProfit"].rolling(4).sum()
            # V0.26 (2026-05-06) bugfix: NetIncome column exists in q_fin pivoted
            # output but FinMind cache for many stocks (e.g. TSMC 2330 from 2020+)
            # has NaN in NetIncome — true value lives in IncomeAfterTaxes instead.
            # re-audit 2026-05-06 confirmed: V0.25 fix made financial_history
            # non-empty, but all rows had period_end ≤ 2019-12-31 because
            # net_income_ttm = rolling(4).sum() of NetIncome → NaN for any window
            # touching 2020+ → roe_ttm NaN → row dropped silently. Fix: fillna
            # NetIncome with IncomeAfterTaxes (synonym in FinMind schema; "稅後
            # 淨利" — same concept different label).
            ni_source = pd.Series(dtype=float)
            if "NetIncome" in qfin_wide.columns:
                ni_source = qfin_wide["NetIncome"].copy()
            if "IncomeAfterTaxes" in qfin_wide.columns:
                if ni_source.empty:
                    ni_source = qfin_wide["IncomeAfterTaxes"].copy()
                else:
                    ni_source = ni_source.fillna(qfin_wide["IncomeAfterTaxes"])
            if not ni_source.empty:
                qfin_wide["net_income_ttm"] = ni_source.rolling(4).sum()
            if "EquityAttributableToOwnersOfParent" in bs_wide.columns:
                bs_wide["equity_avg"] = bs_wide["EquityAttributableToOwnersOfParent"].rolling(4).mean()
            if "TotalAssets" in bs_wide.columns:
                bs_wide["assets_yoy_pct"] = bs_wide["TotalAssets"].pct_change(periods=4)

            # V0.25 (2026-05-06) bugfix: q_fin & bs both contain
            # EquityAttributableToOwnersOfParent + NoncontrollingInterests
            # → pd.DataFrame.join() raised ValueError "columns overlap" silently
            # caught in outer try/except → entire financial_history empty (external audit
            # pre-run audit P0 found D-E quality_v3 完全死). Fix: use rsuffix to
            # disambiguate; we access only computed columns (revenue_ttm /
            # equity_avg / etc.) which don't overlap.
            merged = qfin_wide.join(bs_wide, how="inner", rsuffix="_bs")
            for date_idx, row in merged.iterrows():
                rev = row.get("revenue_ttm", np.nan)
                gp = row.get("gross_profit_ttm", np.nan)
                ni = row.get("net_income_ttm", np.nan)
                eq = row.get("equity_avg", np.nan)
                ay = row.get("assets_yoy_pct", np.nan)
                if pd.isna(rev) or rev == 0 or pd.isna(eq) or eq == 0:
                    continue
                gm_ttm = gp / rev if pd.notna(gp) else np.nan
                roe_ttm = ni / eq if pd.notna(ni) else np.nan
                if pd.isna(gm_ttm) or pd.isna(roe_ttm) or pd.isna(ay):
                    continue
                ts = pd.Timestamp(date_idx)
                rows.append({
                    "symbol": sid,
                    "period_end": ts,
                    "quarter": (ts.month - 1) // 3 + 1,
                    "roe_ttm": float(roe_ttm),
                    "gross_margin_ttm": float(gm_ttm),
                    "assets_yoy_pct": float(ay),
                })
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    df_out = pd.DataFrame(rows).set_index("symbol")
    # V0.26 sanity log: warn if period coverage suspicious (silent dropping is
    # how the V0.25→V0.26 bug hid for 2 audit rounds — surface period stats).
    if not df_out.empty:
        period_max = df_out["period_end"].max()
        period_min = df_out["period_end"].min()
        n_symbols = df_out.index.nunique()
        logger.info(
            "V0.26 _build_financial_history: %d rows, %d symbols, period %s ~ %s",
            len(df_out), n_symbols, period_min.date(), period_max.date(),
        )
        # If most recent period_end < 2 years before today, financial_history
        # is unusable for any backtest end date > 2 years ago.
        from datetime import datetime as _dt
        gap_years = (_dt.now() - period_max.to_pydatetime()).days / 365.25
        if gap_years > 2.0:
            logger.warning(
                "V0.26 SANITY WARN: latest period_end %s is %.1f years stale; "
                "quality_v3 panel will be empty for any rebal date > 2 years "
                "after %s. Likely cause: NetIncome / IncomeAfterTaxes column "
                "missing in cache, schema drift in FinMind API.",
                period_max.date(), gap_years, period_max.date(),
            )
    return df_out


def _build_industry_label_map(cache_dir: pathlib.Path) -> dict[str, str]:
    """Option B (V0.14 R14 caveat): use current stock_info snapshot industry_category."""
    csv_path = cache_dir / "stock_info" / "stock_info_snapshot.csv"
    if not csv_path.exists():
        logger.warning("stock_info_snapshot.csv missing — industry_label_map empty")
        return {}
    df = pd.read_csv(csv_path, dtype=str)
    if "industry_category" not in df.columns:
        return {}
    df = df.dropna(subset=["industry_category"])
    return dict(zip(df["stock_id"].astype(str), df["industry_category"].astype(str)))


def _build_market_returns(cache_dir: pathlib.Path) -> pd.Series:
    """0050 price-only daily returns for IdioVol regression."""
    pkl = cache_dir / "ohlcv" / "0050.pkl"
    if not pkl.exists():
        logger.warning("0050 OHLCV cache missing — market_returns empty")
        return pd.Series(dtype=float)
    df = pd.read_pickle(pkl)
    df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz else pd.to_datetime(df.index)
    return df["close"].pct_change().dropna()


def _build_benchmark_monthly_returns(
    cache_dir: pathlib.Path, month_ends: list[datetime],
    *,
    require_dividend_adjust: bool = True,
) -> pd.Series:
    """0050 dividend-adjusted monthly returns aligned to month_ends.

    V0.24 (2026-05-06) hard-fail fix: previous V0.14 silent fallback to
    price-only when `dividends/0050.pkl` not found violated H_d_v6 spec
    "total-return required". Now reads `dividends/_global.pkl` (list[dict]
    schema covering all stocks) and calls `adjust_dividends(close, divs,
    "0050")` — internal filter handles 0050 rows.

    If `require_dividend_adjust=True` (default per H_d_v6 spec):
    - 0050 dividends not present in _global.pkl → raise FileNotFoundError
    - adjust_dividends fails → re-raise
    Set `require_dividend_adjust=False` only for non-formal smoke / dev runs.
    """
    pkl = cache_dir / "ohlcv" / "0050.pkl"
    if not pkl.exists():
        if require_dividend_adjust:
            raise FileNotFoundError(
                f"0050 OHLCV cache missing at {pkl} — H_d_v6 spec requires "
                f"total-return benchmark; pre-flight gate must enforce this."
            )
        logger.warning("0050 OHLCV cache missing — benchmark monthly returns empty")
        return pd.Series(dtype=float)
    df = pd.read_pickle(pkl)
    df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz else pd.to_datetime(df.index)
    close = df["close"].copy()

    # V0.24: read dividends from _global.pkl (list[dict] schema), filter by symbol
    div_pkl = cache_dir / "dividends" / "_global.pkl"
    if not div_pkl.exists():
        if require_dividend_adjust:
            raise FileNotFoundError(
                f"dividends/_global.pkl missing at {div_pkl} — H_d_v6 spec "
                f"requires total-return benchmark. Run cache_fill to seed."
            )
        logger.warning("dividends/_global.pkl missing — benchmark price-only fallback")
    else:
        try:
            from src.backtest.metrics import adjust_dividends
            divs = pd.read_pickle(div_pkl)
            # divs is list[dict]; adjust_dividends filters by symbol internally
            divs_0050 = [d for d in divs if str(d.get("stock_id", "")) == "0050"]
            if not divs_0050 and require_dividend_adjust:
                raise ValueError(
                    "No dividend records for 0050 in _global.pkl — H_d_v6 spec "
                    "total-return benchmark requires at least one event for the "
                    "validation period 2019-2024 (0050 distributes dividends "
                    "twice yearly Jan/Jul). Cache may be incomplete."
                )
            close = adjust_dividends(close, divs_0050, "0050")
            logger.info("V0.24: 0050 dividend-adjusted close (%d events applied)",
                         len(divs_0050))
        except Exception as exc:
            if require_dividend_adjust:
                raise
            logger.warning("adjust_dividends failed for 0050: %s — using price-only", exc)

    # Sample at each month_end
    rets: list[float] = []
    for i in range(len(month_ends) - 1):
        c0_view = close[close.index <= pd.Timestamp(month_ends[i]).normalize()]
        c1_view = close[close.index <= pd.Timestamp(month_ends[i + 1]).normalize()]
        if c0_view.empty or c1_view.empty:
            rets.append(np.nan)
            continue
        c0, c1 = float(c0_view.iloc[-1]), float(c1_view.iloc[-1])
        rets.append(c1 / c0 - 1.0 if c0 > 0 else np.nan)

    # Index aligned to month_ends[1:] (the "achieved" rebalance dates)
    idx = pd.DatetimeIndex([pd.Timestamp(d).normalize() for d in month_ends[1:]])
    return pd.Series(rets, index=idx, dtype=float).dropna()


# ---------------------------------------------------------------------------
# Factor dispatch — yaml factor_name → production module compute function
# ---------------------------------------------------------------------------
def _compute_factor_panel(
    factor_name: str,
    ctx: CellSweepContext,
    as_of: pd.Timestamp,
) -> pd.Series:
    """Dispatch factor_name → production module compute_*_universe/panel.

    Returns cross-section pd.Series indexed by symbol, value = raw factor score.
    Empty Series when no valid symbols (caller handles via intersection guard).
    """
    if factor_name == "high_proximity":
        return compute_high_proximity_universe(ctx.ohlcv_panel, as_of)
    if factor_name == "pead_eps":
        return compute_pead_eps_universe(ctx.eps_by_symbol, as_of=as_of)
    if factor_name == "margin_short_ratio":
        # 2026-05-11 R30: per-rebalance PIT-asof lookup (was using latest dict
        # across all rebalance dates — R29 finding 1 PIT violation).
        return compute_margin_short_ratio_universe(
            ctx.margin_by_symbol, ctx.issued_by_symbol_at(as_of), as_of=as_of
        )
    if factor_name == "quality_v3":
        return compute_quality_v3_panel(ctx.financial_history, as_of=as_of)
    if factor_name == "industry_momentum":
        return compute_industry_momentum_panel(
            ctx.ohlcv_panel, ctx.industry_label_map, as_of
        )
    if factor_name == "idio_vol_max":
        return compute_idio_vol_max_panel(ctx.ohlcv_panel, ctx.market_returns, as_of)
    raise ValueError(f"Unknown factor: {factor_name}")


def _z_score(s: pd.Series, clip: float = 3.0) -> pd.Series:
    """Cross-section z-score with ±clip σ outlier clip. Mirrors quality_v3._z_score."""
    if s.empty:
        return s
    valid = s.dropna()
    if valid.empty or valid.std() == 0:
        return pd.Series(0.0, index=valid.index)
    z = (valid - valid.mean()) / valid.std()
    return z.clip(-clip, clip)


# ---------------------------------------------------------------------------
# Metrics computation — 7 keys per d_cell_aggregate_v7 expectation
# ---------------------------------------------------------------------------
def _compute_max_drawdown(rets: pd.Series) -> float:
    """Max drawdown of cumulative returns. Returns negative number (e.g. -0.15)."""
    if rets.empty:
        return 0.0
    cum = (1 + rets).cumprod()
    return float((cum / cum.expanding().max() - 1).min())


def _compute_beta_adj_alpha_t(
    port_rets: pd.Series, bench_rets: pd.Series
) -> float:
    """OLS regression: port = α + β × bench + ε. Return t-statistic of α.

    Standard error of intercept: SE(α) = σ_ε × √(1/n + mean(bench)² / Σ(bench - mean(bench))²)
    """
    n = len(port_rets)
    if n < 3:
        return 0.0
    x = bench_rets.values
    y = port_rets.values
    x_mean, y_mean = x.mean(), y.mean()
    x_var = ((x - x_mean) ** 2).sum()
    if x_var == 0:
        return 0.0
    beta = ((x - x_mean) * (y - y_mean)).sum() / x_var
    alpha = y_mean - beta * x_mean
    residuals = y - (alpha + beta * x)
    sigma_eps = float(np.sqrt((residuals ** 2).sum() / (n - 2))) if n > 2 else 0.0
    if sigma_eps == 0:
        return 0.0
    se_alpha = sigma_eps * float(np.sqrt(1 / n + x_mean ** 2 / x_var))
    return float(alpha / se_alpha) if se_alpha > 0 else 0.0


def _compute_cell_metrics(
    monthly_active_returns: pd.Series,
    monthly_port_returns: pd.Series,
    monthly_bench_returns: pd.Series,
) -> dict[str, float]:
    """Compute the 7 metrics expected by d_cell_aggregate_v7.aggregate_cell_results.

    Args:
        monthly_active_returns: portfolio - benchmark, indexed by month_end
        monthly_port_returns: portfolio monthly returns (net of cost)
        monthly_bench_returns: benchmark (0050) monthly returns

    Returns dict with: ir / mean_alpha_monthly / te / max_dd_diff_vs_0050 /
        active_corr / beta_adj_alpha_t / sharpe_for_dsr
    """
    # Align all 3 Series to common index (V0.14 active_corr requires)
    common_idx = (
        monthly_active_returns.index
        .intersection(monthly_port_returns.index)
        .intersection(monthly_bench_returns.index)
    )
    a = monthly_active_returns.loc[common_idx].dropna()
    p = monthly_port_returns.loc[common_idx].dropna()
    b = monthly_bench_returns.loc[common_idx].dropna()
    common_idx2 = a.index.intersection(p.index).intersection(b.index)
    a = a.loc[common_idx2]
    p = p.loc[common_idx2]
    b = b.loc[common_idx2]

    if len(a) < 3:
        return {
            "ir": 0.0,
            "mean_alpha_monthly": 0.0,
            "te": 0.0,
            "max_dd_diff_vs_0050": 0.0,
            "active_corr": 0.0,
            "beta_adj_alpha_t": 0.0,
            "sharpe_for_dsr": 0.0,
        }

    mean_alpha = float(a.mean())
    a_std_monthly = float(a.std())
    te_annualized = a_std_monthly * float(np.sqrt(12))
    ir_annualized = (mean_alpha * 12) / te_annualized if te_annualized > 0 else 0.0
    sharpe_active = mean_alpha * float(np.sqrt(12)) / a_std_monthly if a_std_monthly > 0 else 0.0

    # active_corr per V0.14 P0-4 fix (index alignment enforced internally)
    try:
        ac = active_corr(p, b)
    except ValueError:
        ac = 0.0

    # max_dd_diff: portfolio - benchmark (negative MDD; positive diff means port better)
    port_dd = _compute_max_drawdown(p)
    bench_dd = _compute_max_drawdown(b)
    max_dd_diff = port_dd - bench_dd  # both negative; diff > 0 means port shallower DD

    beta_t = _compute_beta_adj_alpha_t(p, b)

    return {
        "ir": float(ir_annualized),
        "mean_alpha_monthly": mean_alpha,
        "te": float(te_annualized),
        "max_dd_diff_vs_0050": float(max_dd_diff),
        "active_corr": float(ac),
        "beta_adj_alpha_t": float(beta_t),
        "sharpe_for_dsr": float(sharpe_active),
    }


# ---------------------------------------------------------------------------
# Main run_cell_sweep_real — single-cell run
# ---------------------------------------------------------------------------
def run_cell_sweep_real(
    candidate_id: str,
    top_n: int,
    start_date: datetime,
    end_date: datetime,
    *,
    ctx: CellSweepContext | None = None,
    cache_dir: pathlib.Path | None = None,
) -> tuple[dict[str, float], list[float]]:
    """Run a single (candidate_id, top_n) cell — real production-factor backtest.

    Returns:
        (cell_metrics_dict, monthly_active_returns_list)
        - cell_metrics_dict: 7 metrics per d_cell_aggregate_v7 schema
        - monthly_active_returns_list: list of floats per month_end (for S7 walk_forward)
    """
    if candidate_id not in CANDIDATE_FACTOR_SETS:
        raise ValueError(
            f"candidate_id {candidate_id} not in CANDIDATE_FACTOR_SETS "
            f"{CANDIDATE_FACTOR_SETS}; D-A pre-disqualified per V0.13 Assertion 2."
        )
    if top_n not in TOP_N_VALUES:
        raise ValueError(
            f"top_n {top_n} not in TOP_N_VALUES {TOP_N_VALUES} (pre-commit #7 frozen)"
        )

    cfg = load_candidate_config(candidate_id)  # raises if D-A composition (V0.14)
    weights: dict[str, float] = dict(cfg["factors"])

    if ctx is None:
        if cache_dir is None:
            raise ValueError("Either ctx or cache_dir must be provided")
        ctx = CellSweepContext(cache_dir, start_date, end_date)

    month_ends = ctx.month_ends
    bench_monthly = ctx.benchmark_monthly_returns

    monthly_port_rets: list[float] = []
    monthly_active_rets: list[float] = []
    monthly_bench_rets: list[float] = []
    held_prev: set[str] = set()

    for i in range(len(month_ends) - 1):
        rebal = month_ends[i]
        next_rebal = month_ends[i + 1]
        rebal_ts = pd.Timestamp(rebal).normalize()

        # Step 1: compute each factor's panel
        factor_panels: dict[str, pd.Series] = {}
        for fname in weights:
            try:
                panel = _compute_factor_panel(fname, ctx, rebal_ts)
            except Exception as exc:
                logger.warning("[%s/%s/%s] factor %s failed: %s",
                               candidate_id, top_n, rebal.date(), fname, exc)
                panel = pd.Series(dtype=float)
            factor_panels[fname] = _z_score(panel)

        # Step 2: composite = weighted sum on intersection of factor universes
        common_syms = None
        for s in factor_panels.values():
            common_syms = set(s.index) if common_syms is None else common_syms & set(s.index)
        if common_syms is None or len(common_syms) < top_n:
            monthly_port_rets.append(0.0)
            monthly_bench_rets.append(0.0)
            monthly_active_rets.append(0.0)
            continue

        # V0.23 PIT-safe MIN_PRICE filter (replaces forward-looking
        # composite_backtest._load_universe_ohlcv mean filter):
        # only stocks with close >= MIN_PRICE on or before rebal_ts qualify.
        common_syms = {
            sid for sid in common_syms
            if sid in ctx.ohlcv_panel
            and _is_above_min_price_at(ctx.ohlcv_panel[sid], rebal_ts)
        }
        if len(common_syms) < top_n:
            monthly_port_rets.append(0.0)
            monthly_bench_rets.append(0.0)
            monthly_active_rets.append(0.0)
            continue

        composite = pd.Series(0.0, index=list(common_syms))
        for fname, panel in factor_panels.items():
            composite += weights[fname] * panel.reindex(composite.index).fillna(0.0)

        # Step 3: select top_n by composite score
        top_syms = composite.nlargest(top_n).index.tolist()

        # Step 4: forward return per stock; equal-weight portfolio
        rets = []
        for sid in top_syms:
            if sid in ctx.ohlcv_panel:
                r = _next_month_return(ctx.ohlcv_panel[sid], rebal, next_rebal)
                if r is not None:
                    rets.append(r)
        if not rets:
            monthly_port_rets.append(0.0)
            monthly_bench_rets.append(0.0)
            monthly_active_rets.append(0.0)
            continue

        gross_ret = float(np.mean(rets))
        # Turnover cost
        new_set = set(top_syms)
        turnover = (
            len(new_set.symmetric_difference(held_prev)) / (2 * top_n)
            if held_prev else 1.0
        )
        net_ret = gross_ret - turnover * TW_ROUND_TRIP_COST
        held_prev = new_set

        # Benchmark return at this rebal
        bench_ts = pd.Timestamp(next_rebal).normalize()
        bench_ret = float(bench_monthly.get(bench_ts, np.nan))
        if pd.isna(bench_ret):
            bench_ret = 0.0

        monthly_port_rets.append(net_ret)
        monthly_bench_rets.append(bench_ret)
        monthly_active_rets.append(net_ret - bench_ret)

    # Build Series for metrics computation
    rebal_idx = pd.DatetimeIndex(
        [pd.Timestamp(d).normalize() for d in month_ends[1:1 + len(monthly_port_rets)]]
    )
    p_series = pd.Series(monthly_port_rets, index=rebal_idx)
    b_series = pd.Series(monthly_bench_rets, index=rebal_idx)
    a_series = pd.Series(monthly_active_rets, index=rebal_idx)

    metrics = _compute_cell_metrics(a_series, p_series, b_series)
    return metrics, monthly_active_rets


# ---------------------------------------------------------------------------
# 18-cell wrapper + JSON persistence
# ---------------------------------------------------------------------------
def run_full_18_cell_sweep(
    start_date: datetime,
    end_date: datetime,
    output_dir: pathlib.Path,
    cache_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Run all 18 cells (6 candidates × 3 top_n); persist incrementally.

    Outputs:
        <output_dir>/cell_metrics.json — dict["<candidate>|<top_n>" → metrics_dict]
        <output_dir>/cell_monthly_active_returns.json — for S7 walk_forward
    """
    from src.utils.paths import resolve_cache_dir

    if cache_dir is None:
        cache_dir = resolve_cache_dir()

    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "cell_metrics.json"
    returns_path = output_dir / "cell_monthly_active_returns.json"

    logger.info("Building CellSweepContext (loading universe + factor data sources)...")
    ctx = CellSweepContext(cache_dir, start_date, end_date)
    logger.info(
        "Universe: %d OHLCV stocks; benchmark: %d monthly returns",
        len(ctx.ohlcv_panel), len(ctx.benchmark_monthly_returns),
    )

    cell_metrics: dict[str, dict[str, float]] = {}
    cell_returns: dict[str, list[float]] = {}

    for candidate_id in CANDIDATE_FACTOR_SETS:
        for top_n in TOP_N_VALUES:
            cell_key = f"{candidate_id}|{top_n}"
            logger.info("Running cell %s ...", cell_key)
            try:
                metrics, returns = run_cell_sweep_real(
                    candidate_id, top_n, start_date, end_date, ctx=ctx,
                )
            except Exception as exc:
                logger.error("Cell %s FAILED: %s", cell_key, exc)
                metrics = {
                    "ir": 0.0, "mean_alpha_monthly": 0.0, "te": 0.0,
                    "max_dd_diff_vs_0050": 0.0, "active_corr": 0.0,
                    "beta_adj_alpha_t": 0.0, "sharpe_for_dsr": 0.0,
                    "error": str(exc),
                }
                returns = []
            cell_metrics[cell_key] = metrics
            cell_returns[cell_key] = returns
            # Incremental persistence (resilience against mid-run crash)
            metrics_path.write_text(json.dumps(cell_metrics, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            returns_path.write_text(json.dumps(cell_returns, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            logger.info("Cell %s: ir=%.4f mean_α=%.4f te=%.4f",
                        cell_key, metrics.get("ir", 0.0),
                        metrics.get("mean_alpha_monthly", 0.0),
                        metrics.get("te", 0.0))

    logger.info("All 18 cells done. Output: %s", output_dir)
    return {"cell_metrics": cell_metrics, "cell_returns": cell_returns}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="S6.1 wire-up real 18-cell sweep")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--smoke", action="store_true",
                        help="Smoke test: D-B / top_n=8 / 1 month")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")

    if args.smoke:
        logger.info("Smoke test: D-B / top_n=8 / %s ~ %s", start.date(), end.date())
        from src.utils.paths import resolve_cache_dir
        ctx = CellSweepContext(resolve_cache_dir(), start, end)
        metrics, returns = run_cell_sweep_real(
            "D-B", 8, start, end, ctx=ctx,
        )
        logger.info("Smoke metrics: %s", metrics)
        logger.info("Smoke returns (n=%d): %s", len(returns), returns[:5])
    else:
        result = run_full_18_cell_sweep(start, end, args.output_dir)
        logger.info("Done. Cells: %d", len(result["cell_metrics"]))


if __name__ == "__main__":
    main()
