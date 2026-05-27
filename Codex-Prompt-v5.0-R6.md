# Codex Audit v5.0 R6 — Step 4-6 ML pipeline 全鏈 + NO-GO verdict 驗證

**Target audience**:Codex(獨立 audit agent,v5.0 final audit)
**Date anchor**:2026-05-26
**Audit scope**:Step 4-6 全鏈(ML pipeline 工程 + production run + audit + closeout)→ verify NO-GO verdict 與「95% 樣本天花板」根因 attribution 是否 defensible
**Pre-reg**:`reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`(v5.0.2 SIGNED 2026-05-26)
**Plan**:`reports/phase_d_v5/v5_ml_plan.md`(v5.0.2 SIGNED)
**Closeout target**:`reports/phase_d_v5/v5_closeout_outcome.md`

請用**繁體中文**回報。**不要相信本 prompt 任何宣稱** — 逐條獨立驗證。

### Shell 命令

**Bash(WSL/Git Bash)**:
```bash
NUMBA_DISABLE_JIT=1 PYTHONPATH=. "C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -u <script>
```

**PowerShell**:
```powershell
$env:NUMBA_DISABLE_JIT='1'; $env:PYTHONPATH='.'; & "C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -u <script>
```

Bash 構造在 PowerShell 不通:`grep` → `Select-String`,`| head -N` → `| Select-Object -First N`,`| tail -N` → `| Select-Object -Last N`,`diff <(...)` → `Compare-Object`,`sha256sum` → `Get-FileHash`。

---

## 0. Context — Audit chain 收斂歷史

| Round | 抓 findings | 結論 |
|---|---:|---|
| v5.0 R1 | 9 真(2 P0 + 4 P1 + 3 Extra) | NEEDS-FIX |
| v5.0 R2 | 5 真(5 P1) | NEEDS-FIX |
| v5.0 R3 | 4 真(3 P1 + 1 P2) | WAIT-FOR-FIX |
| v5.0 R4 | 3 真(2 P1 + 1 P2) | WAIT-FOR-FIX |
| v5.0 R5 | 1 P2(非阻斷) | **PASS** → user sign-off pre-reg v5.0.2 |
| **v5.0 R6 本輪** | TBD | Final audit on Step 4-6 全鏈 + NO-GO verdict |

R1-R5 audit chain 共 22 個 findings 全修(對 pre-registration 文件)。R6 audit 對象是**程式工程 + production results + closeout 結論**。

---

## 0.1 Step 4-6 inventory(2026-05-26 全部產出)

### Step 4 新模組(9 個,~3500 LOC)

| 模組 | LOC | tests |
|---|---:|---:|
| `src/analysis/ml_pooling.py` | ~290 | 29 |
| `src/analysis/ml_features.py` | ~150 | 5 |
| `src/analysis/ml_contextual.py` | ~270 | 14 |
| `src/analysis/cpcv.py` | ~170 | 17 |
| `src/analysis/ml_models.py` | ~210 | 14 |
| `src/analysis/ml_optuna.py` | ~270 | 11 |
| `src/analysis/ml_shap.py` | ~170 | 10 |
| `src/analysis/ml_audit.py` | ~260 | 7 |
| `src/analysis/ml_experiment.py`(orchestrator)| ~180 | 0(整合 by smoke + production) |

### Step 5 scripts(experiment runners)

| script | 用途 |
|---|---|
| `scripts/_run_v5_ml_experiment.py` | CLI:smoke / production mode |
| `scripts/_finalize_v5_smoke_deliverables.py` | smoke 收尾 |
| `scripts/_run_v5_step6_audit.py` | Step 6 audit |
| `scripts/_run_v5_fair_baseline_check.py` | architecture check |

### Step 5 deliverables(reports/phase_d_v5/)

