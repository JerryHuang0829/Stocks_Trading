"""Regime improvement simulation - research only, no production code changes."""

import json
import pickle
import pandas as pd
import numpy as np
from pathlib import Path


def main():
    # Load 0050 OHLCV from cache
    cache_dir = Path("data/cache/ohlcv")
    with open(cache_dir / "0050.pkl", "rb") as f:
        df = pickle.load(f)

    print(f"0050 data: {len(df)} rows, {df.index[0].date()} to {df.index[-1].date()}")

    # Calculate indicators
    df["sma_fast"] = df["close"].rolling(20).mean()
    df["sma_slow"] = df["close"].rolling(60).mean()
    df["sma200"] = df["close"].rolling(200).mean()

    # ADX calculation (Wilder smoothing)
    period = 14
    high, low, close = df["high"], df["low"], df["close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    plus_dm_raw = high.diff()
    minus_dm_raw = -low.diff()
    plus_dm_raw[plus_dm_raw < 0] = 0
    minus_dm_raw[minus_dm_raw < 0] = 0
    mask_plus = plus_dm_raw > minus_dm_raw
    mask_minus = minus_dm_raw > plus_dm_raw
    plus_dm_final = plus_dm_raw.where(mask_plus, 0)
    minus_dm_final = minus_dm_raw.where(mask_minus, 0)

    plus_di = 100 * plus_dm_final.rolling(period).mean() / atr
    minus_di = 100 * minus_dm_final.rolling(period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    df["adx"] = dx.rolling(period).mean()

    # Load snapshots for rebalance dates
    snap_path = "reports/backtests/backtest_20220101_20251231_snapshots.json"
    with open(snap_path, encoding="utf-8") as f:
        snaps = json.load(f)

    print(f"Rebalance months: {len(snaps)}\n")

    exposure_map = {"risk_on": 0.96, "caution": 0.70, "risk_off": 0.35}
    results = []

    for snap in snaps:
        reb_date = pd.Timestamp(snap["rebalance_date"])
        if reb_date.tzinfo is None:
            reb_date = reb_date.tz_localize("UTC")
        mask = df.index <= reb_date
        if not mask.any():
            continue
        idx = df.index[mask][-1]
        row = df.loc[idx]

        c = float(row["close"])
        sma20 = float(row["sma_fast"]) if pd.notna(row["sma_fast"]) else c
        sma60 = float(row["sma_slow"]) if pd.notna(row["sma_slow"]) else c
        sma200 = float(row["sma200"]) if pd.notna(row["sma200"]) else None
        adx_val = float(row["adx"]) if pd.notna(row["adx"]) else 0

        # Current regime logic
        if adx_val < 20:
            regime = "ranging"
        elif adx_val < 25:
            regime = "ranging"
        else:
            regime = "trending_up" if sma20 > sma60 else "trending_down"

        # Current signal
        if c < sma60 or regime == "trending_down":
            current = "risk_off"
        elif c < sma20 or regime == "ranging":
            current = "caution"
        else:
            current = "risk_on"

        # === Proposed A: SMA200 floor protection ===
        # If close > SMA200, never go below caution
        prop_a = current
        if sma200 and c > sma200 and prop_a == "risk_off":
            prop_a = "caution"

        # === Proposed B: Price position primary ===
        # close > SMA60 AND close > SMA20 AND SMA20 > SMA60 -> risk_on
        # close > SMA60 -> caution
        # else -> risk_off
        if c > sma60 and c > sma20 and sma20 > sma60:
            prop_b = "risk_on"
        elif c > sma60:
            prop_b = "caution"
        else:
            prop_b = "risk_off"

        # === Proposed C: SMA200 + trend hybrid ===
        # Keep current, but if close > SMA200 AND SMA200 rising -> floor = caution
        # If also SMA20 > SMA60 and close > SMA20 -> upgrade to risk_on
        sma200_rising = False
        if sma200:
            lookback_20 = df.loc[:idx].tail(21)
            if len(lookback_20) >= 21:
                sma200_prev = lookback_20["sma200"].iloc[0]
                if pd.notna(sma200_prev) and sma200 > sma200_prev:
                    sma200_rising = True

        prop_c = current
        if sma200 and c > sma200 and sma200_rising:
            if prop_c == "risk_off":
                prop_c = "caution"
            if c > sma20 and sma20 > sma60:
                prop_c = "risk_on"

        actual = snap["market_signal"]
        results.append({
            "date": reb_date.strftime("%Y-%m"),
            "close": c, "sma20": sma20, "sma60": sma60,
            "sma200": sma200, "adx": adx_val, "regime": regime,
            "actual": actual, "current": current,
            "prop_a": prop_a, "prop_b": prop_b, "prop_c": prop_c,
        })

    # Display
    print(f"{'Date':>8} {'ADX':>5} {'Close':>7} {'SMA60':>7} {'SMA200':>7} | "
          f"{'Current':>9} {'A(Floor)':>9} {'B(Price)':>9} {'C(Hybrid)':>9}")
    print("-" * 95)
    for r in results:
        s200 = f"{r['sma200']:7.1f}" if r["sma200"] else "    N/A"
        ca = "*" if r["prop_a"] != r["current"] else " "
        cb = "*" if r["prop_b"] != r["current"] else " "
        cc = "*" if r["prop_c"] != r["current"] else " "
        print(f"{r['date']:>8} {r['adx']:5.1f} {r['close']:7.1f} {r['sma60']:7.1f} {s200} | "
              f"{r['current']:>9} {ca}{r['prop_a']:>8} {cb}{r['prop_b']:>8} {cc}{r['prop_c']:>8}")

    # Summary tables
    def print_summary(label, data):
        print(f"\n=== {label} ===")
        for name, key in [("Current", "current"), ("A: SMA200 floor", "prop_a"),
                          ("B: Price primary", "prop_b"), ("C: SMA200+ADX", "prop_c")]:
            signals = [r[key] for r in data]
            avg_exp = np.mean([exposure_map[s] for s in signals])
            ro = signals.count("risk_on")
            ca = signals.count("caution")
            rf = signals.count("risk_off")
            print(f"  {name:20s}: risk_on={ro:2d} caution={ca:2d} risk_off={rf:2d} "
                  f"| avg_exposure={avg_exp:.1%}")

    print_summary("4Y Summary (2022-2025, 48 months)", results)
    print_summary("2025 Only (12 months, 0050 +37%)", [r for r in results if r["date"].startswith("2025")])
    print_summary("2022 Bear Market (should stay defensive)", [r for r in results if r["date"].startswith("2022")])
    print_summary("2024-H1 Bull (0050 strong rally)", [r for r in results if r["date"] in ("2024-01","2024-02","2024-03","2024-04","2024-05","2024-06")])

    # Estimate Alpha impact for 2025
    print("\n=== Estimated 2025 Alpha Impact ===")
    r2025 = [r for r in results if r["date"].startswith("2025")]
    for name, key in [("Current", "current"), ("A: SMA200 floor", "prop_a"),
                      ("B: Price primary", "prop_b"), ("C: SMA200+ADX", "prop_c")]:
        exposures = [exposure_map[r[key]] for r in r2025]
        avg_exp = np.mean(exposures)
        # Rough estimate: if benchmark did +37%, alpha drag = (1 - avg_exposure) * benchmark
        alpha_drag = (1 - avg_exp) * 0.37
        print(f"  {name:20s}: avg_exposure={avg_exp:.1%}, "
              f"estimated alpha_drag=-{alpha_drag:.1%}, "
              f"improvement vs current: +{(avg_exp - np.mean([exposure_map[r['current']] for r in r2025])) * 0.37:.1%}")


if __name__ == "__main__":
    main()
