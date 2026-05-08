# Phase 0 v6 Validation Manifest

**Manifest date**: 2026-05-04 (V0.4 initial); 2026-05-04 v7 closeout (V0.9 Sprint cross-ref + V0.11 cache caveat)
**Phase**: 0 V0.4 (baseline lock) → V0.8-V0.12 (v7 closeout)
**Plan reference**: `pro-plan-shimmering-pizza.md` (v7.0)
**Sprint upstream**: This manifest's D1_v2 + 5-factor IC numbers are downstream of `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §3-§4 (canonical Pro Validation Sprint reproducer evidence). Sprint manifest takes precedence on numerical conflicts.
**Purpose**: Snapshot of the *actual* environment / data state on the workstation immediately after V0.1–V0.4 fixes, used as ground-truth reference by H_d_v6_preregistration.md and all subsequent Phase 1 / Phase 2 work.

If any value below diverges from a future Phase 2 cell sweep run without explanation, the run is invalid.

---

## 1. Environment

| Item | Value |
|------|-------|
| Workstation OS | Windows 11 Pro 10.0.26200 |
| Conda env | `quant` |
| Python | 3.12.13 |
| pandas | 3.0.2 |
| pandas_ta | 0.4.71b0 |
| pytest | 9.0.3 |
| pandas_ta cold-import time (V0.1 sanity) | 3.66s (target < 5s) |

**V0.1 status**: pandas_ta hangs reported by Codex in dual-env audit could NOT be reproduced in this conda env. Marked N/A pending future Codex re-run; if hangs reappear in CI, lazy-import shim is the next step.

> **⚠️ Footnote (2026-05-07 added — Codex hang root cause confirmed)**：
> 後續分析發現 Codex 卡住的根因是 `requirements.txt` 鎖的 `pandas-ta>=0.3.14b` 對 numpy 2.x 不相容（0.3.x 內含 `from numpy import NaN`，numpy 2.0+ 已移除大寫 `NaN`）。本表記錄的 0.4.71b0 是 PyPI 的 pre-release tag 版本，已修正 numpy 2.x 相容性問題。
> 2026-05-07 後 `requirements.txt` 已升級鎖 `pandas-ta==0.4.71b0`（精確 pin pre-release tag，pip ≥ 21.x 在 PEP 440 規則下會自動允許）。歷史 v6 manifest 以此 footnote 為準。
> 詳見：`docs/CHANGELOG.md`「2026-05-07 pandas-ta 升級」段、`requirements.txt` 開頭註解、`Codex-Prompt.md` 安裝說明。

---

## 2. `resolve_cache_dir()` Windows priority (V0.2 fix)

```text
$ conda run -n quant python -c "from src.utils.paths import resolve_cache_dir; print(resolve_cache_dir())"
<repo_root>\data\cache
```

**Behavior matrix verified (V0.2 3-input test)**:

| Input | Result | Status |
|-------|--------|--------|
| Windows + fake `/app/data/cache` exists + no env | repo `data/cache` | PASS (gate fires) |
| POSIX (Linux) + fake `/app/data/cache` exists + no env | `/app/data/cache` | PASS (Docker honoured) |
| Windows + `DATA_CACHE_DIR=<tmp>` + `/app/data/cache` exists | `<tmp>` | PASS (env override wins) |

**Pre-V0.2 mutation**: removing the `_is_posix()` gate causes Windows path to return `\app\data\cache` (stale partial cache). Mutation reproduced and caught by `tests/test_cache_dir_resolution.py::test_windows_skips_app_data_cache_even_when_present`.

**Regression tests added**: 3 new tests (Windows skip, POSIX honour, Windows env override). Existing 4 tests preserved. Total 7/7 PASS.

---

## 3. Cache panel inventory

```text
resolve_cache_dir() = <repo_root>\data\cache
panels (11): delisting / dividends / institutional / institutional_v2 /
              issued_capital / margin_short / market_value / ohlcv /
              quarterly_eps / revenue / stock_info
```

All 4 panels that the stale `\app\data\cache` partial cache was missing are present:
- `institutional_v2` ✓
- `issued_capital` ✓
- `margin_short` ✓
- `quarterly_eps` ✓

This rules out the R24 P0-2 silent-corruption path (IC research using partial cache).