| 檔 | 內容 |
|---|---|
| `v5_ml_cell_summary.json` | 8 ML cells × OOS Sharpe + best_hyperparams + 4 baselines |
| `v5_ml_vs_baseline.md` | ML vs baseline Sharpe diff table |
| `v5_shap_summary.json` | SHAP top 15 + 5 interaction strengths |
| `v5_outcome.md` | Production summary |
| `v5_dsr_audit.json` | DSR Ψ + L1-L6 per cell |
| `v5_final_outcome.md` | Step 6 audit deliverable |
| `v5_architecture_check.json` | Fair baseline + single factor results |
| `v5_closeout_outcome.md` | **Final NO-GO closeout report**(R6 主 target)|

### Production run 摘要

```
runtime:           13:43-14:34 (51 min)
IS rows:           74,618
OOS rows:          14,016
feature cols:      57 (5 base + 43 sector + 1 size + 3 regime + 5 interactions)
ML cells:          8 (2 models × 4 top_n)
Optuna trials:     400 (50 × 2 × 4, per pre-reg §8.4 Option A)
inner CV splits:   5 (TimeSeriesSplit)
SHAP samples:      14,016 (OOS)
```

### Verdict

**NO-GO**:0/8 cells pass all 6 hard gates;but 8/8 cells beat baseline by ≥ +0.05 Sharpe(per pre-reg §1 condition 1)。

---

## 1. Codex R6 職責

### 職責

1. **Block A**:Step 4 9 個新模組程式 audit(PIT discipline / pre-reg §13 18 locks 遵守 / mutation tests 是否真實有效)
2. **Block B**:Step 5 production results audit(cell_summary 數字一致 / SHAP 計算對 / deliverables 完整)
3. **Block C**:Step 6 audit module 對(L1-L6 計算邏輯 / DSR n_trials=400 correct / verdict logic 對)
4. **Block D**:Closeout report defensibility(95% 樣本天花板 attribution 是否合理 / 4 failure modes 重評是否 honest)
5. **Block E**:整體 v5.0 pre-commit disciplines(18 條)遵守 audit
6. 給 5 組 verdict:O1-O5

### 不職責

- 不要動任何檔
- 不要 commit
- 不要 sign-off closeout(那是 user 的事)
- 不要 second-guess pre-reg LOCK(spec 鎖死 = 治理產物)

---

## 2. Audit Items

### Block A — Step 4 modules audit

#### A1. ml_pooling.py — 純 join + 2 OOS guards

```bash
# 1. label_end OOS guard 真實 — Codex R1 P0-2 fix 不可退化
grep -n "label_end\|forbidden_oos_start\|_validate_oos_boundary" src/analysis/ml_pooling.py | head -15
# PowerShell: Select-String -Pattern "label_end|forbidden_oos_start|_validate_oos_boundary" -Path src/analysis/ml_pooling.py | Select-Object -First 15

# 2. 模組純粹 — 不 import scripts/*,不讀 cache
grep -n "^from\|^import" src/analysis/ml_pooling.py

# 3. tests cover P0-2 fix mutation
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_ml_pooling.py -v 2>&1 | tail -35

# 4. Schema lock 9 cols(5 features 時)
grep -n "LOCKED_SCHEMA_BASE_COLS\|cols = " src/analysis/ml_pooling.py
```

**檢查**:
- `_validate_oos_boundary` 同時擋 as_of 跟 label_end(per Codex R1 P0-2 fix)
- 29 tests PASS,含 `test_oos_guard_raises_when_label_end_in_oos`
- Schema 鎖 base 5 cols + N feature cols

**A1 你判斷**:ml_pooling 是否真的純粹 + PIT 護欄真實?

#### A2. ml_features.py + ml_contextual.py — PIT-aware provider

```bash
# 5 features LOCKED + 順序固定
grep -n "LOCKED_FEATURE_NAMES" src/analysis/ml_features.py

# Contextual interactions LOCKED 5 個
grep -n "LOCKED_INTERACTION_NAMES" src/analysis/ml_contextual.py

# tests pass
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_ml_features.py tests/test_ml_contextual.py -v 2>&1 | tail -25
```

**檢查**:
- `LOCKED_FEATURE_NAMES` 5 個固定(pre-reg §4)
- `LOCKED_INTERACTION_NAMES` 5 個固定(pre-reg §6)
- size_decile 是 float(Codex 之前抓的 NaT 問題已修)

