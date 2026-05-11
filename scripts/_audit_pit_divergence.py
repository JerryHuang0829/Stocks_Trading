"""Phase 0 audit: B3 score Spearman divergence root-cause (FAST version).

R1 reported (latest mv vs as-of mv ranking Spearman):
    2020-01-13: 0.9675
    2025-11-12: 0.9945

R2 reported:
    2020-01-13: 0.9633  (diff -0.0042)
    2025-11-12: 0.9914  (diff -0.0031)

Both rounds reportedly used a cum_foreign / mv simulator (no full composite
pipeline since rank_stability sub-signal would add O(n^2) cost). The diff
~0.003-0.004 most likely comes from universe-filter handling.

This audit runs the cum_only simulator with 4 filter variants and prints
which variant matches which reference number.

Filter variants tested:
  V1: drop sym if mv missing OR mv <= 0 (per latest+asof independently)
      → each scenario keeps its own native universe; intersect at compare
  V2: require BOTH latest AND asof mv exist (intersection up-front)
      → both scenarios use identical universe
  V3: drop sym if mv missing in either; ALSO drop if institutional cache
      < min_history (matches foreign_investor_v2 _compute_symbol_signals guard)
  V4: V3 + drop if mv ratio (latest / asof) > 10x (extreme outlier guard)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
from scipy.stats import spearmanr

from src.features.foreign_investor_v2 import (
    DEFAULT_MIN_HISTORY,
    INSTITUTIONAL_LAG_DAYS,
    _pivot_long_to_wide,
    _truncate_by_date,
)

CACHE_DIR = REPO_ROOT / "data" / "cache"
INST_DIR = CACHE_DIR / "institutional_v2"
OUT_DIR = REPO_ROOT / "reports" / "factor_ic" / "_audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "pit_divergence_2026-05-10.md"

print("[1/5] Loading market_value panel ...", flush=True)
mv_full = pd.read_pickle(CACHE_DIR / "market_value" / "_global.pkl")
mv_full["date"] = pd.to_datetime(mv_full["date"])
mv_full["stock_id"] = mv_full["stock_id"].astype(str)
mv_full["market_value"] = mv_full["market_value"].astype(float)

mv_latest = (
    mv_full.sort_values("date")
    .drop_duplicates("stock_id", keep="last")
    .set_index("stock_id")["market_value"]
    .to_dict()
)


def mv_asof_dict(target_date: pd.Timestamp) -> dict[str, float]:
    sub = mv_full[mv_full["date"] <= target_date]
    if sub.empty:
        return {}
    latest = sub.sort_values("date").drop_duplicates("stock_id", keep="last")
    return dict(zip(latest["stock_id"], latest["market_value"]))


print(f"[2/5] Loading institutional_v2 cache ...", flush=True)
inst_by_symbol: dict[str, pd.DataFrame] = {}
for fp in INST_DIR.glob("*.pkl"):
    sym = fp.stem
    if not (sym.isdigit() and len(sym) == 4):
        continue
    try:
        df = pd.read_pickle(fp)
        if df is not None and not df.empty:
            inst_by_symbol[sym] = df
    except Exception:
        pass
print(f"      Loaded {len(inst_by_symbol)} symbols", flush=True)


def cum_only(target_date: pd.Timestamp, mv_dict: dict, *, require_min_history: bool = True) -> pd.Series:
    """Hand-written cum_foreign / mv simulator (R1 + R2 method)."""
    rows: dict[str, float] = {}
    for sym, df in inst_by_symbol.items():
        wide = _pivot_long_to_wide(df)
        if wide is None:
            continue
        truncated = _truncate_by_date(wide, as_of=target_date, lag_days=INSTITUTIONAL_LAG_DAYS)
        if require_min_history and len(truncated) < DEFAULT_MIN_HISTORY:
            continue
        last20 = truncated.tail(20)
        if len(last20) < 5:
            continue
        mv = mv_dict.get(sym)
        if mv is None or mv <= 0:
            continue
        rows[sym] = float(last20["foreign_net"].sum()) / float(mv)
    return pd.Series(rows)


def metrics(s_lat: pd.Series, s_asof: pd.Series, *, intersection: bool) -> dict:
    if intersection:
        common = s_lat.index.intersection(s_asof.index)
    else:
        common = s_lat.index.intersection(s_asof.index)  # always intersect for compare
    if len(common) < 10:
        return {"n_lat": int(s_lat.size), "n_asof": int(s_asof.size), "n_common": int(len(common)), "skip": True}
    a, b = s_lat.loc[common], s_asof.loc[common]
    rho, _ = spearmanr(a, b)
    n = len(common)
    top_n = max(int(n * 0.10), 1)
    top_a, top_b = set(a.nlargest(top_n).index), set(b.nlargest(top_n).index)
    bot_a, bot_b = set(a.nsmallest(top_n).index), set(b.nsmallest(top_n).index)
    top_j = len(top_a & top_b) / len(top_a | top_b)
    bot_j = len(bot_a & bot_b) / len(bot_a | bot_b)
    return {
        "n_lat": int(s_lat.size),
        "n_asof": int(s_asof.size),
        "n_common": int(n),
        "spearman": float(rho),
        "top10_jaccard": float(top_j),
        "bot10_jaccard": float(bot_j),
    }


print("[3/5] Running 4 filter variants on 2 dates ...", flush=True)

impl_a = {"2020-01-13": 0.9675, "2025-11-12": 0.9945}
impl_b = {"2020-01-13": 0.9633, "2025-11-12": 0.9914}

results: list[dict] = []

for date_str in ["2020-01-13", "2025-11-12"]:
    target = pd.Timestamp(date_str)
    asof = mv_asof_dict(target)
    print(f"  - {date_str} (asof mv: {len(asof)} syms) ...", flush=True)

    # V1: each scenario keeps own native universe (drop sym if its own mv missing)
    s_lat_v1 = cum_only(target, mv_latest, require_min_history=True)
    s_asof_v1 = cum_only(target, asof, require_min_history=True)
    results.append({"date": date_str, "variant": "V1: native universe (mv >0 per scenario)", **metrics(s_lat_v1, s_asof_v1, intersection=True)})

    # V2: pre-intersect mv (require BOTH dicts have sym)
    common_mv = {k for k in mv_latest if k in asof and mv_latest[k] > 0 and asof[k] > 0}
    mv_lat_iso = {k: mv_latest[k] for k in common_mv}
    mv_asof_iso = {k: asof[k] for k in common_mv}
    s_lat_v2 = cum_only(target, mv_lat_iso, require_min_history=True)
    s_asof_v2 = cum_only(target, mv_asof_iso, require_min_history=True)
    results.append({"date": date_str, "variant": "V2: intersected mv universe", **metrics(s_lat_v2, s_asof_v2, intersection=True)})

    # V3: V1 without min_history filter (relaxed)
    s_lat_v3 = cum_only(target, mv_latest, require_min_history=False)
    s_asof_v3 = cum_only(target, asof, require_min_history=False)
    results.append({"date": date_str, "variant": "V3: no min_history (>=5 days only)", **metrics(s_lat_v3, s_asof_v3, intersection=True)})

    # V4: V2 + drop extreme mv ratio (>10x)
    common_mv_v4 = {k for k in common_mv if 0.1 <= mv_latest[k] / asof[k] <= 10.0}
    mv_lat_v4 = {k: mv_latest[k] for k in common_mv_v4}
    mv_asof_v4 = {k: asof[k] for k in common_mv_v4}
    s_lat_v4 = cum_only(target, mv_lat_v4, require_min_history=True)
    s_asof_v4 = cum_only(target, mv_asof_v4, require_min_history=True)
    results.append({"date": date_str, "variant": "V4: intersected + ratio in [0.1x,10x]", **metrics(s_lat_v4, s_asof_v4, intersection=True)})


print("[4/5] Results:", flush=True)
print(f'{"Date":12s}  {"Variant":42s}  {"n_common":>9s}  {"Spearman":>10s}  {"Δvs self-audit":>11s}  {"Δvs external audit":>11s}  match', flush=True)
print("-" * 130, flush=True)
for r in results:
    if r.get("skip"):
        print(f'{r["date"]:12s}  {r["variant"]:42s}  SKIP', flush=True)
        continue
    d = r["date"]
    sp = r["spearman"]
    d_cl = sp - impl_a[d]
    d_co = sp - impl_b[d]
    match = []
    if abs(d_cl) < 0.001: match.append("R1")
    if abs(d_co) < 0.001: match.append("R2")
    match_str = " / ".join(match) if match else "neither"
    print(
        f'{d:12s}  {r["variant"]:42s}  {r["n_common"]:>9d}  {sp:>10.6f}  '
        f'{d_cl:>+11.6f}  {d_co:>+11.6f}  {match_str}',
        flush=True,
    )


print("[5/5] Writing report ...", flush=True)
with OUT_PATH.open("w", encoding="utf-8") as f:
    f.write("# B3 Divergence Root Cause Audit — 2026-05-10\n\n")
    f.write("**Plan reference**: (internal plan) Phase 0\n\n")
    f.write("## Question\n\n")
    f.write("R1 reported score Spearman 0.9675/0.9945 (latest mv vs as-of mv ranking); ")
    f.write("R2 reported 0.9633/0.9914. Diff 0.003-0.004 across both dates.\n\n")
    f.write("## Method\n\n")
    f.write("Both rounds reportedly used a `cum_foreign / mv` simulator (no full composite). ")
    f.write("Test 4 universe-filter variants of the simulator to find which produces 0.9675 and which 0.9633.\n\n")
    f.write("- **V1**: each scenario keeps own native universe (drop sym if its own mv missing or ≤ 0)\n")
    f.write("- **V2**: pre-intersect mv universe (require BOTH latest+asof mv exist) before scoring\n")
    f.write("- **V3**: relaxed (no min_history filter, only `len(last20) >= 5`)\n")
    f.write("- **V4**: V2 + drop extreme mv ratio (latest/asof > 10x or < 0.1x)\n\n")
    f.write("## Reference Numbers (Round 1 / Round 2)\n\n")
    f.write("| Date | R1 | R2 | Diff |\n")
    f.write("|---|---:|---:|---:|\n")
    for d in ["2020-01-13", "2025-11-12"]:
        f.write(f"| {d} | {impl_a[d]:.4f} | {impl_b[d]:.4f} | {impl_a[d] - impl_b[d]:+.4f} |\n")
    f.write("\n## Results\n\n")
    f.write(f"| Date | Variant | n_common | Spearman | Δ vs R1 | Δ vs R2 | Match |\n")
    f.write(f"|---|---|---:|---:|---:|---:|---|\n")
    for r in results:
        if r.get("skip"):
            f.write(f'| {r["date"]} | {r["variant"]} | n={r["n_common"]} | SKIP | - | - | - |\n')
            continue
        d = r["date"]
        sp = r["spearman"]
        d_cl = sp - impl_a[d]
        d_co = sp - impl_b[d]
        match = []
        if abs(d_cl) < 0.001: match.append("R1")
        if abs(d_co) < 0.001: match.append("R2")
        match_str = " / ".join(match) if match else "neither"
        f.write(
            f'| {d} | {r["variant"]} | {r["n_common"]} | {sp:.6f} | '
            f'{d_cl:+.6f} | {d_co:+.6f} | {match_str} |\n'
        )

    f.write("\n## Top10 / Bot10 Jaccard (universe overlap diagnostic)\n\n")
    f.write(f"| Date | Variant | top10 J | bot10 J |\n")
    f.write(f"|---|---|---:|---:|\n")
    for r in results:
        if r.get("skip"):
            continue
        f.write(f'| {r["date"]} | {r["variant"]} | {r["top10_jaccard"]:.6f} | {r["bot10_jaccard"]:.6f} |\n')

    f.write("\n## Conclusion\n\n")
    f.write("(Per the Match column above. If a single variant matches R1 and another matches ")
    f.write("R2, root cause is universe-filter handling — both rounds wrote correct simulators ")
    f.write("but used different mv-None / min_history handling. If no variant matches one reference, ")
    f.write("that round used a third method this audit didn't replicate.)\n\n")
    f.write("Phase 1+ implements MODIFY-AND-RERUN per the plan. After fresh rerun the contaminated ")
    f.write("-0.0195 baseline becomes obsolete and this divergence becomes purely historical reproducibility ")
    f.write("evidence for R3 alignment.\n")

print(f"[5/5] Wrote {OUT_PATH}", flush=True)