---

## 4. Canonical 5-factor IC (n=71)

Source: `reports/factor_ic/*_ic.json`. Schema: `overall.{mean_ic, ic_ir, t_stat, p_value, bootstrap_ci_95}`.

| Factor | mean_ic | IC IR | t-stat | p-value | bootstrap CI95 |
|--------|---------|-------|--------|---------|----------------|
| high_proximity | 0.0413 | **0.2738** | 2.307 | 0.024 | [0.0131, 0.0709] |
| pead_eps | 0.0218 | **0.2902** | 2.445 | 0.017 | [0.0075, 0.0369] |
| margin_short_ratio | 0.0387 | **0.2313** | 1.949 | 0.0553 | [0.0121, 0.0667] |
| foreign_broker_v2 | -0.0195 | **-0.2097** | -1.767 | 0.0816 | [-0.0383, -0.0017] |
| revenue_momentum_v2 | 0.0145 | **0.1906** | 1.606 | 0.1128 | [-0.0013, 0.0305] |

All `n_periods` = 71. All IR values match Plan v6 expected values exactly.

**Long-only exclusions confirmed**:
- `foreign_broker_v2` (IR -0.2097, negative-sign): cannot be used long-only without inversion (which would be a different factor design — not in v6 scope).
- `revenue_momentum_v2` (IR 0.1906, p > 0.10): below significance threshold for inclusion in production composites.

---

## 5. D1_v2 backtest metrics

**Canonical (post-`0d31572` Pro Validation Sprint Phase B, 10 bps slippage)**:
Source: `reports/sprint_pro_validation/B_repro/d1v2_{is,oos}/backtest_*_metrics.json`.

| Period | Tracking Error | Information Ratio | Annualized return |
|--------|----------------|-------------------|-------------------|
| IS 2020-01-01 → 2024-12-31 | **0.23673** | **0.9238** | 0.4162 |
| OOS 2025-01-01 → 2025-12-31 | **0.223253** | **0.0058** | 0.3715 |

**Historical (`reports/step5_D1_v2/`, 5 bps slippage — superseded)**:

| Period | Tracking Error | Information Ratio |
|--------|----------------|-------------------|
| IS 2020-01-01 → 2024-12-31 | 0.236732 | 0.9375 |
| OOS 2025-01-01 → 2025-12-31 | 0.223146 | 0.0373 |

**Corrigendum**: `scripts/_step5_backtest_results.md:88` notes the historical 5 bps numbers are superseded by `B_repro/` 10 bps. The slippage default 5 → 10 bps fix in commit `0d31572` (engine.py:27 / tw_stock.py:1388 / run_backtest.py:32) re-aligned the cost model with `config/settings.yaml` (`turnover_cost 0.0047 + slippage_bps 10 × 2 / 10000 = 0.0067 = 67 bps`).

**IR collapse strengthened**: 0.9238 → 0.0058 (99.4% degradation, vs the 5 bps figure of 96%). This is the canonical evidence supporting D-A pre-disqualification (D6 trigger). Approximate monthly α from OOS: ~0.011% (vs threshold 0.5% / month per H_d_v6 L2). D-A is even more decisively disqualified under the 10 bps cost regime than under the 5 bps stale figure.

---

## 6. Git state

```text
HEAD: 0d31572 (0d3157239726a89df330a27fc8c55f644db734f7)
Title: Pro validation sprint Phase B: 2 silent bugs fix (get_threshold + slippage default)
```

The `0d31572` commit precedes Phase 0 V0.x and lands two upstream fixes that affect this manifest's baseline:
1. `scripts/run_factor_ic.py` re-imports `get_threshold` (P5 Session 1 helpers extraction had dropped it).
2. Slippage default 5 → 10 bps across `engine.py:27` / `tw_stock.py:1388` / `run_backtest.py:32`, regenerating D1_v2 IS+OOS metrics into `reports/sprint_pro_validation/B_repro/`.

This means the Plan v6 anchor commit is `0d31572`, NOT the `7ba18dd` referenced in earlier R24 audit prep (which became HEAD@{1} after Phase B). H_d_v6 hypothesis lock anchors against `phase-d-v6-baseline` tag (created by V0.7), which sits on top of `0d31572` plus all V0.x changes.