**A2 你判斷**:Provider 是否真的 delegate PIT 給 factor functions?

#### A3. cpcv.py — purge + per-test-group embargo

```bash
# Per-test-group embargo (non-contiguous test combos 不該合併 window)
grep -n "for g_idx in test_group_combo" src/analysis/cpcv.py
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_cpcv.py -v 2>&1 | tail -20
```

**檢查**:
- 17 tests PASS
- `test_cpcv_yields_10_paths_default` 過(per-group embargo fix 生效)
- `test_embargo_strictly_drops_more_than_no_embargo` 過
- C(5, 2) = 10 paths

**A3 你判斷**:CPCV 算法跟 LdP 2018 §7.4 描述符合?

#### A4. ml_models.py + ml_optuna.py — 3 models + nested CV

```bash
# 3 models LOCKED — 無 RF / NN / stacking
grep -n "class \|XGBoostClassifierWrapper\|LambdaMARTWrapper\|baseline_score" src/analysis/ml_models.py | head -10

# Optuna search space LOCKED per pre-reg §8.1 + §8.2
grep -n "_sample_xgb_params\|_sample_lambdamart_params" src/analysis/ml_optuna.py

# n_trials default
grep -n "DEFAULT_N_TRIALS\|total_trials_per_full_run" src/analysis/ml_optuna.py

NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_ml_models.py tests/test_ml_optuna.py -v 2>&1 | tail -25
```

**檢查**:
- 14 + 11 tests PASS
- Search space 跟 pre-reg §8.1 + §8.2 一致
- `total_trials_per_full_run = 50 × 2 × 4 = 400`(Option A)

**A4 你判斷**:Optuna search space 跟 pre-reg lock 完全一致?

#### A5. ml_audit.py — L1-L6 + DSR

```bash
# L1-L6 thresholds match pre-reg §10
grep -n "L1_IR_THRESHOLD\|L2_ALPHA\|L3_TE\|L4_MAX_DD\|L5_ACTIVE\|L6_BOOTSTRAP\|DSR_N_TRIALS" src/analysis/ml_audit.py

NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_ml_audit.py -v 2>&1 | tail -15
```

**檢查**:
- L1 ≥ 0.20, L2 ≥ 0.005, L3 ∈ [0.10, 0.30], L4 ≤ 0.05, L5 ≤ 0.50, L6 80% CI > 0
- DSR n_trials = 400(per pre-reg §8.4 Option A)
- bootstrap n=10000, block_len=3, seed=42(per pre-reg §10 L6)
- 7 tests PASS

**A5 你判斷**:L1-L6 計算邏輯跟 pre-reg §10 spec 一致?DSR 計算對 Bailey-LdP 2014?

#### A6. Full pytest baseline regression

```bash
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/ -q --tb=no -W ignore::UserWarning 2>&1 | tail -5
# PowerShell: $env:NUMBA_DISABLE_JIT='1'; $env:PYTHONPATH='.'; pytest tests/ -q --tb=no -W ignore::UserWarning 2>&1 | Select-Object -Last 5
```

**期望**:**~982 passed**(840 baseline + Step 4 100 + Step 6 7 + tabulate 1 + 其他小修 = ~982)+ 1 pre-existing dashboard fail。**Codex 必跑驗實際數字 — 不要採信本表**。

**A6 你判斷**:有 regression 嗎?新 Step 4-6 tests 全 PASS?

---

### Block B — Step 5 production results audit

#### B1. cell_summary.json 數字內部一致

```bash
"C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -c "
import json
d = json.load(open('reports/phase_d_v5/v5_ml_cell_summary.json', encoding='utf-8'))
print('mode:', d['mode'], '| n_trials:', d['n_trials'])
print('cells:', len(d['ml_cells']), '| baselines:', len(d['baseline_cells']))
for c in d['ml_cells']:
    n_returns = len(c['oos_monthly_returns'])
    n_dates = len(c['oos_dates'])
    print(f\"  {c['model_name']:12s} top_n={c['top_n']:2d}  n_returns={n_returns} n_dates={n_dates}  Sharpe={c['oos_sharpe']:.4f}\")
"
```

