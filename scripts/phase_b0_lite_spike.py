"""Phase B0-Lite low_vol_v2 single-factor spike.

Per `reports/phase_b0_lite/H_lite_preregistration.md` (anchor commit 27e5fe6):
    - low_vol_v2 single-factor IC + DSR (n_trials=12) + coverage + turnover
    - Observation metrics: active corr vs 0050, 0050-overlap, regime IC, yearly IC
    - **NO quality_v2** (deferred to full B0; cache absent + lookahead bias risk)

Universe selection per H_main spec:
    TWSE/TPEX top-80 by close × volume per rebalance period
    (matches src/portfolio/tw_stock.py auto_universe_size=80)

Reuses run_factor_ic.py infra:
    _load_universe_ohlcv, _forward_return, _compute_regimes,
    BacktestEngine._generate_rebalance_dates

CLI:
    python scripts/phase_b0_lite_spike.py \\
        --start 2019-01-01 --end 2024-12-31 \\
        --output-dir reports/phase_b0_lite/

Outputs:
    {output-dir}/spike_results.json — machine-readable full results
    {output-dir}/spike_results.md   — human-readable verdict + reject criteria
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Make `src.*` and `scripts.*` importable when invoked from any cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Reuse run_factor_ic.py infra
from src.analysis.ic_analysis import factor_ic_report
from src.backtest.engine import BacktestEngine
from src.features.low_vol_v2 import compute_low_vol_v2_universe
from src.utils.config import load_config
from src.utils.paths import resolve_cache_dir
# Phase P5 Session 1 / R21 finding F6 fix (2026-05-03):
# Import path migrated from scripts.run_factor_ic to scripts._factor_ic_helpers
# (12 helpers extracted as shared utility; cross-script private import 反 pattern fix)
from scripts._factor_ic_helpers import (  # noqa: E402
    REGIME_SYMBOL,
    _compute_regimes,
    _forward_return,
    _load_industry_labels,
    _load_ohlcv,
    _load_universe_ohlcv,
)


TOP_N_UNIVERSE = 80   # per H_main spec
TOP_N_PORTFOLIO = 8   # per H_main spec
DSR_N_TRIALS = 12     # H_lite finding #3 (涵蓋整個研究家族)
BOOTSTRAP_BLOCK_LEN = 3.0
N_PERMUTATION = 300
MIN_HISTORY = 200     # low_vol_v2 default

L2_IC_THRESHOLD = 0.02       # reject below
L4_COVERAGE_THRESHOLD = 0.60  # reject below
L5_TURNOVER_THRESHOLD = 0.30  # reject above (per month)
# H_lite hypothesis 完整陳述含 「DSR Ψ ≥ 0.95」AND condition (line 28)
# institutional-grade，retail monthly 幾乎不可達 (策略研究.md:104)
# strict_outcome 用此 threshold；script_outcome 仍按 reject criteria 主表不含 DSR
DSR_THRESHOLD = 0.95


def _select_top_n_universe(
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    *,
    top_n: int = TOP_N_UNIVERSE,
    lookback_days: int = 20,
) -> list[str]:
    """Top-N TWSE/TPEX symbols by close × volume mean over the past `lookback_days`
    trading days at `as_of`. Mirrors src/portfolio/tw_stock.py:_size_proxy logic
    (close * volume average) but applied to the cached universe directly.
    """
    if as_of.tz is not None:
        as_of = as_of.tz_localize(None)
    scores: dict[str, float] = {}
    for symbol, df in ohlcv_by_symbol.items():
        if df is None or df.empty:
            continue
        view = df[df.index <= as_of]
        if len(view) < lookback_days:
            continue
        recent = view.tail(lookback_days)
        if "close" not in recent.columns or "volume" not in recent.columns:
            continue
        turnover = (recent["close"] * recent["volume"]).mean()
        if pd.notna(turnover) and turnover > 0:
            scores[symbol] = float(turnover)
    if not scores:
        return []
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [s for s, _ in ranked[:top_n]]


def _compute_low_vol_portfolio_holdings(
    factor_scores: pd.Series,
    *,
    top_n: int = TOP_N_PORTFOLIO,
) -> list[str]:
    """Pick top-N symbols by factor score (high score = good per low_vol_v2
    reverse direction = high score = low realized vol)."""
    if factor_scores.empty:
        return []
    ranked = factor_scores.sort_values(ascending=False)
    return list(ranked.head(top_n).index)


def _compute_monthly_turnover(
    holdings_history: list[set[str]],
) -> float:
    """One-way turnover: |new ∩ holdings - old| / top_n averaged across rebalances."""
    if len(holdings_history) < 2:
        return 0.0
    turnovers = []
    for prev, curr in zip(holdings_history[:-1], holdings_history[1:]):
        if not curr:
            continue
        new_positions = curr - prev
        turnovers.append(len(new_positions) / max(len(curr), 1))
    return float(np.mean(turnovers)) if turnovers else 0.0


def _portfolio_monthly_return(
    holdings: list[str],
    close_by_symbol: dict[str, pd.Series],
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    max_gap_days: int = 5,
) -> float | None:
    """Equal-weight monthly return for a holdings list."""
    if not holdings:
        return None
    rets: list[float] = []
    for sym in holdings:
        r = _forward_return(close_by_symbol, sym, start_ts, end_ts, max_gap_days=max_gap_days)
        if r is not None:
            rets.append(r)
    if len(rets) < max(1, len(holdings) // 2):
        return None
    return float(np.mean(rets))


def _compute_active_return_rolling_corr(
    portfolio_rets: list[tuple[pd.Timestamp, float]],
    benchmark_rets: list[tuple[pd.Timestamp, float]],
    window: int = 5,
) -> list[tuple[str, float]]:
    """Rolling correlation between portfolio and benchmark monthly returns."""
    p_df = pd.DataFrame(portfolio_rets, columns=["date", "p"]).set_index("date")
    b_df = pd.DataFrame(benchmark_rets, columns=["date", "b"]).set_index("date")
    merged = p_df.join(b_df, how="inner")
    if len(merged) < window:
        return []
    rolling_corr = merged["p"].rolling(window).corr(merged["b"])
    return [
        (idx.strftime("%Y-%m-%d"), float(v))
        for idx, v in rolling_corr.dropna().items()
    ]


def _benchmark_holdings_proxy(
    cache_dir: Path,
    as_of: pd.Timestamp,
    top_n: int = 50,
) -> set[str]:
    """0050 holdings proxy: top-50 by market_value at as_of (TSE 0050 ETF
    constituents are the 50 largest TWSE stocks)."""
    mv_path = cache_dir / "market_value" / "_global.pkl"
    if not mv_path.exists():
        return set()
    df = pd.read_pickle(mv_path)
    if df is None or df.empty or "stock_id" not in df.columns or "market_value" not in df.columns:
        return set()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_localize(None)
        as_of_naive = as_of.tz_localize(None) if as_of.tz is not None else as_of
        view = df[df["date"] <= as_of_naive]
        if view.empty:
            return set()
        latest = view.sort_values("date").drop_duplicates("stock_id", keep="last")
    else:
        latest = df.drop_duplicates("stock_id", keep="last")
    ranked = latest.sort_values("market_value", ascending=False).head(top_n)
    return set(ranked["stock_id"].astype(str))


def _evaluate_reject_criteria(
    ic_result: dict,
    coverage_mean: float,
    turnover_mean: float,
) -> dict:
    """Apply reject criteria per H_lite_preregistration.

    R21 Codex audit P1/P2 fix (2026-05-03): 同時輸出 script_outcome (按 reject
    criteria 主表 L2/L4/L5) AND strict_outcome (按 H_lite hypothesis 完整陳述
    含 DSR ≥ 0.95 AND condition)。讓後續工具讀 JSON 時看到兩個 outcome 不會
    silently 走錯路。

    decision.outcome 仍保留為 backward-compat alias = strict_outcome（嚴格 win）。
    """
    mean_ic = ic_result["overall"].get("mean_ic")
    dsr = ic_result.get("deflated_sharpe_ratio")

    l2_pass = mean_ic is not None and mean_ic > L2_IC_THRESHOLD
    l4_pass = coverage_mean >= L4_COVERAGE_THRESHOLD
    l5_pass = turnover_mean < L5_TURNOVER_THRESHOLD
    # H_lite hypothesis 完整陳述 (line 28) 含 「DSR Ψ ≥ 0.95」AND condition
    l_dsr_pass = dsr is not None and dsr >= DSR_THRESHOLD

    # Script outcome (按 reject criteria 主表 L2/L4/L5 — 不含 DSR)
    if not l4_pass:
        script_outcome = "Lite-O3"
        script_reason = "infra: low_vol_v2 coverage < 60% (OHLCV cache 不齊)"
        script_next = "halt + 跑 daily_update.sh + cache_health.py 後重跑"
    elif not l2_pass:
        script_outcome = "Lite-O2"
        script_reason = f"low_vol_v2 IC = {mean_ic} ≤ 0.02 (no edge)"
        script_next = "**直接 pivot P5** (整個 quality+lowvol 路線結構性 fail)"
    elif not l5_pass:
        script_outcome = "Lite-O4"
        script_reason = f"low_vol_v2 monthly turnover = {turnover_mean:.1%} ≥ 30%"
        script_next = "進 full B0 但 spec 改 quarterly rebal"
    else:
        script_outcome = "Lite-O1"
        script_reason = "L2 + L4 + L5 全 pass"
        script_next = "進 full B0 (quality_history PIT rewrite + composite + 修正版 A1 gate)"

    # Strict outcome (按 H_lite hypothesis 完整陳述含 DSR ≥ 0.95 AND condition)
    if not l_dsr_pass:
        strict_outcome = "Lite-O2"
        strict_reason = (
            f"H_lite hypothesis 完整陳述 fail: DSR Ψ = {dsr} < {DSR_THRESHOLD} "
            f"(institutional-grade not reachable for retail monthly TW stock — "
            f"對齊 user memory 策略研究.md:104)"
        )
        strict_next = "**pivot P5** (strict hypothesis lock 守住；不寫 full B0)"
    elif not l4_pass:
        strict_outcome = "Lite-O3"
        strict_reason = script_reason
        strict_next = script_next
    elif not l2_pass:
        strict_outcome = "Lite-O2"
        strict_reason = script_reason
        strict_next = script_next
    elif not l5_pass:
        strict_outcome = "Lite-O4"
        strict_reason = script_reason
        strict_next = script_next
    else:
        strict_outcome = "Lite-O1"
        strict_reason = "L2 + L4 + L5 + DSR ≥ 0.95 全 pass"
        strict_next = script_next

    return {
        # Backward-compat: outcome alias = strict_outcome (嚴格 win 紀律)
        "outcome": strict_outcome,
        "reason": strict_reason,
        "next_step": strict_next,
        # Dual outcome fields per R21 P1/P2 fix
        "script_outcome": script_outcome,
        "script_reason": script_reason,
        "script_next_step": script_next,
        "strict_outcome": strict_outcome,
        "strict_reason": strict_reason,
        "strict_next_step": strict_next,
        "L2_ic_pass": l2_pass,
        "L2_mean_ic": mean_ic,
        "L2_dsr": dsr,
        "L_dsr_pass": l_dsr_pass,
        "L_dsr_threshold": DSR_THRESHOLD,
        "L4_coverage_pass": l4_pass,
        "L4_coverage_mean": coverage_mean,
        "L5_turnover_pass": l5_pass,
        "L5_turnover_mean": turnover_mean,
    }


def _write_spike_md(
    output_path: Path,
    args: argparse.Namespace,
    ic_result: dict,
    coverage_mean: float,
    coverage_per_period: list[tuple[str, float]],
    turnover_mean: float,
    decision: dict,
    obs: dict,
) -> None:
    """Write human-readable spike_results.md per H_lite plan."""
    lines: list[str] = []
    lines.append("# Phase B0-Lite Spike Results — low_vol_v2")
    lines.append("")
    lines.append(f"**執行日期**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Sample period**：{args.start} ~ {args.end} (historical validation set)")
    lines.append(f"**Universe**：top-{TOP_N_UNIVERSE} by close × volume per rebalance")
    lines.append(f"**Hypothesis lock**：reports/phase_b0_lite/H_lite_preregistration.md (commit 27e5fe6)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Reject Criteria 評估")
    lines.append("")
    l2_icon = "✅ PASS" if decision["L2_ic_pass"] else "❌ FAIL"
    l4_icon = "✅ PASS" if decision["L4_coverage_pass"] else "❌ FAIL"
    l5_icon = "✅ PASS" if decision["L5_turnover_pass"] else "❌ FAIL"
    ldsr_icon = "✅ PASS" if decision["L_dsr_pass"] else "❌ FAIL"
    lines.append(f"- **L1 (quality_v2 IC)** — DEFERRED to full B0 (cache 不存在 + lookahead bias)")
    lines.append(
        f"- **L2 (low_vol_v2 IC > 0.02)**：{l2_icon} — mean IC = "
        f"{decision['L2_mean_ic']:.4f}, DSR = {decision['L2_dsr']}"
    )
    lines.append(
        f"- **L4 (coverage ≥ 60%)**：{l4_icon} — mean coverage = {coverage_mean:.1%}"
    )
    lines.append(
        f"- **L5 (turnover < 30%/月)**：{l5_icon} — mean monthly one-way turnover = "
        f"{turnover_mean:.1%}"
    )
    lines.append(
        f"- **L_DSR (Ψ ≥ {decision['L_dsr_threshold']} per H_lite hypothesis line 28)**"
        f"：{ldsr_icon} — DSR Ψ = {decision['L2_dsr']}"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## Verdict (dual outcome per R21 P1/P2 fix)")
    lines.append("")
    lines.append(
        f"- **Script outcome (按 reject criteria 主表 L2/L4/L5)**：**{decision['script_outcome']}**"
    )
    lines.append(f"  - reason: {decision['script_reason']}")
    lines.append(f"  - next_step: {decision['script_next_step']}")
    lines.append("")
    lines.append(
        f"- **Strict outcome (按 H_lite hypothesis 完整陳述含 DSR ≥ 0.95 AND condition)**："
        f"**{decision['strict_outcome']}** ← user 拍板採此"
    )
    lines.append(f"  - reason: {decision['strict_reason']}")
    lines.append(f"  - next_step: {decision['strict_next_step']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Detailed IC Results")
    lines.append("")
    overall = ic_result["overall"]
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Periods | {ic_result['n_periods']} |")
    lines.append(f"| Symbols avg | {ic_result['n_symbols_avg']:.1f} |")
    lines.append(f"| Mean rank IC | {overall.get('mean_ic')} |")
    lines.append(f"| Std rank IC | {overall.get('std_ic')} |")
    lines.append(f"| IC IR | {overall.get('ic_ir')} |")
    lines.append(f"| t-stat | {overall.get('t_stat')} |")
    lines.append(f"| p-value | {overall.get('p_value')} |")
    lines.append(f"| Bootstrap CI 95 (block, len=3) | {overall.get('bootstrap_ci_95')} |")
    lines.append(f"| Bootstrap CI 95 (iid) | {overall.get('bootstrap_ci_95_iid')} |")
    lines.append(f"| Permutation p-value | {ic_result['permutation'].get('p_value_empirical')} |")
    lines.append(
        f"| DSR Ψ (n_trials={DSR_N_TRIALS}) | {ic_result.get('deflated_sharpe_ratio')} |"
    )
    lines.append(f"| FDR adjusted p (overall) | {ic_result.get('fdr_adjusted_p')} |")
    lines.append("")
    lines.append("## IC by Regime (O3)")
    lines.append("")
    lines.append("| Regime | Mean IC | n_periods |")
    lines.append("|---|---|---|")
    for regime, stats in ic_result.get("by_regime", {}).items():
        if stats:
            lines.append(
                f"| {regime} | {stats.get('mean_ic')} | {stats.get('n')} |"
            )
    lines.append("")
    lines.append("## IC by Bucket (O4 yearly)")
    lines.append("")
    lines.append("| Bucket | Mean IC | n | FDR p |")
    lines.append("|---|---|---|---|")
    for bucket, stats in ic_result.get("by_bucket", {}).items():
        if stats:
            lines.append(
                f"| {bucket} | {stats.get('mean_ic')} | {stats.get('n')} | "
                f"{stats.get('fdr_adj_p')} |"
            )
    lines.append("")
    lines.append("## Observation Metrics")
    lines.append("")
    lines.append("### O1 — Active return rolling corr vs 0050 (5-month window)")
    lines.append("")
    if obs.get("active_corr_rolling"):
        recent = obs["active_corr_rolling"][-12:]
        lines.append("最近 12 個月 rolling corr：")
        lines.append("")
        lines.append("| Date | Rolling Corr |")
        lines.append("|---|---|")
        for date, corr in recent:
            lines.append(f"| {date} | {corr:.3f} |")
    else:
        lines.append("(無資料)")
    lines.append("")
    lines.append("### O2 — Top-8 holdings vs 0050 top-50 monthly overlap")
    lines.append("")
    overlap_mean = obs.get("benchmark_overlap_mean")
    if overlap_mean is not None:
        lines.append(f"Mean overlap (top-8 portfolio ∩ top-50 0050 proxy) / 8 = **{overlap_mean:.1%}**")
        if overlap_mean > 0.7:
            lines.append("→ ⚠️ overlap 偏高（low_vol top picks 跟 0050 重壓）— full B0 設計 A1 gate 須注意")
        elif overlap_mean < 0.3:
            lines.append("→ low overlap，low_vol top picks 跟 0050 結構不同")
    lines.append("")
    lines.append("### O3 / O4 — 見上方 by_regime / by_bucket")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Coverage by Period")
    lines.append("")
    lines.append(
        f"低於 60% 的 period 數：{sum(1 for _, c in coverage_per_period if c < 0.60)}"
    )
    lines.append(
        f"最低 coverage：{min((c for _, c in coverage_per_period), default=None)}"
    )
    lines.append(
        f"最高 coverage：{max((c for _, c in coverage_per_period), default=None)}"
    )
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase B0-Lite low_vol_v2 spike")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--output-dir", default="reports/phase_b0_lite/")
    parser.add_argument("--rebalance-day", type=int, default=12)
    parser.add_argument("--max-gap-days", type=int, default=5)
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("phase_b0_lite_spike")

    config = load_config(args.config)
    cache_dir = resolve_cache_dir()
    log.info("Cache dir: %s", cache_dir)

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    log.info("Loading universe OHLCV from cache...")
    ohlcv_by_symbol = _load_universe_ohlcv(cache_dir)
    log.info("Loaded %d OHLCV symbols", len(ohlcv_by_symbol))

    benchmark = _load_ohlcv(cache_dir, REGIME_SYMBOL)
    if benchmark is None or benchmark.empty:
        log.error("Benchmark %s OHLCV missing — abort", REGIME_SYMBOL)
        sys.exit(1)

    close_by_symbol: dict[str, pd.Series] = {
        s: df["close"].copy() for s, df in ohlcv_by_symbol.items()
    }

    all_dates = sorted({idx for df in ohlcv_by_symbol.values() for idx in df.index})
    trading_days = pd.DatetimeIndex(all_dates)
    rebalance_dates = BacktestEngine._generate_rebalance_dates(
        start_dt, end_dt, args.rebalance_day, trading_days=trading_days
    )
    log.info("Generated %d rebalance dates", len(rebalance_dates))

    strategy_cfg = config.get("default_strategy", {})
    regimes = _compute_regimes(benchmark, rebalance_dates, strategy_cfg)

    period_data: list = []
    coverage_per_period: list[tuple[str, float]] = []
    holdings_history: list[set[str]] = []
    portfolio_rets: list[tuple[pd.Timestamp, float]] = []
    benchmark_rets: list[tuple[pd.Timestamp, float]] = []
    overlap_per_period: list[float] = []

    benchmark_close = benchmark["close"].copy()
    if benchmark_close.index.tz is not None:
        benchmark_close.index = benchmark_close.index.tz_convert(None)

    for i, date in enumerate(rebalance_dates[:-1]):
        as_of = pd.Timestamp(date)
        if as_of.tz is not None:
            as_of = as_of.tz_convert(None)
        next_ts = pd.Timestamp(rebalance_dates[i + 1])
        if next_ts.tz is not None:
            next_ts = next_ts.tz_convert(None)

        # Top-80 universe by close × volume at as_of
        top80 = _select_top_n_universe(
            ohlcv_by_symbol, as_of, top_n=TOP_N_UNIVERSE, lookback_days=20,
        )
        if len(top80) < 30:
            log.warning("Period %s: top-80 universe only %d symbols — skip", as_of.date(), len(top80))
            continue

        # low_vol_v2 batch (subset to top80)
        sub_ohlcv = {s: ohlcv_by_symbol[s] for s in top80 if s in ohlcv_by_symbol}
        factor_scores = compute_low_vol_v2_universe(
            sub_ohlcv, as_of=as_of, window=252, min_history=MIN_HISTORY,
        )
        if factor_scores.empty:
            log.warning("Period %s: empty factor scores — skip", as_of.date())
            continue

        # L4 coverage: factor scores / top80 universe size
        coverage = len(factor_scores) / len(top80)
        coverage_per_period.append((as_of.strftime("%Y-%m-%d"), coverage))

        # Forward returns for IC
        returns: dict[str, float] = {}
        for sym in factor_scores.index:
            r = _forward_return(
                close_by_symbol, sym, as_of, next_ts, max_gap_days=args.max_gap_days,
            )
            if r is not None:
                returns[sym] = r
        returns_series = pd.Series(returns, dtype=float)
        if len(returns_series) < 10:
            log.warning("Period %s: only %d forward returns — skip", as_of.date(), len(returns_series))
            continue
        period_data.append((as_of, factor_scores, returns_series, regimes[i]))

        # L5 turnover + portfolio return + O1 active corr
        holdings = set(_compute_low_vol_portfolio_holdings(factor_scores, top_n=TOP_N_PORTFOLIO))
        holdings_history.append(holdings)
        port_ret = _portfolio_monthly_return(
            list(holdings), close_by_symbol, as_of, next_ts, max_gap_days=args.max_gap_days,
        )
        bench_start = _resolve_close(benchmark_close, as_of, args.max_gap_days)
        bench_end = _resolve_close(benchmark_close, next_ts, args.max_gap_days)
        if port_ret is not None and bench_start is not None and bench_end is not None and bench_start > 0:
            portfolio_rets.append((as_of, port_ret))
            benchmark_rets.append((as_of, (bench_end / bench_start) - 1.0))

        # O2 0050 overlap
        bench_holdings = _benchmark_holdings_proxy(cache_dir, as_of, top_n=50)
        if bench_holdings:
            overlap = len(holdings & bench_holdings) / max(len(holdings), 1)
            overlap_per_period.append(overlap)

    if not period_data:
        log.error("No usable periods — abort")
        sys.exit(1)

    log.info("Running factor_ic_report on %d periods", len(period_data))
    industry_labels = _load_industry_labels(cache_dir)
    biases = [
        "regime computed from 0050 benchmark (not per-stock)",
        "B0-Lite spike: low_vol_v2 single-factor only; quality_v2 deferred to full B0",
        f"universe = top-{TOP_N_UNIVERSE} by close × volume per rebalance period",
        f"DSR n_trials={DSR_N_TRIALS} (covers entire research family per H_lite)",
        f"forward-return gap filter: max_gap_days={args.max_gap_days}",
        "historical validation set 2019-2024, NOT fresh OOS (Phase A1-A3 已多輪研究 / 調參)",
    ]

    result = factor_ic_report(
        factor_name="low_vol_v2",
        period_data=period_data,
        return_basis="price_only",
        n_permutation=N_PERMUTATION,
        known_biases=biases,
        bootstrap_avg_block_len=BOOTSTRAP_BLOCK_LEN,
        dsr_n_trials=DSR_N_TRIALS,
        industry_labels=industry_labels,
    )

    coverage_mean = float(np.mean([c for _, c in coverage_per_period])) if coverage_per_period else 0.0
    turnover_mean = _compute_monthly_turnover(holdings_history)
    overlap_mean = float(np.mean(overlap_per_period)) if overlap_per_period else None

    obs = {
        "active_corr_rolling": _compute_active_return_rolling_corr(
            portfolio_rets, benchmark_rets, window=5,
        ),
        "benchmark_overlap_mean": overlap_mean,
    }

    decision = _evaluate_reject_criteria(
        result.to_dict(), coverage_mean, turnover_mean,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "spike_results.json"
    json_path.write_text(
        json.dumps(
            {
                "spike_config": {
                    "start": args.start,
                    "end": args.end,
                    "top_n_universe": TOP_N_UNIVERSE,
                    "top_n_portfolio": TOP_N_PORTFOLIO,
                    "dsr_n_trials": DSR_N_TRIALS,
                    "min_history": MIN_HISTORY,
                    "L2_threshold": L2_IC_THRESHOLD,
                    "L4_threshold": L4_COVERAGE_THRESHOLD,
                    "L5_threshold": L5_TURNOVER_THRESHOLD,
                },
                "ic_result": result.to_dict(),
                "coverage_mean": coverage_mean,
                "coverage_per_period": coverage_per_period,
                "turnover_mean": turnover_mean,
                "obs": obs,
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    log.info("Wrote %s", json_path)

    md_path = out_dir / "spike_results.md"
    _write_spike_md(
        md_path, args, result.to_dict(),
        coverage_mean, coverage_per_period, turnover_mean,
        decision, obs,
    )
    log.info("Wrote %s", md_path)

    print("=" * 60)
    print(f"  Phase B0-Lite Spike — {decision['outcome']}")
    print("=" * 60)
    print(f"  L2 IC > 0.02:        {decision['L2_ic_pass']} (mean = {decision['L2_mean_ic']})")
    print(f"  L4 coverage >= 60%:  {decision['L4_coverage_pass']} ({decision['L4_coverage_mean']:.1%})")
    print(f"  L5 turnover < 30%:   {decision['L5_turnover_pass']} ({decision['L5_turnover_mean']:.1%})")
    print(f"  Next step: {decision['next_step']}")
    print("=" * 60)


def _resolve_close(series: pd.Series, target: pd.Timestamp, max_gap_days: int = 5) -> float | None:
    """Mirror run_factor_ic._resolve_price_asof but return only price (not anchor)."""
    target_naive = target.tz_localize(None) if target.tz is not None else target
    view = series[series.index <= target_naive].dropna()
    if view.empty:
        return None
    last_date = view.index[-1]
    if (target_naive - last_date).days > max_gap_days:
        return None
    return float(view.iloc[-1])


if __name__ == "__main__":
    main()
