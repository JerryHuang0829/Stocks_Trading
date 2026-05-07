"""Cache Health Report — 檢查 data/cache/ 的完整性和���蓋率。

Usage:
    python scripts/cache_health.py
    docker compose run --rm --entrypoint python portfolio-bot scripts/cache_health.py
"""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime

import pandas as pd


def main():
    # Resolve cache and data paths
    project_root = pathlib.Path(__file__).resolve().parent.parent
    cache_dir = pathlib.Path(os.environ.get("DATA_CACHE_DIR", project_root / "data" / "cache"))

    print(f"=== Cache Health Report ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===\n")

    # --- Load stock_info (ground truth) ---
    stock_info_csv = cache_dir / "stock_info" / "stock_info_snapshot.csv"
    if not stock_info_csv.exists():
        print(f"ERROR: {stock_info_csv} not found. Run Paper Trading once to generate it.")
        sys.exit(1)

    si = pd.read_csv(stock_info_csv)
    si["stock_id"] = si["stock_id"].astype(str).str.strip()

    # Tradeable: 4-digit codes, exclude ETF/warrants
    tradeable = si[si["stock_id"].str.fullmatch(r"\d{4}")]
    tradeable = tradeable[~tradeable["stock_id"].str.startswith("00")]
    tradeable_set = set(tradeable["stock_id"])

    twse_count = len(tradeable[tradeable["type"].str.contains("twse", case=False, na=False)])
    tpex_count = len(tradeable[tradeable["type"].str.contains("tpex", case=False, na=False)])

    print(f"台股可交易: {len(tradeable_set)} 支（TWSE {twse_count} + TPEX {tpex_count}，排除 ETF/權證）")
    print()

    # --- OHLCV coverage ---
    ohlcv_dir = cache_dir / "ohlcv"
    ohlcv_cached = {p.stem for p in ohlcv_dir.glob("*.pkl")} if ohlcv_dir.exists() else set()
    ohlcv_tradeable = ohlcv_cached & tradeable_set
    ohlcv_missing = tradeable_set - ohlcv_cached

    # Check latest date (read one file)
    ohlcv_latest = "unknown"
    for sym in ["2330", "2317", "0050"]:
        p = ohlcv_dir / f"{sym}.pkl"
        if p.exists():
            try:
                df = pd.read_pickle(p)
                ohlcv_latest = str(df.index.max().date())
                break
            except Exception:
                continue

    print(f"OHLCV:")
    print(f"  cached:    {len(ohlcv_tradeable)} / {len(tradeable_set)} ({100*len(ohlcv_tradeable)/len(tradeable_set):.1f}%)")
    print(f"  latest:    {ohlcv_latest}")
    print(f"  missing:   {len(ohlcv_missing)} 支")

    # --- Revenue coverage ---
    rev_dir = cache_dir / "revenue"
    rev_cached = set()
    rev_real = set()
    rev_sentinel = set()

    if rev_dir.exists():
        for p in rev_dir.glob("*.pkl"):
            sym = p.stem
            rev_cached.add(sym)
            try:
                df = pd.read_pickle(p)
                if not df.empty:
                    rev_real.add(sym)
                else:
                    rev_sentinel.add(sym)
            except Exception:
                rev_sentinel.add(sym)

    rev_tradeable_real = rev_real & tradeable_set
    rev_missing = tradeable_set - rev_real

    print()
    print(f"Revenue:")
    print(f"  real data: {len(rev_tradeable_real)} / {len(tradeable_set)} ({100*len(rev_tradeable_real)/len(tradeable_set):.1f}%)")
    print(f"  sentinel:  {len(rev_sentinel)} (FinMind 抓過但無資料)")
    print(f"  missing:   {len(rev_missing)} 支")

    # --- Institutional coverage ---
    inst_dir = cache_dir / "institutional"
    inst_cached = {p.stem for p in inst_dir.glob("*.pkl")} if inst_dir.exists() else set()
    print()
    print(f"Institutional (weight=0%, 不影響排名):")
    print(f"  cached:    {len(inst_cached & tradeable_set)}")

    # --- Top-80 coverage (most important) ---
    print()
    print("=" * 50)
    print("  Top-80 候選池覆蓋率（最重要的指標）")
    print("=" * 50)

    # Use OHLCV cache close×volume 20-day average (same as strategy)
    try:
        size_proxy: dict[str, float] = {}
        for sym in ohlcv_cached & tradeable_set:
            p = ohlcv_dir / f"{sym}.pkl"
            try:
                df = pd.read_pickle(p)
                if len(df) >= 5:
                    tv = (df["close"] * df["volume"]).tail(20).mean()
                    size_proxy[sym] = float(tv) if pd.notna(tv) else 0.0
            except Exception:
                continue

        if size_proxy:
            ranked = sorted(size_proxy.items(), key=lambda x: -x[1])
            top80 = [r[0] for r in ranked[:80]]

            top80_ohlcv_missing = [s for s in top80 if s not in ohlcv_cached]
            top80_rev_missing = [s for s in top80 if s not in rev_real]

            print()
            print(f"  OHLCV:   {80 - len(top80_ohlcv_missing)}/80 ({100*(80-len(top80_ohlcv_missing))/80:.1f}%)")
            if top80_ohlcv_missing:
                print(f"    缺失:  {', '.join(top80_ohlcv_missing[:10])}")
            else:
                print(f"    ✅ 全部覆蓋")

            print(f"  Revenue: {80 - len(top80_rev_missing)}/80 ({100*(80-len(top80_rev_missing))/80:.1f}%)")
            if top80_rev_missing:
                print(f"    缺失:  {', '.join(top80_rev_missing[:10])}")
                if len(top80_rev_missing) > 10:
                    print(f"           ... 共 {len(top80_rev_missing)} 支")
            else:
                print(f"    ✅ 全部覆蓋")
        else:
            print("  ⚠️ OHLCV cache 不足，無法計算 top-80 排名")
    except Exception as exc:
        print(f"  ⚠️ Top-80 分析失敗: {exc}")

    # --- Meta dates ---
    print()
    print("Cache 更新時間:")
    for ds in ["stock_info", "market_value", "dividends"]:
        meta_path = cache_dir / ds / "_global.meta"
        if meta_path.exists():
            print(f"  {ds}: {meta_path.read_text().strip()}")
        else:
            print(f"  {ds}: not found")

    print()


if __name__ == "__main__":
    main()