**檢查**:
- mode = "production",n_trials = 50
- 8 ML cells + 4 baselines
- 每 cell n_returns == n_dates(per row 一致)
- Sharpe values 跟 prompt 0.1 summary 一致(xgboost top_n=15 ≈ +1.18 etc.)

**B1 你判斷**:cell_summary 內部一致?

#### B2. SHAP summary 結構對

```bash
"C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -c "
import json
d = json.load(open('reports/phase_d_v5/v5_shap_summary.json', encoding='utf-8'))
print('model:', d['model_class'])
print('n_samples:', d['n_samples'])
print('n_features in importance:', len(d['feature_importance']))
print('n_ranked:', len(d['feature_importance_ranked']))
print('n_interactions:', len(d['interaction_strength']))
print('top 5:')
for x in d['feature_importance_ranked'][:5]:
    print(f\"  {x['feature']}: {x['mean_abs_shap']:.4f}\")
"
```

**檢查**:
- model_class = "XGBClassifier"
- n_samples = 14016(matches OOS rows)
- 57 features in importance dict
- 5 interactions(per pre-reg §6 LOCKED list)
- idio_vol_max 最高(per `v5_shap_summary.json` `feature_importance_ranked[0]` 0.188)

**B2 你判斷**:SHAP 結構 + 數字合理?

#### B3. 5 deliverables 全寫

```bash
ls -la reports/phase_d_v5/v5_ml_cell_summary.json reports/phase_d_v5/v5_ml_vs_baseline.md reports/phase_d_v5/v5_shap_summary.json reports/phase_d_v5/v5_outcome.md reports/phase_d_v5/v5_dsr_audit.json reports/phase_d_v5/v5_final_outcome.md reports/phase_d_v5/v5_architecture_check.json reports/phase_d_v5/v5_closeout_outcome.md 2>&1
# PowerShell: Get-Item reports/phase_d_v5/v5_ml_cell_summary.json, reports/phase_d_v5/v5_ml_vs_baseline.md, ...
```

**期望**:8 個檔全存在,non-empty。

**B3 你判斷**:deliverables 完整?

---

### Block C — Step 6 audit module 對

#### C1. L1-L6 計算邏輯 spot-check

```bash
# 取 best cell (xgboost top_n=15) 的 OOS monthly returns + 對應 0050 benchmark
# 手動算 IR (=mean/std × sqrt(12)) 應 ≈ +0.58 (per audit)
"C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -c "
import json, numpy as np, pandas as pd
d = json.load(open('reports/phase_d_v5/v5_dsr_audit.json', encoding='utf-8'))
best = [c for c in d['cell_audits'] if c['cell_id']=='xgboost_top_n_15'][0]
print('L1_IR claim:', [g['value'] for g in best['gates'] if g['name']=='L1_IR'][0])
print('L6_CI_lower claim:', [g['value'] for g in best['gates'] if g['name']=='L6_bootstrap_ci80_lower'][0])
print('DSR Psi claim:', best['dsr_psi'])
"
```

**檢查**:
- best cell L1 IR ≈ +0.58(對應 pass L1 ≥ 0.20)
- L6 CI lower ≈ -0.005(fail L6 > 0)
- DSR Ψ = 0.00(fail ≥ 0.95)

獨立用 Python pandas 跑 IR / std / Sharpe 算法驗 ml_audit.py 內邏輯一致。

**C1 你判斷**:L1-L6 算法 + DSR 計算對?

#### C2. Verdict logic 對

```bash
"C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -c "
import json
d = json.load(open('reports/phase_d_v5/v5_dsr_audit.json', encoding='utf-8'))
n_pass_all = sum(int(c['all_gates_pass']) for c in d['cell_audits'])
print('n_cells_pass_all_gates(claim):', d['n_cells_pass_all_gates'])
print('actual:', n_pass_all)
print('verdict:', d['verdict'])
print('expected: NO-GO (0 cells pass all + sharpe_diff_threshold not enough alone)')
"
```

