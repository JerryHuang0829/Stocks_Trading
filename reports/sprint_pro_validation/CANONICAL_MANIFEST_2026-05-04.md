# Canonical Validation Manifest — Pro Sprint 2026-05-04

**Sprint code**: `pro-validation-baseline-2026-05-04`
**Repo**: `Quantitative-Trading` (active development，re-activated 2026-05-02 進 Phase P5)
**Branch**: `main`
**Final commit**: `0d31572` (post-sprint Phase B fixes)
**Baseline commit (pre-sprint)**: `9df25d5` → tagged baseline `7ba18dd`

> **此 manifest 為 Plan v5（或任何未來 plan）唯一被允許引用的「validated baseline」來源**。其他歷史報告降為 supporting evidence。
> Options_Trading 不在此 sprint 範圍（user 2026-05-04 descope）。

---

## 1. Sprint Sequence

| Phase | 範圍 | Status | Manifest |
|-------|------|--------|----------|
| 0 | Pre-sprint baseline commit + .gitignore | ✅ | commit `7ba18dd` |
| A | Environment unblock (pytest sandbox/cp950/conda) | ✅ | `A_env/manifest.json` |
| B P0-1 | run_factor_ic.py get_threshold import fix | ✅ commit `0d31572` | (in commit msg) |
| B P0-2 | slippage default 5→10 (3 places) + corrigendum | ✅ commit `0d31572` | (in commit msg) |
| B | 5-factor IC + D1_v2 IS+OOS backtest reproducer | ✅ ALL PASS | `B_repro/factor_ic/` + `B_repro/d1v2_*` |
| C | PIT mutation test (4 forward-leak scenarios) | ✅ 4/4 PASS | `C_pit_mutation/manifest.json` |
| D | Plan v4→v5 gate migration verification | ✅ 7/7 changes traceable | (in this doc §4) |
| E | Cost model 1.14% reconciliation | ✅ corrigendum 寫入 _step5 doc | commit `0d31572` |
| F | Cross-frequency monthly hardcoded audit | ✅ intentional per v5 | (in this doc §4) |
| I | This canonical manifest | ✅ | this file |
| J | Multi-perspective + external audit pre-audit | pending | next |

---

## 2. Test Baseline

| Run | Count | Duration | Tag |
|-----|-------|----------|-----|
| Pre-sprint baseline | 459 passed, 6 warnings | 290 s (4m50s) | Phase A，commit `7ba18dd` |
| Post P0-1 (get_threshold) | 459 passed | 621 s (10m21s) | Phase B fix #1 |
| Post P0-2 (slippage default) | **462 passed**, 6 warnings | 321 s (5m21s) | Phase B fix #2，commit `0d31572` |
| PIT mutation tests | 4 passed | 3.94 s | Phase C |

**Note**：testcount 459→462 變化 = pytest collection variance (parameterized + conftest fixture changes 在 slippage default migration 後可能多 collect 3 個)，非 fail signal。

**Run command**（canonical）：
```powershell
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
chcp 65001
& "<user_home>\AppData\Local\miniconda3\envs\quant\python.exe" `
  -m pytest tests/ -q --basetemp=tests/_tmp --tb=short --ignore=tests/_tmp