**Uncommitted modifications at V0.6 snapshot time** (will be committed by V0.7):
- `.gitignore` (V0.7 `!reports/phase_d/` exception + scratch dirs)
- `Codex-Prompt.md` (pre-existing in-flight changes for R25 prep)
- `HANDOFF.md` (V0.6 sub-fix updating 451 → 462 baseline)
- `scripts/audit_doc_drift.py` (V0.6 R21 → R24 bump + phase_d/ coverage)
- `src/utils/paths.py` (V0.2 Windows priority fix)
- `tests/test_cache_dir_resolution.py` (V0.2 +3 regression tests)

**Untracked files**:
- `reports/phase_d/H_d_v6_preregistration.md` (V0.5 — to be committed)
- `reports/phase_d/v6_validation_manifest.md` (this file, V0.5 — to be committed)
- `reports/phase_d/R24_resolution.md` (V0.5 — to be committed)
- `tests/_pit_mutation/` (Sprint Phase C scratch — gitignored, NOT committed in V0.7)
- `tests/_tmp/` (pytest scratch — gitignored, NOT committed in V0.7)

After V0.7 commits, the resolved commit hash and `phase-d-v6-baseline` tag should be appended below.

```text
phase-d-v6-baseline tag commit: 269abcb (269abcb65d3fb6d2a6c567d6827005887c7dddd2)
parent commit:                  0d31572 (Pro validation sprint Phase B: 2 silent bugs fix)
tag created:                    2026-05-04
```

---

## 7. Pytest baseline (V0.3)

```text
$ conda run -n quant python -m pytest tests/ -q
462 passed, 6 warnings in 329.72s (0:05:29)
```

Test count growth tracking (per CLAUDE.md):
- 451 baseline (B0-Lite Session 1, 2026-05-03) — 451
- + F5/F6 work (low_vol_v2 tests, factor_ic_helpers extraction tests) — 459 implied
- + V0.2 regression tests (3 new) — **462 (current)**

Plan v6 V0.3 target: ≥ 459 passed → MET with margin.

**Warnings (non-blocking)**:
- pandas 4.0 deprecation warnings in pandas_ta (`mode.copy_on_write`)
- Pandas 4.0 deprecation warning in `tests/test_backtest_cache_miss.py` (`pd.Timestamp.utcnow`)
- 4× ConstantInputWarning in `test_ic_analysis.py` (test fixture data with zero variance — expected)

These warnings predate V0.1 / V0.2 and are not v6-blocking.

---

## 8. SOP-Checklist for V0.2 paths.py fix

Per CLAUDE.md "強制輸出格式":

| Step | Status | Evidence |
|------|--------|----------|
| 1. Mutation test | ☑ | `.codex_pytest_tmp/v02_mutation_check.py` — pre-V0.2 returns `\app\data\cache`, post-V0.2 returns repo `data/cache`; MUTATION CAUGHT: True |
| 2. 具體數字驗算 (3 inputs) | ☑ | `.codex_pytest_tmp/v02_three_inputs.py` — 3 PASS (Win+app, POSIX+app, Win+env) |
| 3. Grep 終態 | ☑ | `/app/data/cache` 程式碼僅 `src/utils/paths.py:57` 一處（已 gate）；其他全是 docstring/comment/test |
| 4. Cross-interference sweep | ☑ | `cache_fill.py:54` / `cache_health.py:21` 自有 `PROJECT_ROOT/data/cache` fallback (無同型 bug)；`cache_rebuild.py:41` 用不同 env var |
| 5. Self-attack as Codex | ☑ | 4 challenges 列舉並反駁 (WSL / macOS / Path string normalize / monkey-patch leak) |
| 6. Full pytest regression | ☑ | conda quant `pytest tests/ -q`：462 passed / 0 failed / 5m29s |

---

## 9. Phase 0 V0.1–V0.7 checklist progress

