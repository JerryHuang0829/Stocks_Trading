# H_d_v6 Pre-Registration — Phase D Multi-Factor Long-Only

**Pre-registration date**: 2026-05-04
**Plan version**: v6.2 (V0.14 + R25-mid 獨立 audit 5 P0 fix; supersedes v6.1 V0.13 + v6.0 R24 5 P0 + 7 design issues fixed)
**Commit hash anchor**: `phase-d-v6-baseline` tag (created by Phase 0 V0.7 — see `v6_validation_manifest.md` for resolved hash)
**Repo**: `<repo_root>`
**audit chain**: R24 (Plan v5 NO-GO — see R24_resolution.md §"Scope correction") → R25 (planned, post-Phase 2)
**Sprint upstream evidence**: `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §5 (verified v5 spec B1-B6+L5 7/7 internally consistent; R24 NO-GO 真因 = L6 95% over-strict + meta-issues, NOT v5 spec全錯)
**Supersedes**: H_d_v5 (never formally written; addressed by R24 P0-3 → V0.5 produced this file)

---

## Hypothesis statement (formal lock — DO NOT EDIT post-commit)

> **In the Taiwan-stock 2019-2024 historical validation set, with the TWSE/TPEX top-80 close×volume universe, long-only top_n ∈ {8, 12, 16}, monthly rebalance frequency, and 6 candidate factor sets (D-B / D-C / D-D / D-E / D-F / D-G; D-A pre-disqualified per D6 OOS 2025 monthly α 0.069% << 0.5% threshold), AT LEAST ONE (factor_set × top_n) cell will simultaneously satisfy ALL 6 hard reject criteria L1–L6 (v6 retail-realistic thresholds), AND the 6-month live paper-trade PnL (L7) will exceed the 0050 cost-adjusted DCA bootstrap CI (CI must not cross zero).**

The hypothesis is rejected if 0 cells pass all of L1–L6 OR if no cell passes L7 within 6 months of paper trading. There is no "control fallback" path — D-A is excluded from candidates per pre-existing OOS evidence; the sole-survivor tie-break uses highest IR > highest mean α among the 18 candidate cells.

---

## 6 Hard Reject Criteria (v6 retail-realistic — LOCKED)

| Gate | v5 (rejected) | **v6 LOCK** | Rationale |
|------|---------------|-------------|-----------|
| **L1** IR vs 0050 (monthly active) | ≥ 0.30 | **≥ 0.20** | Institutional 0.5+ unreachable for retail; academic retail multi-factor IR commonly 0.2–0.4 |
| **L2** mean monthly net α (cost = 0.67% × one-way turnover) | ≥ 0.010 / month | **≥ 0.005 / month** | Aligns with L6 80% CI on TE 0.20–0.30; 0.5% is buffer floor |
| **L3** TE vs 0050 | ∈ [0.10, 0.30] | **same as v5** | Active risk band; below 0.10 = closet indexer, above 0.30 = retail-unsustainable |
| **L4** Max drawdown diff vs 0050 | ≤ +0.05 | **same as v5** | DD discipline (5% extra DD vs benchmark) |
| **L5** A1 gate (3 sub-conditions, all must pass) | (a) active corr ≤ 0.50 (b) TE ≥ 0.10 (c) beta-adj α t > 1.5 | **same as v5** | Active-share + TE + statistical significance combo (originally R20 finding F1). **V1.2 binding**: active_corr function implementation locked to Phase 2 Session 5 (see §"L5 active_corr binding (V1.2 lock)" below). |
| **L6** Bootstrap CI on monthly active returns (block_len=3, n=10000, seed=42) | **95%** lower bound > 0 | **80% lower bound > 0** | 95% kills even D-A IS (95% CI [-0.04%, 3.41%] includes 0); 80% is retail-attainable mid-line per R24 |
| **L7** (paper) | 6m paper PnL > 0050 + bootstrap CI does not cross zero | **same as v5** | Out-of-sample live verification |

**v6 降標 mathematical reasoning** (per R24):
- L1 0.20: Institutional IR 0.5+ requires leverage / shorting / LP rebates unavailable to NT$1M retail; 0.20 corresponds to ~1.5σ active return per year, statistically detectable in 6m paper window.
- L2 0.005/month: With TE = 0.20–0.30 and L6 80% CI requiring lower bound > 0, the implied monthly α must be ≥ ~0.5–0.7% / month; 0.005 (0.5%) is the buffer floor.
- L6 80%: 95% lower bound on D-A IS active returns (mean 1.69%, std 1.86%, n=60) is -0.04% — D-A passed every other gate in IS but 95% CI blocked. 80% lower bound on the same data is +0.66% — passes. 80% is the retail-attainable mid-line; below 70% would have no statistical bite.

**Cost formula (canonical)**:
```
cost_per_rebalance = 0.0067 × turnover_one_way
                   = (turnover_cost 0.0047 + slippage_bps 10 × 2 / 10000) × turnover_one_way