**檢查**:
- n_cells_pass_all_gates = 0(claim matches actual)
- verdict = "NO-GO"(per logic: NEED BOTH cells_pass_all AND cells_beat_baseline)

**C2 你判斷**:verdict 邏輯正確?

---

### Block D — Closeout defensibility

#### D1. 95% 樣本天花板 attribution 合理嗎?

讀 `reports/phase_d_v5/v5_closeout_outcome.md` §5 + §9。

**Codex 的問題**:
- 12 obs OOS → L6 80% CI bootstrap + DSR n_trials=400 數學上不可能過?是否有支持文獻?
- 我說 v3.3 同根源,但 v3.3 是線性 + 弱訊號,v5.0 是 ML + 強訊號 — 是否真的可比?
- "95%" 是否過度自信?有沒有 ML 設計可能造成 NO-GO 但被歸 A 的情況?

**D1 你判斷**:95% 樣本天花板 attribution 是否 defensible?

#### D2. Architecture check 結果 honest

讀 `v5_architecture_check.json`:

| top_n | XGB | Fair bl 57f | Single idio_vol | Locked bl 5f |
|---:|---:|---:|---:|---:|
| 15 | +1.18 | -0.32 | +0.03 | -0.23 |
| 20 | +0.74 | -0.68 | +0.16 | -0.24 |
| 25 | +0.91 | -0.60 | +0.11 | -0.19 |
| 30 | +0.92 | -0.50 | -0.02 | -0.14 |

`v5_closeout_outcome.md` §9 結論摘要(待 R6 獨立驗 defensibility):
1. Fair baseline 57f 比 locked baseline 5f 還差 → 推翻可疑 #1
2. Single idio_vol +0.16 最好 vs ML +1.18 → 推翻可疑 #3

**Codex 的問題**:
- Fair baseline 57 features 等權 z-score 真的是「fair」嗎?(sector dummies 0/1 被 z-score 後與連續特徵等權平均,本身就是 measurement artifact)
- Single idio_vol 0.16 vs ML 1.18 — 但 ML 也搭其他 4 個 factors + contextual,「真實 alpha = +1.1」claim 是否過度?
- 8/8 cells 都 beat baseline +1+ Sharpe — 是 ML 真強,還是 2025 OOS regime 特別友善?

**D2 你判斷**:Architecture check 結論是否 over-claim?

#### D3. 18/18 pre-commit disciplines 遵守

讀 `v5_closeout_outcome.md` §7 — closeout 自證 18 條全遵守(待 R6 獨立驗每條)。

逐條驗:

```bash
# 1. 5 features 沒改
grep -n "LOCKED_FEATURE_NAMES" src/analysis/ml_features.py
# 應只有 5 個 entry

# 2. n_trials = 50 per cell
grep -n "n_trials" reports/phase_d_v5/v5_ml_cell_summary.json
# production cell_summary 內 n_trials 值

# 3. CPCV k=5 / n_test=2 / embargo=1m
grep -n "DEFAULT_N_SPLITS\|DEFAULT_N_TEST\|DEFAULT_EMBARGO" src/analysis/cpcv.py

# 4. 2025 strict OOS — label_end < 2025-01-01 guard
grep -n "forbidden_oos_start" src/analysis/ml_pooling.py

# 5. Hard gates 沒降標
grep -n "L1_IR_THRESHOLD\|L2_ALPHA\|L6_BOOTSTRAP_CI_LEVEL" src/analysis/ml_audit.py

# 6. Cost formula
grep -n "COST_PER_TURNOVER_ONE_WAY" src/analysis/ml_audit.py

# 7. 3 model lock — 無 RF / NN
grep -rn "RandomForest\|MLPClassifier\|sklearn.ensemble" src/analysis/ml_*.py
# 期望 0 hit

# 10. top_n {15, 20, 25, 30}
grep -n "LOCKED_TOP_N_VALUES" src/analysis/ml_models.py
```

**D3 你判斷**:18 條 pre-commit 是否真的遵守?(逐條報告 PASS/FAIL)

---

### Block E — 整體 v5.0 sign-off chain audit

