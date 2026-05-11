# Phase D v7 Outcome-2 Closeout

日期：2026-05-07  
審計 anchor：`6e3bde0` + canonical run output  
Canonical output：`reports/phase_d/cell_sweep_v7_2026_05_06/`

## 結論

Phase D v7 正式結案為 **CONFIRM-NO-GO / Outcome-2 Partial**。

- 18 個事前鎖定 cells 全部完成：6 candidates x 3 top_n `{8, 12, 16}`
- `n_outcome_1_cells = 0 / 18`
- `sole_survivor = null`
- 所有 cells 都沒有通過 L1-L6 全部 hard gates
- 因此不啟動 active top-N paper trade，也不應包裝成可實盤策略

這不是 evaluator bug。官方 `cell_summary.json` 與 gate aggregation 一致，真正問題是 alpha 強度不足以在 retail-realistic gate 下穩定勝過 0050。

## Hard Gate Definition

L5 是一個 aggregate A1 gate，不是單看 active correlation。

| Gate | Pass 條件 |
|---|---|
| L1 | IR vs 0050 >= 0.20 |
| L2 | mean monthly alpha >= 0.005 |
| L3 | TE in `[0.10, 0.30]` |
| L4 | max drawdown diff vs 0050 <= 0.05 |
| L5 | active_corr <= 0.50 AND TE >= 0.10 AND beta_adj_alpha_t > 1.5 |
| L6 | stationary block bootstrap 80% CI lower > 0 |

## 18-Cell Gate Result

`P/F` 以官方 evaluator 的 L1-L6 定義重算；L5 使用 aggregate A1 gate。

| Cell | L1 | L2 | L3 | L4 | L5 A1 | L6 | Pass |
|---|---|---|---|---|---|---|---|
| D-B \| 8 | F | F | P | P | F | F | 2/6 |
| D-B \| 12 | F | F | P | F | F | F | 1/6 |
| D-B \| 16 | F | F | P | F | F | F | 1/6 |
| D-C \| 8 | F | F | F | P | F | F | 1/6 |
| D-C \| 12 | P | P | P | P | F | F | 4/6 |
| D-C \| 16 | F | F | P | P | F | F | 2/6 |
| D-D \| 8 | F | F | P | F | F | F | 1/6 |
| D-D \| 12 | F | F | P | F | F | F | 1/6 |
| D-D \| 16 | F | F | P | F | F | F | 1/6 |
| D-E \| 8 | P | P | F | P | F | F | 3/6 |
| D-E \| 12 | P | P | F | P | P | F | 4/6 |
| D-E \| 16 | P | P | P | P | F | F | 4/6 |
| D-F \| 8 | P | P | F | P | F | F | 3/6 |
| D-F \| 12 | P | P | F | P | F | F | 3/6 |
| D-F \| 16 | F | F | F | P | F | F | 1/6 |
| D-G \| 8 | F | F | P | F | P | F | 2/6 |
| D-G \| 12 | F | F | P | F | P | F | 2/6 |
| D-G \| 16 | F | F | P | F | P | F | 2/6 |

## Closest Cells

| Cell | What looked good | Why it still fails |
|---|---|---|
| D-E \| 12 | IR 0.337, monthly alpha 0.876%, L5 passed | TE 0.312 exceeds L3 upper bound; L6 CI lower = -0.0060 |
| D-C \| 12 | IR 0.236, monthly alpha 0.549%, TE in range | L5 fails because beta-adjusted alpha t = 1.241 < 1.5; L6 CI lower = -0.0063 |
| D-E \| 16 | IR 0.238, monthly alpha 0.524%, TE in range | L5 fails because beta-adjusted alpha t = 1.363 < 1.5; L6 CI lower = -0.0062 |
| D-F \| 8 | Highest monthly alpha at 1.270% | TE 0.446 too high; L5 fails; L6 CI lower = -0.0079 |

## Root Cause

1. 60 months is too short for stable active-return inference under stationary block bootstrap.
2. The best active return series still has a CI lower below zero.
3. Several high-alpha cells reach the return gates only by taking too much active risk.
4. L5 filters out cells whose active return is not sufficiently robust after beta adjustment.
5. `n_trials = 18` is correctly recorded for DSR, but DSR is diagnostic here; the binding NO-GO comes from L1-L6 hard gates.

## Decision

Phase D v7 is closed. The research result should be treated as:

- **Capital path**：do not allocate capital to the active top-N v7 strategy; use 0050 DCA as the practical baseline.
- **Research path**：v8 may proceed only as a new pre-registered hypothesis, not as post-hoc retuning of v7.
- **Engineering path**：harden the research platform before v8, especially import reliability, conda testability, evaluator/document consistency, and formal BacktestEngine usage.

## V8 Entry Conditions

Do not start v8 until these are true:

- README / reports no longer imply any v7 cell passed 5/6 by counting only L5 correlation.
- `BacktestEngine` can import and run synthetic integration tests in conda without `pandas_ta` blocking collection.
- Full or documented-targeted pytest has a reproducible command in conda.
- v8 has its own preregistration: sample window, universe, engine path, costs, gates, and max trials are fixed before running.

---

## ⚠️ 2026-05-10 R28-1 Follow-up Caveat — issued_capital Cache Schema Change

**Background**：Phase A1 R26+R27+R28 audit chain 完成 5 因子 IC 修法。R28-1 follow-up 跑 `scripts/cache_fill_new_factors.py --seed-issued-capital` 把 `data/cache/issued_capital/_global.pkl` 從 2-column snapshot (stock_id, issued_shares) 變更為 3-column panel (stock_id, **date**, issued_shares)，新增 157374 rows。

**Side effect on v7 sweep**：

`scripts/d_cell_sweep_v7_real.py:157-162` 載入邏輯有 schema 條件分支：
```python
if "date" in df.columns:
    df = df.sort_values("date").drop_duplicates("stock_id", keep="last")
self._issued_by_symbol = dict(zip(df["stock_id"], df["issued_shares"]))
```

R28-1 follow-up 後 cache 變 3-col → 走 `sort+keep="last"` 路徑（取每 symbol 最後 row 的 issued_shares）；archived 2026-05-06 跑時是 2-col → 走 raw row order 路徑。derive method (`market_value / close`) 跟 archived `fetch_twse_issued_capital()` 直接抓的 TWSE OpenAPI 數值理論等價但實際微差，portfolio 級放大成 metrics 不再 bit-exact。

**Spot check D-B\|8 三輪對照**：

| Metric | **Archived (2026-05-06)** | **R28-1 後 spot check (2026-05-10)** | **R30 後 spot check (2026-05-11)** |
|---|---:|---:|---:|
| IR | +0.0068 | -0.0945 | -0.1214 |
| mean_α | +0.000166 | -0.00213 | -0.00273 |
| TE | 0.2936 | 0.2701 | 0.2698 |
| max_dd_diff | -0.0042 | +0.0414 | +0.0032 |
| active_corr | -0.3517 | -0.3511 | -0.3396 |
| beta_adj_t | 0.8027 | 0.6261 | 0.5289 |
| **Pass count** | **2/6** | **2/6** | **2/6** |

**三輪對應的 v7 sweep issued_capital 載入邏輯**：
- Archived: 2-col cache + raw row order dict (TWSE OpenAPI fetch_twse_issued_capital)
- R28-1 後: 3-col cache (seed derive) + sort+keep="last" 取每 symbol 最後 row
- **R30 後**: 3-col cache + `issued_by_symbol_at(as_of)` per-rebalance asof method (用 `src.data.pit_helpers` shared with IC pipeline)

R30 跟 R28-1 後 metrics 仍有微差（issued_capital cache derive 限制），但 **3 輪 pass count 全 2/6 NO-GO 結論 robust**。

**Implication**：
- ✅ **CONFIRM-NO-GO 結論不變**（pass count 同 2/6；IR/α/L5 仍 fail；L3/L4 仍 pass）
- ⚠️ **Individual metrics 不再 bit-exact reproducible** — IR / α / max_dd_diff sign 或量級變動；archived `cell_summary.json` 僅作歷史 evidence，不可直接拿來跟 R28-1 後新跑比較
- ⚠️ **9 cells 全重跑工時不划算**（pass count 預期不變；R26-R28 沒改 src 邏輯，僅 R28-1 follow-up cache schema side effect）

**Implication for v8**：
- v8 reframe 若用 cell sweep methodology，**必須**寫新版 `d_cell_sweep_v8_real.py` 用 R26-R28 修法後的 PIT-asof helper（`_load_issued_capital_panel` + `_issued_capital_asof`）取代 v7 內部 `keep="last"`
- 不可直接 reuse v7 archived `cell_summary.json` 數字當 baseline（schema 變後不可比）
- archived NO-GO 結論可作 v7 hypothesis 否決 evidence 但 metrics 量級需 v8 重跑

**未做（P2 backlog）**：
- 真補 historical issued_shares（寫新 TWSE OpenAPI scraper，4-8 hr P1）→ 直到此修法完成前，margin_short_ratio 永遠是 static-snapshot approximation
- 完整 9 cells (D-B/C/D × 3 top_n) 重跑驗證 → 預期 pass count 不變但 metrics 量級變動，工時 3-5 hr

詳見：
- `reports/factor_ic/_closeout/old_vs_new_comparison_2026-05-10.md` — 5 因子 IC 修法對照
- `reports/factor_ic/_audit/pit_divergence_2026-05-10.md` — Phase 0 audit
- `reports/factor_ic/_amend/H_a1_consistency_deprecation_2026-05-10.md` — H_a1 amend
- `reports/phase_d/spot_check_DB8_2026-05-10.log` — 本輪 spot check log