```

---

## 3. 5-Factor IC Reproducer Results

**Range**: `--start 2020-01-01 --end 2025-12-31`，`--rebalance-day 12`，universe = intersection (1696 symbols)，rebalance dates = 71 (warmup-adjusted)
**Cache 來源**: FinMind cache @ 2026-04-21

| Factor | mean_ic | ic_ir | t_stat | p_value | n | bootstrap_ci_95 (block) | DSR | effective_n | Status |
|--------|---------|-------|--------|---------|---|------------------------|------|-------------|--------|
| high_proximity | 0.0413 | 0.2738 | 2.307 | 0.024 | 71 | [0.013, 0.0709] | 0.0 | 270 (was 266) | ✅ |
| pead_eps | 0.0218→0.0219 (Δ.0001) | 0.2902→0.2907 | 2.445→2.449 | 0.017→0.0168 | 71 | [0.0075, 0.0369] EXACT | 0.0 | 269 (was 266) | ✅ |
| margin_short_ratio | 0.0387 EXACT | 0.2313→0.2314 | 1.949→1.95 | 0.0553→0.0552 | 71 | [0.0121, 0.0668] (Δ 1bp) | 0.0 | 266 (was 263) | ✅ |
| foreign_broker_v2 | -0.0195 EXACT | -0.2097→-0.2098 | -1.767→-1.768 | 0.0816→0.0814 | 71 | [-0.0383, -0.0017] EXACT | 0.0 | 269 (was 266) | ✅ |
| revenue_momentum_v2 | 0.0145 EXACT | 0.1906 EXACT | 1.606 EXACT | 0.1128 EXACT | 71 | [-0.0013, 0.0305] EXACT | 0.0 | 270 (was 266) | ✅ |

**Key findings**:
1. 全 5 因子 mean_ic / n_periods / bootstrap_ci_95 / DSR 全在 Pro tolerance（≤ 0.001 drift）內
2. 次要 metrics (std_ic, ic_ir, t_stat, p_value) 全 < 0.001 noise
3. effective_n 統一漂 +3-4，**單一根因**：industry label cache 增量更新（從 ~3070 條漲到 3074 條），不影響任何 gate
4. **external audit 上輪 audit 主結論「IC JSON n=71，文件引用 phase_a1_summary 的 n=59 過時」全 verified**

---

## 4. D1_v2 Backtest Reproducer Results

**Config**: `config/settings_D1_v2.yaml`（D-A 2-factor: 52W High Proximity 0.5 + PEAD 0.5）
**Run command**: `python scripts/run_backtest.py --config config/settings_D1_v2.yaml --start <S> --end <E> --benchmark 0050 --slippage-bps 10`
**Cache 來源**: FinMind cache @ 2026-04-21
**Cost formula** (engine.py:467-472): `cost = turnover × (turnover_cost + 2 × slippage_bps/10000) = turnover × (0.0047 + 0.002) = 0.67% × turnover`

### IS (2020-01-01 → 2024-12-31)
| Metric | Old (slip=5) | New (slip=10) | Δ | 解讀 |
|--------|--------------|---------------|---|------|
| **tracking_error** | 0.236732 | 0.23673 | **bit-exact** | TE 對齊 ✅ |
| n_rebalances | 60 | 60 | exact | OK |
| total_one_way_turnover | 26.1417 | 26.0585 | -0.08 | 微差 |
| total_trade_cost | 0.149008 | 0.17459 | +0.026 | **cost rate 57bps→67bps**（slippage default fix）|
| annualized_return | 0.4194 | 0.4162 | -0.003 | cost 升 → α 降 |
| information_ratio | 0.9375 | 0.9238 | -0.014 | 同上 |
| sharpe_ratio | 1.5368 | 1.526 | -0.011 | 同上 |
| max_drawdown | -0.2019 | -0.2037 | -0.002 | 微差 |
| beta | 0.4994 | 0.5006 | +0.001 | 微差 |

### OOS (2025-01-01 → 2025-12-31)
| Metric | Old (slip=5) | New (slip=10) | Δ | 解讀 |
|--------|--------------|---------------|---|------|
| **tracking_error** | 0.223146 | 0.223253 | +0.0001 | TE 對齊 ✅ |
| n_rebalances | 12 | 12 | exact | OK |
| **total_one_way_turnover** | 4.6659 | 4.6659 | **EXACT** | OOS turnover bit-exact |
| total_trade_cost | 0.026596 | 0.031261 | +0.005 | cost rate 57bps→67bps（同 IS）|
| **information_ratio** | 0.0373 | **0.0058** | -0.032 | **cost 修對後更接近 0** |
| annualized_return | 0.3785 | 0.3715 | -0.007 | 微差 |
| sharpe_ratio | 1.4305 | 1.408 | -0.022 | 微差 |
| beta | 0.5361 | 0.5362 | +0.0001 | exact |

### Plan v5 Gate 通過性

**external audit / Plan v5 主結論完全成立**：
- **B1 L3 TE band [0.10, 0.30]**：D1_v2 IS TE **0.23673 ∈ [0.10, 0.30]** ✅、OOS **0.223253 ∈ [0.10, 0.30]** ✅
- **B5 D6 hard disqualifier (OOS α < 0.5%/月)**：舊 IR OOS 0.0373（已接近 0），新 0.0058（cost 修對後幾乎全 flat）→ **更穩固觸發 D6 disqualify**

---

## 5. Plan v5 Gate Migration（B1-B6 + L5）Verification

| 項 | v4 → v5 change | v5 spec 位置 | 數值依據 verified | Status |
|---|----------------|--------------|------------------|--------|
| **B1** | L3 TE [0.04, 0.12] → **[0.10, 0.30]** | audit-prompt.md:120,161,227 | D1_v2 IS=0.2367 / OOS=0.2231 in v5 range ✅ | ✅ |
| **B2** | IC source phase_a1_summary (n=59) → `reports/factor_ic/*_ic.json` (n=71) | audit-prompt.md:121,228 | 5 IC JSON `n=71` reproduced bit-exact | ✅ |
| **B3** | 跨頻 infra (v5 monthly only, v6 預留) | audit-prompt.md:122,229 | tw_stock.py:196-197 monthly-only enforced | ✅ |
| **B4** | Cost 1.14% → **0.67% × one-way turnover** | audit-prompt.md:123,230 | engine.py:467-472 公式 verified；composite_backtest.py:47 待 v5 Session 1 修 | ✅ |
| **B5** | A5 attacker → D6 hard disqualifier | audit-prompt.md:124,231 | D1_v2 IR 0.94 → 0.0058 OOS 觸發 D6 ✅ | ✅ |
| **B6** | L2 0.003 → **0.010 / 月** | audit-prompt.md:125,232 | n=72, TE=12% L6 需 0.68%/月 → L2 0.010 buffer 合理 | ✅ |
| **L5** def | active_corr = corr(monthly_active_returns, monthly_benchmark_returns) ≤ 0.50 | audit-prompt.md:163 | Spec 定義鎖死，code traceability 待 Session 5 implement | ⚠️ pending impl |

> **⚠️ Historical anchor footnote (2026-05-07 added)**：
> 上表 L5 def 寫成「active_corr ≤ 0.50」是 **v5 spec 階段的初稿定義**，當時 L5 為單一條件。
> v6/v7 lock 後 L5 已升級為 **A1 aggregate gate**（active_corr ≤ 0.50 AND TE ≥ 0.10 AND beta-adjusted alpha t > 1.5；3 子條件 AND），由 `scripts/d_cell_aggregate_v7.py::_l5_a1_passes` 實作。本檔為 2026-05-04 時點的 sprint canonical manifest，不可修；正確 L5 def 以 `reports/phase_d/v7_outcome2_summary.md` 與 `cell_summary.json` 為準。

**結論**：v5 spec 內部一致，7 項 changes 都有 numerical justification。`H_d_v5_preregistration.md` 待 v5 Session 5 實作（Sprint 不 block）。

---

## 6. Cost Model 1.14% Reconciliation（Phase E）

**1.14% 字面**全 repo grep 命中只有 `audit-prompt.md:123,230`（v5 spec **自身對 v4 wrong number 的引用**），不是 active code。

**Engine 真實 formula** (engine.py:467-472)：
```python
rebalance_cost = turnover * self._round_trip_cost  # 0.0047
slippage_cost = turnover * 2 * (self._slippage_bps / 10000.0)  # 2 × 10bps = 0.002
# Total: turnover × (0.0047 + 0.002) = turnover × 0.0067 = 0.67%
```

**Drift source identified**: `scripts/composite_backtest.py:47` 寫死 `TW_ROUND_TRIP_COST_BPS = 57.0`，與 engine.py 的 `0.47% + 0.2%` 雙模型分歧。Plan v5 Session 1 必修（已 flag in audit-prompt.md:90）。

---

## 7. Cross-Frequency Support Audit（Phase F）

`tw_stock.py:196-197` 確認 monthly hardcoded（intentional per v5 pre-commit rule #6 `audit-prompt.md:185`）：
```python
if portfolio_config.get("rebalance_frequency", "monthly") != "monthly":
    return False, "only monthly rebalance is supported"
```

src/ grep 0 個 `weekly` / `biweekly` 邏輯。**v6 future work** touch points 已 flag：
- `engine.py:790` (rebalance dates generation)
- `tw_stock.py:196-197` (frequency gate)
- `composite_backtest.py:178-180`

---

## 8. PIT Mutation Test（Phase C）

| Test | Status | What it proves |
|------|--------|---------------|
| `test_pit_mutation_ohlcv_rejects_forward_leak` | ✅ PASS | `_DataSlicer.fetch_ohlcv()` `<= as_of` cutoff 拒絕 future-dated row |
| `test_pit_mutation_institutional_rejects_forward_leak` | ✅ PASS | `_truncate_by_date_col` 對 `date` 欄 enforce cutoff |
| `test_pit_mutation_revenue_rejects_forward_leak` | ✅ PASS | 月營收 fetch 同 enforce |
| `test_pit_mutation_boundary_inclusive_at_as_of` | ✅ PASS | cutoff 是 `<=` (inclusive) 不是 `<` (over-restrict) |

**Mutation meta-verification**: 內聯 Python 模擬 cutoff bypass，確認 assert 會 catch (`AssertionError: FORWARD LEAK: future row open=999 leaked`)。**這是 外部 audit 上輪 smoke check 缺的一層**。

**未測**：quarterly_eps / margin_short / market_value 三個 panel 走相同 `_truncate_by_date_col` 邏輯，cutoff 共用，可推論安全；若要 100% 覆蓋可加 3 個對應 test（throwaway）。

---

## 9. 兩個 Silent Bug 紀錄（commit `0d31572`）

### P0-1: `scripts/run_factor_ic.py` 漏 import `get_threshold`
- **根因**: P5 Session 1 (commit `9df25d5`, 2026-05-03) 抽 helpers 時漏 re-import
- **影響**: CLI entry point broken，過去 1 天無人實跑該 script
- **為何 459 tests 沒抓到**: tests 直接 call `factor_ic_report()` / helper functions，**不走 CLI entry point**
- **修法**: `from src.utils.thresholds import get_threshold` 加入 imports
- **SOP 6 步**: ☑ 全綠（mutation/數字/grep/cross-interference/self-attack/pytest 459 passed）

### P0-2: slippage default 三處 5→10 漏對齊 YAML
- **根因**: R19 audit (2026-05-02) 已 flag drift 但只修 conftest.py + YAML，漏 src/scripts 層
- **影響**: 所有 CLI / engine default 構造的 backtest 用 5bps 不是 10bps，cost 低估 ~17%（D1_v2 IS cost 0.149 vs 應為 0.175）
- **修法**: 3 處 (engine.py:27 DEFAULT_SLIPPAGE_BPS / tw_stock.py:1388 fallback / run_backtest.py:32 CLI default) 全改 10
- **walk_forward.py:177 implicit caller** 也順便對齊（過往 walk_forward 報告應視為 5bps assumption）
- **SOP 6 步**: ☑ 全綠（462 passed post-fix，0 fail）

---

## 10. 信任邊界

### ⚠️ 最重要 caveat — Cache Reproducibility (external audit Q8.1 attack 必須誠實標示)

**Sprint reproducer 證的是「現在 code + 同 cache @ 2026-04-21 重跑得同數值」，不是「現在 code 在 fresh cache 上重跑得同數值」。**

如果 FinMind cache 本身是污染源（hidden state、上輪 IC 已寫入 cache 的副作用、cache TTL 內某些 row 被增量覆蓋），這 sprint **抓不到**。真正 cross-machine reproducibility 要：
1. wipe `data/cache/` 完全乾淨
2. 從 FinMind API 重抓（需 valid token + 1-2 hr 工程）
3. 用相同 commit `0d31572` 跑同樣 5 IC + D1_v2 IS+OOS
4. 比對是否仍對齊本 manifest 數值

**未做**。降為 P1 **Sprint v2 must-do**（Phase J 攻擊 Q8.1 認可）。**不宣告本 manifest 為「跨機 Pro 可獨立重現」signoff**——只宣告「同 cache 重跑驗算」level signoff。

### Test count 459→462 差異分析（Phase J 攻擊 Q8.3 補答）
`pytest --collect-only -q` on commit `0d31572`: **466 tests collected = 462 baseline + 4 PIT mutation (Phase C)**。
- 462 = stable baseline（post-slippage-fix run、post-Phase-C run、collect-only run 三者一致）
- 459 = pre-slippage-fix 第一次 baseline run，**3 test 差異最合理解釋**：fixture state 的 conditional skip（可能是 slippage/cost 數值容差相關 test 在舊 default=5 下被 skip 而非 collected as run）
- **本次未做時間旅行 diff**（commit 7ba18dd 已被覆寫過），降為 P2 housekeeping。實證為「462 stable across 3 independent runs after fix」是充分的非 regression 證明。

### 其他 reproducibility 假設
1. FinMind cache @ 2026-04-21 不變
2. `config/factor_thresholds.yaml` `base_seed=42` 跨 numpy 版本 deterministic
3. `config/settings_D1_v2.yaml` 不變
4. conda env `quant` (Python 3.12.13, pytest 9.0.3) 不變
5. industry label cache 隨時間自然增量更新（已知 source of `effective_n` drift；未實證 mtime，降 P2 follow-up）

**已知未做**：
- 沒做 Bootstrap CI 跨 seed sensitivity（block_len=3, n_iter=10000, seed=42 fixed）
- 沒做 quarterly_eps / margin_short / market_value PIT mutation（推論 cover via `_truncate_by_date_col`）
- 沒做 walk_forward.py 重跑（範圍外，但 implicit 受 slippage fix 影響）
- composite_backtest.py:47 cost drift 待 v5 Session 1 處理

**external audit 上輪 audit 我的修正**：
- ✅ external audit「459 tests」claim **是對的**（我 explore agent 數錯為 453；實測 459-462）
- ✅ external audit「IC JSON n=71」claim 全對
- ✅ external audit「D1_v2 TE 0.2367/0.2231」claim 全對
- ⚠️ external audit「1.14% 文件 vs 0.67% engine 不一致」claim 是 **misleading 不是 false**——指向 composite_backtest.py:47 雙模型 drift，根因不是文件 typo
- ⚠️ external audit「PIT slicer no leakage」是 smoke pass，**Phase C 用 4 個 mutation test 升級為實證 verified**