#### E1. v5.0 R1-R5 已修的所有 finding 後續沒退化

```bash
# 隨機抽 3 條前輪 finding 驗證沒退化
# v5.0 R1 P0-2 DSR=400 spec
grep -n "n_trials = 400\|n_trials=400" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md
# v5.0 R1 P1-4 _compute_regimes adjust
grep -n "adjust_splits_ohlc" scripts/_factor_ic_helpers.py
# v5.0 R3 P1-2 backlog resolved
grep -n "目前無未修\|RESOLVED" reports/phase_d_v5/v5_ml_plan.md
```

**E1 你判斷**:R1-R5 audit chain 修法是否仍有效(沒因 Step 4-6 而退化)?

#### E2. LLM provenance 全清(含本 prompt 自身)

```bash
# 必含本 prompt 檔 — 不能只 grep reports/ src/ tests/
# (前輪 R5 fix 已在 reports/src/tests 清乾淨;本 prompt 自身也要 0 hits)
# 註:用 [c] character-class 讓搜索 pattern 自己不被命中(self-grep avoidance trick)
rg -n -i "[c]laude|[a]nthropic|\.[c]laude|[C]LAUDE\.md" Codex-Prompt-v5.0-R6.md reports/phase_d_v5/ src/analysis/ml_*.py scripts/_run_v5_*.py tests/test_ml_*.py 2>&1
# PowerShell: Select-String -Pattern "[c]laude|[a]nthropic|\.[c]laude|[C]LAUDE\.md" -Path Codex-Prompt-v5.0-R6.md,reports/phase_d_v5/*.md,src/analysis/ml_*.py,scripts/_run_v5_*.py,tests/test_ml_*.py
```

**期望**:**0 hit literally**(因為 [c] character class trick,搜索 pattern 本身不會 self-match)。
若有 hit 必為文書 / artifact 真實殘留 → NEEDS-FIX(列 file:line 給修法建議)。

**E2 你判斷**:LLM-private provenance(含本 prompt)是否完全清除 → 0 hits?

---

## 3. Hard Constraints

| # | Item |
|---|---|
| H1 | 跳過 audit step 必須明示原因 |
| H2 | 數字附 evidence(grep / pytest / python 輸出) |
| H3 | 不要相信本 prompt 任何宣稱 — 逐條驗證 |
| H4 | 若抓到錯誤,列 file:line + 應改為什麼 |
| H5 | **不要動任何檔;不要 commit;不要 sign-off closeout** |
| H6 | 結尾必須給 O1-O5 五組 verdict |
| H7 | D1: 明確答覆「95% 樣本天花板 attribution 是否 defensible」(YES / NO / NEEDS-QUALIFY) |
| H8 | D2: 明確答覆「Architecture check 是否 over-claim」 |
| H9 | D3: 18 條 pre-commit discipline 逐條 PASS/FAIL |
| H10 | 若認為 v5.0 應改 verdict(GO 而非 NO-GO),或加額外 paper trading 警告 → 明示 |

---

## 4. Output Format

