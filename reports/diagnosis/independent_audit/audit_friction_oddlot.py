"""Independent audit: small capital friction WITH odd-lot (盤中零股) simulation.

Task C: extend the prior-round whole-lot model to include 1-share odd-lot
trading. Three groups:
  - whole_lot_feasible: can afford at least 1 lot
  - odd_lot_only: cannot afford 1 lot but can afford 1 share
  - cannot_afford: cannot afford 1 share

Also computes capital_utilization_rate = actual_deployed / target_deployed
(not 'drift', which can be 100% when you can't afford anything).

And verifies the turnover assumption:
  avg_turnover_per_rebalance * 2 * 12 == total_one_way_turnover * 2 / 4_years?
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SNAPSHOTS = Path("reports/backtests/backtest_20220101_20251231_snapshots.json")
METRICS_4Y = Path("reports/backtests/backtest_20220101_20251231_metrics.json")
METRICS_2025 = Path("reports/backtests/backtest_20250101_20251231_metrics.json")
CACHE_DIR = Path("data/cache/ohlcv")
OUT_PATH = Path("reports/diagnosis/independent_audit/friction_oddlot.json")

SHARES_PER_LOT = 1000  # 台股 1 張 = 1000 股
COMMISSION_BPS = 6.0  # 0.06% discounted
TAX_BPS_SELL = 30.0  # 0.3% on sell
SLIPPAGE_BPS = 10  # realistic per side
COMMISSION_MIN_NTD = 20  # 最低手續費 20 元

PRICE_CACHE: dict[str, pd.Series] = {}


def load_close(sym: str) -> pd.Series | None:
    if sym in PRICE_CACHE:
        return PRICE_CACHE[sym]
    path = CACHE_DIR / f"{sym}.pkl"
    if not path.exists():
        PRICE_CACHE[sym] = None
        return None
    df = pd.read_pickle(path)
    if df is None or df.empty or "close" not in df.columns:
        PRICE_CACHE[sym] = None
        return None
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    close = close.sort_index()
    PRICE_CACHE[sym] = close
    return close


def price_on_or_before(sym: str, date: pd.Timestamp) -> float | None:
    close = load_close(sym)
    if close is None or close.empty:
        return None
    view = close[close.index <= date]
    if view.empty:
        return None
    return float(view.iloc[-1])


def quantize(target_ntd: float, price: float, allow_odd_lot: bool) -> dict:
    """Return actual deployed NTD, shares, category, and effective commission."""
    if price <= 0 or target_ntd <= 0:
        return {"actual_ntd": 0.0, "shares": 0, "category": "cannot_afford",
                "per_side_commission_ntd": 0.0}
    # Try whole lot first
    lot_cost = price * SHARES_PER_LOT
    lots = int(target_ntd // lot_cost)
    if lots > 0:
        actual_ntd = lots * lot_cost
        commission = max(actual_ntd * COMMISSION_BPS / 10000, COMMISSION_MIN_NTD)
        return {"actual_ntd": actual_ntd, "shares": lots * SHARES_PER_LOT,
                "category": "whole_lot", "per_side_commission_ntd": commission}
    # Cannot afford a whole lot. Try odd lot.
    if not allow_odd_lot:
        return {"actual_ntd": 0.0, "shares": 0, "category": "cannot_afford",
                "per_side_commission_ntd": 0.0}
    shares = int(target_ntd // price)
    if shares <= 0:
        return {"actual_ntd": 0.0, "shares": 0, "category": "cannot_afford",
                "per_side_commission_ntd": 0.0}
    actual_ntd = shares * price
    commission = max(actual_ntd * COMMISSION_BPS / 10000, COMMISSION_MIN_NTD)
    return {"actual_ntd": actual_ntd, "shares": shares, "category": "odd_lot_only",
            "per_side_commission_ntd": commission}


def simulate(snapshots: list[dict], capital: float, allow_odd_lot: bool) -> dict:
    """Simulate portfolio execution, tracking utilization & category breakdown."""
    categories = {"whole_lot": 0, "odd_lot_only": 0, "cannot_afford": 0}
    total_target = 0.0
    total_actual = 0.0
    per_period_util = []
    total_commission_paid = 0.0
    total_tax_paid = 0.0
    total_slippage = 0.0
    n_rebalances = len(snapshots)

    for snap in snapshots:
        date = pd.Timestamp(snap["rebalance_date"]).tz_localize(None)
        gross_exposure = snap.get("gross_exposure", 0.96)
        one_way_turnover = snap.get("one_way_turnover", 0.5)
        positions = snap.get("positions", [])
        if not positions:
            continue

        period_target = 0.0
        period_actual = 0.0
        for pos in positions:
            sym = pos.get("symbol")
            w = pos.get("weight", 0)
            if not sym or w <= 0:
                continue
            target_ntd = capital * gross_exposure * w
            price = price_on_or_before(sym, date)
            if price is None:
                continue
            q = quantize(target_ntd, price, allow_odd_lot)
            categories[q["category"]] += 1
            period_target += target_ntd
            period_actual += q["actual_ntd"]

        total_target += period_target
        total_actual += period_actual
        if period_target > 0:
            per_period_util.append(period_actual / period_target)

        # Cost for this rebalance: one_way_turnover is fraction of portfolio traded
        # Dollar traded per side = capital * gross_exposure * one_way_turnover
        dollar_traded = capital * gross_exposure * one_way_turnover
        # Buy-side: commission + slippage
        buy_commission = max(dollar_traded * COMMISSION_BPS / 10000, COMMISSION_MIN_NTD) if dollar_traded > 0 else 0
        buy_slippage = dollar_traded * SLIPPAGE_BPS / 10000
        # Sell-side: commission + slippage + tax
        sell_commission = max(dollar_traded * COMMISSION_BPS / 10000, COMMISSION_MIN_NTD) if dollar_traded > 0 else 0
        sell_slippage = dollar_traded * SLIPPAGE_BPS / 10000
        sell_tax = dollar_traded * TAX_BPS_SELL / 10000
        total_commission_paid += buy_commission + sell_commission
        total_slippage += buy_slippage + sell_slippage
        total_tax_paid += sell_tax

    mean_util = sum(per_period_util) / len(per_period_util) if per_period_util else 0.0
    total_cost = total_commission_paid + total_tax_paid + total_slippage

    # Effective annual cost drag
    years = n_rebalances / 12.0
    annual_cost_drag_pct = (total_cost / capital / years) * 100 if years > 0 else 0

    return {
        "capital": capital,
        "allow_odd_lot": allow_odd_lot,
        "mean_capital_utilization": round(mean_util, 4),
        "target_total_ntd": round(total_target, 2),
        "actual_total_ntd": round(total_actual, 2),
        "overall_utilization": round(total_actual / total_target, 4) if total_target > 0 else 0,
        "category_counts": categories,
        "total_positions_simulated": sum(categories.values()),
        "pct_cannot_afford": round(categories["cannot_afford"] / max(sum(categories.values()), 1) * 100, 2),
        "pct_odd_lot_only": round(categories["odd_lot_only"] / max(sum(categories.values()), 1) * 100, 2),
        "pct_whole_lot": round(categories["whole_lot"] / max(sum(categories.values()), 1) * 100, 2),
        "total_commission_paid_ntd": round(total_commission_paid, 2),
        "total_tax_paid_ntd": round(total_tax_paid, 2),
        "total_slippage_ntd": round(total_slippage, 2),
        "total_cost_ntd": round(total_cost, 2),
        "annual_cost_drag_pct": round(annual_cost_drag_pct, 4),
    }


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshots = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))
    m4y = json.loads(METRICS_4Y.read_text(encoding="utf-8"))
    m2025 = json.loads(METRICS_2025.read_text(encoding="utf-8"))

    print(f"Loaded {len(snapshots)} snapshots")
    print(f"4Y metrics: sharpe={m4y['sharpe_ratio']} ann_alpha={m4y['annualized_alpha']:+.4f}"
          f" bench_ann_ret={m4y['benchmark_annualized_return']:+.4f}")
    print(f"2025 metrics: sharpe={m2025['sharpe_ratio']} ann_alpha={m2025['annualized_alpha']:+.4f}"
          f" bench_ann_ret={m2025['benchmark_annualized_return']:+.4f}")

    # Turnover assumption verification
    avg_tov_per_rb = m4y.get("avg_turnover_per_rebalance", 0)
    total_one_way = m4y.get("total_one_way_turnover", 0)
    n_rebalances_4y = len(snapshots)
    # Derivation 1: avg × 2 × 12 = annual round-trip turnover (rough)
    d1 = avg_tov_per_rb * 2 * 12
    # Derivation 2: total_one_way / years × 2
    years_4y = n_rebalances_4y / 12.0
    d2 = total_one_way / years_4y * 2
    print(f"\nTurnover check: avg_per_rb×2×12 = {d1:.4f}, total_ow/years×2 = {d2:.4f}")
    print(f"  total_one_way = {total_one_way}, n_rebalances = {n_rebalances_4y}, years = {years_4y:.2f}")

    # Run simulations
    capitals = [25000, 100000, 300000, 1000000, 3000000]
    results = []
    for cap in capitals:
        whole_only = simulate(snapshots, cap, allow_odd_lot=False)
        with_odd = simulate(snapshots, cap, allow_odd_lot=True)
        # Gross alpha baselines (from metrics + engine cost add-back)
        # engine turnover_cost = 0.0047 per round-trip × one_way_turnover × n_rebalances / years
        # approximate gross alpha = net alpha + engine cost drag
        engine_cost_4y = (total_one_way * 0.0047 + total_one_way * 20 / 10000) / years_4y  # both sides slippage
        engine_cost_2025 = engine_cost_4y  # approximate, assuming similar turnover
        gross_alpha_4y = m4y["annualized_alpha"] + engine_cost_4y
        gross_alpha_2025 = m2025["annualized_alpha"] + engine_cost_2025
        # Net alpha under new friction model
        net_alpha_4y = gross_alpha_4y - with_odd["annual_cost_drag_pct"] / 100
        net_alpha_2025 = gross_alpha_2025 - with_odd["annual_cost_drag_pct"] / 100
        results.append({
            "capital": cap,
            "whole_lot_only": whole_only,
            "with_odd_lot": with_odd,
            "gross_alpha_4y_estimate": round(gross_alpha_4y, 4),
            "gross_alpha_2025_estimate": round(gross_alpha_2025, 4),
            "net_alpha_4y_after_friction": round(net_alpha_4y, 4),
            "net_alpha_2025_after_friction": round(net_alpha_2025, 4),
        })

    # Print summary
    print("\n" + "=" * 110)
    print("  WHOLE-LOT ONLY (no odd-lot support, matches prior-round)")
    print("=" * 110)
    print(f"{'Capital':>10}{'Util':>8}{'%CanNotAfford':>16}{'%WholeLot':>12}{'CostDrag%':>12}"
          f"{'Net4Y':>10}{'Net2025':>10}")
    for r in results:
        w = r["whole_lot_only"]
        print(f"{r['capital']:>10,}{w['mean_capital_utilization']:>8.3f}"
              f"{w['pct_cannot_afford']:>15.1f}%{w['pct_whole_lot']:>11.1f}%"
              f"{w['annual_cost_drag_pct']:>11.3f}%"
              f"{r['net_alpha_4y_after_friction']:>+10.3%}"
              f"{r['net_alpha_2025_after_friction']:>+10.3%}")

    print("\n" + "=" * 110)
    print("  WITH ODD-LOT (盤中零股, low 20 NTD commission floor)")
    print("=" * 110)
    print(f"{'Capital':>10}{'Util':>8}{'%CanNotAfford':>16}{'%OddLot':>10}{'%WholeLot':>12}"
          f"{'CostDrag%':>12}{'Net4Y':>10}{'Net2025':>10}")
    for r in results:
        w = r["with_odd_lot"]
        print(f"{r['capital']:>10,}{w['mean_capital_utilization']:>8.3f}"
              f"{w['pct_cannot_afford']:>15.1f}%{w['pct_odd_lot_only']:>9.1f}%"
              f"{w['pct_whole_lot']:>11.1f}%{w['annual_cost_drag_pct']:>11.3f}%"
              f"{r['net_alpha_4y_after_friction']:>+10.3%}"
              f"{r['net_alpha_2025_after_friction']:>+10.3%}")

    # Additional: top_n = 4 exploration (simulate hypothetical 4-stock concentration)
    print(f"\nNote: 4Y net alpha baseline = {m4y['annualized_alpha']:+.4f} (published)")
    print(f"      estimated engine cost drag = {engine_cost_4y:+.4f} (added back)")
    print(f"      estimated gross alpha 4Y  = {gross_alpha_4y:+.4f}")
    print(f"      2025 gross alpha estimate = {gross_alpha_2025:+.4f}")

    out = {
        "config": {
            "shares_per_lot": SHARES_PER_LOT,
            "commission_bps": COMMISSION_BPS,
            "tax_bps_sell": TAX_BPS_SELL,
            "slippage_bps": SLIPPAGE_BPS,
            "commission_min_ntd": COMMISSION_MIN_NTD,
        },
        "turnover_check": {
            "avg_turnover_per_rebalance": avg_tov_per_rb,
            "total_one_way_turnover": total_one_way,
            "derivation_a_avg_x2x12": round(d1, 4),
            "derivation_b_total_over_years_x2": round(d2, 4),
        },
        "by_capital": results,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
