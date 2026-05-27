# Codex Audit v5.0 R2 — ML pipeline Pre-Engineering Verification(v5.0 R1 fix 已修)

> **Final status(2026-05-26)**:本檔已完成歷史使命。Codex v5.0 R1-R5 audit chain 完整收斂(R1=9 / R2=5 / R3=4 / R4=3 / R5=0 阻斷)→ user sign-off pre-reg v5.0.2 LOCKED 2026-05-26 → 進 Step 4 ML pipeline 工程。本檔保留作 audit trail。

**Target audience**:Codex(獨立 audit agent,v5.0 **第二輪 audit**)
**Date anchor**:2026-05-25
**v5.0 R1 verdict 摘要**(Codex v5.0 R1 給的):
- O1 NEEDS-VERIFY(v5.0 R1 沒跑 pytest)/ O2 NEEDS-FIX-FIRST / O3 WAIT-FOR-FIX
- 6 finding 全 confirmed(2 P0 + 4 P1)
- v5.0 R1 → v5.0 R2 修法摘要見本檔 §0.1(產出者:**prior LLM assistant**;由 Codex R2 獨立驗證真偽)

**v5.0 R2 audit scope**:**3 blocks** —
- **Block A**:Today's code changes(2026-05-25 全部新檔 + 修改)— factor implementations / IC scripts / dispatcher updates / SN IC research
- **Block B**:Pre-registration discipline(`reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`)— 18 章節 lock 是否完整、可證偽、無 silent escape hatch
- **Block C**:Step 4 ML pipeline 架構 readiness — pre-engineering plan(`ml_pooling.py` / `cpcv.py` / XGBoost+LambdaMART / Optuna)是否準備好進工程(尚未開工程,確認 plan 沒漏洞)

**Plan reference**:`reports/phase_d_v5/v5_ml_plan.md`(v5.0.1 — Codex v5.0 R1 P0-1 fix:從 LLM 助理私有目錄複製到 repo-local)
**v3.3 baseline**:tag `phase-d-v7-baseline`(`d55d4ea`)
**Pre-registration file**:`reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`(~550 行,18 章節 + Sign-off block)

請用**繁體中文**回報。**不要相信本 prompt 任何宣稱**——逐條獨立驗證。用 quant conda env:

**Bash(WSL / Git Bash)**:
```bash
NUMBA_DISABLE_JIT=1 PYTHONPATH=. \
  "C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -u <script.py>
```