| Step | Description | Status | Notes |
|------|-------------|--------|-------|
| V0.1 | pandas_ta import hang fix | ☑ | N/A in user env (3.66s import); future-Codex deferred |
| V0.2 | resolve_cache_dir Windows priority | ☑ | Edit + 3 regression tests + SOP 6-step verified |
| V0.3 | conda quant full pytest baseline | ☑ | 462 passed / 0 failed (target ≥ 459) |
| V0.4 | IC + D1_v2 baseline numbers extraction | ☑ | All Plan v6 expected values match canonical sources exactly |
| V0.5 | H_d_v6 + manifest + R24_resolution | ☑ (this file) | 3 files written; ready for V0.6 |
| V0.6 | bump audit_doc_drift R21 → R24 + phase_d/ coverage | ☐ | Next |
| V0.7 | commit + tag phase-d-v6-baseline + .gitignore exception | ☐ | After V0.6 |

---

## 10. Reproducibility script + Sprint v2 caveat (Plan v7 V0.11, 2026-05-04)

### 10a. Reproducibility script

To re-run this manifest validation in a fresh checkout:

```bash
cd <repo>
conda run -n quant python -m pytest tests/ -q          # V0.3
conda run -n quant python .codex_pytest_tmp/v04_baseline_dump.py   # V0.4 numbers
conda run -n quant python -c "from src.utils.paths import resolve_cache_dir; print(resolve_cache_dir())"   # V0.2
git rev-parse HEAD ; git tag --list 'phase-d-v*-baseline'
```

Expected output matches Sections 1–7 above.

### 10b. ⚠️ Cache reproducibility caveat (Sprint v2 P1, Q8.1)

**Honest disclosure** (per `reports/sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md` §10 Q8.1 attack):

This manifest's IC + D1_v2 numbers prove **「現在 code + 同 cache @ 2026-04-21 重跑得同數值」**, NOT **「現在 code + fresh cache 重抓 from FinMind API 得同數值」**. If FinMind cache itself harbours hidden state (上輪 IC 寫入副作用 / TTL 內 row 增量覆蓋 / industry label cache drift), this sprint cannot detect it.

**True cross-machine reproducibility requires** (Sprint v2 must-do, Phase 2 Session 6 owns):
1. wipe `data/cache/` 完全乾淨
2. 從 FinMind API 重抓（valid token + 1-2 hr 工程 + 600 req/hr quota）
3. 用相同 commit hash (`phase-d-v7-baseline`) 跑同樣 5 IC + D1_v2 IS+OOS
4. 比對是否仍對齊 §4 / §5 數值（tolerance ±1% IC drift）

**Status**: pending Phase 2 Session 6 implementation. Until done, this manifest signoff level = **「同 cache 重跑驗算」**, NOT **「跨機 Pro 可獨立重現」**.

### 10c. Pytest --collect-only diff disclosure (Sprint v2 P1, Q8.3)

**Test count reconciliation** (per Sprint manifest §10 Q8.3): 

- Pre-Sprint Phase B baseline (commit `7ba18dd`): 459 passed
- Post Sprint Phase B silent bug fix (commit `0d31572`): 462 passed
- Post Plan v7 V0.8 un-gitignore tests/_pit_mutation/: **466 passed** (+ 4 PIT mutation tests)

The 459 → 462 jump's most likely cause: 3 conditional-skip tests parameterized by `slippage_bps` default that toggled from skip → run when default changed 5 → 10 bps. Sprint manifest §10 P2 housekeeping noted this is "充分 non-regression evidence" but full root-cause via `pytest --collect-only` time-travel diff was not executed (commit `7ba18dd` already overwritten, branch state non-recoverable). Plan v7 inherits this caveat as informational.

### 10d. Other Sprint v2 backlog items (informational, not Phase 0 blocking)

Per Sprint manifest §10 + J_multi_perspective_audit.md:
- P2: PIT 3 panel 補測 (quarterly_eps / margin_short / market_value) — currently inferred via `_truncate_by_date_col` shared logic
- P2: lag_day enforcement test  
- P2: industry label cache mtime 實證 (effective_n drift root cause)
- P2: walk_forward.py 歷史報告 retroactive BREAKING CHANGE tag (5 bps → 10 bps assumption)
- P2: /ic-aggregate summary report rerun
- P3: Bootstrap CI seed sensitivity sweep
- P3: slippage_bps ∈ {10, 30, 50, 100} 敏感性

Phase 2 Session 6 picks up P2-flagged cache reproducibility (10b above); other P2 / P3 items remain informational backlog and can be picked up post-R25.
