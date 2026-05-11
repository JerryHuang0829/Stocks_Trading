"""Post-process: enrich a fresh foreign_investor_v2_ic.json with new diagnostics.

Adds (from saved period_factor_scores + OHLCV cache):
  - decile_returns_per_period: list of 10-element dicts per rebalance date
  - decile_avg_returns_across_periods: 10-element dict averaged
  - monotonicity_spearman_rho: rho(decile_idx, avg_ret)
  - peak_in_middle_t_stats: {d5_d0, d5_d9, d9_d0} per-period spread t-stats
  - price_score_corr_per_period: 71 score-vs-close-price Spearman per period
  - pit_violation: {violated: false, fresh_rerun_date: "2026-05-10"} (overwrites
    the contaminated flag if present from pre-rerun JSON)

Plan reference: (internal plan) Phase 3.

Usage:
    python scripts/_enrich_factor_ic_diagnostics.py reports/factor_ic/foreign_investor_v2_ic.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = REPO_ROOT / "data" / "cache" / "ohlcv"


def _load_close(sym: str) -> pd.Series | None:
    fp = OHLCV_DIR / f"{sym}.pkl"
    if not fp.exists():
        return None
    try:
        df = pd.read_pickle(fp)
    except Exception:
        return None
    if "close" not in df.columns:
        return None
    s = df["close"]
    if hasattr(s.index, "tz") and s.index.tz is not None:
        s.index = s.index.tz_convert(None)
    return s


def _resolve_price(s: pd.Series, ts: pd.Timestamp, max_gap_days: int = 5) -> float | None:
    view = s[s.index <= ts].dropna()
    if view.empty:
        return None
    last = view.index[-1]
    if (ts - last).days > max_gap_days:
        return None
    return float(view.iloc[-1])


def enrich(json_path: Path, fresh_rerun_date: str = "2026-05-10") -> None:
    print(f"[1/6] Loading {json_path} ...", flush=True)
    with json_path.open(encoding="utf-8") as f:
        d = json.load(f)

    pfs = d.get("period_factor_scores")
    if not pfs:
        raise RuntimeError("period_factor_scores missing — cannot enrich")

    # Build (period, sym, score, ret) rows
    print("[2/6] Loading close panels for symbols in saved scores ...", flush=True)
    all_syms: set[str] = set()
    for entry in pfs:
        scores = entry.get("scores") or {}
        all_syms.update(scores.keys())
    close_by: dict[str, pd.Series] = {}
    for sym in all_syms:
        cs = _load_close(sym)
        if cs is not None:
            close_by[sym] = cs
    print(f"      {len(close_by)} / {len(all_syms)} close series loaded", flush=True)

    print("[3/6] Computing per-period decile + price-score corr ...", flush=True)
    sorted_dates = sorted(e["rebalance_date"] for e in pfs)
    score_by_period = {e["rebalance_date"]: e["scores"] for e in pfs}

    all_rows: list[dict] = []
    price_score_corrs: list[dict] = []
    for i, pdate in enumerate(sorted_dates[:-1]):
        cur = pd.Timestamp(pdate)
        nxt = pd.Timestamp(sorted_dates[i + 1])
        scores = score_by_period[pdate]
        per_period_pairs: list[tuple[float, float]] = []  # (price, score) for corr
        for sym, sc in scores.items():
            if sym not in close_by:
                continue
            sp = _resolve_price(close_by[sym], cur)
            ep = _resolve_price(close_by[sym], nxt)
            if sp is None or ep is None or sp <= 0:
                continue
            ret = ep / sp - 1
            all_rows.append({"period": pdate, "sym": sym, "score": float(sc), "ret": ret})
            per_period_pairs.append((sp, float(sc)))
        if len(per_period_pairs) >= 30:
            arr = np.array(per_period_pairs)
            rho_ps, _ = stats.spearmanr(arr[:, 0], arr[:, 1])
            price_score_corrs.append({"rebalance_date": pdate, "spearman": float(rho_ps)})
        else:
            price_score_corrs.append({"rebalance_date": pdate, "spearman": None})

    df_all = pd.DataFrame(all_rows)
    print(f"      Total period-symbol rows: {len(df_all)}", flush=True)

    print("[4/6] Computing decile means per period ...", flush=True)
    decile_per_period: list[dict] = []
    for pdate, grp in df_all.groupby("period"):
        if len(grp) < 30:
            continue
        g = grp.copy()
        g["decile"] = pd.qcut(
            g["score"].rank(method="first"), 10, labels=False, duplicates="drop"
        )
        means = g.groupby("decile")["ret"].mean().to_dict()
        decile_per_period.append({
            "rebalance_date": pdate,
            "decile_means": {str(int(k)): float(v) for k, v in means.items()},
            "n_symbols": int(len(g)),
        })

    # Average decile means across periods
    if decile_per_period:
        decile_df = pd.DataFrame([d["decile_means"] for d in decile_per_period])
        decile_avg = decile_df.mean()
        decile_avg_dict = {str(k): float(v) for k, v in decile_avg.items()}
    else:
        decile_avg_dict = {}

    print("[5/6] Computing monotonicity rho + peak-in-middle t-stats ...", flush=True)
    if decile_avg_dict:
        keys_sorted = sorted(decile_avg_dict.keys(), key=int)
        idx = np.array([int(k) for k in keys_sorted])
        vals = np.array([decile_avg_dict[k] for k in keys_sorted])
        mono_rho, _ = stats.spearmanr(idx, vals)
    else:
        mono_rho = None

    # Per-period spread t-stats
    spread_d5_d0 = []
    spread_d5_d9 = []
    spread_d9_d0 = []
    for entry in decile_per_period:
        m = entry["decile_means"]
        if "0" in m and "5" in m:
            spread_d5_d0.append(m["5"] - m["0"])
        if "5" in m and "9" in m:
            spread_d5_d9.append(m["5"] - m["9"])
        if "9" in m and "0" in m:
            spread_d9_d0.append(m["9"] - m["0"])

    def _tstat(arr: list[float]) -> float | None:
        if len(arr) < 2:
            return None
        a = np.array(arr)
        sd = a.std(ddof=1)
        if sd == 0:
            return None
        return float(a.mean() / (sd / np.sqrt(len(a))))

    peak_t_stats = {
        "d5_d0_t": _tstat(spread_d5_d0),
        "d5_d9_t": _tstat(spread_d5_d9),
        "d9_d0_t": _tstat(spread_d9_d0),
        "n_periods": len(decile_per_period),
    }

    print("[6/6] Writing enriched JSON ...", flush=True)
    d["decile_returns_per_period"] = decile_per_period
    d["decile_avg_returns_across_periods"] = decile_avg_dict
    d["monotonicity_spearman_rho"] = float(mono_rho) if mono_rho is not None else None
    d["peak_in_middle_t_stats"] = peak_t_stats
    d["price_score_corr_per_period"] = price_score_corrs
    if price_score_corrs:
        valid = [c["spearman"] for c in price_score_corrs if c["spearman"] is not None]
        if valid:
            d["price_score_corr_summary"] = {
                "mean": float(np.mean(valid)),
                "std": float(np.std(valid, ddof=1)),
                "min": float(np.min(valid)),
                "max": float(np.max(valid)),
                "n_periods": len(valid),
            }
    # 2026-05-10 R28-5 fix: per-factor differentiated fixes_applied (was
    # hardcoded foreign_broker-specific list which was provenance-bug for the
    # 4 non-foreign factors that don't use cum_ratio / last20 / consistency /
    # covered-weight composite).
    # 2026-05-11 R31 finding 1 fix: Phase D 3 factors (quality_v3 /
    # industry_momentum / idio_vol_max) were enriched by this same script so
    # their JSON has schema parity with the Phase A1 5; the fixes_applied list
    # for them describes the 2026-05-11 single-IC 補測 (not a fresh-rerun fix).
    PHASE_D_FACTORS = ("quality_v3", "industry_momentum", "idio_vol_max")
    factor_name = d.get("factor_name", "unknown")
    if factor_name == "foreign_investor_v2":
        fixes_applied = [
            "P0-A: as-of market_value lookup (was latest mv via keep='last')",
            "P0-B: dollar-denominated cum_ratio + rank_stability (was shares/NTD = 1/price量綱錯)",
            "P0-C: pit_violation flag (this field) re-applied post fresh rerun overwrite",
            "P1-C: last20 stale span guard 35d",
            "P1-D: consistency sub-signal weight 0.20 -> 0 (R26 sparsity)",
            "P1-E: covered-weight composite rescale + 0.5 threshold",
        ]
        violated = False
    elif factor_name == "margin_short_ratio":
        fixes_applied = [
            "P1-A: as-of issued_capital lookup (was latest via keep='last')",
            "P0-2 R27: issued_capital fallback Timestamp.min for missing date column "
            "(static-snapshot PIT approximation; cache lacks date history per R28-1)",
            "Universe extended 2020-01 ~ 2025-11 (n=59 -> 71)",
        ]
        violated = False
    elif factor_name in PHASE_D_FACTORS:
        # Phase D 3 factors: brand-new single-factor IC 跑於 2026-05-11 via
        # scripts/run_phase_d_factor_ic.py (CellSweepContext data sources).
        # Never contaminated; previously only appeared inside the v7 cell-sweep
        # aggregate, not as a stand-alone IC report.
        fixes_applied = [
            "2026-05-11: NEW single-factor IC 補測 via run_phase_d_factor_ic.py "
            "(CellSweepContext: financial_history / industry_label_map / market_returns)",
            "Universe = per-factor natural universe (NOT intersection with Phase A1 5 panels) "
            "— cross-factor comparison universe-asymmetric (see known_biases)",
            "Enriched 2026-05-11 with decile / monotonicity / peak-in-middle / price-score-corr "
            "diagnostics for schema parity with Phase A1 5 factor JSONs (R31 finding 1 fix)",
        ]
        violated = False
    else:
        # high_proximity / revenue_momentum_v2 / pead_eps: aux_panel=None, not
        # affected by R26+R27 PIT-aux修法; baseline rerun for cross-factor
        # consistency + universe extension to 2025-11.
        fixes_applied = [
            "No PIT-violated aux_panel (factor uses None aux); baseline rerun for cross-factor consistency",
            "Universe extended 2020-01 ~ 2025-11 (n=59 -> 71)",
        ]
        violated = False

    pit_violation: dict = {
        "violated": violated,
        "plan": "(internal plan reference)",
        "fixes_applied": fixes_applied,
    }
    if factor_name in PHASE_D_FACTORS:
        pit_violation["single_ic_date"] = "2026-05-11"
        pit_violation["note"] = (
            "Phase D factor — never contaminated; this is the first stand-alone single-factor "
            "IC report (previously only in v7 cell-sweep aggregate)."
        )
    else:
        pit_violation["fresh_rerun_date"] = fresh_rerun_date
    d["pit_violation"] = pit_violation
    d["enriched_diagnostics_date"] = datetime.now().strftime("%Y-%m-%d")

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

    print("\nSummary:", flush=True)
    print(f"  n_periods: {d.get('n_periods')}", flush=True)
    overall = d.get("overall", {})
    print(f"  mean_ic: {overall.get('mean_ic')}", flush=True)
    print(f"  ic_ir: {overall.get('ic_ir')}", flush=True)
    if decile_avg_dict:
        keys = sorted(decile_avg_dict.keys(), key=int)
        print(f"  decile_avg D0..D9:", flush=True)
        for k in keys:
            print(f"    D{k}: {decile_avg_dict[k]:.6f}", flush=True)
    print(f"  monotonicity_rho: {mono_rho}", flush=True)
    print(f"  peak_t_stats: {peak_t_stats}", flush=True)
    if "price_score_corr_summary" in d:
        s = d["price_score_corr_summary"]
        print(f"  price_score_corr 71-period mean: {s['mean']:.4f} ± {s['std']:.4f}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        path = REPO_ROOT / "reports" / "factor_ic" / "foreign_investor_v2_ic.json"
    else:
        path = Path(sys.argv[1])
    enrich(path)
    print(f"\nDone. Enriched: {path}")