**PowerShell**(Codex v5.0 R1 P1-2 fix:Windows 環境統一指令;PowerShell **不接受 `\` 換行**,以下 3 種寫法擇一):
```powershell
# 寫法 A — 單行(最穩):
$env:NUMBA_DISABLE_JIT='1'; $env:PYTHONPATH='.'; & "C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -u <script.py>

# 寫法 B — backtick `` ` `` 換行(行尾不可有空白):
$env:NUMBA_DISABLE_JIT='1'; $env:PYTHONPATH='.'; `
  & "C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -u <script.py>

# 寫法 C — env 變數分行設定:
$env:NUMBA_DISABLE_JIT='1'
$env:PYTHONPATH='.'
& "C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" -u <script.py>
```

**Docker**(deploy / Linux 環境;per `docker-compose.yml`):
```bash
docker compose run --rm --entrypoint python portfolio-bot <script.py>
```

**重要**:本 prompt audit items 內的命令範例**以 bash 為主**(`|`、`grep`、`pytest`、`python` pipe 都通);**以下 bash-only 構造在 PowerShell 不通**,需手動轉換:

| bash 用法 | PowerShell 等價 |
|---|---|
| `grep -n PATTERN FILE` | `Select-String -Pattern PATTERN -Path FILE` |
| `\| head -N` | `\| Select-Object -First N` |
| `\| tail -N` | `\| Select-Object -Last N`(stream 末)或 `Get-Content -Tail N`(file)|
| `diff <(cmdA) <(cmdB)` | `Compare-Object (cmdA) (cmdB)` — **無 `<(...)` process substitution** |
| `sha256sum FILE` | `Get-FileHash FILE -Algorithm SHA256` |
| `VAR=$(cmd)` | `$VAR = (cmd)` 或 `$VAR = Invoke-Expression cmd` |
| `cmd1 \| cmd2` | 同(pipe 在 PowerShell 也通,但傳的是物件不是字串)|

Codex 自行依環境轉換;若用 Git Bash / WSL,bash 範例直接可跑。

---

## 0.1 v5.0 R1 → v5.0 R2 修法摘要(Codex v5.0 R1 6 finding 全修)

| # | v5.0 R1 Finding | 修法 | 驗證 |
|---|---|---|---|
| **P0-1** | LLM-private path 依賴(prior plan 存於 LLM 助理私有目錄)| Plan 複製到 `reports/phase_d_v5/v5_ml_plan.md`(repo-local);pre-reg + audit prompt 全部改引用 repo 路徑;原「依 LLM 助理本地指南」改「依 Self-Audit SOP(本檔 §X)」| 修法後 repo 內 LLM-private path 引用 = 0(含歷史 quote 全清)|
| **P0-2** | DSR n_trials 100 vs 400 不一致 | **user 拍板 Option A** — 每 (model, top_n) 獨立 Optuna → n_trials = 50 × 2 × 4 = **400**;pre-reg §8.4 + §12 + §13 條 2 + §14 deliverables 全 amend | grep `n_trials = 400` in pre-reg ≥ 4 hits |
| **P1-1** | Codex prompt 數字錯(8 entry,新增 4)| 改為「v2.x 5 → 昨天 +2 → 今天 +2 = 9 entries;Extra-3 demote 後 = 7」| 本檔 §3.2 修正 |
| **P1-2** | bash 命令不能跑 PowerShell | 本檔頂部加 PowerShell + Docker 替代命令 | 本檔開頭三組命令並列 |
| **P1-3** | xgboost/optuna/shap 缺 deps | `requirements-dev.txt` 加 3 個 deps + 寫 `tests/test_ml_imports.py`(5 smoke tests)+ 已用 pip install 於 quant env | `pytest tests/test_ml_imports.py -v` 5/5 PASS |
| **P1-4** | `_compute_regimes` 0050 OHLC 未 adjust split → 2025 OOS regime 必污染 | 寫 `src/backtest/metrics.adjust_splits_ohlc()` + `_compute_regimes` 整合 + `tests/test_metrics.TestAdjustSplitsOHLC`(10 tests)| `pytest tests/test_metrics.py::TestAdjustSplitsOHLC` 10/10 PASS |
| **P1-5** | Retail cost sensitivity 缺揭露 | pre-reg §10.1 加 non-binding sensitivity disclosure(5/10/20/30 bps 4 個 slippage scenarios)| pre-reg §10.1 存在 |
| **Extra-1** | §14 偽造 14:00 timestamp | 全部改成「中段對話」/「下午」等模糊時段 | `grep "14:00\|14:30\|15:00"` in pre-reg + Codex-Prompt = 0 hit |
| **Extra-3** | size_factor + momentum_12_1 IC fail 但仍在 FACTOR_REGISTRY | 從 FACTOR_REGISTRY 拿掉(import 註解掉),src/features/ + tests/ 保留作 audit trail | grep FACTOR_REGISTRY entries = 7 |

**自報 pytest 數字(R2 必須獨立重跑驗證,不要相信本表)**(2026-05-25 v5.0 R1→R2 fix 後):
```
聲稱:840 passed / 1 failed (5m36s)
- 1 failed = test_load_factor_correlation_5x5 (pre-existing dashboard fail, NOT regression)
- 840 = 769 baseline + 56 today factors (size 16 + GP 24 + mom 16) + 15 v5.0 R1 fixes (10 OHLC + 5 ML imports)
```
**R2 必跑**:Codex 必須獨立跑 `pytest tests/ -q --tb=no -W ignore::UserWarning` 拿到實際數字對照,**禁止採信本表**(防 silent regression)。

v5.0 R2 audit 任務:**驗證 §0.1 9 條修法是否真的修完,沒有 silent escape;以及獨立重跑 pytest 驗 baseline**。

---

## 0. Context — 從 v3.3 NO-GO 到 v5.0 ML

### Audit chain 位置

| 階段 | Verdict | 結束 |
|---|---|---|
| v3.3(18-cell sweep, R26-R34 audit chain)| CONFIRM-NO-GO(0/18 過 6/6,binding=L6 bootstrap CI)| 2026-05-07 |
| v4.0(整股 realism fix sweep)| **CANCELLED**(user 評估後取消;100 萬 整張結構不可行,lot_sizing.py 保留為工具)| 2026-05-22 |
| **v5.0**(100 萬 零股 reframe + **ML-based 因子合成**)| **進行中**(本輪 audit 為 Step 3→Step 4 過渡)| 2026-05-25 |

### v5.0 設計(已 user 拍板)

| 維度 | 值 |
|---|---|
| 啟動資金 | 100 萬 NTD |
| 交易單位 | 盤中零股 |
| 樣本 | 2020-2024 IS(60 月)+ 2025 strict OOS(12 月)|
| 換倉 | 月頻 |
| top_n | {15, 20, 25, 30} |
| 因子組合 | **ML**(XGBoost classifier + LambdaMART ranking + Linear baseline)|
| 多檢定校正 | **CPCV(LdP 2018)+ Optuna nested CV;DSR n_trials=400(Option A — 每 (model, top_n) cell 獨立 Optuna)** |
| **Feature set lock** | **5 個**:idio_vol_max / high_proximity / value_ep_sn / pead_eps_sn / reversal_1m |
| Hard gates | L1-L6 沿用 v3.3(L7 paper 不在 v5.0 binding)|

### B+B 拍板(2026-05-25(中段),user 拍板)

- **B1**:SN 取代 raw — value_ep_sn / pead_eps_sn 替代 raw(7×7 矩陣顯示 raw ↔ SN 自我配對 ρ=0.91-0.93,違反 |ρ|>0.7 二選一守則)
- **B2**:3 candidate research — size / GP / mom12-1 → **3/3 全 IC fail**(p ≥ 0.85),確認 5 feature 為 retail 100 萬 月頻 L-only TW 的 alpha 上限

---

## 1. 你的職責 / 不職責

### 職責

1. **Block A**:今天程式產出(factor implementations + IC scripts + dispatcher 改動)逐檔 audit(file:line + grep + 跑 test + mutation)
2. **Block B**:pre-registration 18 章節 audit — hypothesis 可證偽性 / lock 充分性 / silent escape hatch / DSR n_trials 數學正確
3. **Block C**:Step 4 ML pipeline 架構 plan audit — 寫進 plan 但**尚未開工程**的部分(`ml_pooling.py` / `cpcv.py` 等);找架構漏洞讓 user 知道
4. 給三組 verdict:O1(今天 code clean?)/ O2(pre-reg sign-off ready?)/ O3(Step 4 工程可開工?)

### 不職責

- **不要動任何檔**(audit only)
- **不要 commit**
- **不要 sign-off pre-registration**(那是 user 的事)
- **不要寫 Step 4 程式**(那是 Step 4 工程才開始)

---

## 2. Today's modifications inventory(2026-05-25 全部產出)

### 2.1 新檔 — Foundation(2a)

| 檔 | LOC | 用途 |
|---|---:|---|
| `src/features/value_ep.py` | 201 | Value E/P 因子(quarter-aware lag + shift=1 + F-F drop negative TTM)|
| `src/features/reversal_1m.py` | 113 | DeBondt-Thaler 短期反轉(sign-flipped)|
| `src/utils/factor_neutralize.py` | 138 | sector_neutralize + size_neutralize helpers |
| `tests/test_value_ep.py` | 242 | 15 tests PASS |
| `tests/test_reversal_1m.py` | 144 | 11 tests PASS |
| `tests/test_factor_neutralize.py` | 149 | 10 tests PASS |

### 2.2 新檔 — 3 candidate research(2e)

| 檔 | LOC | 用途 | IC 結果 |
|---|---:|---|---|
| `src/features/size_factor.py` | 116 | SMB(-log market cap,sign-flipped)| ❌ DROP(p=0.76)|
| `src/features/gross_profitability.py` | 210 | Novy-Marx 2013 GP/Assets | ❌ DROP(p=0.85)|
| `src/features/momentum_12_1.py` | 162 | Carhart 1997 12-月-1 動量 | ❌ DROP(p=0.97)|
| `tests/test_size_factor.py` | 213 | 16 tests PASS |
| `tests/test_gross_profitability.py` | 249 | 24 tests PASS |
| `tests/test_momentum_12_1.py` | 228 | 16 tests PASS |

### 2.3 新檔 — Research scripts

| 檔 | LOC | 用途 |
|---|---:|---|
| `scripts/_research_sector_neutral_ic.py` | 232 | SN IC(value_ep + pead_eps)研究 — 2c |
| `scripts/_research_gp_ic.py` | 188 | GP IC research(bypass FACTOR_REGISTRY,需 2 panel)|
| `scripts/_compute_factor_correlation_v5.py` | 245 | 7×7 相關性矩陣(5 raw + 2 SN)— 2d |

### 2.4 已修改 — Dispatcher / Plan

| 檔 | 改動 |
|---|---|
| `scripts/run_factor_ic.py` | (a) import `compute_size_universe` + `compute_momentum_12_1_universe` + `compute_value_ep_universe` + `compute_reversal_1m_universe`(b)FACTOR_REGISTRY 加 4 個 entry(value_ep / reversal_1m / size_factor / momentum_12_1)|
| `scripts/_run_daily_factor_ic.py` | 加 reversal_1m dispatch + DAILY_APPLICABLE include reversal_1m;value_ep 跳過(季資料,如 PEAD)|
| `scripts/d_cell_sweep_v7_real.py` | `_build_market_returns`(L374-389)+ `_build_benchmark_monthly_returns`(L412-422)加 `adjust_splits`(修 0050 2025 1:4 split)|

### 2.5 新檔 — Reports & Pre-registration

| 檔 | LOC | 用途 |
|---|---:|---|
| `reports/factor_ic/value_ep_ic.json` | (大檔)| Value E/P IC research result |
| `reports/factor_ic/reversal_1m_ic.json` | (大檔)| Reversal 1m IC |
| `reports/factor_ic/value_ep_sn_ic.json` | (大檔)| Value SN IC |
| `reports/factor_ic/pead_eps_sn_ic.json` | (大檔)| PEAD SN IC |
| `reports/factor_ic/size_factor_ic.json` | (大檔)| Size IC(DROP)|
| `reports/factor_ic/momentum_12_1_ic.json` | (大檔)| Mom12-1 IC(DROP)|
| `reports/factor_ic/gross_profitability_ic.json` | (大檔)| GP IC(DROP)|
| `reports/factor_ic/factor_correlation_matrix_v5.{json,md}` | -- | 7×7 相關性 |
| **`reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`** | **~550** | **v5.0 ML pre-registration v5.0.1(Codex v5.0 R1 fix 後;待 R2 PASS sign-off → v5.0.2)** |

### 2.6 新檔 — Plan

| 檔 | 改動 |
|---|---|
| `reports/phase_d_v5/v5_ml_plan.md` | v5.0 ML plan 完整重寫(7 步驟,2a-2e 完成,3-6 待跑)— v5.0 R1 P0-1 fix 從 LLM 助理私有目錄複製進 repo |

---

## 3. Architectural adjustments(架構調整)

### 3.1 Feature set 演進

```
v3.3 結案:3 factors(price_momentum + revenue_momentum + trend_quality 線性)
   ↓ NO-GO
v5.0 初版規畫:5 raw KEEP factors(idio_vol / 52W / pead / value_ep / rev_1m)
   ↓ Option X(2026-05-25 上午)
v5.0 中版:5 raw + 2 SN = 7 features
   ↓ 7×7 矩陣顯示 raw ↔ SN ρ=0.91-0.93(B+B 拍板)
v5.0 最終:5 features(SN 取代 raw,3 candidates 全 DROP)
```

### 3.2 Dispatcher 擴充

`run_factor_ic.py` FACTOR_REGISTRY 演進(v5.0 R1 P1-1 fix:原 prompt 數字錯,實際 9 entries):
- v2.x baseline 5:high_proximity / revenue_momentum_v2 / margin_short_ratio / foreign_investor_v2 / pead_eps
- 2026-05-24(昨天)加 2:value_ep / reversal_1m
- **2026-05-25(今天)加 2:size_factor / momentum_12_1**(但 IC 全 DROP → Extra-3 修法:從 REGISTRY 拿掉,保留 src 檔)
- **目前 total = 9 entries**(2026-05-25 早段);**v5.0 R1 Extra-3 修後 = 7 entries**(size/mom12-1 demoted)

**注意**:GP 不在 FACTOR_REGISTRY,因為需要 `quarterly_financial_full` + `balance_sheet` 兩個 cache,**目前 dispatcher 的 `panel_type` 只支援 5 種**(ohlcv / revenue / margin_short / institutional_v2 / quarterly_eps)。為了不擴充 dispatcher,我寫了 `_research_gp_ic.py` 客製腳本(bypass FACTOR_REGISTRY)。**這個架構決策需要 Codex 評估**:GP 若未來進 ML feature 也要走客製路線?還是該擴 dispatcher 加 `quarterly_financial_full` + `balance_sheet` 兩個 panel_type?

### 3.3 SN IC 客製 script

`_research_sector_neutral_ic.py` 同樣 bypass FACTOR_REGISTRY,因為 SN 是「raw factor + post-processing」,不是新 factor 本身。**這也需要 Codex 評估**:如果 SN 進 ML feature,要怎麼 PIT-cleanly 整合?(每月跑 raw → SN → 進 ML 還是 cache SN 結果?)

### 3.4 0050 split 修復(v5.0 R1 P1-4 整合後 — RESOLVED)

`d_cell_sweep_v7_real.py` 加 `adjust_splits`(close-only)在 2 處(`_build_market_returns` + `_build_benchmark_monthly_returns`)。

**~~Sibling backlog~~ → 已修(v5.0 R1 P1-4)**:
- 新增 `src/backtest/metrics.py:adjust_splits_ohlc()` — OHLC 4-column adjustment
- 整合 `scripts/_factor_ic_helpers.py:_compute_regimes` line 383(`benchmark_adjusted = adjust_splits_ohlc(benchmark_ohlcv)`)
- 新增 `tests/test_metrics.py::TestAdjustSplitsOHLC`(10 mutation tests)
- 2025 OOS regime contextual feature 不再被 split 污染 ✓

→ 此項從 backlog 移除,**v5.0 ML pipeline 開工前 0050 split 議題已完整解決**。

---

## 4. What's next(Step 4-6 plan)

### Step 4 — ML pipeline 工程(待 pre-registration sign-off 後開工)

| 子步驟 | 模組 | 工時估 | Codex 評估點 |
|---|---|---:|---|
| 4a | `src/analysis/ml_pooling.py` + tests | 3-5 天 | PIT enforcement / target leakage / 78K rows × 25-35 cols 規模可行性 |
| 4b | Contextual features(sector / size / regime / 5 鎖定 interactions)| 2-3 天 | Industry_map PIT(static snapshot caveat)/ interaction 是否事前鎖死(已 lock §6)|
| 4c | `src/analysis/cpcv.py` + tests | 1-2 天 | embargo=1 month 是否足夠 / k=5 splits 在 60 月樣本是否合理 / mutation tests 是否抓 leakage |
| 4d | XGBoost / LambdaMART / Linear baseline wrappers | 1-2 天 | 3 model 鎖死(無 RF / NN / stacking)/ Linear baseline z-score 等權公式 |
| 4e | Optuna nested CV | 1-2 天 | n_trials=50 × 2 models × 4 top_n = **400**(Option A) / DSR anchor=400 / nested CV outer=CPCV inner=TimeSeriesSplit |
| 4f | SHAP + outcome writer | 1 天 | SHAP 計算對齊 / report deliverables |

### Step 5 — 跑 ML 實驗

訓練 + CPCV + Optuna + 2025 OOS + SHAP + linear baseline 對照 → 8 cells(4 top_n × 2 ML models)+ 4 baseline cells → L1-L6 評估 → outcome 分類。

### Step 6 — Self-Audit + 回歸

self-audit SOP 6 步 + forensic-sweep + full pytest 全綠。

---

## 5. Audit Items

### Block A — Today's code(2026-05-25 全部產出)

#### A1. SN IC research script(`scripts/_research_sector_neutral_ic.py`)

```bash
# 1. PIT discipline — 確認 raw factor 計算用 _DataSlicer-equivalent (close_by_symbol PIT)
grep -n "close_by_symbol\|as_of\|shift" scripts/_research_sector_neutral_ic.py

# 2. sector_neutralize 用法 — 確認每期單獨 neutralize(非全期合 neutralize)
grep -n "sector_neutralize" scripts/_research_sector_neutral_ic.py

# 3. industry_map PIT caveat — 確認 known_biases 有寫
grep -n "known_biases\|industry" scripts/_research_sector_neutral_ic.py

# 4. 跑 SN IC unit test(若有)
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_factor_neutralize.py -v
```

**期望**:(a)`close_by_symbol` PIT-truncated;(b)`sector_neutralize` 在每期 loop 內呼叫;(c)`known_biases` 明寫「industry labels from current stock_info snapshot (Option B; small drift caveat)」;(d)10 tests PASS。

**A1 你判斷**:SN IC 結果可信嗎?Option B PIT caveat 影響多大?

#### A2. 7×7 correlation script(`scripts/_compute_factor_correlation_v5.py`)

```bash
# 1. 7 features list 正確?
grep -n "V5_FEATURES" scripts/_compute_factor_correlation_v5.py

# 2. Spearman corr 計算正確(per period 然後 mean)?
grep -n "spearmanr\|mean(" scripts/_compute_factor_correlation_v5.py

# 3. 結果重現(hash before/after)— Codex v5.0 R2 P1-3 fix
#    舊版用 `diff <(cat X) <(cat X)` 比同檔自己,永遠 pass,無效驗證。
#    正確流程:先 hash 現存 artifact → 重跑 script → 重 hash → 比對。
HASH_BEFORE=$(sha256sum reports/factor_ic/factor_correlation_matrix_v5.md 2>/dev/null | cut -d' ' -f1)
echo "before: $HASH_BEFORE"
NUMBA_DISABLE_JIT=1 PYTHONPATH=. \
  "C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe" \
  scripts/_compute_factor_correlation_v5.py
HASH_AFTER=$(sha256sum reports/factor_ic/factor_correlation_matrix_v5.md 2>/dev/null | cut -d' ' -f1)
echo "after:  $HASH_AFTER"
[ "$HASH_BEFORE" = "$HASH_AFTER" ] && echo "REPRODUCIBLE ✓" || echo "DIVERGED ✗ — investigate"
```
PowerShell 版:`Get-FileHash <path> -Algorithm SHA256` 取前後 hash 對照。
```

**期望**:(a)V5_FEATURES list 7 個 features;(b)per-period Spearman 再 average,排除 < 10 common symbols;(c)結果 reproducible(數字一致)。

**A2 你判斷**:7×7 矩陣方法學正確?有沒有 IC 結果引入 spurious correlation 的風險?

#### A3. Size factor(`src/features/size_factor.py` + tests)

```bash
# 1. PIT shift=1 — close at (as_of - 1d)
grep -n "_price_asof\|cutoff\|Timedelta(days=1)" src/features/size_factor.py

# 2. SMB sign convention — -log(mcap) so smaller=higher score
grep -n "-float(np.log\|sign\|SMB" src/features/size_factor.py

# 3. issued_shares PIT — aux_panel dict from _issued_capital_asof
grep -n "aux_panel\|issued_shares" src/features/size_factor.py

# 4. 跑 tests
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_size_factor.py -v
```

**期望**:16 tests PASS;PIT shift=1 正確;sign convention 正確(smaller cap → higher score)。

**A3 你判斷**:Size 因子 IC=0.003 / p=0.76(DROP)是真的 TW SMB 不成立還是方法學問題(intersection universe 1647 太廣)?

#### A4. GP factor(`src/features/gross_profitability.py` + tests + IC script)

```bash
# 1. quarter-aware lag(Q4 = 90d, Q1-3 = 45d)
grep -n "QUARTERLY_EPS_LAG\|_earliest_asof_for_row" src/features/gross_profitability.py

# 2. TTM logic
grep -n "_ttm_gp\|min_quarters" src/features/gross_profitability.py

# 3. TotalAssets PIT
grep -n "TotalAssets\|_latest_assets" src/features/gross_profitability.py

# 4. tests
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_gross_profitability.py -v

# 5. IC script(custom because GP needs 2 panels)
grep -n "panel_type\|quarterly_financial_full\|balance_sheet" scripts/_research_gp_ic.py
```

**期望**:24 tests PASS;quarter-aware lag 對齊 pead_eps 既有 helper;TTM=sum(last 4 quarters);PIT excludes future quarters。

**A4 你判斷**:GP IC=-0.002 / p=0.85(DROP)的方法學乾淨嗎?(注意:GP needs 2 panels — quarterly_financial_full + balance_sheet,**不在 FACTOR_REGISTRY 的 panel_type 列表**;我用 custom script bypass。這個架構債未來怎麼處理?)

#### A5. Momentum 12-1 factor(`src/features/momentum_12_1.py` + tests)

```bash
# 1. skip_days=21, lookback_days=252 (Carhart 標配)
grep -n "DEFAULT_SKIP_DAYS\|DEFAULT_LOOKBACK_DAYS" src/features/momentum_12_1.py

# 2. PIT 嚴格 — strict < as_of(不是 <=)
grep -n "close.index <\|cutoff\|strict" src/features/momentum_12_1.py

# 3. Sign convention — winner = higher score(無 sign-flip)
grep -n "p_recent / p_far" src/features/momentum_12_1.py

# 4. tests
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/test_momentum_12_1.py -v
```

**期望**:16 tests PASS;strict PIT(< as_of,不是 ≤);Carhart sign convention(winner = higher)。

**A5 你判斷**:Mom12-1 IC=0.0005 / p=0.97(DROP)是真的 TW 12 月動量不成立?還是被 high_proximity(52W high)+ reversal_1m 覆蓋?

#### A6. Dispatcher 更新(`scripts/run_factor_ic.py`)

```bash
# 1. FACTOR_REGISTRY 7 個 active entry(v5.0 R1 Extra-3 demote 後)?
grep -nE '^\s+"[a-z_0-9]+":\s*\{' scripts/run_factor_ic.py

# 2. close_by_symbol dispatch 正確(value_ep 需要,foreign_investor_v2 需要)?
grep -n "close_by_symbol\|factor_kwargs\[" scripts/run_factor_ic.py

# 3. size_factor + momentum_12_1 已從 dict 移除(僅保留 src/features/ 程式)
grep -n "size_factor\|momentum_12_1" scripts/run_factor_ic.py
```

**期望**:
- 共 **7 active entries**:high_proximity / revenue_momentum_v2 / margin_short_ratio / foreign_investor_v2 / pead_eps / value_ep / reversal_1m
- size_factor / momentum_12_1 只能在 DROPPED comment 註解中出現(視為已 demote)
- value_ep close_by_symbol 加進 kwargs
- v5.0 R1 修法前是 9 entries → 修後 7

**A6 你判斷**:dispatcher 改動有沒有 break 既有因子的 IC?有沒有 side-effect 影響 v3.3 cell sweep 既有結果?

#### A7. Full pytest baseline regression

```bash
NUMBA_DISABLE_JIT=1 PYTHONPATH=. pytest tests/ -q --tb=no -W ignore::UserWarning 2>&1 | tail -10
# PowerShell: $env:NUMBA_DISABLE_JIT='1'; $env:PYTHONPATH='.'; pytest tests/ -q --tb=no -W ignore::UserWarning 2>&1 | Select-Object -Last 10
```

**期望**:**769 + 16 + 24 + 16 = 825 passed**(15 value_ep + 11 reversal_1m + 10 factor_neutralize 在 769 baseline 已含;今天新增 16+24+16 = 56 from size/GP/mom12-1)。實際數應為 **769 + 56 = 825**(或加 baseline 1 個 pre-existing dashboard fail)。

**A7 你判斷**:有 regression 嗎?新增 56 tests 全 PASS?

---

### Block B — Pre-registration discipline audit

#### B1. Hypothesis 可證偽性(§1, §3)

```bash
grep -n "Hypothesis\|Falsification\|reject" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md | head -20
# PowerShell: Select-String -Pattern "Hypothesis|Falsification|reject" -Path "reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md" | Select-Object -First 20
```

**檢查**:
- §1 hypothesis 有明確 numerical threshold(L1-L6 + Sharpe diff ≥ +0.05)?
- §3 falsification 3 條夠不夠 escape-hatch-proof?
- 有沒有 silent escape path(例:「ML 沒過但 baseline 過 → 用 baseline」)?
- NO-GO 條件夠不夠決絕(「結案不修參數」是否被 hard-coded)?

**B1 你判斷**:hypothesis 真的不可 escape 嗎?跑完 ML 後有沒有方法後驗合理化?

#### B2. Feature set lock(§4)

```bash
# 1. 5 features 明列 + DROP 7 因子明列?
grep -n "Locked feature set\|DROP" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md | head -15
# PowerShell: Select-String -Pattern "Locked feature set|DROP" -Path "reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md" | Select-Object -First 15

# 2. reversal_1m 例外保留 — 明寫?
grep -n "reversal_1m exception\|reversal_1m.*example\|p=0.18" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md
```

**檢查**:
- 5 features 明列 + IC 數字 + 來源 src 路徑?
- 10 個 DROP 因子全明列(margin_short / industry_mom / foreign_v2 / revenue_v2 / quality_v3 / value_ep raw / pead_eps raw / size / GP / mom12-1)?
- reversal_1m 個別 p=0.18 不過 IC gate 卻保留為 ML feature — **這是 silent bug 嗎**?事前 reasoning 是否充分?

**B2 你判斷**:reversal_1m 例外是合理 Pro 設計還是 cherry-pick 為了湊 5 feature?

#### B3. Contextual features + interactions lock(§6)

```bash
grep -n "Contextual features\|Locked interaction columns" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md
```

**檢查**:
- Contextual list(sector / size / regime / interactions)鎖死?
- 5 interaction columns 事前列出(避免 OOS 後新增)?
- industry_map PIT caveat 是否承接 SN script 的 Option B?

**B3 你判斷**:Step 4b 寫程式時是否能照這個 lock 跑(夠具體)?

#### B4. Optuna + DSR n_trials 數學(§7-8)

```bash
grep -n "n_trials\|DSR\|search space" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md | head -15
# PowerShell: Select-String -Pattern "n_trials|DSR|search space" -Path "reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md" | Select-Object -First 15
```

**檢查**:
- pre-reg §8.4 鎖定 **DSR n_trials = 400**(Option A:50 trials × 2 models × 4 top_n = 400;v5.0 R1 P0-2 修法後)— **數學是否真的對應 §12 protocol**?
- Search space A(XGBoost)+ B(LambdaMART)鎖死?
- nested CV(outer=CPCV / inner=TimeSeriesSplit 5 splits)沒漏 leakage 路徑?
- Linear baseline 不算 trial(無 hyperparameter)— 合理嗎?

**B4 你判斷**:`pre-reg §8.4 + §12 + §13 條 2 + §14 deliverables`(**active spec 段**)是否一致都是 400?
- 判 PASS:active spec 段全 400;若 100 出現,**必須**在 「修法歷史」段(§8.4 末尾)或「Rationale Option A vs B」段(§12 末尾)內,作為說明文字。
- 判 NEEDS-FIX:active spec 段(LOCK 表 / pre-commit / deliverables filename)出現 100,或 Option A binding 內混入 100。

#### B5. CPCV setup(§9)

```bash
grep -n "CPCV\|k=5\|embargo\|combinatorial" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md
```

**檢查**:
- k=5, n_test=2, embargo=1 month — 60 IS months / 5 splits = 12 month per split,夠不夠?
- embargo=1 month 對抗 monthly forward return 的 label leakage 足夠嗎?
- C(5,2)=10 combinatorial paths 取 mean Sharpe — paths SE 是否提供 Optuna pruner?

**B5 你判斷**:CPCV setup 在 60-month sample 上合理嗎?embargo 該更長嗎?

#### B6. 2025 OOS strict holdout(§5.3, §13)

```bash
grep -n "2025\|strict\|holdout\|touch" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md
```

**檢查**:
- 「touch」定義(讀 2025 row 進 fit/CV/parameter selection)明確?
- imputer fit / scaler fit / column stat 在 IS only?
- Pre-commit lock §13 條 4 是否覆蓋所有 leakage 路徑?

**B6 你判斷**:有沒有間接路徑會偷看 2025?(e.g. feature engineering on full sample then split?)

#### B7. Hard gates(§10)

```bash
grep -n "L1\|L2\|L3\|L4\|L5\|L6\|L7" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md | head -15
# PowerShell: Select-String -Pattern "L1|L2|L3|L4|L5|L6|L7" -Path "reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md" | Select-Object -First 15
```

**檢查**:
- L1-L6 沿用 v3.3 retail-realistic — 沒降標?
- L7(paper)明寫**不在 v5.0 binding** — 有沒有 silent escape(跑完 ML 過 5/6 → 拉進 paper 補?)
- Cost formula 0.67% × turnover_one_way — 與 v3.3 對齊?

**B7 你判斷**:gates 紀律守得住嗎?

#### B8. 18 pre-commit disciplines(§13)

```bash
grep -n "pre-commit\|LOCKED\|violation = NO-GO" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md | head -20
# PowerShell: Select-String -Pattern "pre-commit|LOCKED|violation = NO-GO" -Path "reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md" | Select-Object -First 20
```

**檢查 18 條每條是否可機械驗證(後可由 Codex grep 出來查證)**:
1. 5 features locked
2. n_trials = 50 × 2 models × 4 top_n = **400**(Option A,v5.0 R1 P0-2 fix)
3. CPCV k=5, n_test=2, embargo=1
4. 2025 strict OOS
5. Hard gates 不降標
6. Cost formula 鎖死
7. 3 model 鎖死
8. Sample 2020-2024 IS
9. Intersection universe ~1300
10. top_n ∈ {15, 20, 25, 30}
11. Target = top-decile binary
12. PIT 護欄
13. SHAP 必算
14. 5 deliverables 必有
15. Self-Audit SOP 全跑
16. No paper until v5.0 closed
17. Commit 紀律
18. 不 push

**B8 你判斷**:18 條哪一條最容易被「無意違反」?

#### B9. B+B decision audit chain(§14-15)

```bash
grep -n "B+B\|Option X\|Decision audit chain" reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md
```

**檢查**:
- §14 B+B 決策過程紀錄是否準確(2026-05-25(中段) user 拍板)?
- §15 conversation evidence 是否有 audit trail 可重建?
- 3 candidate DROP 證據(IC numbers / p-values / CI)是否與 `reports/factor_ic/*.json` 對得上?

**B9 你判斷**:audit chain 真實可重建嗎?

---

### Block C — Step 4 ML pipeline architecture readiness

#### C1. ml_pooling.py(§5)— architectural review(pre-engineering)

**設計要點**(from §5.2):
- `build_training_matrix(features_by_symbol_month, returns_by_symbol_month, as_of_range)` → DataFrame
- 強制 PIT
- 強制 forward-return label

**Codex 評估點**:
- 78,000 rows × 25-35 cols 規模在 conda quant env 是否可行?(memory / speed)
- PIT enforcement 怎麼測 mutation(改 t → t+1 必須 fail)?
- target label(top-decile binary)cross-section per period or pooled?(per period 才避免跨期 ranking leakage)
- Sparse / missing feature 處理策略?(value_ep_sn 有時某股無 EPS → NaN,XGBoost 內建 NaN handling vs imputer 二選一)

**C1 你判斷**:Step 4a 是否準備好開工?還是需要先補哪些設計細節?

#### C2. cpcv.py(§9)— architectural review

**設計要點**:
- `cpcv_splits(n_obs_per_month, n_splits=5, n_test_splits=2, embargo_months=1)` → list of (train_idx, test_idx)
- C(5,2) = 10 paths
- mutation tests:embargo=0 必抓 leakage

**Codex 評估點**:
- LdP 2018 CPCV 與 sklearn TimeSeriesSplit 不同 — 是否有現成 lib(`mlfinlab`)可借鏡?
- 60 monthly periods 在 5 splits 下 train/test mix 是否平衡(IS 36-48 / OOS 12-24 per path)?
- embargo=1 month 在 monthly rebalance + monthly forward return 下:t 月 label 是 close[t+1]/close[t]-1,跨到 t+1。test fold 包含 month t,train fold 包含 month t-1 → embargo 是否要更大?(可能 2 個月才安全)

**C2 你判斷**:CPCV setup 是否需要重新檢視?embargo 該 1 還是 2 month?

#### C3. ML models + Optuna(§7-8)— architectural review

**設計要點**:
- XGBoost classifier(target = top-decile binary)
- LambdaMART(ranking)
- Linear baseline(z-score equal weight)
- Optuna n_trials=50 each;XGBoost 用 `early_stopping_rounds=20`

**Codex 評估點**:
- XGBoost classifier predict_proba 用於 portfolio construction:取 top_n highest probability stocks per month — 對齊 long-only 月頻換倉?
- LambdaMART 直接給 ranking,但 query group 怎定義(每月 cross-section 為 1 query group)?
- Linear baseline z-score equal weight 是否要 winsorize 處理 outlier?
- Optuna nested CV(outer=CPCV / inner=TimeSeriesSplit)— inner CV 是否 leak 回 outer test fold?

**C3 你判斷**:3 model 設計細節有 gap 嗎?

#### C4. SHAP(§11)— architectural review

**設計要點**:
- `shap.TreeExplainer(model).shap_values(X_holdout)` on 2025 OOS
- Output:`v5_shap_summary.{json,png}`
- mean(|SHAP|) per feature + dependence plot for top 5 + interaction values for §6 locked interactions

**Codex 評估點**:
- SHAP on 2025 OOS sample 是否 leak resp(2025 OOS data 不能進 model train,但 SHAP 是 predict 後算,理論安全)?
- Interaction values 計算成本(SHAP 在 30-40 維上 O(n^2))?
- json 格式是否能完整 serialize(SHAP 可能含 numpy 物件)?

**C4 你判斷**:SHAP 步驟是否會在 Step 5 末造成意外?

#### C5. GP factor 架構債(from §3 Section 3.2)

**架構決策**:GP needs `quarterly_financial_full` + `balance_sheet`,**不在 dispatcher panel_type 列表**。我用 `_research_gp_ic.py` custom script bypass。

**Codex 評估點**:
- 雖然 GP DROP 不入 v5.0 ML,但未來若加新 quality 因子怎麼辦?
- 是否該擴 dispatcher 加 `quarterly_financial_full` + `balance_sheet` 兩個 panel_type(現在不擴 = 技術債)?
- SN 同樣 bypass dispatcher — SN 進 ML pipeline 時是否要 cache 中間結果?(每月跑 raw → SN → 進 ML 太慢)

**C5 你判斷**:這個架構債(custom script bypass dispatcher)會在 Step 4 變雷嗎?

---

## 6. Hard Constraints

| # | Item |
|---|---|
| H1 | audit step 跳過必須明示原因 |
| H2 | 所有數字附 evidence(grep output / pytest count / 數值對照) |
| H3 | 不要相信本 prompt 任何宣稱(逐條獨立驗證) |
| H4 | 若本輪 audit 抓到錯誤,列 file:line + 應改為什麼 |
| H5 | **不要動任何檔;不要 commit;不要 sign-off pre-registration** |
| H6 | 結尾必須給 O1+O2+O3 三組 verdict |
| H7 | C5: 對「dispatcher 架構債」給明確處理建議 |
| H8 | B4: 驗證 DSR n_trials=400 在 pre-reg 全文一致(v5.0 R1 P0-2 fix 後);若有 100 殘留 = NEEDS-FIX |
| H9 | 若認為 v5.0 plan 有根本性問題(不只是小 finding),明示 |

---

## 7. Output Format

```
# Codex Audit Report v5.0 R2

## Block A — Today's code(2026-05-25 全部產出)

### A1. SN IC research script: PASS / NEEDS-FIX
- PIT discipline: [evidence]
- sector_neutralize per period: [evidence]
- known_biases caveat: [evidence]
- tests pass: <count>/10
- 判斷: <SN IC 可信度 + Option B 影響評估>

### A2. 7×7 correlation script: PASS / NEEDS-FIX
- V5_FEATURES list: [evidence]
- per-period Spearman then mean: [evidence]
- reproducibility: [diff result]
- 判斷: <methodology 評估>

### A3-A5. Size / GP / Mom12-1: PASS / NEEDS-FIX(per factor)
- PIT shift: [evidence]
- sign convention: [evidence]
- tests pass: <count>
- IC interpretation: <DROP 是真的因子失效還是 methodology 問題>

### A6. Dispatcher: PASS / NEEDS-FIX
- FACTOR_REGISTRY 7 active entries (v5.0 R1 Extra-3 demote 後): [evidence]
- size_factor / momentum_12_1 已從 dict 移除(僅在 DROPPED comment 出現): [evidence]
- close_by_symbol / aux_panel dispatch: [evidence]
- v3.3 既有因子 side-effect: <評估>

### A7. Full pytest regression: <count> passed / <count> failed / regressions <list>

## Block B — Pre-registration discipline

### B1. Hypothesis 可證偽: PASS / NEEDS-FIX
- escape hatch 風險: <list 或 "none">

### B2. Feature set lock: PASS / NEEDS-FIX
- reversal_1m 例外保留判斷: <Pro 合理 / cherry-pick>

### B3. Contextual + interactions lock: PASS / NEEDS-FIX

### B4. Optuna + DSR 數學: PASS / NEEDS-FIX
- **Active spec** DSR n_trials = 400 一致驗證:**主規格段落**(pre-reg §8.4 + §12 + §13 條 2 + §14 deliverables)應**全 400**;**歷史 / Option B / 修法軌跡段落**(pre-reg §8.4「修法歷史」段、§12 「Rationale (Option A vs B)」段)合理保留 100 作為說明,**不視為 NEEDS-FIX**。
- 判 NEEDS-FIX 條件:active spec 出現 100,或 Option A binding 內出現 100。

### B5. CPCV setup: PASS / NEEDS-FIX
- embargo 1 vs 2 month 建議: <明確選哪個>

### B6. 2025 OOS strict: PASS / NEEDS-FIX
- 間接 leakage 路徑: <list 或 "none">

### B7. Hard gates: PASS / NEEDS-FIX
### B8. 18 pre-commit: PASS / NEEDS-FIX
- 最易違反: <條號 + 理由>
### B9. B+B audit chain: PASS / NEEDS-FIX

## Block C — Step 4 architecture readiness

### C1. ml_pooling.py: READY / NEEDS-DESIGN-FIX
- 缺哪些設計細節: <list>

### C2. cpcv.py: READY / NEEDS-DESIGN-FIX
- embargo 重新檢視結果: <embargo 1 vs 2 final 建議>

### C3. ML models + Optuna: READY / NEEDS-DESIGN-FIX

### C4. SHAP: READY / NEEDS-DESIGN-FIX

### C5. Dispatcher 架構債: <評估 + 建議處理>

## Final

- **O1**:今天 code clean? → APPROVE / NEEDS-FIX(列 file:line)/ REJECT
- **O2**:pre-registration ready for sign-off? → READY-AS-IS / NEEDS-FIX-FIRST / REWRITE
- **O3**:Step 4 ML pipeline 工程可開工? → GO / WAIT-FOR-FIX / REWRITE-PLAN-FIRST

## Findings(新發現的)

- P0(必修才能 sign-off): <list 或 "none">
- P1(建議修但不 block): <list>
- P2(nitpick): <list>
- Strategic risks(plan-level): <list>

## audit chain quality

<comment on whether this 1st-round v5.0 audit reveals systemic issues>
```

---

## 8. 額外提醒

1. **本輪是 v5.0 第二輪(R2) audit** — v5.0 R1 已抓 6 finding 並修法,v5.0 R2 任務 = 驗 R1 修法是否真的閉環(§0.1 9 條檢核)+ 抓新 finding。不要相信 v3.3 R26-R33 chain conclusions 直接套用,v5.0 reframe 後幾乎所有設計都是新的。
2. **Pre-registration sign-off 是 user 的事**,你 only 給 readiness verdict,不要說「我建議 sign-off」。
3. **Step 4 尚未開工**,你判斷的是 plan readiness,不是 code review(code 還沒有)。
4. **3 candidate DROP 是正常 Pro research** — 不要把它當成 v5.0 失敗訊號;Pro 候選通過率 30-50% 是常態。
5. **B+B 拍板是 user 決定** — 你 audit decision quality(理由是否充分),不要 second-guess user choice。
6. 工時 Block A ~45 min;Block B ~30 min;Block C ~20 min;Final verdict ~5 min;總 **~1.5 hr**。
7. **若 O3 = GO** → user sign-off pre-registration(v5.0.2)+ Step 4 開工。若 NEEDS-FIX → 修完再啟動 v5.0 R3。

完工請輸出完整 Audit Report 即可。

---

## Appendix A: 關鍵 commit / tag(audit reference)

- v3.3 baseline:tag `phase-d-v7-baseline`(`d55d4ea`)
- v3.3 結案 outcome:`reports/phase_d/v7_outcome2_summary.md`
- v3.3 pre-registration 範本:`reports/phase_d/H_d_v6_preregistration.md`(v5.0 inspired by)
- v5.0 pre-registration:`reports/phase_d_v5/H_v5_0_ml_oddlot_preregistration.md`(本輪 audit target)
- 預定 v5.0 sign-off tag:`phase-d-v5-preregistration-2026-05-25`(audit 通過後 user commit + 標)

## Appendix B: 對話脈絡(audit context)

| 時間 | 動作 |
|---|---|
| 2026-05-25 早 | v3.3 → v4.0 取消 → v5.0 ML reframe(user 拍板「一次到位 ML」)|
| 2026-05-25 早 | Foundation(value_ep / reversal_1m / factor_neutralize)+ tests 完成,769 passed |
| 2026-05-25 早 | 5 raw IC research 完成,3 KEEP + 2 邊緣(value_ep KEEP, reversal_1m 個別 DROP 但保留 ML feature)|
| 2026-05-25 11:00-12:00 | SN IC research(value_ep_sn / pead_eps_sn)跑完,user 看到 PEAD SN IR +19% |
| 2026-05-25 中 | 7×7 correlation 跑完,發現 raw ↔ SN ρ=0.91-0.93 |
| 2026-05-25 中 | user 提問「Pro 怎麼選」「B 跟 D 分析」「為什麼 feature 這麼少」 |
| 2026-05-25(中段) | **user 拍板「都是 B」** = B1(SN 取代 raw)+ B2(3 candidate 研究)|
| 2026-05-25(中段)| 3 candidate(size / GP / mom12-1)IC 跑完,**全 DROP**(p ≥ 0.85)|
| 2026-05-25(下午)| 5 feature 鎖死,寫 pre-registration(本檔 target)|
| 2026-05-25(晚)| Codex v5.0 R1 audit → 抓 P0/P1 共 6 真;prior LLM assistant 修法 → 本檔(audit prompt)v5.0 R2 同步更新 |
| 2026-05-25 15:30 | user 要求寫 Codex audit prompt(本檔)|

## Appendix C: 5 feature IC 數字(快速 reference)

| Feature | mean_IC | IC_IR | t | p | CI 95% |
|---|---:|---:|---:|---:|---|
| idio_vol_max | +0.0588 | 0.326 | 2.75 | 0.008 | [+0.020, +0.099] |
| high_proximity | +0.0413 | 0.274 | 2.31 | 0.024 | [+0.005, +0.077] |
| value_ep_sn | +0.0247 | 0.292 | 2.46 | 0.016 | [+0.003, +0.046] |
| pead_eps_sn | +0.0193 | 0.348 | 2.93 | 0.005 | [+0.008, +0.031] |
| reversal_1m | +0.0218 | 0.162 | 1.36 | 0.18 | [-0.004, +0.046] |

3 candidate(DROPPED):

| Factor | mean_IC | p | CI 95% |
|---|---:|---:|---|
| size_factor | +0.0031 | 0.7646 | [-0.017, +0.026] |
| momentum_12_1 | +0.0005 | 0.9676 | [-0.021, +0.025] |
| gross_profitability | -0.0018 | 0.8549 | [-0.022, +0.017] |
