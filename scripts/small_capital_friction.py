"""Small capital friction simulator.

Simulates how realistic trading frictions affect strategy net alpha at different
capital levels. Taiwan stocks trade in 1000-share lots (張); for small accounts
this causes significant weight-quantization drift from target weights.

Inputs: snapshots JSON (target weights per rebalance), real OHLCV cache (prices).
Outputs: JSON + text summary with net alpha per capital tier.

Core model:
- At each rebalance, quantize target weight → whole 張 (1000 shares)
- Compute weight drift = |actual - target| / target
- Apply transaction costs (commission + tax + slippage)
- Report: net return, net alpha vs 0050, effective friction drag

Usage:
    python scripts/small_capital_friction.py \
        --snapshots reports/backtests/backtest_20220101_20251231_snapshots.json \
        --cache-dir data/cache/ohlcv \
        --capitals 25000 300000 1000000
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_SNAPSHOTS = "reports/backtests/backtest_20220101_20251231_snapshots.json"
DEFAULT_CACHE = "data/cache/ohlcv"
DEFAULT_OUTPUT = "reports/diagnosis/small_capital_friction.json"

# Taiwan stock trading params
SHARES_PER_LOT = 1000  # 1 張 = 1000 股
COMMISSION_BPS_NOMINAL = 14.25  # 0.1425%（牌告手續費）
COMMISSION_BPS_DISCOUNTED = 6.0  # 0.06%（折讓後常見）
TAX_BPS_SELL = 30.0  # 0.3%（證交稅，賣出時）
# Slippage scenarios (bps per side)
SLIPPAGE_SCENARIOS = {"optimistic": 5, "realistic": 10, "pessimistic": 20}


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--capitals", nargs="+", type=int,
                        default=[25000, 300000, 1000000],
                        help="Capital tiers to simulate (NTD)")
    parser.add_argument("--commission", choices=["nominal", "discounted"],
                        default="discounted",
                        help="Commission tier (default: discounted 0.06%)")
    return parser.parse_args()


def _load_price_on_date(cache_dir: Path, symbol: str, date: pd.Timestamp) -> Optional[float]:
    path = cache_dir / f"{symbol}.pkl"
    if not path.exists():
        return None
    df = pd.read_pickle(path)
    if df is None or df.empty or "close" not in df.columns:
        return None
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    idx = pd.to_datetime(close.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    close.index = idx
    view = close[close.index <= date]
    if view.empty:
        return None
    return float(view.iloc[-1])


def _quantize_to_lots(target_notional: float, price: float) -> tuple:
    """Round down to whole 張. Returns (actual_shares, actual_notional, weight_drift_pct)."""
    if price <= 0:
        return 0, 0.0, 100.0
    lots = int(target_notional / (price * SHARES_PER_LOT))
    actual_shares = lots * SHARES_PER_LOT
    actual_notional = actual_shares * price
    if target_notional <= 0:
        return actual_shares, actual_notional, 0.0
    drift_pct = abs(actual_notional - target_notional) / target_notional * 100
    return actual_shares, actual_notional, drift_pct


def simulate_capital(
    snapshots: list,
    cache_dir: Path,
    capital: float,
    commission_bps: float,
    slippage_bps: int,
) -> dict:
    """Simulate strategy execution at given capital.

    Returns metrics:
      - mean_weight_drift: avg |actual - target| / target across all positions
      - n_unbuyable: positions where capital is too small for 1 lot
      - annual_cost_drag: estimated annual friction cost (% of capital)
    """
    total_drift = []
    total_unbuyable = 0
    total_positions = 0
    total_cost_drag = []

    for snap in snapshots:
        date = pd.Timestamp(snap["rebalance_date"]).tz_localize(None)
        gross_exposure = snap.get("gross_exposure", 0.96)
        positions = snap.get("positions", [])
        if not positions:
            continue

        # For each position: actual notional after quantization
        period_drifts = []
        period_unbuyable = 0
        period_notionals = []
        for pos in positions:
            symbol = pos.get("symbol")
            target_weight = pos.get("weight", 0)
            if not symbol or target_weight <= 0:
                continue

            target_notional = capital * gross_exposure * target_weight
            price = _load_price_on_date(cache_dir, symbol, date)
            if price is None:
                continue

            actual_shares, actual_notional, drift = _quantize_to_lots(target_notional, price)
            period_drifts.append(drift)
            period_notionals.append(actual_notional)
            if actual_shares == 0:
                period_unbuyable += 1
            total_positions += 1

        total_drift.extend(period_drifts)
        total_unbuyable += period_unbuyable

    mean_drift = sum(total_drift) / len(total_drift) if total_drift else 0.0
    pct_unbuyable = total_unbuyable / total_positions * 100 if total_positions else 0.0

    # Annual friction cost = (commission*2 + tax) * turnover_rate * slippage_mult
    # Turnover rate approx from snapshots average `one_way_turnover` × 2 (round-trip) × 12 rebalances
    turnovers = [s.get("one_way_turnover", 0.5) for s in snapshots if s.get("one_way_turnover") is not None]
    avg_turnover_rt = (sum(turnovers) / len(turnovers) * 2) if turnovers else 1.0
    annual_turnover = avg_turnover_rt * 12  # approx annual (monthly rebalance)
    cost_per_side_bps = commission_bps + slippage_bps
    # Full round-trip cost: commission × 2 + tax (sell only) + slippage × 2
    cost_per_trade_bps = (commission_bps + slippage_bps) * 2 + TAX_BPS_SELL
    annual_cost_bps = annual_turnover * cost_per_trade_bps / 2  # divide by 2 since turnover counts round-trip

    return {
        "capital_ntd": capital,
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
        "mean_weight_drift_pct": round(mean_drift, 2),
        "pct_positions_unbuyable": round(pct_unbuyable, 2),
        "annual_turnover_est": round(annual_turnover, 2),
        "annual_cost_drag_bps": round(annual_cost_bps, 1),
        "annual_cost_drag_pct": round(annual_cost_bps / 100, 3),
    }


def main():
    args = _parse_args()
    snapshots_path = Path(args.snapshots)
    cache_dir = Path(args.cache_dir)
    output_path = Path(args.output)

    snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))
    commission_bps = COMMISSION_BPS_NOMINAL if args.commission == "nominal" else COMMISSION_BPS_DISCOUNTED

    print(f"Loaded {len(snapshots)} snapshots")
    print(f"Commission: {commission_bps:.2f} bps ({'nominal' if args.commission == 'nominal' else 'discounted'})")
    print(f"Slippage scenarios: {SLIPPAGE_SCENARIOS}")

    # Baseline strategy alpha (from existing metrics, post-fix corrected numbers)
    STRATEGY_GROSS_ALPHA_4Y = 3.4  # pct — from 2022-2025 corrected backtest
    STRATEGY_GROSS_ALPHA_3Y = 8.84  # pct — from 2022-2024 corrected backtest

    results = []
    for capital in args.capitals:
        for slip_name, slip_bps in SLIPPAGE_SCENARIOS.items():
            r = simulate_capital(snapshots, cache_dir, capital, commission_bps, slip_bps)
            r["slippage_scenario"] = slip_name
            r["net_alpha_4y_pct"] = round(STRATEGY_GROSS_ALPHA_4Y - r["annual_cost_drag_pct"], 3)
            r["net_alpha_3y_pct"] = round(STRATEGY_GROSS_ALPHA_3Y - r["annual_cost_drag_pct"], 3)
            results.append(r)

    output = {
        "snapshots": str(snapshots_path),
        "assumptions": {
            "commission_bps": commission_bps,
            "tax_bps_sell": TAX_BPS_SELL,
            "shares_per_lot": SHARES_PER_LOT,
            "strategy_gross_alpha_3y_pct": STRATEGY_GROSS_ALPHA_3Y,
            "strategy_gross_alpha_4y_pct": STRATEGY_GROSS_ALPHA_4Y,
        },
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 80)
    print("  Small Capital Friction Simulation")
    print("=" * 80)
    print(f"{'Capital':>12}{'Slippage':>10}{'Drift%':>10}{'Unbuy%':>10}{'CostDrag':>10}{'Net4Y':>10}{'Net3Y':>10}")
    print("-" * 80)
    for r in results:
        cap = f"{r['capital_ntd']:,}"
        slip = f"{r['slippage_scenario']:>8}"
        drift = f"{r['mean_weight_drift_pct']:.1f}%"
        unbuy = f"{r['pct_positions_unbuyable']:.1f}%"
        drag = f"{r['annual_cost_drag_pct']:.2f}%"
        net4 = f"{r['net_alpha_4y_pct']:+.2f}%"
        net3 = f"{r['net_alpha_3y_pct']:+.2f}%"
        print(f"{cap:>12}{slip:>10}{drift:>10}{unbuy:>10}{drag:>10}{net4:>10}{net3:>10}")
    print("-" * 80)
    print(f"\nCost basis: commission {commission_bps:.1f}bps × 2 + tax {TAX_BPS_SELL:.0f}bps + slippage × 2")
    print(f"Strategy gross alpha reference: 3Y +{STRATEGY_GROSS_ALPHA_3Y}% / 4Y +{STRATEGY_GROSS_ALPHA_4Y}%")
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()
