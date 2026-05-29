"""v5.0 Step 6 — Self-Audit module: DSR + L1-L6 hard gates evaluation.

Pre-registration reference: `reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
  §10 Hard gates L1-L6 (LOCKED v3.3 retail-realistic)
  §8.4 DSR n_trials = 400 (Option A LOCK)
  §13 condition 14 deliverable: `v5_dsr_audit.json`

Consumes Step 5 cell_summary.json + benchmark 0050 monthly returns →
produces per-cell L1-L6 PASS/FAIL + DSR Ψ + GO/NO-GO outcome.

L1-L6 (LOCKED v3.3 retail-realistic):
  L1: IR vs 0050 monthly active ≥ 0.20
  L2: monthly net α ≥ 0.005 (cost 0.0067 × turnover_one_way; turnover
       proxy = 2.0 for v5.0 monthly long-only top_n at v3.3-era ratio)
  L3: TE vs 0050 ∈ [0.10, 0.30]
  L4: Max DD diff vs 0050 ≤ +0.05
  L5: active_corr ≤ 0.50 AND TE ≥ 0.10 AND beta-adj α t-stat > 1.5
  L6: 80% bootstrap CI on monthly active returns lower > 0
  (L7 NOT in v5.0 binding per pre-reg §10)

DSR per cell:
  Ψ = deflated_sharpe_ratio(observed_sr=per_period_active_sr, n_obs=n_oos_periods, n_trials=400)
  where per_period_active_sr = mean(active)/std(active) per-period (NOT annualized,
  NOT absolute oos_sharpe). Aligns DSR with L1-L6 active-return basis.
  Ψ ≥ 0.95 → strong (per Bailey-LdP 2014); n_trials=400 reflects pre-reg
  §8.4 Option A multi-test family.

  Pre-reg §8.4 originally specified oos_sharpe; per-period switch requires
  amendment + audit chain re-sign (see H_v5_1_dsr_amendment.md).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.analysis.active_correlation import active_corr
from src.analysis.ic_analysis import deflated_sharpe_ratio

logger = logging.getLogger(__name__)


# LOCKED per pre-reg §10
L1_IR_THRESHOLD = 0.20
L2_ALPHA_MONTHLY_THRESHOLD = 0.005
L3_TE_LOW = 0.10
L3_TE_HIGH = 0.30
L4_MAX_DD_DIFF_LIMIT = 0.05
L5_ACTIVE_CORR_LIMIT = 0.50
L5_BETA_ADJ_T_THRESHOLD = 1.5
L6_BOOTSTRAP_CI_LEVEL = 0.80
L6_BOOTSTRAP_BLOCK_LEN = 3
L6_BOOTSTRAP_N = 10_000
L6_BOOTSTRAP_SEED = 42

COST_PER_TURNOVER_ONE_WAY = 0.0067   # pre-reg §10
DEFAULT_TURNOVER_ONE_WAY = 2.0       # legacy proxy — superseded by compute_actual_turnover()
DSR_N_TRIALS = 400                   # pre-reg §8.4 Option A LOCK
# DSR_N_TRIALS is module-level for ergonomic import; tests that need a different
# value MUST pass the dsr_n_trials kwarg explicitly to evaluate_cell_gates /
# run_audit_from_cell_summary rather than monkeypatching this module constant
# (monkeypatching leaks across tests in the same process).
ANNUALIZATION = 12


def compute_actual_turnover_one_way(
    holdings_by_period: list[set[str]],
) -> float:
    """Compute realised average per-period one-way turnover from actual portfolio
    holdings, replacing the v3.3-era 2.0 proxy.

    For an equal-weight long-only top_n portfolio:
        turnover_one_way[t] = |new_t \\ held_{t-1}| / top_n
    (fraction of position that's NEW vs prior period)

    Returns the average one-way turnover across periods (excluding the first
    period which has no prior). For monthly rebalance with 30% name turnover,
    this yields ~0.30 ⇒ cost = 0.0067 × 0.30 = 0.20%/month.
    """
    if len(holdings_by_period) < 2:
        return 0.0
    turnovers = []
    for prev, curr in zip(holdings_by_period[:-1], holdings_by_period[1:]):
        if not prev or not curr:
            continue
        # New names entering this period as fraction of current position count
        new_names = len(curr - prev)
        denom = max(len(curr), 1)
        turnovers.append(new_names / denom)
    return float(np.mean(turnovers)) if turnovers else 0.0


def compute_beta_adj_t_stat(
    portfolio: pd.Series, bench: pd.Series,
) -> tuple[float, float, float]:
    """Proper OLS regression intercept t-stat (replaces naive residual-mean / SE).

    OLS y = α + β·x + ε:
        SE(α) = sqrt(MSE × (1/n + x̄² / Σ(x − x̄)²))
        t(α) = α / SE(α)
    where MSE = Σε² / (n − 2).

    Returns (alpha, beta, t_alpha).
    """
    n = len(portfolio)
    if n < 3 or bench.var(ddof=1) <= 0:
        return 0.0, 0.0, 0.0
    x = bench.astype(float).values
    y = portfolio.astype(float).values
    x_mean = x.mean()
    y_mean = y.mean()
    sxx = float(((x - x_mean) ** 2).sum())
    if sxx <= 0:
        return 0.0, 0.0, 0.0
    sxy = float(((x - x_mean) * (y - y_mean)).sum())
    beta = sxy / sxx
    alpha = y_mean - beta * x_mean
    residuals = y - alpha - beta * x
    mse = float((residuals ** 2).sum() / (n - 2))
    se_alpha = float(np.sqrt(mse * (1.0 / n + x_mean ** 2 / sxx)))
    t_alpha = alpha / se_alpha if se_alpha > 0 else 0.0
    return float(alpha), float(beta), float(t_alpha)


@dataclass
class GateResult:
    name: str
    value: float | None
    threshold_desc: str
    passed: bool
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": (None if self.value is None else float(self.value)),
            "threshold": self.threshold_desc,
            "passed": bool(self.passed),
            "note": self.note,
        }


DSR_FORMULA_VERSION = "2026-05-28-per-period-active-sr"
DSR_OBSERVED_SR_BASIS = "per_period_active_sr (mean(active)/std(active), monthly)"
# Bump on any breaking change to audit JSON shape so downstream consumers can
# detect drift instead of silently reading a stale schema.
AUDIT_JSON_SCHEMA_VERSION = "2"  # v2 adds dsr_observed_sr fields over v1


@dataclass
class CellAudit:
    cell_id: str   # e.g. "xgboost_top_n_15"
    model_name: str
    top_n: int
    oos_sharpe: float
    n_oos_periods: int
    gates: list[GateResult] = field(default_factory=list)
    dsr_psi: float | None = None
    dsr_n_trials: int = DSR_N_TRIALS
    # The actual SR fed to DSR (not oos_sharpe — see DSR_OBSERVED_SR_BASIS)
    dsr_observed_sr: float | None = None
    dsr_observed_sr_basis: str = DSR_OBSERVED_SR_BASIS
    dsr_formula_version: str = DSR_FORMULA_VERSION
    n_gates_pass: int = 0
    all_gates_pass: bool = False

    def to_dict(self) -> dict:
        return {
            "cell_id": self.cell_id,
            "model_name": self.model_name,
            "top_n": self.top_n,
            "oos_sharpe": self.oos_sharpe,
            "n_oos_periods": self.n_oos_periods,
            "dsr_psi": self.dsr_psi,
            "dsr_n_trials": self.dsr_n_trials,
            "dsr_observed_sr": self.dsr_observed_sr,
            "dsr_observed_sr_basis": self.dsr_observed_sr_basis,
            "dsr_formula_version": self.dsr_formula_version,
            "n_gates_pass": self.n_gates_pass,
            "all_gates_pass": self.all_gates_pass,
            "gates": [g.to_dict() for g in self.gates],
        }


# ---------------------------------------------------------------------------
# Per-cell L1-L6 computation
# ---------------------------------------------------------------------------
def _bootstrap_ci_lower(returns: pd.Series,
                        block_len: int = L6_BOOTSTRAP_BLOCK_LEN,
                        n_boot: int = L6_BOOTSTRAP_N,
                        ci_level: float = L6_BOOTSTRAP_CI_LEVEL,
                        seed: int = L6_BOOTSTRAP_SEED) -> float:
    """Stationary block bootstrap (Politis-Romano 1994) on mean.

    Returns the lower bound of the CI at `ci_level` (e.g. 0.80 → 10th pctile).
    """
    rng = np.random.RandomState(seed)
    arr = returns.dropna().values
    n = len(arr)
    if n < 2:
        return float("nan")
    means = np.empty(n_boot)
    p = 1.0 / max(block_len, 1)
    for i in range(n_boot):
        sample = np.empty(n)
        idx = rng.randint(0, n)
        for j in range(n):
            sample[j] = arr[idx]
            if rng.random() < p:
                idx = rng.randint(0, n)
            else:
                idx = (idx + 1) % n
        means[i] = sample.mean()
    means.sort()
    lo_idx = int((1 - ci_level) / 2 * n_boot)
    return float(means[lo_idx])


def evaluate_cell_gates(
    oos_returns: pd.Series,         # monthly portfolio returns (12 OOS months)
    bench_monthly: pd.Series,        # 0050 monthly returns aligned to oos_returns
    cell_id: str,
    model_name: str,
    top_n: int,
    oos_sharpe: float,
    turnover_one_way: float | None = None,    # None → DEFAULT_TURNOVER_ONE_WAY
    dsr_n_trials: int = DSR_N_TRIALS,
) -> CellAudit:
    """Compute L1-L6 + DSR for one cell."""
    audit = CellAudit(
        cell_id=cell_id, model_name=model_name, top_n=top_n,
        oos_sharpe=oos_sharpe, n_oos_periods=int(len(oos_returns)),
        dsr_n_trials=int(dsr_n_trials),
    )

    # Align bench to oos_returns dates
    bench_aligned = bench_monthly.reindex(oos_returns.index).dropna()
    common_idx = oos_returns.index.intersection(bench_aligned.index)
    portfolio = oos_returns.loc[common_idx].astype(float)
    bench = bench_aligned.loc[common_idx].astype(float)
    active = portfolio - bench

    if len(active) < 2:
        audit.gates = [GateResult(name=name, value=None, threshold_desc="-",
                                  passed=False, note="insufficient n_oos")
                       for name in ("L1_IR", "L2_alpha", "L3_TE", "L4_DD_diff",
                                    "L5_combo", "L6_CI80")]
        return audit

    # L1: IR = mean(active) / std(active) × sqrt(12)
    te_monthly = float(active.std(ddof=1))
    ir_annual = float(active.mean() / te_monthly * np.sqrt(ANNUALIZATION)) \
                if te_monthly > 0 else 0.0
    te_annual = te_monthly * np.sqrt(ANNUALIZATION)
    audit.gates.append(GateResult(
        name="L1_IR", value=ir_annual,
        threshold_desc=f">= {L1_IR_THRESHOLD}",
        passed=(ir_annual >= L1_IR_THRESHOLD),
    ))

    # L2: monthly net α — prefer actual turnover; else proxy
    gross_alpha = float(active.mean())
    effective_turnover = (turnover_one_way if turnover_one_way is not None
                          else DEFAULT_TURNOVER_ONE_WAY)
    cost = COST_PER_TURNOVER_ONE_WAY * effective_turnover
    net_alpha = gross_alpha - cost
    turnover_src = "actual" if turnover_one_way is not None else "proxy=2.0"
    audit.gates.append(GateResult(
        name="L2_net_alpha_monthly", value=net_alpha,
        threshold_desc=f">= {L2_ALPHA_MONTHLY_THRESHOLD} "
                       f"(cost={cost:.4f}, turnover={effective_turnover:.3f} {turnover_src})",
        passed=(net_alpha >= L2_ALPHA_MONTHLY_THRESHOLD),
    ))

    # L3: TE band
    audit.gates.append(GateResult(
        name="L3_TE_annual", value=te_annual,
        threshold_desc=f"in [{L3_TE_LOW}, {L3_TE_HIGH}]",
        passed=(L3_TE_LOW <= te_annual <= L3_TE_HIGH),
    ))

    # L4: max DD diff
    def _mdd(r):
        nav = (1 + r).cumprod()
        peak = nav.cummax()
        return float((nav / peak - 1).min())
    mdd_port = _mdd(portfolio)
    mdd_bench = _mdd(bench)
    dd_diff = mdd_bench - mdd_port   # positive if port's DD worse than bench
    audit.gates.append(GateResult(
        name="L4_max_dd_diff", value=dd_diff,
        threshold_desc=f"<= {L4_MAX_DD_DIFF_LIMIT}",
        passed=(dd_diff <= L4_MAX_DD_DIFF_LIMIT),
    ))

    # L5: active_corr ≤ 0.50 AND TE ≥ 0.10 AND beta-adj α t > 1.5
    try:
        ac = active_corr(portfolio, bench)
    except Exception:
        ac = float("nan")
    # proper OLS intercept SE (was naive residual SE)
    _alpha_ols, _beta_ols, t_alpha = compute_beta_adj_t_stat(portfolio, bench)
    l5_pass = (
        (not np.isnan(ac) and ac <= L5_ACTIVE_CORR_LIMIT) and
        te_annual >= L3_TE_LOW and
        t_alpha > L5_BETA_ADJ_T_THRESHOLD
    )
    audit.gates.append(GateResult(
        name="L5_active_corr_te_t",
        value=ac,
        threshold_desc=(
            f"active_corr <= {L5_ACTIVE_CORR_LIMIT} (got {ac:.3f}) "
            f"AND TE >= {L3_TE_LOW} (got {te_annual:.3f}) "
            f"AND beta-adj-t > {L5_BETA_ADJ_T_THRESHOLD} (got {t_alpha:.3f})"
        ),
        passed=l5_pass,
    ))

    # L6: 80% bootstrap CI lower > 0
    ci_lower = _bootstrap_ci_lower(active)
    audit.gates.append(GateResult(
        name="L6_bootstrap_ci80_lower", value=ci_lower,
        threshold_desc=f"> 0 (block_len={L6_BOOTSTRAP_BLOCK_LEN}, "
                       f"n={L6_BOOTSTRAP_N}, seed={L6_BOOTSTRAP_SEED})",
        passed=(ci_lower > 0),
    ))

    # DSR input must be PER-PERIOD ACTIVE Sharpe (mean/std of monthly active),
    # not annualized portfolio Sharpe. Two reasons:
    #   (a) DSR must match the basis of L1-L6 (all active-return based)
    #   (b) Mertens variance formula assumes per-period SR with per-period n_obs;
    #       annualizing one side creates a unit mismatch.
    active_mean_per_period = float(active.mean())
    active_std_per_period = float(active.std(ddof=1))
    if active_std_per_period > 1e-12:
        per_period_active_sr: float | None = (
            active_mean_per_period / active_std_per_period
        )
    else:
        # Degenerate std: keep dsr_observed_sr=None so JSON readers can
        # distinguish "no signal computable" from "signal computed = 0".
        per_period_active_sr = None
    audit.dsr_observed_sr = (
        float(per_period_active_sr) if per_period_active_sr is not None else None
    )
    if per_period_active_sr is None:
        audit.dsr_psi = None
    else:
        try:
            psi = deflated_sharpe_ratio(
                observed_sr=per_period_active_sr,
                n_obs=audit.n_oos_periods,
                n_trials=dsr_n_trials,
            )
            audit.dsr_psi = float(psi) if psi is not None else None
        except Exception as exc:
            logger.warning("DSR failed for %s: %s", cell_id, exc)
            audit.dsr_psi = None

    audit.n_gates_pass = sum(int(g.passed) for g in audit.gates)
    audit.all_gates_pass = (audit.n_gates_pass == 6)
    return audit


def run_audit_from_cell_summary(
    cell_summary_path: str,
    bench_monthly: pd.Series,
    out_path: str,
    sharpe_diff_threshold: float = 0.05,
    dsr_n_trials: int = DSR_N_TRIALS,
) -> dict:
    """Read cell_summary JSON → run audit → write v5_dsr_audit.json.

    Returns the audit dict.
    """
    with open(cell_summary_path, encoding="utf-8") as f:
        cs = json.load(f)

    audits: list[CellAudit] = []
    baseline_by_top_n = {b["top_n"]: b for b in cs.get("baseline_cells", [])}

    for c in cs.get("ml_cells", []):
        oos_dates = [pd.Timestamp(d) for d in c["oos_dates"]]
        oos_returns = pd.Series(c["oos_monthly_returns"], index=oos_dates)
        cell_id = f"{c['model_name']}_top_n_{c['top_n']}"
        # compute realised turnover from holdings if present
        holdings = c.get("oos_holdings", [])
        actual_turnover = None
        if holdings:
            holdings_sets = [set(h) for h in holdings]
            actual_turnover = compute_actual_turnover_one_way(holdings_sets)
        audit = evaluate_cell_gates(
            oos_returns, bench_monthly, cell_id=cell_id,
            model_name=c["model_name"], top_n=c["top_n"],
            oos_sharpe=c["oos_sharpe"],
            turnover_one_way=actual_turnover,
            dsr_n_trials=dsr_n_trials,
        )
        audits.append(audit)

    # Vs baseline
    vs_baseline = []
    for a in audits:
        base = baseline_by_top_n.get(a.top_n)
        base_sharpe = base["oos_sharpe"] if base else float("nan")
        diff = a.oos_sharpe - base_sharpe
        vs_baseline.append({
            "cell_id": a.cell_id,
            "ml_sharpe": a.oos_sharpe,
            "baseline_sharpe": base_sharpe,
            "sharpe_diff": diff,
            "beats_baseline_by_threshold": diff >= sharpe_diff_threshold,
        })

    # Overall verdict
    cells_pass_all_gates = [a for a in audits if a.all_gates_pass]
    cells_beat_baseline = [
        v for v in vs_baseline if v["beats_baseline_by_threshold"]
    ]
    verdict = "GO" if cells_pass_all_gates and cells_beat_baseline else "NO-GO"

    out = {
        "schema_version": AUDIT_JSON_SCHEMA_VERSION,
        "audit_date": pd.Timestamp.now().isoformat(timespec="seconds"),
        "source_cell_summary": cell_summary_path,
        "dsr_n_trials": dsr_n_trials,
        "dsr_formula_version": DSR_FORMULA_VERSION,
        "sharpe_diff_threshold": sharpe_diff_threshold,
        "n_cells": len(audits),
        "n_cells_pass_all_gates": len(cells_pass_all_gates),
        "n_cells_beat_baseline": len(cells_beat_baseline),
        "verdict": verdict,
        "cell_audits": [a.to_dict() for a in audits],
        "vs_baseline": vs_baseline,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    logger.info("audit written: %s | verdict=%s | %d/%d cells pass all gates",
                out_path, verdict, len(cells_pass_all_gates), len(audits))
    return out