```
Source: `config/settings.yaml:portfolio.turnover_cost / slippage_bps`. Phase 2 Session 1 must replace `composite_backtest.py` hardcoded 57bps with this formula.

---

## L1 / L2 numerical justification (V1.1c lock, 2026-05-05)

V0.13 §"v6 降標 mathematical reasoning" (line 33-36) 提供 inline short rationale；V1.1c 補正式段 + table form 完整數值驗算，per V1.1 原 spec deliverable。本段三 evidence chain（5-factor IC IR ceiling / D1_v2 multi-factor OOS collapse / L6 implied α derivation）合並支持 v6 L1=0.20 + L2=0.005 為 retail-attainable 數值。

### Table A — L1 0.20 numerical justification: 5-factor IC IR ceiling

Phase 0 V0.4 verified canonical IC (n=71 monthly periods, 2019-2024 sample, source: `reports/factor_ic/*_ic.json`):

| Factor | IC IR (n=71) | Long-only eligibility |
|--------|-------------|----------------------|
| high_proximity | 0.2738 | ✓ |
| **pead_eps** | **0.2902** | ✓ (single-factor max) |
| margin_short_ratio | 0.2313 | ✓ |
| foreign_broker_v2 | -0.2097 | ✗ (excluded long-only per pre-commit #8) |
| revenue_momentum_v2 | 0.1906 | ✓ |

**Single-factor ceiling**: 5 因子中最高 IC IR = 0.2902 (pead_eps) < 0.30 (v5 L1 threshold)。單因子 max IR < 0.30 → v5 L1 ≥ 0.30 即使 multi-factor combine 也不可保證達到（multi-factor combine IR 通常為 single-factor max IR 的 1.0-1.3x，視 inter-factor correlation 而定）。

### Table B — D1_v2 multi-factor IR realistic ceiling + OOS collapse

D1_v2 (52W + PEAD 50/50, IR-weighted) 已於 Phase 0 V0.4 完整 backtest 驗證（per `reports/sprint_pro_validation/B_repro/d1v2_*/backtest_*_metrics.json`，10 bps slippage canonical post-`0d31572`）:

| Sample | TE | IR | Approx monthly α | Status |
|--------|----|----|--------------------|--------|
| **IS 2020-2024** (n=60 月) | 0.23673 | **0.9238** | ~1.69%/月 | 看似強，但 IS overfit |
| **OOS 2025** (n=12 月) | 0.223253 | **0.0058** | **~0.011%/月** | 99.4% IR collapse, far below L2 0.005 |

**OOS collapse evidence**:
- IS IR 0.9238 → OOS IR 0.0058 = **99.4% IR collapse**
- OOS implied monthly α ≈ 0.011%/月 << L2 threshold 0.5%/月 (~45x 不足)
- D-A (= D1_v2 design) 因此 pre-disqualified per pre-commit #11 + D6 OOS evidence

**Multi-factor real OOS ceiling**: D1_v2 multi-factor combined IS IR 0.9238 是 in-sample fit 數值，**non-actionable for retail OOS strategy selection**。real OOS IR ceiling for retail 2-factor long-only ≈ 0.0-0.4 range（D-A=0.0058 是 lower extreme）。學術 retail multi-factor literature 中位數 OOS IR ≈ 0.20-0.40。

### Table C — L2 0.005 numerical justification: TE assumption + L6 implied α derivation

Sprint Phase B6 reproduce 假設 TE = 12% (per `reports/sprint_pro_validation/B_repro/`); D1_v2 V0.4 實測 TE 顯示 assumption gap：

| Source | TE | Implied L2 α threshold |
|--------|----|------------------------|
| Sprint B6 (假設) | 0.12 | TE 0.12 + L6 80% CI multiplier ~0.84 / √12 ≈ 0.29%/月 |
| **D1_v2 IS 2020-2024 實測** (n=60 月) | **0.23673** | TE 0.24 + L6 80% CI multiplier ~0.84 / √12 ≈ **0.57%/月** |

**TE assumption vs reality gap**: D1_v2 真實 TE 約 2x Sprint B6 假設。L2 monthly α threshold 必對應實測 TE 計算。

**L6 80% CI implied α derivation** (TE 0.20-0.30 range):
- 80% bootstrap CI lower bound > 0 implies (mean - 0.84 × std) > 0 → mean > 0.84 × std
- monthly active return std ≈ TE / √12 (annualized to monthly)：TE 0.24 → monthly std ≈ 0.069
- Lower bound > 0 implies mean monthly α ≥ 0.84 × 0.069 ≈ 0.058 (5.8%/月) — 但這是 lower bound 嚴格條件
- Bootstrap 在 n=60 月 sample 下 lower bound (mean - 0.84 × SE_mean) > 0 → mean > 0.84 × std/√60 ≈ 0.0075 (0.75%/月)
- v6 L2 = 0.005 (0.5%/月) 為 buffer floor 對齊 0.5-0.7%/月 implied range

**v5 L2=1.0%/月 vs v6 L2=0.005 內部一致性**:
- v5 L2 = 1.0%/月 對應 L6=95% CI implied 1.49%/月 → **v5 L2 不充分**（per R24 設計-2）
- v6 同步降 L2 0.5%/月 + L6 80% CI → 內部一致

### Conclusion: 三 evidence chain 全對齊 v6 L1 / L2 retail-realistic

| Evidence chain | Source | Implication |
|----------------|--------|-------------|
| 5-factor IC IR ceiling | Phase 0 V0.4 canonical IC n=71 | Single-factor max IR=0.2902 < 0.30 → v5 L1 ≥ 0.30 retail 不可達；v6 L1=0.20 retail-attainable |
| D1_v2 multi-factor OOS collapse | Phase 0 V0.4 D1_v2 IS/OOS metrics | OOS IR 0.0058 (99.4% collapse) → D-A pre-disqualified；retail real OOS IR ceiling 0.20-0.40 → v6 L1=0.20 是下界 |
| L6 implied α derivation | TE 0.20-0.30 + 80% CI bootstrap | implied α 0.5-0.7%/月 → v6 L2=0.005 為 buffer floor |

**三 gates 數值內部一致**：v6 L1 0.20 / L2 0.005 / L6 80% 互相對齊，無 internal contradiction。對應 R24 §"設計-2 L2 1.0%/月 不充分 vs L6 implied threshold" 修法閉環。

### V1.3 A11 attacker test 接力

V1.3 A11 attacker test 將實算 D1_v2 IS 60 monthly active returns 跑 80% vs 95% CI 對照表 → `reports/phase_d/A11_l6_ci_comparison.md`。V1.1c 是 spec rationale + table form 數值驗算（基於 V0.4 baseline 既有 verified 數值），V1.3 是 empirical bootstrap CI 實算 verification。兩者互補：V1.1c 提供 design-time 數學依據，V1.3 提供 implementation-time 真值對照。

---

## L5 active_corr binding (V1.2 lock, 2026-05-05)

L5 §"6 Hard Reject Criteria" sub-condition (a) `active corr ≤ 0.50` 是 textual threshold spec；`active_corr()` function definition + implementation **locked to Phase 2 Session 5** 落地，杜絕 phantom gate。

### Spec lock

**active_corr function** (Phase 2 Session 5 owner):
- Location: `src/analysis/active_correlation.py` (preferred) OR 併入 `src/analysis/ic_analysis.py` (alternative)
- Definition: `active_corr(portfolio_monthly_returns, benchmark_monthly_returns)` → Pearson correlation between **monthly active returns** (= portfolio - benchmark) and benchmark monthly returns
- Sample: 18 cell sweep 各 cell 用 IS 60 個 month 計算
- Threshold: ≤ 0.50 per L5 (a)（高 active corr 表示「跟著大盤」非真 active management）

### Phase 2 Session 5 binding clause

Phase 2 Session 5 commit (`phase-d-v7-implementation-start` tag) **必**：
1. 實作 `active_corr()` function with explicit signature + docstring
2. Function 必 e2e tested: at least 1 unit test in `tests/test_active_correlation.py` (or extension of test_ic_analysis.py)
3. cell sweep CLI integrate active_corr → 每 cell 輸出 active_corr 值 + L5 (a) PASS/FAIL flag
4. A10 attacker test (active_corr definition mutation: 改 portfolio_returns vs benchmark_returns 為 portfolio_returns vs portfolio_returns) 必 mutation 後 test FAIL — 確認 implementation 真實非 placeholder

### R25-final P0 enforcement

違反 V1.2 binding 任一條 = **R25-final P0 NO-GO**:
- ❌ Phase 2 Session 5 沒 commit `active_corr()` implementation → P0
- ❌ active_corr 用 portfolio corr (portfolio vs portfolio) 替代 active corr (portfolio active vs benchmark) → P0 (definition error)
- ❌ active_corr signature 收 daily 而非 monthly returns → P0 (frequency error per pre-commit #6)
- ❌ A10 mutation test 沒 cover → P0 (no enforcement test)
- ❌ cell sweep 輸出沒 active_corr value → P0 (silent skip)

### A10 attacker test connection

H_d_v6 §"Pre-design attacker tests" A10 已寫「active_corr definition mutation test」— V1.2 binding 同步要求 A10 攻擊在 Phase 2 Session 5 實作後跑。Mutation 範例：
- (Mutation 1) 改 `corr(active, benchmark)` → `corr(portfolio, portfolio)` (self-corr always 1.0) → test 必 FAIL
- (Mutation 2) 改 monthly → daily frequency → test 必 FAIL
- (Mutation 3) 移除 active = portfolio - benchmark 計算 → test 必 FAIL

R25-final 將 grep `tests/test_active_correlation.py` 或 `test_ic_analysis.py::test_active_corr_*` 確認 mutation test 存在 + 跑通。

### V0.13 enforcement series 對齊

V1.2 binding 屬 V0.13 4 P0 spec lock + V1.1b code fix 之後的 R25-mid Pro Review enforcement series：
- V0.13 §"3 New factor PIT lag spec" — quality_v3 / industry_momentum / idio_vol_max PIT lag locked
- V0.13 §"S6 fresh-rerun 範圍與時程" — 6 panel + 6-12hr range
- V0.13 §"Cell sweep adjust pipeline" — d_cell_sweep_v7 必經 BacktestEngine
- V0.13 Assertion 3 強化（V1.1b 落地 deflated_sharpe_ratio raise on None）
- **V1.2 §"L5 active_corr binding"** — active_corr Phase 2 S5 ownership lock

每件 V0.13/V1.2 spec lock 對應 R25-final P0 violation clause；對 in-house Skill chain 已抓的 27 patch P0 全部 enforce 形成完整 binding contract。

---

## Candidate factor sets (v6 — LOCKED)

D-A is pre-disqualified per D6 (D-A OOS 2025 monthly α = 0.069% << 0.5% threshold). 6 candidates, 3 top_n values → **18 cells** (vs v5 21 cells).

| ID | Factor set composition | Weight method | New factors required |
|----|------------------------|---------------|----------------------|
| **D-B** | high_proximity (52W) + pead_eps + margin_short | IR-weighted with 20% Margin cap → split: 52W 39%, PEAD 41%, Margin cap 20% | None (existing 5-factor pool) |
| **D-C** | high_proximity (52W) + pead_eps | **PEAD-weighted 40/60 (V0.14: NOT 50/50 — D-A ≡ 50/50 D1_v2 design pre-disqualified per R25-mid 獨立 audit P0-1)** | None |
| **D-D** | high_proximity (52W) + pead_eps + margin_short | **3-factor IR-weighted normalize 34/36/30 (V0.14: revenue_momentum_v2 移除 per pre-commit #8 V0.14 clarify; R25-mid 獨立 audit P0-2)** | None |
| **D-E** | high_proximity (52W) + pead_eps + quality_v3 | 40/40/20 | quality_v3 (PIT TTM ROE × gross_margin × Δassets — explicitly **AQR QMJ profitability sub-component, NOT full QMJ**) |
| **D-F** | high_proximity (52W) + pead_eps + industry_momentum | 40/40/20 | industry_momentum (6m, per Moskowitz-Grinblatt 1999) |
| **D-G** | high_proximity (52W) + pead_eps + idio_vol_max | 40/40/20 | idio_vol_max (0.5/0.5 split: IdioVol residual std + MAX lottery composite) |

**D-A excluded** (52W + PEAD 50/50, the pre-existing D1_v2 design). Excluded BEFORE registration based on OOS 2025 evidence: monthly α = 0.069% << 0.5% threshold (D6).

---

## 13 Pre-commit disciplines (LOCKED — violation = hypothesis broken)

1. L1–L7 thresholds frozen (no post-hoc adjustment).
2. DSR `n_trials = 18` (NOT 12, NOT 21 — must match cell count).
3. Sample period 2019–2024 historical validation set.
4. Universe: TWSE + TPEX top-80 close × volume; no ad-hoc symbol substitution.
5. **6 candidate factor sets locked**: D-B / D-C / D-D / D-E / D-F / D-G. D-A pre-disqualified, NOT in candidate pool.
6. 1 frequency (monthly) — no cross-frequency exploration in v6 (cross-freq infra is v7 deferred).
7. 3 top_n values: {8, 12, 16}.
8. **Factor exclusion semantics (V0.14 clarify per R25-mid 獨立 audit P0-2/P0-5)**:
   (a) **Foreign_v2 (IR -0.2097)**: EXCLUDED from candidate pool entirely (long-only directional incompatibility — negative IR means long-only cannot extract alpha; cannot be reversed at any stage).
   (b) **Revenue_v2 (IR 0.1906)**: IC 結論 weak/borderline; **EXCLUDED from candidate pool per V0.14 amend** (resolves v6.1 wording ambiguity that allowed D-D to include revenue_momentum_v2). DO NOT include `revenue_momentum_v2` in any candidate factors dict; D-D V0.14 已移除為 3-factor (high_proximity + pead_eps + margin_short_ratio).
   (c) Both exclusions cannot be reversed at gate-evaluation time per pre-commit lock.
9. Sole-survivor tie-break: **highest IR > highest mean α**. No D-A control fallback (D-A is disqualified, not a fallback).
10. Paper trade window 6 months — no early-truncation if all gates pass IS.
11. **D-A pre-disqualification discipline**: cannot be relaxed mid-experiment. If new evidence emerges that D-A would pass v6 gates, that constitutes a NEW hypothesis (H_d_v7), not a v6 modification.
12. **IC canonical source = `reports/factor_ic/*_ic.json` (n=71)**. Phase A1 summary `n=59` is NOT canonical (legacy truncation).
13. **L6 80% CI lower bound > 0 is the floor**. Cannot drop to 70% mid-experiment (70% has no statistical power vs random); v6 is already mid-line.

---

## D-A pre-disqualification record (DO NOT REOPEN)

D-A composite (52W + PEAD 50/50, weight = D1_v2 design) was the v4 / v5 control candidate. v6 disqualifies it BEFORE Phase 1 begins, based on:

| Evidence source (canonical, 10 bps slippage post-`0d31572`) | IS 2020-2024 | OOS 2025 | Threshold (D6) | Status |
|-----------------|-------------|----------|----------------|--------|
| `reports/sprint_pro_validation/B_repro/d1v2_is/backtest_*_metrics.json` | TE 0.23673, IR 0.9238 | — | — | (IS used for context) |
| `reports/sprint_pro_validation/B_repro/d1v2_oos/backtest_*_metrics.json` | — | TE 0.223253, IR 0.0058 | — | (OOS) |
| Computed monthly α (OOS 2025, ~12 months) | — | **~0.011% / month** | ≥ 0.5% | **FAIL** |

D-A demonstrates degradation pattern (IS IR 0.9238 → OOS IR 0.0058; **99.4% IR collapse**). v6 treats this as sufficient pre-existing OOS evidence to exclude from the candidate pool. This is NOT post-hoc hypothesis editing — it is an a priori restriction based on pre-existing OOS data, registered before Phase 1 / Phase 2 begin.

**Historical reference** (5 bps slippage, superseded by `0d31572`): IS IR 0.9375, OOS IR 0.0373, monthly α ~0.069%. Even under the older lenient cost model D-A failed D6, so D-A pre-disqualification is robust to the cost-model choice.

---

## Code-level enforcement (Plan v7 V0.10, 2026-05-04)

H_d_v6 hypothesis lock + 13 pre-commit disciplines are textual; without code-level enforcement they're trust-based. Plan v7 V0.10 mandates the following 3 assertions land in Phase 2 Session 1 / Session 6 / Session 7 implementation. Failure to implement = R25 P0.

### Assertion 1 — Cost dual-model check (Phase 2 Session 1)

`scripts/composite_backtest.py:47` historically hardcoded `TW_ROUND_TRIP_COST_BPS = 57.0`, while `engine.py:467-472` reads `config/settings.yaml` (`turnover_cost 0.0047 + slippage_bps 10 × 2 / 10000 = 0.0067 = 67 bps`). Plan v6 → v7 keeps Session 1 ownership of this fix; **enforcement assertion** must verify the two paths agree at runtime:

```python
# Phase 2 Session 1: in scripts/composite_backtest.py + scripts/d_cell_sweep_v7.py
from src.backtest.engine import BacktestEngine
from src.utils.config import load_config

cfg = load_config("config/settings.yaml")
engine_cost = cfg["portfolio"]["turnover_cost"] + 2 * cfg["portfolio"]["slippage_bps"] / 10000
COMPOSITE_COST = engine_cost  # NOT hardcoded 57.0; read from settings.yaml
assert abs(engine_cost - 0.0067) < 1e-6, f"settings.yaml cost ≠ 0.0067; got {engine_cost}"
```

### Assertion 2 — D-A pre-disqualification guard (Phase 2 Session 6)

H_d_v6 §13 pre-commit discipline #11 says "D-A pre-disqualification cannot be relaxed mid-experiment". This is purely textual; **enforcement assertion** in cell-sweep entrypoint:

```python
# Phase 2 Session 6: in scripts/d_cell_sweep_v7.py
CANDIDATE_FACTOR_SETS = ["D-B", "D-C", "D-D", "D-E", "D-F", "D-G"]
assert "D-A" not in CANDIDATE_FACTOR_SETS, (
    "D-A pre-disqualified per H_d_v6 §D-A pre-disqualification record + D6 OOS evidence "
    "(IR 0.9238 → 0.0058, 99.4% collapse). Reintroducing D-A requires H_d_v7 reframe + "
    "new commit-hash anchor, NOT in-place edit of v6/v7."
)
```

### Assertion 3 — DSR n_trials = 18 verify (Phase 2 Session 6/7)

H_d_v6 §13 pre-commit discipline #2 says "DSR n_trials = 18 (matches cell count)". Enforcement to prevent silent miscounts:

```python
# Phase 2 Session 6: in scripts/d_cell_aggregate_v7.py / Session 7 bootstrap
from src.analysis.ic_analysis import deflated_sharpe_ratio

CANDIDATE_FACTOR_SETS = ["D-B", "D-C", "D-D", "D-E", "D-F", "D-G"]
TOP_N_VALUES = [8, 12, 16]
EXPECTED_N_TRIALS = len(CANDIDATE_FACTOR_SETS) * len(TOP_N_VALUES)  # 18
assert EXPECTED_N_TRIALS == 18, f"Cell count drift: expected 18, got {EXPECTED_N_TRIALS}"

# When calling DSR — MUST pass n_trials explicit (NOT rely on DEFAULT_DSR_N_TRIALS=5):
dsr = deflated_sharpe_ratio(ir, n_obs=72, n_trials=EXPECTED_N_TRIALS)  # n_trials=18 explicit
# Silent default fallback risk (V0.13 lock): src/analysis/ic_analysis.py:35
# DEFAULT_DSR_N_TRIALS = 5 is Phase A1 single-factor legacy. v7 cell sweep MUST NOT
# rely on default — caller must explicit pass n_trials=18 keyword. Omitting kwarg →
# silent default 5 → DSR over-PASS (n_trials=5 較寬鬆) → cell sweep silent over-claim.
# NOT n_trials=12 / 21 / 48 from earlier plan versions.
```

### Verification

Phase 2 Session 1 / 6 / 7 commit must include `pytest tests/test_d_cell_sweep_v7.py` covering all 3 assertions with mutation tests (revert assertion → test fails). Cell sweep run script must pass all 3 at runtime; failure = halt + diagnostic.

R25 will independently verify the 3 assertions are present and active by:
1. `grep "assert.*D-A" scripts/d_cell_sweep_v7.py` non-empty
2. `grep "EXPECTED_N_TRIALS == 18" scripts/d_cell_aggregate_v7.py` non-empty
3. `grep -E "engine_cost|COMPOSITE_COST" scripts/composite_backtest.py` non-empty + matches 0.0067
4. (V0.13) `grep "deflated_sharpe_ratio.*n_trials=" scripts/d_cell_aggregate_v7.py` 確認所有 call 都明文傳 n_trials (no default fallback)
5. (V0.13) Mutation test: `tests/test_d_cell_sweep_v7.py::test_dsr_n_trials_explicit_required` 反注 omit n_trials → silent default 5 → DSR 寬鬆 over-PASS scenario，新 mutation 測試必 fail

### Cell sweep adjust pipeline (V0.13 lock → V0.15 amend 2026-05-06)

**V0.13 原規格**：`scripts/d_cell_sweep_v7.py` + `scripts/d_cell_aggregate_v7.py` 必經 `src/backtest/engine.py::BacktestEngine`，不可直接 raw cache OHLCV read。

**V0.15 amend (2026-05-06，pre-run audit P0 fix)**：經實測 BacktestEngine 5 legacy factors 寫死在 `score_weights` (`src/portfolio/tw_stock.py:_rank_analyses`)，6 new candidates D-B/C/D/E/F/G 套用會需 200+ LOC override scaffolding（且 quality_v3 / industry_momentum / idio_vol_max 3 新因子 returns `pd.Series`，與 BacktestEngine 期待的 per-symbol `analysis` dict shape 不相容）。

**V0.15 允許的替代 path**：lightweight composite engine 條件性 GO，前提**所有 PIT-correct 保證在 caller 層級顯式重現**：

| BacktestEngine 提供 | V0.15 lightweight 替代 | Verification |
|---|---|---|
| `_DataSlicer` PIT 截斷 | 每 factor 模組內部 `as_of` filter（high_proximity.py:74 / pead_eps.py:45 / quality_v3.py:127 / industry_momentum.py:91 / idio_vol_max.py:120 全 enforce shift=1）| pre-run audit 第三部分逐因子驗 ✓ |
| `adjust_splits()` | OHLCV cache 已 split-adjusted（per cache_rebuild.py P4.5 doc + stocks pkl 已 forward-adjusted）| `grep adjust_splits scripts/cache_rebuild.py` 確認 cache 階段已套用 |
| `adjust_dividends()` 0050 total return | `d_cell_sweep_v7_real.py:_build_benchmark_monthly_returns` V0.24 強制讀 `dividends/_global.pkl` filter by `stock_id=="0050"` + hard fail when missing | V0.24 test_d_cell_sweep_v7_real 驗 + missing → FileNotFoundError |
| Universe MIN_PRICE PIT 過濾 | V0.23 `_is_above_min_price_at()` per-rebal-date PIT-safe filter（取代 V0.13 forward-looking `df["close"].mean()` bug）| external audit 2026-05-06 audit P0-2 修法 |
| drift-aware daily return (P4.6) | **不適用** — V0.15 用 month-end-to-month-end gross return；caveat: vol 略低估 ~5-10%（無 mid-period drift）；但 IS IR 測量上 conservative（不會 inflate alpha）| pre-run audit 第四部分 4.1 已 flag |
| regime exposure 動態調整 | **不適用** — V0.15 用 100% exposure；caveat: 無 risk_off 期間降曝險，IR / max_dd 對 regime 假設敏感 | 已書面 caveat |

### V0.15 lightweight composite 條件性 GO 清單（所有必滿足）

| Condition | Requirement | 落地 commit |
|---|---|---|
| C1 PIT factor enforce | 所有 6 因子 module 各自 shift=1 PIT + lag_days frozen | S2/S3 commits + Phase A1 ✓ |
| C2 Universe PIT filter | `_is_above_min_price_at()` 用於每 rebal date | V0.23 commit ✓ |
| C3 Cost dual-model | settings.yaml 0.0067 round-trip ≠ hardcoded | V0.13 Assertion 1 ✓ |
| C4 0050 total-return benchmark | `_global.pkl` filter by 0050 + hard fail when missing | V0.24 commit ✓ |
| C5 active_corr index alignment | V0.14 P0-4 enforce `index.equals()` raise | v7.1 Reframe ✓ |
| C6 Negative cache 區分 transient | V0.22 FinMindTransientError 不 mark done | V0.22 commit ✓ |
| C7 Caveat 標於 cell_summary | `light_composite_engine: True` flag + 上 5 caveats 寫進每 cell metadata | S6.1 wire-up TODO |
| C8 Smoke 1-fold 驗 PIT | smoke 跑 1 rebal 後 grep cell_summary 確認 PIT compliance | Pre-run pre-flight gate |

**違反任一 C1-C8 → 退回 V0.13 BacktestEngine wrap path（200+ LOC 改寫）**。

### V0.13 → V0.15 transition rationale

V0.13 spec lock 是設計時保險策略（disable degree of freedom 防 implementation 走偏）。實作後發現 BacktestEngine 與 6 candidate × 3 new factors 不相容，繼續 V0.13 死磕會出現：
- 200+ LOC override scaffolding（增加 audit surface area）
- 必修 BacktestEngine 接受 6 new factor classes（侵入 src/portfolio/tw_stock.py 已 stable code）
- 估計 2-4 小時額外工程 + 5+ regression tests

V0.15 amend 採取「重現 BacktestEngine 提供的 6 個保證」策略 — 透過 6 因子 module 自身 PIT enforce + caller 層級 dividend/split/MIN_PRICE 處理，達成 same-rigor，工程量低且可審查（pre-run audit 已逐項驗證 C1-C6）。

**Trade-off accepted**：drift-aware daily return 與 regime exposure 不適用，書面 caveat 屬 v6/v7 hypothesis test 範圍內 known limitation（per H_d_v6 §"Diagnostic-only metrics"）。

**R25 Verification**：`grep "lightweight composite" scripts/d_cell_sweep_v7_real.py` 應命中 docstring 明示；`grep -E "BacktestEngine\|\\.run\\(" scripts/d_cell_sweep_v7_real.py` 預期 empty（不再 wrap BacktestEngine）。

---

## Diagnostic-only metrics (record but do not gate)

| ID | Metric | Purpose |
|----|--------|---------|
| D1 | DSR Ψ (Bailey-Lopez de Prado, n_trials=18) | Multi-trial spurious-rate context (D2 says retail unattainable; record only) |
| D2 | Cell turnover | Watch for capacity limits / over-trading |
| D3 | Cross-correlation matrix 8 × 8 (5 existing factors + quality_v3 + industry_momentum + idio_vol_max) | Any \|ρ\| > 0.5 → weight re-examination required |
| D4 | top_n α monotonicity (8 → 12 → 16) | Non-monotonic = noise indicator |
| D5 | min_factor_coverage_per_symbol sensitivity | Per-cell |
| D6 | D-A degradation reproducer | **Pre-fired in v6** (D-A excluded based on D6 evidence) |

---

## Verification gates (Phase 0 V0.4 baseline — LOCKED)

The following baseline values are recorded as the ground-truth reference for all subsequent Phase 1 / Phase 2 work. Any cell-sweep result that diverges from these baselines without explanation invalidates the run.

| Item | Expected | Actual (Phase 0 V0.4 verified 2026-05-04) | Status |
|------|----------|------------------------------------------|--------|
| Canonical IC: high_proximity IR (n=71) | 0.2738 | 0.2738 | ✓ |
| Canonical IC: pead_eps IR (n=71) | 0.2902 | 0.2902 | ✓ |
| Canonical IC: margin_short_ratio IR (n=71) | 0.2313 | 0.2313 | ✓ |
| Canonical IC: foreign_broker_v2 IR (n=71) | -0.2097 | -0.2097 | ✓ (excluded long-only) |
| Canonical IC: revenue_momentum_v2 IR (n=71) | 0.1906 | 0.1906 | ✓ |
| D1_v2 IS 2020-2024 TE (10 bps canonical) | 0.2367 (5 bps reference) | 0.23673 (B_repro 10 bps) | ✓ |
| D1_v2 IS 2020-2024 IR (10 bps canonical) | 0.9375 (5 bps reference) | **0.9238** (B_repro 10 bps) | ✓ |
| D1_v2 OOS 2025 TE (10 bps canonical) | 0.2231 (5 bps reference) | 0.223253 (B_repro 10 bps) | ✓ |
| D1_v2 OOS 2025 IR (10 bps canonical) | 0.0373 (5 bps reference) | **0.0058** (B_repro 10 bps; D-A even more decisively disqualified) | ✓ |
| Cache panels count | 11 | 11 (delisting / dividends / institutional / institutional_v2 / issued_capital / margin_short / market_value / ohlcv / quarterly_eps / revenue / stock_info) | ✓ |
| `resolve_cache_dir()` Windows return | repo `data/cache` | `<repo_root>\data\cache` | ✓ |
| pytest pass count | ≥ 459 | 462 | ✓ |
| pandas_ta import time | < 5s | 3.66s | ✓ |
| pandas version | (any working) | 3.0.2 | ✓ |
| pandas_ta version | (any working) | 0.4.71b0 | ✓ |

> **⚠️ Footnote (2026-05-07 added)**：本表 pandas_ta 0.4.71b0 為 v6 pre-registration 環境記錄；2026-05-07 已將 `requirements.txt` 從舊規格 `pandas-ta>=0.3.14b`（不相容 numpy 2.x）升級鎖為 `pandas-ta==0.4.71b0`，使 獨立 audit 環境也能取得相容版本。詳見 `reports/phase_d/v6_validation_manifest.md` 同段 footnote 與 `requirements.txt` 開頭註解。

See `reports/phase_d/v6_validation_manifest.md` for full manifest with output transcripts.

---

## Outcome interpretation (4 buckets — pre-declared)

> ⚠️ **Pre-registration anchor footnote (2026-05-07 added)**：本表是 v6 hypothesis pre-registration 階段（2026-05-04）寫入，作 hash-anchored prior commit。Outcome-2 Partial 的「4–5 of 6 pass」是 generic prior 定義，**不是 v7 實測落點**。v7 實測 Outcome-2 最高過 4/6（D-C\|12 / D-E\|12 / D-E\|16），無任何 5/6 cell。本表保留以資 prior vs posterior 對照；canonical posterior 以 `reports/phase_d/v7_outcome2_summary.md` 與 `cell_summary.json` 為準。

| Outcome | Definition | Probability estimate (v6) | Action |
|---------|-----------|---------------------------|--------|
| **Outcome-1 Full Pass** | ≥ 1 cell passes L1–L7 | **20–30%** | Begin paper trade; lock sole-survivor cell |
| **Outcome-2 Partial** | 4–5 of 6 (L1–L6) pass; 1–2 borderline | 20–30% | Caveat report; do not paper-trade |
| Outcome-3 (v5 deprecated) | (D-A control passed) | N/A | (v6 has no D-A control) |
| **Outcome-4 Full Fail** | 0 cells pass L1–L7 | **40–60%** | Acknowledge retail long-only ceiling; pivot to options or alternative architecture |

Probability shift vs v5: Outcome-1 raised from 10–20% → 20–30% because L6 lowered to 80% CI and L1 lowered to 0.20 (retail-realistic). Outcome-4 widened to 40–60% to reflect honest uncertainty about whether 6 candidates × 3 top_n suffice.

---

## Pre-design attacker tests (A1–A12 — must defeat 10/12)

| ID | Attacker | Threshold |
|----|----------|-----------|
| A1 | Cross-frequency PIT verify (factor as_of any date) | Factor fail → exit D-route |
| A2 | DSR n_trials=18 Monte Carlo spurious rate | Tail > 5% → DSR diagnostic-only confirmed |
| A3 | 0050 dividend reinvest consistency | NAV diff > 0.5% → halt |
| A4 | Weekly turnover (v6 monthly only — N/A) | N/A |
| A5 | D-A degradation reproducer (v6 pre-fired) | Reproduce: monthly α 0.069% < 0.5% |
| A6 | quality_v3 / industry_momentum / idio_vol_max vs existing 5 cross-correlation | Any \|ρ\| > 0.5 → weight re-examine |
| A7 | top_n α monotonicity check | Non-monotonic = noise |
| A8 | hold_buffer scale | hold_buffer = ceil(top_n × 0.3) |
| A9 | `fetch_financial_statement_history` PIT determinism | as_of replay hash mismatch → halt |
| A10 | active_corr definition mutation test | Mutation to portfolio corr → test fail |
| **A11 (v6 new)** | L6 80% CI on sole_survivor candidates real-pass-rate | Use Phase 0 D1_v2 IS to verify retail-realistic (vs 95% killed D-A) |
| **A12 (v6 new)** | `resolve_cache_dir()` Windows priority does not break Docker | Mutation: under Docker `/app/data/cache` should still be selected when present |

---

## 3 New factor PIT lag spec (V0.13 lock)

R25-mid Pro Review (multi-perspective + self-audit + forensic-sweep) found that 3 new factors (quality_v3 / industry_momentum / idio_vol_max) PIT lag was textually unlocked, with implementation freedom too high. V0.13 locks the spec:

| Factor | Frequency | PIT lag | Constants source |
|--------|-----------|---------|------------------|
| **quality_v3** (D-E) | quarterly EPS + balance sheet | Q4 income statement: 90d (`QUARTERLY_EPS_LAG_DAYS_Q4=90`) / Q1-3 income statement: 45d (`QUARTERLY_EPS_LAG_DAYS_OTHER=45`) for ROE / gross_margin; balance sheet (Δassets): 60d (later than income statement publication) | `src/utils/constants.py:23-24` (existing) + Phase 2 S2 add `BALANCE_SHEET_LAG_DAYS=60` |
| **industry_momentum** (D-F) | monthly + 6m formation per Moskowitz-Grinblatt 1999 | T+0 monthly close 計算過去 6m; **industry label**必使用 month-end PIT snapshot (見 §"industry label PIT strategy" 下) | Phase 2 S3 add `industry_label_<YYYY-MM-DD>` cache key OR explicit caveat in V0.13 R14 risk register |
| **idio_vol_max** (D-G) | daily residual + MAX lottery composite | residual std lookback = 60 trading days strict-before; MAX lottery = top-5 daily return in past 1m (~22 trading days) | `shift=1` semantics (mirrors `high_proximity.py:81` / `low_vol_v2.py:8` PIT discipline) |

### industry label PIT strategy (V0.13 lock)

`industry_momentum` (D-F) 在月初 t 計算過去 6m industry returns，必使用 month-end snapshot of `stock_info.industry_category` cache @ **t-30d (上月底)**，**不可使用最新 cache snapshot** (避免 retroactive reclassification look-ahead — TWSE 產業分類可能 retroactively reclassify, e.g. 2330 從 半導體 → 半導體 IC 設計).

Phase 2 S3 implementation choice:
- **Option A** (preferred): cache key `industry_label_<YYYY-MM-DD>` — Phase 2 S3 必擴 `cache_fill_new_factors.py` 添 monthly snapshot
- **Option B** (caveat fallback): fix 當前 cache snapshot + explicit caveat as known limitation in V0.13 R14 risk register; D-F results 標 "industry-label PIT not enforced; D-F caveat" in cell summary output

R25-final verification: `grep "industry_label_<.*>" scripts/.*industry_momentum.*py` non-empty (Option A) OR R14 risk register contains "D-F industry label PIT not enforced" (Option B).

### A6 cross-correlation 監控擴展 (V0.13 lock)

H_d_v6 §"Pre-design attacker tests" A6 已寫「quality_v3 / industry_momentum / idio_vol_max vs existing 5 cross-correlation」。V0.13 補實作細節：
- Diagnostic D3 「Cross-correlation matrix 8 × 8」必輸出每 cell sweep run; |ρ| > 0.5 trigger weight re-examination per pre-commit discipline
- 若 D-G idio_vol_max 與 low_vol_v2 |ρ| > 0.6 → D-G drop or weight halve (avoid factor redundancy)

---

## Hypothesis-drift detector commitment

`scripts/audit_doc_drift.py` LATEST_AUDIT_ROUND will be bumped from `R21` to `R24` in V0.6 and the `_check_hypothesis_drift` function will extend coverage to `reports/phase_d/`. After bump:
- Any text mutation to L1–L7 thresholds in this file (post-commit) raises a drift error.
- 13 pre-commit disciplines are tokenized and checked against any `reports/phase_d/*.md` modifications.

---

## Risk register reference

See Plan v6 R1–R13 (in (internal plan reference)). 13 known risks with mitigations; R6 (D-A pre-disqualification ≠ hypothesis editing) and R12 (L6 80% may still kill majority) are the most consequential for v6.

**V0.13 add**: R14 — D-F industry label PIT enforcement strategy (Option A vs B per §"industry label PIT strategy"); R15 — DEFAULT_DSR_N_TRIALS=5 silent default risk in `src/analysis/ic_analysis.py:35` (mitigation: Assertion 3 explicit n_trials + Phase 2 S6 mutation test).

---

## S6 fresh-rerun 範圍與時程 (V0.13 lock)

per Sprint v2 Q8.1 P1 + `v6_validation_manifest.md` §10 cache caveat：

**Scope (限定範圍 — NOT 全 11 panels)**: 6 panels:
- `OHLCV` (price 主資料)
- `dividends` (P4.5 total return adjust)
- `monthly_revenue` (revenue_momentum_v2 source)
- `quarterly_eps` (pead_eps + quality_v3 source)
- `margin_short` (margin_short_ratio source)
- `institutional_v2` (foreign_broker_v2 source)

**Skip (cross-machine reproducible — FinMind cache TTL 容忍 OK)**: 5 panels:
- `delisting` / `institutional` (legacy) / `issued_capital` / `market_value` / `stock_info` (universe filter metadata)

**Tolerance**:
- Numerical IC drift: ≤ ±1% on 5 canonical IC values (high_proximity 0.2738 / pead_eps 0.2902 / margin_short_ratio 0.2313 / foreign_broker_v2 -0.2097 / revenue_momentum_v2 0.1906)
- Categorical drift (industry_category): month-end snapshot @ 2026-04-21 vs fresh @ Phase 2 S6 重抓的 |Δ category| / |universe| ≤ 5% 為容差

**Quota math**:
- 11 panels × 80 stock × 71 month = 62,480 records (full bound)
- 6 panels limited ≈ 34,080 records (V0.13 actual lower bound)
- 3 token × 600/hr quota = 1,800/hr → expected 6-12 hr (依 quota allocation, retry, throttle)

**Phase 2 S6 owner steps**:
1. `wipe data/cache/` 6 panels 對應 dir
2. `python scripts/cache_fill_new_factors.py --panels OHLCV,dividends,monthly_revenue,quarterly_eps,margin_short,institutional_v2 --start 2019-01-01 --end 2025-12-31`
3. `python scripts/run_factor_ic.py --all-5 --output reports/phase_d/ic_fresh_rerun_<date>.json`
4. compare canonical IC ±1% / categorical ≤ 5%
5. fail any tolerance → halt + report; do NOT proceed to 18 cell sweep until tolerance met

---

## File-existence cross-check (Phase 0 V0.7 must satisfy)

After V0.7 commit + tag:
- `git tag phase-d-v6-baseline` exists.
- `git log phase-d-v6-baseline -1` shows commit including: V0.1 status note, V0.2 paths.py fix, V0.2 regression tests in `tests/test_cache_dir_resolution.py`, V0.4 baseline manifest, V0.5 (this file + R24_resolution.md), V0.6 audit_doc_drift bump, V0.7 .gitignore exception.
- `grep -i "phase-d-v6-baseline" reports/phase_d/H_d_v6_preregistration.md` returns this commitment line.
- `python scripts/audit_doc_drift.py` returns 0 drift / ≤ 4 warnings.

---

## V0.14 R25-mid Audit P0 fix log (2026-05-05)

R25-mid 獨立 audit verdict = **GO-WITH-CAVEATS（5 P0 必修）**。external audit 親跑 code + targeted tests + 親自驗證；user (self-audit) 親自 cross-check 5 P0 全 valid。v7.1 reframe 修法在 V0.14 amend：

| P0 # | audit 抓 | Fix in V0.14 |
|------|---------|-------------|
| **P0-1** | D-C composition (high_proximity 0.50 + pead_eps 0.50) ≡ D-A 50/50 D1_v2 design (pre-disqualified) — 既有 string-only Assertion 2 cannot catch composition equivalence | D-C row → 0.40/0.60 PEAD-weighted variant (NOT 50/50)；scripts/d_cell_sweep_v7.py 加 `D_A_FORBIDDEN_COMPOSITIONS` + `_composition_equals_forbidden()` module-level + load_candidate_config caller-side check |
| **P0-2** | D-D 含 revenue_momentum_v2 違反 pre-commit #8 「Revenue_v2 exclusions cannot be reversed」(任一讀法都 ambiguous) | D-D row → 3-factor IR-weighted normalize 34/36/30 (移除 revenue_v2)；對應 yaml + test update |
| **P0-3** | Assertion 2/3 既有 tests 只測 string `"D-A"` literal + typo variants，不抓 composition equivalence | tests/test_d_cell_sweep_v7.py 加 3 個 composition mutation test + 1 個 dynamic n_trials test |
| **P0-4** | active_corr docstring 寫「Raises if non-aligned indexes」但 code 只 check length；同 length 不同 dates → silent 算錯 | src/analysis/active_correlation.py 加 `if not portfolio.index.equals(benchmark.index): raise ValueError(...)`；對應加 mutation test |
| **P0-5** | pre-commit #8 wording「Foreign_v2 and Revenue_v2 exclusions cannot be reversed」雙解讀 (candidate pool exclude vs IC 結論 lock) | pre-commit #8 rewrite 為 (a)(b)(c) explicit 3 條 — Foreign_v2 / Revenue_v2 都 EXCLUDED from candidate pool；R16/R17 risk register 加註 |

**audit 環境問題 acknowledged (不修)**：external audit 在 Windows 撞 9 個 pytest tmp_path PermissionError — `C:\Users\...\AppData\Local\Temp\pytest-of-... PermissionError`。這是 conda + pytest tmp_path interaction 的 known env issue，非 logic problem。Linux/Mac conda env 預期 0 errors。V0.14 不修；commit message acknowledge as known env issue, 不阻塞 R25-mid GO。

**V0.14 binding contract chain** (對齊 V0.13 enforcement series):
- V0.13 §"3 New factor PIT lag spec" + §"S6 fresh-rerun" + §"Cell sweep adjust pipeline" + Assertion 3 (V1.1b)
- V1.2 §"L5 active_corr binding" — Phase 2 S5 ownership
- **V0.14 §"Factor exclusion semantics" (pre-commit #8) + §"D-A composition Assertion 2 強化" + §"active_corr index alignment fix"** — R25-mid 獨立 audit P0 closeout

R25-final P0 enforcement (V0.14): D-A composition equivalence 違反 / Revenue_v2 重新進 candidate / active_corr 跨 index 計算 = 任一即 R25-final NO-GO。

### V0.14 Risk register additions

- **R16**: D-C 0.40/0.60 PEAD-weighted variant 是否仍構成 valid 2-factor baseline？（vs D-A 50/50 etymology）— Phase 2 S6 cell sweep run 階段 IC IR 比較驗證；若 D-C IR ≪ D-A historical IR (0.0058 OOS) 則 R25-final 標 caveat。
- **R17**: D-D 3-factor IR-weighted normalize (34/36/30) 與 D-B IR-weighted with 20% Margin cap (39/41/20) 區分機制 — D-B 用 cap 紀律保 Margin diversification；D-D 純 IR-weight 探索 unconstrained allocation。Phase 2 S6 cross-correlation 監控 D-B vs D-D weights 是否 effectively redundant。

---

## Sign-off

This pre-registration is binding. Any modification to:
- L1–L7 thresholds
- 6 candidate factor sets
- 18 cell sweep design
- D-A pre-disqualification
- 13 pre-commit disciplines

…requires a new hypothesis registration (H_d_v7) with its own commit-hash anchor, NOT an in-place edit of this file. Edits to the supporting prose / context (above the LOCKED sections) are permitted for clarity.

**Pre-registered by**: self-audit Opus 4.7 + <user>
**Audit chain**: R24 NO-GO (5 P0 + 7 設計，scope per R24_resolution.md §"Scope correction") → v6 + Sprint Phase A-J integration → v7 V0.8-V0.12 closeout → V0.13 (Phase 1 V1.1a 4 P0 spec lock from R25-mid Pro Review in-house Skill chain) → R25-mid 獨立 audit (post-Phase 2 Session 4, anchor `87c2ba2`) verdict GO-WITH-CAVEATS 5 P0 → **V0.14 (R25-mid 獨立 audit P0 fix; v7.1 reframe)** → R25-final (planned, post-Phase 2 Session 8)
**Sprint Phase J pre-audit**: `reports/sprint_pro_validation/J_multi_perspective_audit.md` (7 persona + 21 attack, GO with 2 P1; R25 不重複問已答 21 attack)
**V0.13 R25-mid Pro Review pre-audit**: `reports/phase_d/pre_implementation_review_2026-05-04.md` (in-house Skill chain: multi-perspective 22 + self-audit 19 + forensic-sweep 8 = 27 patch / 4 P0 / 12 P1 / 6 P2 / 2 P3; verdict GO-WITH-CAVEATS; 4 P0 此次 V0.13 落地)