```
# Codex Audit Report v5.0 R6

## Block A — Step 4 modules
### A1. ml_pooling: PASS / NEEDS-FIX (file:line)
### A2. ml_features + ml_contextual: PASS / NEEDS-FIX
### A3. cpcv: PASS / NEEDS-FIX
### A4. ml_models + ml_optuna: PASS / NEEDS-FIX
### A5. ml_audit: PASS / NEEDS-FIX
### A6. Full pytest: <count> passed / <count> failed (regressions <list>)

## Block B — Step 5 production
### B1. cell_summary 內部一致: PASS / NEEDS-FIX
### B2. SHAP 結構: PASS / NEEDS-FIX
### B3. deliverables 完整: PASS / NEEDS-FIX

## Block C — Step 6 audit module
### C1. L1-L6 算法 + DSR: PASS / NEEDS-FIX
### C2. Verdict logic: PASS / NEEDS-FIX

## Block D — Closeout defensibility
### D1. 95% 樣本天花板 attribution: YES / NO / NEEDS-QUALIFY (理由)
### D2. Architecture check over-claim?: YES / NO (理由)
### D3. 18 pre-commit disciplines:
       1. <PASS/FAIL>  2. <PASS/FAIL>  ... 18. <PASS/FAIL>

## Block E — Sign-off chain integrity
### E1. R1-R5 finding 沒退化: PASS / NEEDS-FIX
### E2. LLM provenance 清(含本 prompt): PASS / NEEDS-FIX

## Final Verdicts

- O1 (Step 4 code clean?):      APPROVE / NEEDS-FIX (file:line)
- O2 (Step 5 production honest?): APPROVE / NEEDS-FIX
- O3 (Step 6 NO-GO verdict defensible?): YES / NO / NEEDS-QUALIFY
- O4 (Closeout report ready for sign-off?): READY-AS-IS / NEEDS-FIX-FIRST / REWRITE
- O5 (Path B Paper Trading recommendation 合理?): YES / NO / WAIT

## Findings

- P0 (must fix before user closeout sign-off): <list or "none">
- P1 (suggest fix but not blocker): <list>
- P2 (nitpick): <list>
- Strategic concerns (sample size / 2025 regime / overfit risk): <list>

## audit chain quality

<comment on whether this final R6 reveals systemic issues vs converged clean>
```

---

## 5. 額外提醒

1. **這是 v5.0 final audit** — R1-R5 已對 pre-reg 文件收斂;R6 是對 production results + closeout 結論驗收。
2. **Closeout report 不是 spec sign-off**(那是 user 的事),Codex only 給 readiness verdict。
3. **NO-GO verdict 已寫入 `v5_dsr_audit.json` repo artifact**,Codex 不需要重跑 production;只驗 ml_audit.py 算法 + closeout attribution 是否 defensible(對照 dsr_audit.json + final_outcome.md)。
4. **若 Codex 發現 NO-GO 應該是 GO**(或 vice-versa)→ 必須詳細解釋 + file:line 證據。
5. 收斂指數:R1=9 / R2=5 / R3=4 / R4=3 / R5=1(P2)→ R6 預期 0-2 個小 finding。若 R6 抓到 ≥3 個 P0/P1 → 表示 Step 4-6 工程沒對齊 R1-R5 紀律,需要徹底 review。
6. 工時 Block A ~30 min;Block B ~10 min;Block C ~15 min;Block D ~20 min;Block E ~5 min;Final verdict ~5 min;**~1.5 hr 完成**。
7. 若 O1-O5 全 PASS / READY → user 可 sign-off closeout + 進 Path B Paper Trading。

完工請輸出完整 Audit Report。

---

## Appendix A: Key files reference

- **Pre-reg(已 SIGNED)**:`reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`
- **Plan(已 SIGNED)**:`reports/phase_d_v5/v5_ml_plan.md`
- **Step 5 deliverables**:`reports/phase_d_v5/v5_ml_cell_summary.json` / `v5_ml_vs_baseline.md` / `v5_shap_summary.json` / `v5_outcome.md`
- **Step 6 deliverables**:`v5_dsr_audit.json` / `v5_final_outcome.md`
- **Architecture check**:`v5_architecture_check.json`
- **Closeout(R6 main target)**:`v5_closeout_outcome.md`
- **Production log**:`scripts/_run_v5_ml_experiment.py`(背景跑 13:43-14:34)
- **Audit script**:`scripts/_run_v5_step6_audit.py`
- **Fair baseline script**:`scripts/_run_v5_fair_baseline_check.py`

## Appendix B: Production headline numbers(claim — Codex 必獨立驗證)

```
8 ML cells OOS Sharpe:  +0.74 ~ +1.18
8 vs baseline diff:     +0.99 ~ +1.41
0 cells pass all gates
Best:                   xgboost top_n=15 (Sharpe +1.18, 3/6 gates)
DSR Psi all cells:      0.00 (n_trials=400, n_obs=11)
SHAP top feature:       idio_vol_max (0.188 = 54%)
Fair baseline 57f:      -0.32 ~ -0.68 (all negative, worse than locked 5f)
Single idio_vol:        +0.03 ~ +0.16
```
