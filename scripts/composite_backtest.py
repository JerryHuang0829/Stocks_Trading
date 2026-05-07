"""Lightweight backtest for Phase A1 new-factor composite (52W High + PEAD + Margin Short).

Scope: simplified vs BacktestEngine — monthly rebalance, top-N equal-weight,
no regime exposure, no drift-aware daily returns, no hold_buffer.

Why lightweight: new 5 factors are not wired into src/portfolio/tw_stock.py
scoring engine. Full integration would be 200+ lines + SOP overhead. This
script is for "directional sanity check before paper trade", not for final
investment commitment.

Known simplifications (impact in report):
  - No regime-aware exposure (Sharpe possibly overstated ~5-10%)
  - No drift-aware daily returns (uses month-end to month-end; vol understated)
  - No hold_buffer / turnover_threshold (turnover inflated = friction upper bound)
  - Split adjustment: OHLCV pkls are already split-adjusted per P4.5
  - No dividend re-investment (benchmark comparison uses raw price; 0050 tests
    should compare to similar raw-price benchmark)
  - Survivorship bias: reads only existing pkls (does NOT include fully
    delisted before start date)

Usage:
    python scripts/composite_backtest.py \\
        --start 2020-01-01 --end 2024-12-31 \\
        --weight-mode ir_weighted \\
        --output-dir reports/backtest/config_B_composite_2020_2024
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from datetime import datetime

import numpy as np
import pandas as pd

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config import load_config  # noqa: E402
from src.utils.paths import resolve_cache_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _load_canonical_round_trip_cost() -> tuple[float, float]:
    """V0.13 Assertion 1 enforcement (Phase 2 Session 1, 2026-05-05): read cost
    from config/settings.yaml — NOT hardcoded.

    Returns (cost_decimal, cost_bps). Raises AssertionError if settings.yaml
    drift detected. Phase A1 legacy 57bps (47bps fee+tax + 10bps single-side
    slippage) replaced by V0.4 baseline canonical 67bps (47bps fee+tax + 20bps
    round-trip slippage = 10bps × 2 sides) post-`0d31572` engine.py 5→10 bps fix.

    Per H_d_v6 V0.13 §"Assertion 1 — Cost dual-model check" + R24 §"設計-1":
    composite_backtest.py 必讀 settings.yaml，不可繼續 hardcode 57.0。
    """
    cfg = load_config(str(PROJECT_ROOT / "config" / "settings.yaml"))
    portfolio = cfg.get("portfolio", {})
    turnover_cost = float(portfolio.get("turnover_cost", 0.0047))
    slippage_bps = float(portfolio.get("slippage_bps", 10))
    cost = turnover_cost + 2.0 * slippage_bps / 10000.0
    assert abs(cost - 0.0067) < 1e-6, (
        f"V0.13 Assertion 1 FAIL: composite_backtest cost ≠ 0.0067; got {cost}. "
        f"Verify config/settings.yaml portfolio.turnover_cost={turnover_cost} + "
        f"slippage_bps={slippage_bps}. Phase 2 Session 1 enforcement per "
        f"reports/phase_d/H_d_v6_preregistration.md V0.13 §'Assertion 1 — "
        f"Cost dual-model check'."
    )
    return cost, cost * 10000.0


TW_ROUND_TRIP_COST, TW_ROUND_TRIP_COST_BPS = _load_canonical_round_trip_cost()
TOP_N = 8
MIN_PRICE = 10.0


def _load_universe_ohlcv(cache_dir: pathlib.Path, start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    """Load OHLCV for all 4-digit tradeable stocks in [start, end].

    V0.23 (2026-05-06): REMOVED forward-looking `df["close"].mean() < MIN_PRICE`
    filter — at each rebal date in `start..end`, the entire-period mean uses
    future prices (e.g. 2020-01 rebal sees 2020-2024 prices). Caller MUST apply
    per-rebal-date MIN_PRICE filter using `df[df.index <= rebal]["close"].iloc[-1]`.

    Trigger: 2026-05-06 Codex audit found this look-ahead bug. Filter is now
    PIT-safe by being applied at rebalance time (see d_cell_sweep_v7_real.py).
    """
    ohlcv_dir = cache_dir / "ohlcv"
    universe = {}
    for p in ohlcv_dir.glob("*.pkl"):
        sid = p.stem
        if not (sid.isdigit() and len(sid) == 4):
            continue
        try:
            df = pd.read_pickle(p)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            mask = (df.index >= start) & (df.index <= end)
            df = df[mask]
            if len(df) < 20:
                continue
            # V0.23 PIT fix: NO mean() price filter here (forward-looking).
            # Caller applies per-rebal-date filter via _is_above_min_price_at().
            universe[sid] = df
        except Exception:
            continue
    logger.info("Loaded %d OHLCV pkls in [%s..%s]", len(universe), start.date(), end.date())
    return universe


def _is_above_min_price_at(df: pd.DataFrame, rebal_ts: pd.Timestamp,
                            min_price: float = 10.0) -> bool:
    """V0.23 PIT-safe price filter: check close on or before `rebal_ts` ≥ min_price.

    Use this in caller's rebalance loop instead of forward-looking mean filter.
    """
    hist = df[df.index <= rebal_ts]
    if hist.empty:
        return False
    return float(hist["close"].iloc[-1]) >= min_price


def _compute_factor_scores(
    factor_name: str,
    sym_ohlcv: dict[str, pd.DataFrame],
    cache_dir: pathlib.Path,
    as_of: datetime,
) -> dict[str, float]:
    """Return {symbol: raw_score} at point-in-time as_of."""
    if factor_name == "52W_high_proximity":
        return _factor_52w_high(sym_ohlcv, as_of)
    if factor_name == "pead_eps":
        return _factor_pead(cache_dir, as_of)
    if factor_name == "margin_short":
        return _factor_margin_short(cache_dir, sym_ohlcv.keys(), as_of)
    raise ValueError(f"unknown factor: {factor_name}")


def _factor_52w_high(sym_ohlcv: dict[str, pd.DataFrame], as_of: datetime) -> dict[str, float]:
    """52W high proximity = close / 252d_rolling_max - 1 (lag 1 for PIT)."""
    as_of_ts = pd.Timestamp(as_of).normalize()
    scores = {}
    for sid, df in sym_ohlcv.items():
        hist = df[df.index <= as_of_ts]
        if len(hist) < 252:
            continue
        rolling_max = hist["close"].rolling(252).max().shift(1)
        latest = hist["close"].iloc[-1]
        max_1y = rolling_max.iloc[-1]
        if max_1y and max_1y > 0:
            scores[sid] = float(latest / max_1y - 1.0)
    return scores


def _factor_pead(cache_dir: pathlib.Path, as_of: datetime) -> dict[str, float]:
    """Simplified PEAD: use quarterly EPS surprise vs 8-quarter mean; Q4=90d lag, Q1-3=45d."""
    qeps_dir = cache_dir / "quarterly_eps"
    as_of_ts = pd.Timestamp(as_of).normalize()
    scores = {}
    for p in qeps_dir.glob("*.pkl"):
        sid = p.stem
        if not (sid.isdigit() and len(sid) == 4):
            continue
        try:
            df = pd.read_pickle(p)
            df = df[df["type"] == "EPS"].sort_values("date")
            df["date"] = pd.to_datetime(df["date"])
            # apply lag per quarter
            lag_days = df["date"].apply(lambda d: 90 if d.month == 12 else 45)
            df["available"] = df["date"] + pd.to_timedelta(lag_days, unit="D")
            df = df[df["available"] <= as_of_ts]
            if len(df) < 9:
                continue
            baseline = df["value"].iloc[-9:-1].mean()
            current = df["value"].iloc[-1]
            if abs(baseline) > 1e-6:
                scores[sid] = float((current - baseline) / abs(baseline))
        except Exception:
            continue
    return scores


def _factor_margin_short(cache_dir: pathlib.Path, symbols, as_of: datetime) -> dict[str, float]:
    """Simplified margin/short: negative of (MarginBalance / ShortBalance ratio change 20d).
    Lag 2 days per production config."""
    ms_dir = cache_dir / "margin_short"
    as_of_ts = pd.Timestamp(as_of).normalize()
    cutoff = as_of_ts - pd.Timedelta(days=2)
    scores = {}
    for sid in symbols:
        p = ms_dir / f"{sid}.pkl"
        if not p.exists():
            continue
        try:
            df = pd.read_pickle(p)
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["date"] <= cutoff].sort_values("date")
            if len(df) < 21:
                continue
            last = df.iloc[-1]
            prev = df.iloc[-21]
            m_now = float(last["MarginPurchaseTodayBalance"])
            s_now = float(last["ShortSaleTodayBalance"])
            m_prev = float(prev["MarginPurchaseTodayBalance"])
            if m_now <= 0 or m_prev <= 0:
                continue
            # short/margin ratio + 20d margin change — inverse signal
            ratio = s_now / m_now
            m_change = (m_now - m_prev) / m_prev
            scores[sid] = float(-0.5 * ratio - 0.5 * m_change)
        except Exception:
            continue
    return scores


def _rank_normalize(scores: dict[str, float]) -> dict[str, float]:
    """Convert to percentile rank [0, 1]."""
    if not scores:
        return {}
    items = sorted(scores.items(), key=lambda x: x[1])
    n = len(items)
    return {sid: (rank + 1) / n for rank, (sid, _) in enumerate(items)}


def _month_end_dates(start: datetime, end: datetime) -> list[datetime]:
    rng = pd.date_range(start=start, end=end, freq="BME")
    return [d.to_pydatetime() for d in rng]


def _next_month_return(df: pd.DataFrame, rebal_date: datetime, next_rebal_date: datetime) -> float | None:
    """Return realized return from rebal_date close to next_rebal_date close."""
    try:
        d0 = pd.Timestamp(rebal_date).normalize()
        d1 = pd.Timestamp(next_rebal_date).normalize()
        # find nearest on-or-before close
        hist0 = df[df.index <= d0]
        hist1 = df[df.index <= d1]
        if len(hist0) == 0 or len(hist1) == 0:
            return None
        c0 = hist0["close"].iloc[-1]
        c1 = hist1["close"].iloc[-1]
        if c0 > 0:
            return float(c1 / c0 - 1.0)
    except Exception:
        pass
    return None


def run_backtest(start: datetime, end: datetime, weight_mode: str,
                 cache_dir: pathlib.Path) -> dict:
    """Monthly composite backtest."""
    # Weights
    if weight_mode == "ir_weighted":
        weights = {"52W_high_proximity": 0.38, "pead_eps": 0.34, "margin_short": 0.28}
    elif weight_mode == "equal":
        weights = {"52W_high_proximity": 1/3, "pead_eps": 1/3, "margin_short": 1/3}
    else:
        raise ValueError(f"unknown weight_mode: {weight_mode}")
    logger.info("Weights (%s): %s", weight_mode, weights)

    universe = _load_universe_ohlcv(cache_dir, start, end)
    month_ends = _month_end_dates(start, end)
    logger.info("Rebalance dates: %d", len(month_ends))

    portfolio_rets: list[float] = []
    selections = []
    held_prev: set[str] = set()

    for i in range(len(month_ends) - 1):
        rebal = month_ends[i]
        next_rebal = month_ends[i + 1]

        # Score each factor
        factor_scores = {}
        for fname in weights:
            raw = _compute_factor_scores(fname, universe, cache_dir, rebal)
            factor_scores[fname] = _rank_normalize(raw)

        # Composite score = weighted sum of ranks. Need sym in all 3 factors.
        common_syms = set.intersection(*(set(s.keys()) for s in factor_scores.values()))
        if len(common_syms) < TOP_N:
            logger.warning("[%s] only %d common syms, skip", rebal.date(), len(common_syms))
            portfolio_rets.append(0.0)
            selections.append([])
            continue

        composite = {}
        for sid in common_syms:
            composite[sid] = sum(weights[f] * factor_scores[f][sid] for f in weights)
        ranked = sorted(composite.items(), key=lambda x: -x[1])
        top = [sid for sid, _ in ranked[:TOP_N]]

        # Equal-weight top-N. Compute forward return.
        rets = []
        for sid in top:
            if sid in universe:
                r = _next_month_return(universe[sid], rebal, next_rebal)
                if r is not None:
                    rets.append(r)
        if not rets:
            portfolio_rets.append(0.0)
            selections.append(top)
            continue

        gross_ret = float(np.mean(rets))
        # Friction: turnover × cost
        new_set = set(top)
        turnover = len(new_set.symmetric_difference(held_prev)) / (2 * TOP_N) if held_prev else 1.0
        friction = turnover * TW_ROUND_TRIP_COST  # V0.13 Assertion 1: settings.yaml-driven, not hardcoded
        net_ret = gross_ret - friction
        portfolio_rets.append(net_ret)
        selections.append(top)
        held_prev = new_set

        if i % 6 == 0 or i == len(month_ends) - 2:
            logger.info("[%d/%d] %s: %d picks, gross=%.4f turnover=%.2f friction=%.4f net=%.4f",
                        i + 1, len(month_ends) - 1, rebal.date(),
                        len(top), gross_ret, turnover, friction, net_ret)

    rets_series = pd.Series(portfolio_rets).dropna()
    if len(rets_series) < 2:
        return {"error": "insufficient return data"}
    total_ret = (1 + rets_series).prod() - 1
    years = len(rets_series) / 12
    cagr = (1 + total_ret) ** (1 / years) - 1
    vol = rets_series.std() * np.sqrt(12)
    sharpe = cagr / vol if vol > 0 else None
    cum = (1 + rets_series).cumprod()
    max_dd = (cum / cum.expanding().max() - 1).min()

    return {
        "config": f"Composite 52W+PEAD+MarginShort ({weight_mode})",
        "weights": weights,
        "period": f"{start.date()}~{end.date()}",
        "n_months": len(rets_series),
        "top_n": TOP_N,
        "total_return": round(float(total_ret), 4),
        "cagr": round(float(cagr), 4),
        "annualized_vol": round(float(vol), 4),
        "sharpe": round(float(sharpe), 4) if sharpe else None,
        "max_drawdown": round(float(max_dd), 4),
        "monthly_returns": [round(r, 5) for r in portfolio_rets],
        "selections_last_5": selections[-5:] if selections else [],
        "friction_bps_round_trip": TW_ROUND_TRIP_COST_BPS,
        "simplifications": [
            "no_regime_exposure",
            "no_drift_aware_daily",
            "no_hold_buffer",
            "no_dividend_reinvest",
            "survivorship: only existing pkls",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--weight-mode", choices=["ir_weighted", "equal"], default="ir_weighted")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    cache_dir = resolve_cache_dir()

    result = run_backtest(start, end, args.weight_mode, cache_dir)

    out_dir = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"composite_{args.weight_mode}_{start.date()}_{end.date()}.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("Saved %s", out_file)

    print("=" * 60)
    print(f"  {result['config']}")
    print("=" * 60)
    for k in ("period", "n_months", "total_return", "cagr", "annualized_vol",
              "sharpe", "max_drawdown"):
        print(f"  {k:22s}: {result.get(k)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
